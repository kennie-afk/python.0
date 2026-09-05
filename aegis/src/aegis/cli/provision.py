from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from uuid import UUID, uuid4

from aegis.auth.tokens import generate_api_key, hash_api_key
from aegis.governance.policy import TenantPolicy
from aegis.persistence.repositories import ApiKeyRepository, PolicyRepository
from aegis.persistence.session import Database

POSTURES = {
    "conservative": TenantPolicy.conservative,
    "permissive": TenantPolicy.permissive,
}


@dataclass(frozen=True, slots=True)
class ProvisionedTenant:
    tenant_id: str
    name: str
    label: str
    api_key: str
    posture: str
    roles: tuple[str, ...]


def provision_tenant(
    database: Database,
    name: str,
    posture: str = "conservative",
    label: str = "console",
    roles: tuple[str, ...] = ("ADMIN",),
    tenant_id: str | None = None,
) -> ProvisionedTenant:
    if posture not in POSTURES:
        raise ValueError(f"posture must be one of {sorted(POSTURES)}, not {posture!r}")

    identifier = str(UUID(tenant_id)) if tenant_id else str(uuid4())
    key = generate_api_key()

    with database.session() as session:
        PolicyRepository(session).upsert(identifier, name, POSTURES[posture](identifier))
        ApiKeyRepository(session).issue(identifier, label, hash_api_key(key), roles)

    return ProvisionedTenant(
        tenant_id=identifier,
        name=name,
        label=label,
        api_key=key,
        posture=posture,
        roles=roles,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="aegis-provision",
        description="Create a tenant, store its governance posture and issue an API key",
    )
    parser.add_argument("name", help="the organisation this tenant belongs to")
    parser.add_argument("--posture", choices=sorted(POSTURES), default="conservative")
    parser.add_argument("--label", default="console", help="what the issued key is for")
    parser.add_argument("--role", action="append", dest="roles", default=None)
    parser.add_argument("--tenant-id", default=None, help="reuse an existing tenant identifier")
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--json", action="store_true", help="emit machine readable output")
    args = parser.parse_args(argv)

    database = Database(args.database_url) if args.database_url else Database()
    database.create_all()

    try:
        tenant = provision_tenant(
            database,
            name=args.name,
            posture=args.posture,
            label=args.label,
            roles=tuple(args.roles or ("ADMIN",)),
            tenant_id=args.tenant_id,
        )
    except ValueError as error:
        print(f"aegis-provision: {error}", file=sys.stderr)
        return 2
    finally:
        database.dispose()

    if args.json:
        print(json.dumps(asdict(tenant) | {"roles": list(tenant.roles)}, indent=2))
        return 0

    print(f"tenant   {tenant.tenant_id}")
    print(f"name     {tenant.name}")
    print(f"posture  {tenant.posture}")
    print(f"key      {tenant.api_key}")
    print()
    print("This key is shown once. Sign in to the console with it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
