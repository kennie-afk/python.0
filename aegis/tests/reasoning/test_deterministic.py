from __future__ import annotations

import json

from aegis.reasoning.deterministic import DeterministicModel
from aegis.reasoning.provider import Prompt


def complete(requirement: str, **attributes: object) -> dict[str, object]:
    lines = [f"role_requirement: {requirement}", "candidate_brief:"]
    lines.extend(f"  {key}: {value}" for key, value in attributes.items())
    prompt = Prompt(system="assess the brief", user="\n".join(lines))
    return json.loads(DeterministicModel().complete(prompt).text)


def test_experience_short_of_the_requirement_does_not_advance() -> None:
    result = complete("five years of backend engineering", years_experience=2)
    assert result["score"] < 0.75
    assert result["recommendation"] != "ADVANCE"


def test_experience_is_scored_against_the_requirement() -> None:
    result = complete("five years of backend engineering", years_experience=2)
    assert result["score"] == 0.4


def test_experience_beyond_the_requirement_advances() -> None:
    result = complete("five years of backend engineering", years_experience=7)
    assert result["score"] == 1.0
    assert result["recommendation"] == "ADVANCE"


def test_meeting_the_requirement_exactly_advances() -> None:
    assert complete("five years of backend engineering", years_experience=5)["score"] == 1.0


def test_more_experience_never_scores_lower() -> None:
    weaker = complete("five years of backend engineering", years_experience=3)["score"]
    stronger = complete("five years of backend engineering", years_experience=4)["score"]
    assert stronger > weaker


def test_a_digit_requirement_is_read_the_same_way() -> None:
    spelled = complete("five years of backend engineering", years_experience=2)
    digits = complete("5 years of backend engineering", years_experience=2)
    assert spelled["score"] == digits["score"]


def test_proportions_are_used_as_they_are() -> None:
    assert complete("five years of backend engineering", skill_match=0.5)["score"] == 0.5


def test_a_skill_signal_outweighs_a_plain_one() -> None:
    balanced = complete("four years of delivery", skill_match=1.0, years_experience=2)
    assert balanced["score"] > 0.5


def test_a_magnitude_without_a_threshold_is_not_evidential() -> None:
    result = complete("a track record of shipping", years_experience=2)
    assert "years_experience" not in result["signals_considered"]


def test_the_rationale_names_the_score() -> None:
    result = complete("five years of backend engineering", years_experience=7)
    assert "1.00" in str(result["rationale"])


def test_scoring_is_deterministic() -> None:
    first = complete("five years of backend engineering", years_experience=6)
    second = complete("five years of backend engineering", years_experience=6)
    assert first == second


def test_a_brief_with_no_numbers_is_flagged_as_not_evidential() -> None:
    result = complete("five years of backend engineering", summary="strong communicator")
    assert "not evidential" in str(result["rationale"])
