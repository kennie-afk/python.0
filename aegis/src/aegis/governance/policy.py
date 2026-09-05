from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from aegis.governance.actions import ActionType, RestrictedDomain


class Verdict(StrEnum):
    ALLOW = "ALLOW"
    REQUIRE_HUMAN_APPROVAL = "REQUIRE_HUMAN_APPROVAL"
    ESCALATE = "ESCALATE"
    DENY = "DENY"


@dataclass(frozen=True, slots=True)
class Decision:
    verdict: Verdict
    reasons: tuple[str, ...]
    approver_role: str | None = None
    escalate_to: str | None = None

    @property
    def may_execute_now(self) -> bool:
        return self.verdict is Verdict.ALLOW

    @property
    def blocked(self) -> bool:
        return self.verdict is Verdict.DENY

    def summary(self) -> str:
        return "; ".join(self.reasons) if self.reasons else "permitted by policy"


@dataclass(frozen=True, slots=True)
class TenantPolicy:
    tenant_id: str
    autonomous_actions: frozenset[ActionType] = field(default_factory=frozenset)
    forbidden_actions: frozenset[ActionType] = field(default_factory=frozenset)
    readable_domains: frozenset[RestrictedDomain] = field(default_factory=frozenset)
    confidence_floor: float = 0.70
    approver_role: str = "HR_BUSINESS_PARTNER"
    escalation_role: str = "HR_BUSINESS_PARTNER"

    def __post_init__(self) -> None:
        overlap = self.autonomous_actions & self.forbidden_actions
        if overlap:
            raise ValueError(
                "an action cannot be both autonomous and forbidden: "
                + ", ".join(sorted(overlap))
            )
        if not 0.0 <= self.confidence_floor <= 1.0:
            raise ValueError("confidence_floor must be within [0,1]")

    @classmethod
    def conservative(cls, tenant_id: str) -> TenantPolicy:
        return cls(
            tenant_id=tenant_id,
            autonomous_actions=frozenset(
                {
                    ActionType.SCORE_CANDIDATE,
                    ActionType.SEND_MESSAGE,
                    ActionType.SCHEDULE_INTERVIEW,
                    ActionType.REQUEST_DOCUMENT,
                    ActionType.VERIFY_DOCUMENT,
                    ActionType.ASSIGN_LEARNING_PATH,
                    ActionType.SCHEDULE_CHECK_IN,
                    ActionType.FLAG_RETENTION_RISK,
                }
            ),
        )

    @classmethod
    def permissive(cls, tenant_id: str) -> TenantPolicy:
        return cls(
            tenant_id=tenant_id,
            autonomous_actions=frozenset(ActionType) - frozenset({ActionType.REJECT_APPLICATION}),
        )
