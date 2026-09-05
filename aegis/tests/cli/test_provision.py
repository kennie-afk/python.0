from __future__ import annotations

import json
from collections.abc import Iterator
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from aegis.api.app import Platform, app, get_platform
from aegis.auth.tokens import hash_api_key
from aegis.cli import provision_tenant
from aegis.persistence import ApiKeyRepository, Database, PolicyRepository


@pytest.fixture
def database() -> Iterator[Database]:
    db = Database("sqlite+pysqlite:///:memory:")
    db.create_all()
    yield db
    db.dispose()


class TestProvisioningATenant:
    def test_it_issues_a_key_that_resolves_to_the_new_tenant(self, database: Database) -> None:
        tenant = provision_tenant(database, name="Acme Ltd")

        with database.session() as session:
            row = ApiKeyRepository(session).resolve(hash_api_key(tenant.api_key))

        assert row is not None
        assert row.tenant_id == tenant.tenant_id
        assert row.roles == ["ADMIN"]

    def test_it_stores_the_governance_posture(self, database: Database) -> None:
        tenant = provision_tenant(database, name="Acme Ltd", posture="conservative")

        with database.session() as session:
            policy = PolicyRepository(session).load(tenant.tenant_id)

        assert policy is not None
        assert policy.tenant_id == tenant.tenant_id

    def test_the_tenant_id_is_a_usable_uuid(self, database: Database) -> None:
        tenant = provision_tenant(database, name="Acme Ltd")

        assert UUID(tenant.tenant_id)

    def test_two_tenants_never_share_a_key(self, database: Database) -> None:
        first = provision_tenant(database, name="Acme Ltd")
        second = provision_tenant(database, name="Globex")

        assert first.api_key != second.api_key
        assert first.tenant_id != second.tenant_id

    def test_an_unknown_posture_is_refused(self, database: Database) -> None:
        with pytest.raises(ValueError, match="posture must be one of"):
            provision_tenant(database, name="Acme Ltd", posture="anything-goes")

    def test_the_issued_key_signs_into_the_api(self, database: Database) -> None:
        tenant = provision_tenant(database, name="Acme Ltd")
        platform = Platform(database=database)
        app.dependency_overrides[get_platform] = lambda: platform
        try:
            with TestClient(app) as client:
                exchanged = client.post("/v1/auth/token", json={"api_key": tenant.api_key})
                assert exchanged.status_code == 200
                assert exchanged.json()["tenant_id"] == tenant.tenant_id
                token = exchanged.json()["token"]
                assert (
                    client.get("/v1/runs", headers={"Authorization": f"Bearer {token}"}).status_code
                    == 200
                )
        finally:
            app.dependency_overrides.clear()


class TestTheCommandLine:
    def test_json_output_carries_the_key_and_tenant(
        self, tmp_path: object, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from aegis.cli import main

        url = f"sqlite+pysqlite:///{tmp_path}/provision.db"
        code = main(["Acme Ltd", "--json", "--database-url", url])

        assert code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["api_key"].startswith("aeg_")
        assert UUID(payload["tenant_id"])
        assert payload["roles"] == ["ADMIN"]

    def test_an_unknown_posture_exits_with_a_message(
        self, tmp_path: object, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from aegis.cli import main

        url = f"sqlite+pysqlite:///{tmp_path}/provision.db"
        with pytest.raises(SystemExit):
            main(["Acme Ltd", "--posture", "reckless", "--database-url", url])
