"""Create, list, rotate and revoke API keys; tokens die with their key; sign-in is rate limited."""

from __future__ import annotations

import time

import pytest

from aegis.auth import generate_api_key, hash_api_key
from aegis.persistence import ApiKeyRepository
from conftest import OTHER, TENANT, Gov


def issue(gov: Gov, *roles: str, label: str = "ci", tenant: str = TENANT) -> str:
    """A key created straight in the database, standing in for the one the CLI prints."""
    secret = generate_api_key()
    with gov.platform.database.session() as session:
        ApiKeyRepository(session).issue(tenant, label, hash_api_key(secret), list(roles))
    return secret


def sign_in(gov: Gov, secret: str) -> dict[str, str]:
    response = gov.client.post("/v1/auth/token", json={"api_key": secret})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


def create(gov: Gov, label: str = "svc", roles: tuple[str, ...] = ("VIEWER",)) -> dict[str, object]:
    response = gov.client.post(
        "/v1/keys", json={"label": label, "roles": list(roles)}, headers=gov.headers("ADMIN")
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


def test_an_admin_creates_a_key_and_sees_the_secret_once(gov: Gov) -> None:
    made = create(gov, "reporting", ("VIEWER", "AUDITOR"))

    assert str(made["api_key"]).startswith("aeg_") and made["roles"] == ["AUDITOR", "VIEWER"]
    listed = gov.client.get("/v1/keys", headers=gov.headers("ADMIN")).json()
    assert [k["label"] for k in listed] == ["reporting"]
    assert "api_key" not in listed[0] and "key_hash" not in listed[0]
    assert (
        gov.client.get("/v1/overview", headers=sign_in(gov, str(made["api_key"]))).status_code
        == 200
    )


def test_key_management_is_for_admins_only(gov: Gov) -> None:
    for roles in (("OPERATOR",), ("APPROVER",), ("AUDITOR",), ("VIEWER",)):
        denied = gov.client.post(
            "/v1/keys", json={"label": "x", "roles": ["VIEWER"]}, headers=gov.headers(*roles)
        )
        assert denied.status_code == 403
        assert gov.client.get("/v1/keys", headers=gov.headers(*roles)).status_code == 403


def test_unknown_roles_are_refused(gov: Gov) -> None:
    for roles in (["SUPERUSER"], ["viewer", "root"], []):
        response = gov.client.post(
            "/v1/keys", json={"label": "x", "roles": roles}, headers=gov.headers("ADMIN")
        )
        assert response.status_code == 422


def test_revoking_a_key_ends_the_tokens_it_already_issued(gov: Gov) -> None:
    made = create(gov)
    token = sign_in(gov, str(made["api_key"]))
    assert gov.client.get("/v1/overview", headers=token).status_code == 200

    revoked = gov.client.post(f"/v1/keys/{made['key_id']}/revoke", headers=gov.headers("ADMIN"))

    assert revoked.status_code == 200 and revoked.json()["active"] is False
    after = gov.client.get("/v1/overview", headers=token)
    assert after.status_code == 401 and "revoked" in after.json()["detail"]
    assert gov.client.post("/v1/auth/token", json={"api_key": made["api_key"]}).status_code == 401
    assert (
        gov.client.get("/v1/overview", headers={"X-Api-Key": str(made["api_key"])}).status_code
        == 401
    )


def test_revoking_only_the_tokens_keeps_the_key_usable(gov: Gov) -> None:
    made = create(gov)
    old = sign_in(gov, str(made["api_key"]))
    time.sleep(0.01)

    gov.client.post(f"/v1/keys/{made['key_id']}/revoke-tokens", headers=gov.headers("ADMIN"))

    assert gov.client.get("/v1/overview", headers=old).status_code == 401
    time.sleep(0.01)
    fresh = sign_in(gov, str(made["api_key"]))
    assert gov.client.get("/v1/overview", headers=fresh).status_code == 200
    assert gov.client.get("/v1/keys", headers=gov.headers("ADMIN")).json()[0]["tokens_valid_from"]


def test_rotating_replaces_the_key_and_kills_the_old_ones_tokens(gov: Gov) -> None:
    made = create(gov, "pipeline", ("OPERATOR",))
    old_token = sign_in(gov, str(made["api_key"]))

    rotated = gov.client.post(f"/v1/keys/{made['key_id']}/rotate", headers=gov.headers("ADMIN"))
    new = rotated.json()

    assert rotated.status_code == 201 and new["key_id"] != made["key_id"]
    assert new["label"] == "pipeline" and new["roles"] == ["OPERATOR"]
    assert gov.client.get("/v1/overview", headers=old_token).status_code == 401
    assert gov.client.post("/v1/auth/token", json={"api_key": made["api_key"]}).status_code == 401
    assert gov.client.get("/v1/overview", headers=sign_in(gov, new["api_key"])).status_code == 200
    again = gov.client.post(f"/v1/keys/{made['key_id']}/rotate", headers=gov.headers("ADMIN"))
    assert again.status_code == 404  # the old key is already gone


def test_the_last_admin_key_cannot_be_revoked(gov: Gov) -> None:
    only = create(gov, "root", ("ADMIN",))
    refused = gov.client.post(f"/v1/keys/{only['key_id']}/revoke", headers=gov.headers("ADMIN"))
    assert refused.status_code == 409 and "last active ADMIN" in refused.json()["detail"]

    second = create(gov, "deputy", ("ADMIN",))
    assert (
        gov.client.post(
            f"/v1/keys/{only['key_id']}/revoke", headers=gov.headers("ADMIN")
        ).status_code
        == 200
    )
    assert second["key_id"]


def test_one_tenant_cannot_touch_anothers_keys(gov: Gov) -> None:
    made = create(gov)
    stranger = gov.headers("ADMIN", tenant=OTHER)

    assert gov.client.get("/v1/keys", headers=stranger).json() == []
    for action in ("revoke", "rotate", "revoke-tokens"):
        assert (
            gov.client.post(f"/v1/keys/{made['key_id']}/{action}", headers=stranger).status_code
            == 404
        )
    assert (
        gov.client.get("/v1/overview", headers=sign_in(gov, str(made["api_key"]))).status_code
        == 200
    )


def test_key_changes_are_in_the_audit_trail(gov: Gov) -> None:
    made = create(gov, "audited")
    gov.client.post(
        f"/v1/keys/{made['key_id']}/rotate", headers=gov.headers("ADMIN", subject="boss")
    )
    entries = gov.client.get(
        "/v1/ledger/search?workflow=access", headers=gov.headers("AUDITOR")
    ).json()

    assert {(e["action_type"], e["outcome"]) for e in entries} == {
        ("API_KEY_ISSUED", "ISSUED"),
        ("API_KEY_ROTATED", "ROTATED"),
    }
    assert all(e["approver"] for e in entries)
    assert "aeg_" not in str(entries)  # a secret never reaches the ledger


def test_a_token_from_a_key_that_no_longer_exists_is_refused(gov: Gov) -> None:
    secret = issue(gov, "ADMIN")
    token = sign_in(gov, secret)
    with gov.platform.database.session() as session:
        from aegis.persistence import ApiKeyRow

        session.query(ApiKeyRow).delete()

    assert gov.client.get("/v1/overview", headers=token).status_code == 401


def test_repeated_failures_lock_the_sign_in_for_a_minute(
    gov: Gov, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AEGIS_TOKEN_FAILURES_PER_MINUTE", "3")
    good = issue(gov, "VIEWER")

    codes = [
        gov.client.post("/v1/auth/token", json={"api_key": f"aeg_wrong_{i}"}).status_code
        for i in range(5)
    ]
    assert codes == [401, 401, 401, 429, 429]
    locked = gov.client.post("/v1/auth/token", json={"api_key": good})
    assert locked.status_code == 429 and int(locked.headers["retry-after"]) >= 1


def test_successful_sign_ins_are_not_counted_against_the_limit(
    gov: Gov, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AEGIS_TOKEN_FAILURES_PER_MINUTE", "3")
    good = issue(gov, "VIEWER")

    assert all(
        gov.client.post("/v1/auth/token", json={"api_key": good}).status_code == 200
        for _ in range(8)
    )
