from __future__ import annotations

from aegis.agents.workflow import StepDefinition, WorkflowDefinition
from aegis.governance.actions import ActionType

TALENT_ACQUISITION = WorkflowDefinition(
    name="talent_acquisition",
    steps=(
        StepDefinition(
            key="source",
            action_type=ActionType.SCORE_CANDIDATE,
            description="score the candidate against the requisition skill model",
        ),
        StepDefinition(
            key="shortlist",
            action_type=ActionType.SHORTLIST_CANDIDATE,
            description="shortlist the candidate for recruiter review",
            requires=("source",),
        ),
        StepDefinition(
            key="engage",
            action_type=ActionType.SEND_MESSAGE,
            description="open personalised outreach and confirm continued interest",
            requires=("shortlist",),
            requires_context=("recipient_email", "subject", "body"),
        ),
        StepDefinition(
            key="schedule",
            action_type=ActionType.SCHEDULE_INTERVIEW,
            description="match interviewer availability and book the interview",
            requires=("engage",),
            requires_context=("attendees", "starts_at"),
        ),
        StepDefinition(
            key="offer",
            action_type=ActionType.EXTEND_OFFER,
            description="extend the employment offer",
            requires=("schedule",),
        ),
    ),
)

ONBOARDING = WorkflowDefinition(
    name="onboarding",
    steps=(
        StepDefinition(
            key="request_documents",
            action_type=ActionType.REQUEST_DOCUMENT,
            description="request identity, tax and eligibility documentation",
        ),
        StepDefinition(
            key="verify_documents",
            action_type=ActionType.VERIFY_DOCUMENT,
            description="verify submitted identity and eligibility documents",
            requires=("request_documents",),
        ),
        StepDefinition(
            key="background_check",
            action_type=ActionType.ORDER_BACKGROUND_CHECK,
            description="order the background check and await the provider result",
            requires=("verify_documents",),
            awaits_external=True,
        ),
        StepDefinition(
            key="order_hardware",
            action_type=ActionType.ORDER_HARDWARE,
            description="raise the hardware shipment order for the role",
            requires=("verify_documents",),
            awaits_external=True,
        ),
        StepDefinition(
            key="provision_access",
            action_type=ActionType.PROVISION_ACCESS,
            description="provision software platforms, security roles and single sign-on",
            requires=("background_check",),
        ),
        StepDefinition(
            key="learning_path",
            action_type=ActionType.ASSIGN_LEARNING_PATH,
            description="assign the 30-60-90 day learning pathway and peer buddy",
            requires=("provision_access",),
        ),
        StepDefinition(
            key="milestone_check_ins",
            action_type=ActionType.SCHEDULE_CHECK_IN,
            description="schedule milestone check-ins against the role requirements",
            requires=("learning_path",),
            requires_context=("attendees", "starts_at"),
        ),
    ),
)

RETENTION_INTERVENTION = WorkflowDefinition(
    name="retention_intervention",
    steps=(
        StepDefinition(
            key="flag_risk",
            action_type=ActionType.FLAG_RETENTION_RISK,
            description="raise the predicted flight risk to the employee's manager",
        ),
        StepDefinition(
            key="recommend_move",
            action_type=ActionType.RECOMMEND_INTERNAL_MOVE,
            description="recommend internal roles matching the employee's skill adjacency",
            requires=("flag_risk",),
            optional=True,
        ),
        StepDefinition(
            key="schedule_conversation",
            action_type=ActionType.SCHEDULE_CHECK_IN,
            description="schedule a retention conversation with the manager",
            requires=("flag_risk",),
            requires_context=("attendees", "starts_at"),
        ),
    ),
)

OFFBOARDING = WorkflowDefinition(
    name="offboarding",
    steps=(
        StepDefinition(
            key="terminate",
            action_type=ActionType.TERMINATE_EMPLOYMENT,
            description="record the employment termination",
        ),
        StepDefinition(
            key="revoke_access",
            action_type=ActionType.REVOKE_ACCESS,
            description="revoke platform access and single sign-on",
            requires=("terminate",),
        ),
    ),
)

CATALOGUE: dict[str, WorkflowDefinition] = {
    definition.name: definition
    for definition in (TALENT_ACQUISITION, ONBOARDING, RETENTION_INTERVENTION, OFFBOARDING)
}
