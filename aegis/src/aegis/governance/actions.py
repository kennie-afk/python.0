from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4


class ActionType(StrEnum):
    SCORE_CANDIDATE = "SCORE_CANDIDATE"
    SHORTLIST_CANDIDATE = "SHORTLIST_CANDIDATE"
    REJECT_APPLICATION = "REJECT_APPLICATION"
    SEND_MESSAGE = "SEND_MESSAGE"
    SCHEDULE_INTERVIEW = "SCHEDULE_INTERVIEW"
    REQUEST_DOCUMENT = "REQUEST_DOCUMENT"
    VERIFY_DOCUMENT = "VERIFY_DOCUMENT"
    ORDER_BACKGROUND_CHECK = "ORDER_BACKGROUND_CHECK"
    PROVISION_ACCESS = "PROVISION_ACCESS"
    REVOKE_ACCESS = "REVOKE_ACCESS"
    ORDER_HARDWARE = "ORDER_HARDWARE"
    ASSIGN_LEARNING_PATH = "ASSIGN_LEARNING_PATH"
    SCHEDULE_CHECK_IN = "SCHEDULE_CHECK_IN"
    FLAG_RETENTION_RISK = "FLAG_RETENTION_RISK"
    RECOMMEND_INTERNAL_MOVE = "RECOMMEND_INTERNAL_MOVE"

    EXTEND_OFFER = "EXTEND_OFFER"
    WITHDRAW_OFFER = "WITHDRAW_OFFER"
    TERMINATE_EMPLOYMENT = "TERMINATE_EMPLOYMENT"
    SET_PERFORMANCE_RATING = "SET_PERFORMANCE_RATING"
    APPROVE_PROMOTION = "APPROVE_PROMOTION"
    ADJUST_COMPENSATION = "ADJUST_COMPENSATION"
    GRANT_EQUITY = "GRANT_EQUITY"


IRREVERSIBLE_ACTIONS: frozenset[ActionType] = frozenset(
    {
        ActionType.EXTEND_OFFER,
        ActionType.WITHDRAW_OFFER,
        ActionType.TERMINATE_EMPLOYMENT,
        ActionType.SET_PERFORMANCE_RATING,
        ActionType.APPROVE_PROMOTION,
        ActionType.ADJUST_COMPENSATION,
        ActionType.GRANT_EQUITY,
    }
)


class RestrictedDomain(StrEnum):
    COMPENSATION_BAND = "COMPENSATION_BAND"
    EQUITY_GRANT = "EQUITY_GRANT"
    HEALTH_RECORD = "HEALTH_RECORD"
    DISCIPLINARY_RECORD = "DISCIPLINARY_RECORD"
    IMMIGRATION_STATUS = "IMMIGRATION_STATUS"


DOMAIN_BY_ACTION: dict[ActionType, RestrictedDomain] = {
    ActionType.ADJUST_COMPENSATION: RestrictedDomain.COMPENSATION_BAND,
    ActionType.GRANT_EQUITY: RestrictedDomain.EQUITY_GRANT,
}


@dataclass(frozen=True, slots=True)
class ProposedAction:
    action_type: ActionType
    subject_id: str
    tenant_id: UUID
    agent: str
    rationale: str
    payload: dict[str, Any] = field(default_factory=dict)
    touches_domains: frozenset[RestrictedDomain] = field(default_factory=frozenset)
    confidence: float | None = None
    action_id: UUID = field(default_factory=uuid4)

    def __post_init__(self) -> None:
        if not self.rationale.strip():
            raise ValueError(
                f"{self.action_type} proposed without a rationale; every agent action must "
                "record why it was taken"
            )
        if self.confidence is not None and not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence must be within [0,1] but was {self.confidence}")

    @property
    def is_irreversible(self) -> bool:
        return self.action_type in IRREVERSIBLE_ACTIONS

    @property
    def restricted_domains(self) -> frozenset[RestrictedDomain]:
        implied = DOMAIN_BY_ACTION.get(self.action_type)
        if implied is None:
            return self.touches_domains
        return self.touches_domains | {implied}
