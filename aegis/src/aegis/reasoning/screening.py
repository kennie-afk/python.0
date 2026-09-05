from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from aegis.anonymization.engine import AnonymizationEngine, LeakageError
from aegis.reasoning.provider import (
    Completion,
    LanguageModel,
    Prompt,
    ReasoningError,
    UnparseableResponseError,
)

SYSTEM_PROMPT = (
    "You assess anonymised candidate briefs against a role requirement. "
    "You never infer or comment on age, gender, ethnicity, nationality, religion, "
    "disability, marital or family status, and you never speculate about attributes "
    "that are absent from the brief. "
    "Reply with a single JSON object containing: score (0.0 to 1.0), "
    "recommendation (ADVANCE, REVIEW or HOLD), rationale (one sentence citing only "
    "stated evidence), and signals_considered (a list of field names you used). "
    "Reply with nothing else."
)

VALID_RECOMMENDATIONS = frozenset({"ADVANCE", "REVIEW", "HOLD"})


@dataclass(frozen=True, slots=True)
class ScreeningResult:
    subject_key: str
    score: float
    recommendation: str
    rationale: str
    signals_considered: tuple[str, ...]
    model: str
    prompt_fingerprint: str

    @property
    def advances(self) -> bool:
        return self.recommendation == "ADVANCE"


class CandidateScreener:
    def __init__(self, model: LanguageModel, anonymizer: AnonymizationEngine) -> None:
        self._model = model
        self._anonymizer = anonymizer

    def screen(self, record: Mapping[str, Any], requirement: str) -> ScreeningResult:
        anonymised = self._anonymizer.anonymize(record)
        self._anonymizer.assert_clean(anonymised.attributes)

        prompt = Prompt(
            system=SYSTEM_PROMPT,
            user=self._brief(anonymised.attributes, requirement),
            temperature=0.0,
            schema_hint="score, recommendation, rationale, signals_considered",
        )

        completion = self._model.complete(prompt)
        return self._parse(anonymised.subject_key, completion)

    def _brief(self, attributes: Mapping[str, Any], requirement: str) -> str:
        lines = [f"role_requirement: {requirement}", "candidate_brief:"]
        for key in sorted(attributes):
            if key == "subject_key":
                continue
            lines.append(f"  {key}: {attributes[key]}")
        return "\n".join(lines)

    def _parse(self, subject_key: str, completion: Completion) -> ScreeningResult:
        payload = completion.as_json()

        missing = [
            field for field in ("score", "recommendation", "rationale") if field not in payload
        ]
        if missing:
            raise UnparseableResponseError(
                f"model {completion.model} omitted required fields: {missing}"
            )

        try:
            score = float(payload["score"])
        except (TypeError, ValueError) as error:
            raise UnparseableResponseError("score was not a number") from error

        if not 0.0 <= score <= 1.0:
            raise ReasoningError(f"model returned a score of {score}, outside [0,1]")

        recommendation = str(payload["recommendation"]).upper()
        if recommendation not in VALID_RECOMMENDATIONS:
            raise ReasoningError(
                f"model returned an unknown recommendation {recommendation!r}; "
                f"expected one of {sorted(VALID_RECOMMENDATIONS)}"
            )

        signals = payload.get("signals_considered") or []
        if not isinstance(signals, list):
            raise UnparseableResponseError("signals_considered was not a list")

        return ScreeningResult(
            subject_key=subject_key,
            score=score,
            recommendation=recommendation,
            rationale=str(payload["rationale"]),
            signals_considered=tuple(str(item) for item in signals),
            model=completion.model,
            prompt_fingerprint=completion.prompt_fingerprint,
        )

    def screen_or_raise_leak(self, record: Mapping[str, Any], requirement: str) -> ScreeningResult:
        try:
            return self.screen(record, requirement)
        except LeakageError:
            raise
