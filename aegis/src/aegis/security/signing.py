"""Keys that live outside the database, and the HMAC used with them.

Two things are signed so that write access to the database is not enough to forge them:

* trained-model blobs (`AEGIS_MODEL_SIGNING_KEY`; when unset, derived from `AEGIS_JWT_SECRET`,
  which the service already refuses to start without), and
* every ledger entry and the ledger head (`AEGIS_LEDGER_SIGNING_KEY`; required when
  `AEGIS_ENV=production`, optional elsewhere so development and tests stay simple).
"""

from __future__ import annotations

import hashlib
import hmac
import os

MIN_KEY_LENGTH = 32


class SigningKeyError(RuntimeError):
    pass


def sign(key: bytes, *parts: str) -> str:
    """HMAC-SHA256 over length-prefixed parts, so ("ab", "c") and ("a", "bc") differ."""
    message = "".join(f"{len(part)}:{part}" for part in parts).encode("utf-8")
    return hmac.new(key, message, hashlib.sha256).hexdigest()


def constant_time_equal(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


def is_production() -> bool:
    return os.environ.get("AEGIS_ENV", "").strip().lower() == "production"


def model_signing_key() -> bytes:
    explicit = os.environ.get("AEGIS_MODEL_SIGNING_KEY", "")
    if explicit:
        if len(explicit) < MIN_KEY_LENGTH:
            raise SigningKeyError(
                f"AEGIS_MODEL_SIGNING_KEY must be at least {MIN_KEY_LENGTH} characters"
            )
        return explicit.encode("utf-8")
    base = os.environ.get("AEGIS_JWT_SECRET", "")
    if len(base) < MIN_KEY_LENGTH:
        raise SigningKeyError(
            "no model signing key: set AEGIS_MODEL_SIGNING_KEY (or AEGIS_JWT_SECRET)"
        )
    return hmac.new(base.encode("utf-8"), b"aegis-model-signing-v1", hashlib.sha256).digest()


def ledger_signing_key() -> bytes | None:
    """The ledger key, None when signing is off, an error in production when it is missing."""
    explicit = os.environ.get("AEGIS_LEDGER_SIGNING_KEY", "")
    if not explicit:
        if is_production():
            raise SigningKeyError(
                "AEGIS_LEDGER_SIGNING_KEY must be set when AEGIS_ENV=production; without it the "
                "audit trail is tamper-evident only against partial edits"
            )
        return None
    if len(explicit) < MIN_KEY_LENGTH:
        raise SigningKeyError(
            f"AEGIS_LEDGER_SIGNING_KEY must be at least {MIN_KEY_LENGTH} characters"
        )
    return explicit.encode("utf-8")


def key_fingerprint(key: bytes) -> str:
    """A short identifier for a key that reveals nothing usable about it."""
    return hmac.new(key, b"aegis-key-fingerprint", hashlib.sha256).hexdigest()[:16]
