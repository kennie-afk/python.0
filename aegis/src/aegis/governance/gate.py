from __future__ import annotations

from aegis.governance.actions import IRREVERSIBLE_ACTIONS, ProposedAction
from aegis.governance.policy import Decision, TenantPolicy, Verdict


class GovernanceGate:
    def __init__(self, policy: TenantPolicy) -> None:
        self._policy = policy

    @property
    def policy(self) -> TenantPolicy:
        return self._policy

    def evaluate(self, action: ProposedAction) -> Decision:
        if str(action.tenant_id) != self._policy.tenant_id:
            return Decision(
                verdict=Verdict.DENY,
                reasons=("action belongs to a different tenant than the governing policy",),
            )

        if action.action_type in self._policy.forbidden_actions:
            return Decision(
                verdict=Verdict.DENY,
                reasons=(f"{action.action_type} is forbidden for this tenant",),
            )

        unreadable = action.restricted_domains - self._policy.readable_domains
        if unreadable:
            return Decision(
                verdict=Verdict.DENY,
                reasons=("agent may not touch restricted data: " + ", ".join(sorted(unreadable)),),
            )

        reasons: list[str] = []

        if action.is_irreversible:
            reasons.append(
                f"{action.action_type} is irreversible and always requires human approval"
            )

        if action.action_type not in self._policy.autonomous_actions:
            reasons.append(
                f"{action.action_type} is not delegated to autonomous execution by this tenant"
            )

        if action.confidence is not None and action.confidence < self._policy.confidence_floor:
            reasons.append(
                f"confidence {action.confidence:.2f} is below the tenant floor of "
                f"{self._policy.confidence_floor:.2f}"
            )

        if not reasons:
            return Decision(verdict=Verdict.ALLOW, reasons=())

        return Decision(
            verdict=Verdict.REQUIRE_HUMAN_APPROVAL,
            reasons=tuple(reasons),
            approver_role=self._policy.approver_role,
        )

    def escalate(self, action: ProposedAction, trigger: str) -> Decision:
        return Decision(
            verdict=Verdict.ESCALATE,
            reasons=(f"escalation trigger fired: {trigger}",),
            escalate_to=self._policy.escalation_role,
        )

    @staticmethod
    def irreversible_actions() -> frozenset[str]:
        return frozenset(str(action) for action in IRREVERSIBLE_ACTIONS)
