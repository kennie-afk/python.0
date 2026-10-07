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
    # The key the token was issued from and when it was issued, so the API can check the key still
    # stands (see api_keys.active / not_before). None for a token not issued from a key.
    key_id: str | None = None
    issued_at: datetime | None = None

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

    def mint(
        self,
        tenant_id: str,
        subject: str,
        roles: frozenset[str] = frozenset(),
        key_id: str | None = None,
    ) -> str:
        now = datetime.now(UTC)
        extra: dict[str, object] = {"kid": key_id} if key_id else {}
        return jwt.encode(
            {
                **extra,
                "jti": secrets.token_hex(8),
                "iss": self._issuer,
                "aud": self._audience,
                "sub": subject,
                "tid": tenant_id,
                "roles": sorted(roles),
                "iat": int(now.timestamp()),
                "iat_ms": int(now.timestamp() * 1000),
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
            key_id=str(claims["kid"]) if claims.get("kid") else None,
            issued_at=datetime.fromtimestamp(
                int(claims.get("iat_ms", int(claims["iat"]) * 1000)) / 1000, UTC
            ),
        )

def generate_api_key() -> str:
    return "aeg_" + secrets.token_urlsafe(32)

def hash_api_key(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()
