from __future__ import annotations

import json

import pytest

from aegis.anonymization import AnonymizationEngine
from aegis.reasoning import (
    CandidateScreener,
    DeterministicModel,
    Prompt,
    ReasoningError,
    ScriptedModel,
    UnparseableResponseError,
)
from aegis.verification import DeterminismProbe, canonical_json

SALT = "a-sufficiently-long-tenant-salt"


def anonymizer() -> AnonymizationEngine:
    return AnonymizationEngine(salt=SALT, reference_year=2026)


def candidate() -> dict[str, object]:
    return {
        "national_id": "12345678",
        "full_name": "Amina Wanjiru",
        "gender": "female",
        "university": "University of Nairobi",
        "years_experience": 7,
        "skill_match": 0.88,
        "summary": "Backend engineer with distributed systems experience.",
    }


class TestPromptDiscipline:
    def test_a_sampling_temperature_is_refused_for_employment_decisions(self) -> None:
        with pytest.raises(ValueError, match="temperature 0"):
            Prompt(system="s", user="u", temperature=0.7)

    def test_zero_temperature_is_accepted(self) -> None:
        assert Prompt(system="s", user="u", temperature=0.0).temperature == 0.0

    def test_the_same_prompt_fingerprints_identically(self) -> None:
        first = Prompt(system="s", user="u")
        second = Prompt(system="s", user="u")

        assert first.fingerprint() == second.fingerprint()

    def test_a_changed_prompt_fingerprints_differently(self) -> None:
        assert Prompt(system="s", user="u").fingerprint() != Prompt(
            system="s", user="different"
        ).fingerprint()

    def test_the_system_prompt_forbids_protected_inference(self) -> None:
        from aegis.reasoning import SYSTEM_PROMPT

        for forbidden in ("age", "gender", "ethnicity", "religion", "disability"):
            assert forbidden in SYSTEM_PROMPT


class TestScreening:
    def test_a_candidate_is_screened_into_a_structured_result(self) -> None:
        screener = CandidateScreener(DeterministicModel(), anonymizer())

        result = screener.screen(candidate(), "senior backend engineer")

        assert result.subject_key.startswith("subj_")
        assert 0.0 <= result.score <= 1.0
        assert result.recommendation in ("ADVANCE", "REVIEW", "HOLD")
        assert result.rationale

    def test_the_model_never_receives_protected_or_identifying_fields(self) -> None:
        model = DeterministicModel()
        CandidateScreener(model, anonymizer()).screen(candidate(), "engineer")

        brief = model.calls[0].user
        assert "Amina" not in brief
        assert "female" not in brief
        assert "University of Nairobi" not in brief
        assert "12345678" not in brief

    def test_job_relevant_signals_do_reach_the_model(self) -> None:
        model = DeterministicModel()
        CandidateScreener(model, anonymizer()).screen(candidate(), "engineer")

        assert "years_experience" in model.calls[0].user
        assert "skill_match" in model.calls[0].user

    def test_the_result_records_which_model_and_prompt_produced_it(self) -> None:
        result = CandidateScreener(DeterministicModel(), anonymizer()).screen(
            candidate(), "engineer"
        )

        assert result.model == "aegis-deterministic-v1"
        assert len(result.prompt_fingerprint) == 16


class TestMalformedResponses:
    def test_non_json_is_rejected_rather_than_guessed_at(self) -> None:
        screener = CandidateScreener(ScriptedModel(["I think they are great!"]), anonymizer())

        with pytest.raises(UnparseableResponseError, match="valid JSON"):
            screener.screen(candidate(), "engineer")

    def test_a_missing_field_is_named(self) -> None:
        screener = CandidateScreener(ScriptedModel(['{"score": 0.9}']), anonymizer())

        with pytest.raises(UnparseableResponseError, match="recommendation"):
            screener.screen(candidate(), "engineer")

    def test_a_score_outside_the_range_is_refused(self) -> None:
        payload = json.dumps({"score": 1.7, "recommendation": "ADVANCE", "rationale": "x"})
        screener = CandidateScreener(ScriptedModel([payload]), anonymizer())

        with pytest.raises(ReasoningError, match="outside"):
            screener.screen(candidate(), "engineer")

    def test_an_invented_recommendation_is_refused(self) -> None:
        payload = json.dumps({"score": 0.9, "recommendation": "HIRE_NOW", "rationale": "x"})
        screener = CandidateScreener(ScriptedModel([payload]), anonymizer())

        with pytest.raises(ReasoningError, match="unknown recommendation"):
            screener.screen(candidate(), "engineer")

    def test_a_fenced_code_block_is_unwrapped_rather_than_failing(self) -> None:
        payload = (
            '```json\n{"score": 0.8, "recommendation": "ADVANCE", '
            '"rationale": "strong match", "signals_considered": ["skill_match"]}\n```'
        )
        screener = CandidateScreener(ScriptedModel([payload]), anonymizer())

        assert screener.screen(candidate(), "engineer").advances


class TestDeterminism:
    def test_the_reasoning_layer_is_verifiably_deterministic(self) -> None:
        screener = CandidateScreener(DeterministicModel(), anonymizer())
        probe = DeterminismProbe(repetitions=10, normalizer=canonical_json)

        report = probe.probe(
            "candidate_screening",
            lambda: json.dumps(
                {
                    "score": screener.screen(candidate(), "engineer").score,
                    "recommendation": screener.screen(candidate(), "engineer").recommendation,
                }
            ),
        )

        assert report.is_deterministic

    def test_a_flapping_model_is_caught_by_the_same_probe(self) -> None:
        flapping = ScriptedModel(
            [
                '{"score": 0.9, "recommendation": "ADVANCE", "rationale": "a"}',
                '{"score": 0.2, "recommendation": "HOLD", "rationale": "b"}',
            ]
        )
        screener = CandidateScreener(flapping, anonymizer())
        probe = DeterminismProbe(repetitions=10, normalizer=canonical_json)

        report = probe.probe(
            "flapping",
            lambda: json.dumps({"score": screener.screen(candidate(), "engineer").score}),
        )

        assert not report.passes
        assert report.distinct_outputs == 2
