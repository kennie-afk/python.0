"""The unprivileged role the API runs as, and how its password reaches the database.

The migration `tenant_isolation` creates `aegis_app` without a login. It owns nothing, is not a
superuser and does not bypass row-level security. Its password lives in the environment, never in a
migration, and is applied here after every migration run so a changed value takes effect on the next
start (the same idea as a Flyway afterMigrate callback).
"""

from __future__ import annotations

import os
import re

from sqlalchemy import Connection, text

ROLE = "aegis_app"
# The password is written into an ALTER ROLE statement, which cannot take bind parameters, so it
# must
# not be able to carry a quote. Hex keys from `openssl rand -hex` satisfy this.
_SAFE = re.compile(r"^[A-Za-z0-9_.+=@#%^*!~-]{16,128}$")


def apply_runtime_role_password(connection: Connection) -> bool:
    """Give `aegis_app` its login password from AEGIS_APP_PASSWORD. Returns whether it was
    applied."""
    if connection.dialect.name != "postgresql":
        return False
    password = os.environ.get("AEGIS_APP_PASSWORD")
    if not password:
        return False
    if not _SAFE.match(password):
        raise RuntimeError(
            "AEGIS_APP_PASSWORD must be 16-128 characters from A-Z a-z 0-9 _ . + = @ # % ^ * ! ~ -"
        )
    exists = connection.execute(
        text("SELECT 1 FROM pg_roles WHERE rolname = :role"), {"role": ROLE}
    ).scalar()
    if not exists:
        return False
    connection.execute(text(f"ALTER ROLE {ROLE} WITH LOGIN PASSWORD '{password}'"))
    return True
