"""Sign ledger entries written before signing was on: `python -m aegis.ops.sign_ledger`.

A one-time step after setting AEGIS_LEDGER_SIGNING_KEY on a deployment that already has history.
For each tenant the hash chain is verified first and a broken chain is refused, not signed: this
trusts the history as it stands now, which is the most a retroactive step can do. Afterwards set
AEGIS_ENV=production so verification requires every entry to be signed.
"""

from __future__ import annotations

import logging

from aegis.ops.checks import tenant_ids
from aegis.persistence.repositories import LedgerRepository
from aegis.persistence.session import Database, admin_database_url
from aegis.security.signing import ledger_signing_key

log = logging.getLogger("aegis.ops")


def sign_all(runtime: Database, admin: Database) -> dict[str, int | str]:
    key = ledger_signing_key()
    if key is None:
        raise SystemExit("AEGIS_LEDGER_SIGNING_KEY is not set; there is nothing to sign with")
    outcome: dict[str, int | str] = {}
    for tenant in tenant_ids(admin):
        try:
            with runtime.session(tenant) as session:
                outcome[tenant] = LedgerRepository(session).sign_unsigned(tenant, key)
        except RuntimeError as error:
            outcome[tenant] = f"refused: {error}"
    return outcome


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    for tenant, result in sign_all(Database(), Database(admin_database_url())).items():
        log.info("%s: %s", tenant, result)


if __name__ == "__main__":
    main()
