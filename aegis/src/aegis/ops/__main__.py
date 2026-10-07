"""Run the scheduled checks for every tenant: `python -m aegis.ops [--once]`.

Meant for a Kubernetes CronJob or a compose service of its own, so the API replicas stay stateless.
Tenants are listed with the owner connection (AEGIS_ADMIN_DATABASE_URL or AEGIS_MIGRATION_URL)
because the runtime role cannot see across tenants; the checks themselves run as the runtime role,
inside each tenant's row-level security.
"""

from __future__ import annotations

import argparse
import logging
import os
import time

from aegis.integrations.email import (
    EmailError,
    EmailTransport,
    MockEmailTransport,
    SmtpEmailTransport,
)
from aegis.ops.alerts import AlertDispatcher
from aegis.ops.checks import run_checks, tenant_ids
from aegis.persistence.session import Database, admin_database_url


def main() -> None:
    parser = argparse.ArgumentParser(description="Aegis scheduled checks")
    parser.add_argument("--once", action="store_true", help="run one pass and exit")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    log = logging.getLogger("aegis.ops")

    runtime = Database()
    admin = Database(admin_database_url())
    email: EmailTransport
    try:
        email = SmtpEmailTransport.from_environment()
    except EmailError:
        email = MockEmailTransport()
    dispatcher = AlertDispatcher.from_environment(runtime, email)
    interval = int(os.environ.get("AEGIS_CHECK_INTERVAL_SECONDS", "900"))

    while True:
        for tenant in tenant_ids(admin):
            try:
                summary = run_checks(runtime, tenant, dispatcher)
                log.info("checked %s: %s", tenant, summary)
            except Exception:
                log.exception("checks failed for %s", tenant)
        if args.once:
            return
        time.sleep(interval)


if __name__ == "__main__":
    main()
