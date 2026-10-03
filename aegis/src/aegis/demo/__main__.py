from __future__ import annotations

import argparse
import os
import sys
import time
import uuid

import httpx

from aegis.cli.provision import provision_tenant
from aegis.demo.seed import DemoSeeder
from aegis.persistence.repositories import purge_tenant
from aegis.persistence.session import Database, admin_database_url

DEMO_TENANT = str(uuid.uuid5(uuid.NAMESPACE_URL, "aegis-demo:kijani-logistics"))
DEMO_NAME = "Kijani Logistics (demo)"


def _wait_for_api(client: httpx.Client, seconds: int = 60) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            if client.get("/health").status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(1)
    raise SystemExit("the Aegis API did not become healthy; is it running?")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m aegis.demo",
        description="Create a demo tenant and fill it through the real API",
    )
    parser.add_argument(
        "--api-url", default=os.environ.get("AEGIS_DEMO_API_URL", "http://127.0.0.1:8000")
    )
    parser.add_argument("--reset", action="store_true", help="delete the demo tenant, then reseed")
    parser.add_argument("--database-url", default=None)
    args = parser.parse_args(argv)

    if os.environ.get("AEGIS_ALLOW_DEMO_SEED", "").lower() != "true":
        print(
            "aegis.demo: refusing to run. It issues a tenant and fills it with invented people;\n"
            "set AEGIS_ALLOW_DEMO_SEED=true to say that is what you want here.",
            file=sys.stderr,
        )
        return 2

    database = Database(args.database_url) if args.database_url else Database(admin_database_url())
    if database.is_sqlite:
        database.create_all()

    if args.reset:
        with database.session() as session:
            purge_tenant(session, DEMO_TENANT)
        print("demo tenant removed")

    tenant = provision_tenant(
        database, DEMO_NAME, posture="conservative", label="demo", tenant_id=DEMO_TENANT
    )
    database.dispose()

    with httpx.Client(base_url=args.api_url, timeout=60.0) as client:
        _wait_for_api(client)
        token = client.post("/v1/auth/token", json={"api_key": tenant.api_key}).json()["token"]
        client.headers["Authorization"] = f"Bearer {token}"

        already = client.get("/v1/overview").json()
        if already["runs"] > 0 or already["screenings"]:
            print(f"{DEMO_NAME} is already populated ({already['runs']} runs); not seeding twice.")
            print("Run with --reset to rebuild it.")
        else:
            summary = DemoSeeder(client).run()
            print()
            print(f"screened {summary.screened} applicants, {summary.advance} advanced")
            for label, verdict in summary.reports:
                print(f"  {verdict:<18} {label}")
            print(f"{summary.runs} workflow runs, {summary.employees_scored} employees scored")
            verdict = "intact" if summary.chain_intact else "BROKEN"
            print(f"audit chain {verdict} across {summary.chain_entries} entries")

    print()
    print(f"tenant   {tenant.tenant_id}")
    print(f"name     {DEMO_NAME}")
    print(f"key      {tenant.api_key}")
    print()
    print("Sign in to the console with that key. It is shown once; run this again for a new one.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
