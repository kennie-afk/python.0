from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from aegis.governance import (
    IRREVERSIBLE_ACTIONS,
    ActionType,
    GovernanceGate,
    ProposedAction,
    RestrictedDomain,
    TenantPolicy,
    Verdict,
)

TENANT = UUID("11111111-1111-1111-1111-111111111111")


def action(
    action_type: ActionType,
    *,
    tenant: UUID = TENANT,
    confidence: float | None = None,
    domains: frozenset[RestrictedDomain] = frozenset(),
) -> ProposedAction:
    return ProposedAction(
        action_type=action_type,
        subject_id="candidate-42",
        tenant_id=tenant,
        agent="sourcing-agent",
        rationale="matched the requisition skill model at 0.88",
        confidence=confidence,
        touches_domains=domains,
    )


def gate(policy: TenantPolicy | None = None) -> GovernanceGate:
    return GovernanceGate(policy or TenantPolicy.conservative(str(TENANT)))


class TestRoutineAutomation:
    def test_a_delegated_reversible_action_runs_autonomously(self) -> None:
        decision = gate().evaluate(action(ActionType.SCHEDULE_INTERVIEW))

        assert decision.verdict is Verdict.ALLOW
        assert decision.may_execute_now
        assert decision.reasons == ()

    def test_scoring_and_messaging_are_delegated_by_default(self) -> None:
        for action_type in (ActionType.SCORE_CANDIDATE, ActionType.SEND_MESSAGE):
            assert gate().evaluate(action(action_type)).may_execute_now

    def test_an_action_outside_the_delegated_set_needs_a_human(self) -> None:
        decision = gate().evaluate(action(ActionType.PROVISION_ACCESS))

        assert decision.verdict is Verdict.REQUIRE_HUMAN_APPROVAL
        assert not decision.may_execute_now
        assert decision.approver_role == "HR_BUSINESS_PARTNER"


class TestIrreversibleActions:
    @pytest.mark.parametrize("action_type", sorted(IRREVERSIBLE_ACTIONS))
    def test_no_irreversible_action_can_ever_execute_autonomously(
        self, action_type: ActionType
    ) -> None:
        permissive = TenantPolicy.permissive(str(TENANT))
        permissive_gate = GovernanceGate(permissive)

        decision = permissive_gate.evaluate(action(action_type, confidence=1.0))

        assert decision.verdict is not Verdict.ALLOW
        assert not decision.may_execute_now

    def test_a_tenant_cannot_configure_away_the_human_approval_of_an_offer(self) -> None:
        everything = TenantPolicy(
            tenant_id=str(TENANT),
            autonomous_actions=frozenset(ActionType),
            readable_domains=frozenset(RestrictedDomain),
            confidence_floor=0.0,
        )

        decision = GovernanceGate(everything).evaluate(
            action(ActionType.EXTEND_OFFER, confidence=1.0)
        )

        assert decision.verdict is Verdict.REQUIRE_HUMAN_APPROVAL
        assert any("irreversible" in reason for reason in decision.reasons)

    def test_termination_always_requires_a_human(self) -> None:
        decision = gate().evaluate(action(ActionType.TERMINATE_EMPLOYMENT))

        assert not decision.may_execute_now

    def test_a_performance_rating_is_never_set_by_an_agent(self) -> None:
        decision = gate().evaluate(action(ActionType.SET_PERFORMANCE_RATING))

        assert not decision.may_execute_now


