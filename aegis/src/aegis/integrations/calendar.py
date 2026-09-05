from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from aegis.agents.tools import ToolResult
from aegis.governance.actions import ActionType, ProposedAction


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


class CalendarTool:
    def __init__(self, calendar: InMemoryCalendar, default_minutes: int = 45) -> None:
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
