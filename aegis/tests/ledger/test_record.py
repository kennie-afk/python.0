from __future__ import annotations

import dataclasses
from itertools import pairwise

from aegis.ledger import GENESIS, DecisionLedger


def ledger_with(count: int) -> DecisionLedger:
    ledger = DecisionLedger()
    for index in range(count):
        ledger.append(
            tenant_id="tenant-1",
            workflow="talent_acquisition",
            run_id="run-1",
            step=f"step_{index}",
            action_type="SCORE_CANDIDATE",
            subject_id="candidate-42",
            agent="sourcing-agent",
            outcome="COMPLETED",
            reasons=("matched the requisition skill model",),
        )
    return ledger


class TestAppending:
    def test_an_empty_ledger_heads_at_genesis(self) -> None:
        assert DecisionLedger().head_hash == GENESIS
        assert len(DecisionLedger()) == 0

    def test_the_first_entry_links_to_genesis(self) -> None:
        entry = ledger_with(1).entries[0]

        assert entry.previous_hash == GENESIS
        assert entry.sequence == 0
        assert len(entry.entry_hash) == 64

    def test_each_entry_links_to_the_one_before(self) -> None:
        entries = ledger_with(4).entries

        for previous, current in pairwise(entries):
            assert current.previous_hash == previous.entry_hash
            assert current.sequence == previous.sequence + 1

    def test_an_intact_chain_verifies(self) -> None:
        report = ledger_with(6).verify()

        assert report.intact
        assert report.entries_checked == 6
        assert report.broken_at is None


class TestTamperEvidence:
    def test_editing_an_entry_is_detected_at_that_entry(self) -> None:
        ledger = ledger_with(5)
        original = ledger.entries[2]
        ledger._entries[2] = dataclasses.replace(original, outcome="DENIED")

        report = ledger.verify()

        assert not report.intact
        assert report.broken_at == 2
        assert report.reason is not None
        assert "does not match its stored hash" in report.reason

    def test_deleting_an_entry_breaks_the_sequence(self) -> None:
        ledger = ledger_with(5)
        del ledger._entries[2]

        report = ledger.verify()

        assert not report.intact
        assert report.reason is not None
        assert "sequence gap" in report.reason

    def test_changing_the_approver_is_detected(self) -> None:
        ledger = DecisionLedger()
        ledger.append(
            tenant_id="tenant-1",
            workflow="talent_acquisition",
            run_id="run-1",
            step="offer",
            action_type="EXTEND_OFFER",
            subject_id="candidate-42",
            agent="aegis-runtime",
            outcome="COMPLETED",
            approver="hr.partner@example.com",
        )
        ledger._entries[0] = dataclasses.replace(
            ledger.entries[0], approver="someone.else@example.com"
        )

        assert not ledger.verify().intact

    def test_reordering_entries_is_detected(self) -> None:
        ledger = ledger_with(4)
        ledger._entries[1], ledger._entries[2] = ledger._entries[2], ledger._entries[1]

        assert not ledger.verify().intact


class TestQuerying:
    def test_entries_can_be_filtered_by_subject(self) -> None:
        ledger = DecisionLedger()
        for subject in ("candidate-1", "candidate-2", "candidate-1"):
            ledger.append(
                tenant_id="tenant-1",
                workflow="talent_acquisition",
                run_id="run-1",
                step="source",
                action_type="SCORE_CANDIDATE",
                subject_id=subject,
                agent="sourcing-agent",
                outcome="COMPLETED",
            )

        assert len(ledger.for_subject("candidate-1")) == 2
        assert len(ledger.for_subject("candidate-2")) == 1

    def test_entries_can_be_filtered_by_tenant(self) -> None:
        ledger = DecisionLedger()
        for tenant in ("tenant-1", "tenant-2"):
            ledger.append(
                tenant_id=tenant,
                workflow="onboarding",
                run_id="run-1",
                step="request_documents",
                action_type="REQUEST_DOCUMENT",
                subject_id="employee-1",
                agent="documentation-agent",
                outcome="COMPLETED",
            )

        assert len(ledger.for_tenant("tenant-1")) == 1

    def test_human_approvals_are_separable_for_audit(self) -> None:
        ledger = ledger_with(2)
        ledger.append(
            tenant_id="tenant-1",
            workflow="talent_acquisition",
            run_id="run-1",
            step="offer",
            action_type="EXTEND_OFFER",
            subject_id="candidate-42",
            agent="aegis-runtime",
            outcome="COMPLETED",
            approver="hr.partner@example.com",
        )

        approvals = ledger.human_approvals()

        assert len(approvals) == 1
        assert approvals[0].was_human_approved
        assert approvals[0].action_type == "EXTEND_OFFER"

    def test_the_reasons_behind_a_decision_are_retained(self) -> None:
        ledger = DecisionLedger()
        ledger.append(
            tenant_id="tenant-1",
            workflow="talent_acquisition",
            run_id="run-1",
            step="offer",
            action_type="EXTEND_OFFER",
            subject_id="candidate-42",
            agent="aegis-runtime",
            outcome="AWAITING_APPROVAL",
            reasons=("EXTEND_OFFER is irreversible and always requires human approval",),
        )

        assert "irreversible" in ledger.entries[0].reasons[0]
