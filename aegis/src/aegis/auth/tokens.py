from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt


class AuthError(Exception):
    pass


class TokenExpiredError(AuthError):
    pass


@dataclass(frozen=True, slots=True)
class Principal:
    tenant_id: str
    subject: str
    roles: frozenset[str]

    def __post_init__(self) -> None:
        try:
            UUID(self.tenant_id)
        except (ValueError, AttributeError, TypeError) as error:
            raise AuthError(f"tenant claim {self.tenant_id!r} is not a valid tenant id") from error

    @property
    def tenant_uuid(self) -> UUID:
        return UUID(self.tenant_id)

    def has_role(self, role: str) -> bool:
        return role in self.roles

    def require_role(self, role: str) -> None:
        if not self.has_role(role):
            raise AuthError(f"principal {self.subject!r} lacks the required role {role!r}")


class TokenService:
    def __init__(
        self,
        secret: str,
        issuer: str = "aegis",
        audience: str = "aegis-api",
        ttl_minutes: int = 60,
    ) -> None:
        if len(secret) < 32:
            raise ValueError("signing secret must be at least 32 characters")
        self._secret = secret
        self._issuer = issuer
        self._audience = audience
        self._ttl = timedelta(minutes=ttl_minutes)

    def mint(self, tenant_id: str, subject: str, roles: frozenset[str] = frozenset()) -> str:
        now = datetime.now(UTC)
        return jwt.encode(
            {
                "iss": self._issuer,
                "aud": self._audience,
                "sub": subject,
                "tid": tenant_id,
                "roles": sorted(roles),
                "iat": int(now.timestamp()),
                "exp": int((now + self._ttl).timestamp()),
            },
            self._secret,
            algorithm="HS256",
        )

    def verify(self, token: str) -> Principal:
        try:
            claims = jwt.decode(
                token,
                self._secret,
                algorithms=["HS256"],
                issuer=self._issuer,
                audience=self._audience,
                options={"require": ["exp", "iat", "sub", "iss", "aud"]},
            )
        except jwt.ExpiredSignatureError as error:
            raise TokenExpiredError("token has expired") from error
        except jwt.InvalidTokenError as error:
            raise AuthError(f"token rejected: {error}") from error

        tenant = claims.get("tid")
        if not tenant:
            raise AuthError("token carries no tenant claim")

        return Principal(
            tenant_id=str(tenant),
            subject=str(claims["sub"]),
            roles=frozenset(str(role) for role in claims.get("roles", [])),
        )


def generate_api_key() -> str:
    return "aeg_" + secrets.token_urlsafe(32)


def hash_api_key(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()