class TestPermissionBoundaries:
    def test_an_agent_cannot_touch_compensation_without_the_domain_granted(self) -> None:
        decision = gate().evaluate(action(ActionType.ADJUST_COMPENSATION))

        assert decision.verdict is Verdict.DENY
        assert decision.blocked
        assert any("COMPENSATION_BAND" in reason for reason in decision.reasons)

    def test_equity_is_denied_by_default(self) -> None:
        assert gate().evaluate(action(ActionType.GRANT_EQUITY)).blocked

    def test_reading_a_health_record_is_denied_even_for_a_routine_action(self) -> None:
        decision = gate().evaluate(
            action(
                ActionType.SEND_MESSAGE,
                domains=frozenset({RestrictedDomain.HEALTH_RECORD}),
            )
        )

        assert decision.blocked
        assert any("HEALTH_RECORD" in reason for reason in decision.reasons)

    def test_granting_the_domain_downgrades_a_denial_to_human_approval(self) -> None:
        policy = TenantPolicy(
            tenant_id=str(TENANT),
            autonomous_actions=frozenset({ActionType.ADJUST_COMPENSATION}),
            readable_domains=frozenset({RestrictedDomain.COMPENSATION_BAND}),
        )

        decision = GovernanceGate(policy).evaluate(action(ActionType.ADJUST_COMPENSATION))

        assert decision.verdict is Verdict.REQUIRE_HUMAN_APPROVAL
        assert not decision.blocked


class TestTenantIsolation:
    def test_an_action_from_another_tenant_is_denied(self) -> None:
        decision = gate().evaluate(action(ActionType.SCHEDULE_INTERVIEW, tenant=uuid4()))

        assert decision.blocked
        assert any("different tenant" in reason for reason in decision.reasons)

    def test_a_tenant_can_forbid_an_action_outright(self) -> None:
        policy = TenantPolicy(
            tenant_id=str(TENANT),
            autonomous_actions=frozenset({ActionType.SEND_MESSAGE}),
            forbidden_actions=frozenset({ActionType.REJECT_APPLICATION}),
        )

        assert GovernanceGate(policy).evaluate(action(ActionType.REJECT_APPLICATION)).blocked

    def test_an_action_cannot_be_both_delegated_and_forbidden(self) -> None:
        with pytest.raises(ValueError, match="both autonomous and forbidden"):
            TenantPolicy(
                tenant_id=str(TENANT),
                autonomous_actions=frozenset({ActionType.SEND_MESSAGE}),
                forbidden_actions=frozenset({ActionType.SEND_MESSAGE}),
            )


class TestConfidence:
    def test_a_low_confidence_action_is_routed_to_a_human(self) -> None:
        decision = gate().evaluate(action(ActionType.SCORE_CANDIDATE, confidence=0.41))

        assert decision.verdict is Verdict.REQUIRE_HUMAN_APPROVAL
        assert any("below the tenant floor" in reason for reason in decision.reasons)

    def test_a_confident_action_within_the_delegated_set_proceeds(self) -> None:
        assert gate().evaluate(action(ActionType.SCORE_CANDIDATE, confidence=0.93)).may_execute_now

    def test_an_impossible_confidence_is_rejected_at_construction(self) -> None:
        with pytest.raises(ValueError, match="confidence"):
            action(ActionType.SCORE_CANDIDATE, confidence=1.4)


class TestAccountability:
    def test_an_action_without_a_rationale_cannot_be_constructed(self) -> None:
        with pytest.raises(ValueError, match="rationale"):
            ProposedAction(
                action_type=ActionType.SEND_MESSAGE,
                subject_id="candidate-42",
                tenant_id=TENANT,
                agent="sourcing-agent",
                rationale="   ",
            )

    def test_every_blocking_reason_is_reported_not_just_the_first(self) -> None:
        decision = gate().evaluate(action(ActionType.EXTEND_OFFER, confidence=0.2))

        assert len(decision.reasons) >= 2
        assert "irreversible" in decision.summary()

    def test_an_escalation_names_the_role_it_goes_to(self) -> None:
        decision = gate().escalate(
            action(ActionType.FLAG_RETENTION_RISK), "team sentiment dropped two bands"
        )

        assert decision.verdict is Verdict.ESCALATE
        assert decision.escalate_to == "HR_BUSINESS_PARTNER"
        assert "sentiment" in decision.summary()
