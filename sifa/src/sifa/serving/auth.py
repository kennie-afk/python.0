from __future__ import annotations

import hashlib
import hmac
import os
import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass
from enum import IntEnum
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status

MIN_KEY_LENGTH = 24


class ConfigurationError(RuntimeError):
    pass


class Role(IntEnum):
    VIEWER = 1
    OPERATOR = 2
    ADMIN = 3


@dataclass(frozen=True, slots=True)
class Principal:
    key_id: str
    role: Role

    @property
    def actor(self) -> str:
        return f"{self.role.name.lower()}:{self.key_id}"


def _parse(entry: str) -> tuple[str, Role]:
    """`key:role`; a bare key keeps its old meaning and is an admin."""
    key, separator, label = entry.rpartition(":")
    if separator and label.strip().upper() in Role.__members__:
        return key.strip(), Role[label.strip().upper()]
    if separator and label.strip().isalpha() and len(label.strip()) < MIN_KEY_LENGTH:
        raise ConfigurationError(
            f"unknown role {label.strip()!r} in SIFA_API_KEYS; use viewer, operator or admin"
        )
    return entry.strip(), Role.ADMIN


def configured_principals() -> list[tuple[str, Role]]:
    """Several keys may be listed at once, which is how a key is rotated: add the new one, move
    the clients over, then remove the old one."""
    raw = os.environ.get("SIFA_API_KEYS", "")
    entries = [_parse(item) for item in raw.split(",") if item.strip()]

    if not entries:
        raise ConfigurationError(
            "SIFA_API_KEYS is not set; the platform refuses to serve without authentication"
        )
    if any(len(key) < MIN_KEY_LENGTH for key, _ in entries):
        raise ConfigurationError(
            f"every SIFA_API_KEYS entry must be at least {MIN_KEY_LENGTH} characters"
        )
    return entries


def configured_keys() -> tuple[str, ...]:
    return tuple(key for key, _ in configured_principals())


def key_id(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()[:8]


def authenticate(x_api_key: Annotated[str | None, Header()] = None) -> Principal:
    if not x_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="an X-Api-Key header is required",
        )

    matched: Principal | None = None
    for candidate, role in configured_principals():
        # Compare against every key so timing does not reveal which entry nearly matched.
        if hmac.compare_digest(x_api_key, candidate) and matched is None:
            matched = Principal(key_id(candidate), role)
    if matched is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="that api key is not valid"
        )
    return matched


def require_api_key(principal: Annotated[Principal, Depends(authenticate)]) -> str:
    return principal.key_id


def requires(minimum: Role) -> Callable[..., Principal]:
    def dependency(principal: Annotated[Principal, Depends(authenticate)]) -> Principal:
        if principal.role < minimum:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"this needs the {minimum.name.lower()} role; this key is "
                f"{principal.role.name.lower()}",
            )
        return principal

    return dependency


class RateLimiter:
    """Sliding window per key and action, in process memory (single replica, see the README)."""

    def __init__(self) -> None:
        self._hits: dict[tuple[str, str], deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str, action: str, limit: int, window: float = 60.0) -> None:
        now = time.monotonic()
        with self._lock:
            hits = self._hits[(key, action)]
            while hits and now - hits[0] > window:
                hits.popleft()
            if len(hits) >= limit:
                retry = max(1, int(window - (now - hits[0])) + 1)
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=f"{action} is limited to {limit} per minute per key",
                    headers={"Retry-After": str(retry)},
                )
            hits.append(now)

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


limiter = RateLimiter()


def rate_limited(action: str) -> Callable[..., None]:
    def dependency(principal: Annotated[Principal, Depends(authenticate)]) -> None:
        limit = int(os.environ.get("SIFA_EXPENSIVE_PER_MINUTE", "12"))
        limiter.check(principal.key_id, action, limit)

    return dependency
