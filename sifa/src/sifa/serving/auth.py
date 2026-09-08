from __future__ import annotations

import hmac
import os
from typing import Annotated

from fastapi import Header, HTTPException, status

MIN_KEY_LENGTH = 24


class ConfigurationError(RuntimeError):
    pass


def configured_keys() -> tuple[str, ...]:
    raw = os.environ.get("SIFA_API_KEYS", "")
    keys = tuple(key.strip() for key in raw.split(",") if key.strip())

    if not keys:
        raise ConfigurationError(
            "SIFA_API_KEYS is not set; the platform refuses to serve without authentication"
        )

    short = [key for key in keys if len(key) < MIN_KEY_LENGTH]
    if short:
        raise ConfigurationError(
            f"every SIFA_API_KEYS entry must be at least {MIN_KEY_LENGTH} characters"
        )

    return keys


def require_api_key(x_api_key: Annotated[str | None, Header()] = None) -> str:
    if not x_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="an X-Api-Key header is required",
        )

    for candidate in configured_keys():
        if hmac.compare_digest(x_api_key, candidate):
            return candidate

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED, detail="that api key is not valid"
    )
