from __future__ import annotations

from datetime import UTC, datetime


def now() -> datetime:
    return datetime.now(UTC)


def ensure_utc(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        raise ValueError("timestamps must carry a timezone; a naive time cannot be ordered safely")
    return moment.astimezone(UTC)
