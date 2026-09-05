from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from aegis.governance import ActionType, ProposedAction
from aegis.integrations import (
    CalendarError,
    CalendarTool,
    EmailTool,
    InMemoryCalendar,
    MockEmailTransport,
    Slot,
)

TENANT = UUID("99999999-9999-9999-9999-999999999999")


def action(action_type: ActionType, **payload: object) -> ProposedAction:
    return ProposedAction(
        action_type=action_type,
        subject_id="candidate-42",
        tenant_id=TENANT,
        agent="engagement-agent",
        rationale="candidate confirmed interest",
        payload=dict(payload),
    )


def soon(hours: int = 24) -> str:
    return (datetime.now(UTC) + timedelta(hours=hours)).isoformat()


class TestEmail:
    def test_a_well_formed_message_is_delivered(self) -> None:
        transport = MockEmailTransport()

        result = EmailTool(transport).execute(
            action(
                ActionType.SEND_MESSAGE,
                recipient_email="candidate@example.com",
                subject="Interview invitation",
                body="Are you available on Thursday?",
            )
        )

        assert result.succeeded
        assert result.output["email_recipient"] == "candidate@example.com"
        assert len(transport.sent) == 1
        assert transport.sent[0].subject == "Interview invitation"

    def test_a_malformed_address_is_refused_before_sending(self) -> None:
        transport = MockEmailTransport()

        result = EmailTool(transport).execute(
            action(
                ActionType.SEND_MESSAGE,
                recipient_email="not-an-address",
                subject="Hello",
                body="Hi",
            )
        )

        assert not result.succeeded
        assert "not a deliverable address" in (result.detail or "")
        assert transport.sent == []

    def test_an_empty_body_is_refused(self) -> None:
        transport = MockEmailTransport()

        result = EmailTool(transport).execute(
            action(
                ActionType.SEND_MESSAGE,
                recipient_email="candidate@example.com",
                subject="Hello",
                body="   ",
            )
        )

        assert not result.succeeded
        assert transport.sent == []

    def test_a_missing_recipient_is_refused(self) -> None:
        result = EmailTool(MockEmailTransport()).execute(
            action(ActionType.SEND_MESSAGE, subject="Hello", body="Hi")
        )

        assert not result.succeeded

    def test_the_tool_declares_only_the_action_it_handles(self) -> None:
        assert EmailTool(MockEmailTransport()).handles() == frozenset({ActionType.SEND_MESSAGE})


class TestCalendar:
    def test_a_free_slot_is_booked(self) -> None:
        calendar = InMemoryCalendar()

        result = CalendarTool(calendar).execute(
            action(
                ActionType.SCHEDULE_INTERVIEW,
                attendees=["interviewer@example.com", "candidate@example.com"],
                starts_at=soon(),
            )
        )

        assert result.succeeded
        assert result.output["duration_minutes"] == 45
        assert len(calendar.availability("interviewer@example.com")) == 1

    def test_a_clashing_slot_is_refused_with_the_conflict_named(self) -> None:
        calendar = InMemoryCalendar()
        tool = CalendarTool(calendar)
        when = soon()
        tool.execute(
            action(
                ActionType.SCHEDULE_INTERVIEW,
                attendees=["interviewer@example.com"],
                starts_at=when,
            )
        )

        result = tool.execute(
            action(
                ActionType.SCHEDULE_INTERVIEW,
                attendees=["interviewer@example.com"],
                starts_at=when,
            )
        )

        assert not result.succeeded
        assert "already booked" in (result.detail or "")

    def test_adjacent_slots_do_not_clash(self) -> None:
        calendar = InMemoryCalendar()
        tool = CalendarTool(calendar)
        first = datetime.now(UTC) + timedelta(hours=24)
        tool.execute(
            action(
                ActionType.SCHEDULE_INTERVIEW,
                attendees=["interviewer@example.com"],
                starts_at=first.isoformat(),
                minutes=30,
            )
        )

        result = tool.execute(
            action(
                ActionType.SCHEDULE_INTERVIEW,
                attendees=["interviewer@example.com"],
                starts_at=(first + timedelta(minutes=30)).isoformat(),
                minutes=30,
            )
        )

        assert result.succeeded

    def test_a_meeting_in_the_past_is_refused(self) -> None:
        result = CalendarTool(InMemoryCalendar()).execute(
            action(
                ActionType.SCHEDULE_INTERVIEW,
                attendees=["interviewer@example.com"],
                starts_at=(datetime.now(UTC) - timedelta(days=1)).isoformat(),
            )
        )

        assert not result.succeeded
        assert "in the past" in (result.detail or "")

    def test_no_attendees_is_refused(self) -> None:
        result = CalendarTool(InMemoryCalendar()).execute(
            action(ActionType.SCHEDULE_INTERVIEW, attendees=[], starts_at=soon())
        )

        assert not result.succeeded

    def test_a_malformed_timestamp_is_refused(self) -> None:
        result = CalendarTool(InMemoryCalendar()).execute(
            action(
                ActionType.SCHEDULE_INTERVIEW,
                attendees=["a@example.com"],
                starts_at="next tuesday",
            )
        )

        assert not result.succeeded
        assert "ISO 8601" in (result.detail or "")

    def test_slots_overlap_symmetrically(self) -> None:
        base = datetime.now(UTC)
        first = Slot(base, 60)
        second = Slot(base + timedelta(minutes=30), 60)

        assert first.overlaps(second)
        assert second.overlaps(first)

    def test_booking_raises_rather_than_silently_double_booking(self) -> None:
        calendar = InMemoryCalendar()
        when = datetime.now(UTC) + timedelta(days=1)
        calendar.book(["a@example.com"], Slot(when, 60))

        with pytest.raises(CalendarError, match="already booked"):
            calendar.book(["a@example.com"], Slot(when, 60))
