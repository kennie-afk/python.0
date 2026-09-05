from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import jwt
import pytest

from aegis.auth import (
    AuthError,
    Principal,
    TokenExpiredError,
    TokenService,
    generate_api_key,
    hash_api_key,
)

SECRET = "a-signing-secret-that-is-long-enough-to-use"
TENANT = "88888888-8888-8888-8888-888888888888"


def service(**kwargs: object) -> TokenService:
    return TokenService(secret=SECRET, **kwargs)  # type: ignore[arg-type]


class TestMintingAndVerifying:
    def test_a_minted_token_verifies_back_to_its_principal(self) -> None:
        token = service().mint(TENANT, "hr.partner@example.com", frozenset({"ADMIN"}))

        principal = service().verify(token)

        assert principal.tenant_id == TENANT
        assert principal.subject == "hr.partner@example.com"
        assert principal.has_role("ADMIN")

    def test_a_token_signed_with_another_secret_is_rejected(self) -> None:
        token = service().mint(TENANT, "user")
        other = TokenService(secret="a-completely-different-secret-value-here")

        with pytest.raises(AuthError):
            other.verify(token)

    def test_a_tampered_token_is_rejected(self) -> None:
        token = service().mint(TENANT, "user")
        tampered = token[:-4] + ("aaaa" if not token.endswith("aaaa") else "bbbb")

        with pytest.raises(AuthError):
            service().verify(tampered)

    def test_an_expired_token_is_rejected_distinctly(self) -> None:
        token = TokenService(secret=SECRET, ttl_minutes=0).mint(TENANT, "user")
        time.sleep(1.1)

        with pytest.raises(TokenExpiredError):
            service().verify(token)

    def test_a_token_for_another_audience_is_rejected(self) -> None:
        token = TokenService(secret=SECRET, audience="other-api").mint(TENANT, "user")

        with pytest.raises(AuthError):
            service().verify(token)

    def test_a_token_from_another_issuer_is_rejected(self) -> None:
        token = TokenService(secret=SECRET, issuer="somebody-else").mint(TENANT, "user")

        with pytest.raises(AuthError):
            service().verify(token)

    def test_garbage_is_rejected_rather_than_crashing(self) -> None:
        with pytest.raises(AuthError):
            service().verify("not-a-token")

    def test_a_short_secret_is_refused_at_construction(self) -> None:
        with pytest.raises(ValueError, match="at least 32"):
            TokenService(secret="too-short")


class TestRoles:
    def test_a_missing_role_is_reported_by_name(self) -> None:
        principal = Principal(tenant_id=TENANT, subject="user", roles=frozenset({"VIEWER"}))

        with pytest.raises(AuthError, match="ADMIN"):
            principal.require_role("ADMIN")

    def test_a_present_role_passes(self) -> None:
        Principal(tenant_id=TENANT, subject="user", roles=frozenset({"ADMIN"})).require_role(
            "ADMIN"
        )

    def test_roles_survive_the_round_trip(self) -> None:
        token = service().mint(TENANT, "user", frozenset({"ADMIN", "REVIEWER"}))

        assert service().verify(token).roles == frozenset({"ADMIN", "REVIEWER"})


class TestApiKeys:
    def test_a_generated_key_is_prefixed_and_unguessable(self) -> None:
        key = generate_api_key()

        assert key.startswith("aeg_")
        assert len(key) > 30
        assert key != generate_api_key()

    def test_hashing_is_stable_and_one_way(self) -> None:
        key = generate_api_key()

        assert hash_api_key(key) == hash_api_key(key)
        assert key not in hash_api_key(key)
        assert len(hash_api_key(key)) == 64

    def test_different_keys_hash_differently(self) -> None:
        assert hash_api_key(generate_api_key()) != hash_api_key(generate_api_key())


class TestTenantClaimValidation:
    def test_a_token_carrying_a_malformed_tenant_is_rejected(self) -> None:
        service = TokenService(secret="a" * 32)
        forged = jwt.encode(
            {
                "iss": "aegis",
                "aud": "aegis-api",
                "sub": "attacker@example.com",
                "tid": "not-a-tenant",
                "roles": ["ADMIN"],
                "iat": int(datetime.now(UTC).timestamp()),
                "exp": int((datetime.now(UTC) + timedelta(minutes=5)).timestamp()),
            },
            "a" * 32,
            algorithm="HS256",
        )

        with pytest.raises(AuthError, match="not a valid tenant id"):
            service.verify(forged)

    def test_a_valid_tenant_claim_exposes_a_uuid(self) -> None:
        service = TokenService(secret="a" * 32)
        tenant = str(uuid4())
        principal = service.verify(service.mint(tenant, "hr@example.com"))

        assert principal.tenant_uuid == UUID(tenant)
