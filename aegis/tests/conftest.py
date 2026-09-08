from __future__ import annotations

import os

os.environ.setdefault("AEGIS_ANONYMISATION_SALT", "aegis-test-anonymisation-salt")
os.environ.setdefault("AEGIS_JWT_SECRET", "aegis-test-signing-secret-of-sufficient-length")
