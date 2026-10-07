from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Protocol

from aegis.agents.tools import ToolResult
from aegis.governance.actions import ActionType, ProposedAction

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


class CalendarError(RuntimeError):
    pass

@dataclass(frozen=True, slots=True)
class Slot:
    starts_at: datetime
    minutes: int

    @property
    def ends_at(self) -> datetime:
        return self.starts_at + timedelta(minutes=self.minutes)

    def overlaps(self, other: Slot) -> bool:
        return self.starts_at < other.ends_at and other.starts_at < self.ends_at

class Calendar(Protocol):
    def availability(self, attendee: str) -> list[Slot]: ...

    def book(self, attendees: list[str], slot: Slot) -> str: ...

@dataclass
class InMemoryCalendar:
    booked: dict[str, list[Slot]] = field(default_factory=dict)

    def availability(self, attendee: str) -> list[Slot]:
        return self.booked.setdefault(attendee, [])

    def book(self, attendees: list[str], slot: Slot) -> str:
        for attendee in attendees:
            for existing in self.availability(attendee):
                if existing.overlaps(slot):
                    raise CalendarError(
                        f"{attendee} is already booked between "
                        f"{existing.starts_at.isoformat()} and {existing.ends_at.isoformat()}"
                    )

        for attendee in attendees:
            self.availability(attendee).append(slot)

        return f"evt-{int(slot.starts_at.timestamp())}-{len(attendees)}"

class PersistentCalendar:
    """Interview slots kept in the database, for one tenant. Two tenants can have an attendee of
    the same name, so every lookup carries the tenant; a booking takes the tenant's advisory lock
    so two requests cannot both take the same slot."""

    def __init__(self, session: Session, tenant_id: str) -> None:
        self._session = session
        self._tenant = tenant_id

    def availability(self, attendee: str) -> list[Slot]:
        from aegis.persistence.repositories import CalendarRepository

        return [
            Slot(
                row.starts_at if row.starts_at.tzinfo else row.starts_at.replace(tzinfo=UTC),
                row.minutes,
            )
            for row in CalendarRepository(self._session).slots(self._tenant, attendee)
        ]

    def book(self, attendees: list[str], slot: Slot) -> str:
        from aegis.persistence.repositories import CalendarRepository, LedgerRepository

        LedgerRepository(self._session).lock_tenant(self._tenant)
        for attendee in attendees:
            for existing in self.availability(attendee):
                if existing.overlaps(slot):
                    raise CalendarError(
                        f"{attendee} is already booked between "
                        f"{existing.starts_at.isoformat()} and {existing.ends_at.isoformat()}"
                    )
        reference = f"evt-{int(slot.starts_at.timestamp())}-{len(attendees)}"
        CalendarRepository(self._session).add(
            self._tenant, attendees, slot.starts_at, slot.minutes, reference
        )
        return reference

class CalendarTool:
    def __init__(self, calendar: Calendar, default_minutes: int = 45) -> None:
        self._calendar = calendar
        self._default_minutes = default_minutes

    def handles(self) -> frozenset[ActionType]:
        return frozenset({ActionType.SCHEDULE_INTERVIEW, ActionType.SCHEDULE_CHECK_IN})

    def execute(self, action: ProposedAction) -> ToolResult:
        attendees = action.payload.get("attendees")
        if not isinstance(attendees, list) or not attendees:
            return ToolResult.failed("refusing to book a meeting with no attendees")

        raw_start = action.payload.get("starts_at")
        if not raw_start:
            return ToolResult.failed("refusing to book a meeting with no start time")

        try:
            starts_at = datetime.fromisoformat(str(raw_start))
        except ValueError:
            return ToolResult.failed(f"{raw_start!r} is not an ISO 8601 timestamp")

        if starts_at.tzinfo is None:
            starts_at = starts_at.replace(tzinfo=UTC)

        if starts_at < datetime.now(UTC):
            return ToolResult.failed("refusing to book a meeting in the past")

        minutes = int(action.payload.get("minutes", self._default_minutes))
        try:
            reference = self._calendar.book(
                [str(attendee) for attendee in attendees], Slot(starts_at, minutes)
            )
        except CalendarError as error:
            return ToolResult.failed(str(error))

        return ToolResult.ok(
            calendar_reference=reference,
            scheduled_for=starts_at.isoformat(),
            duration_minutes=minutes,
        )
