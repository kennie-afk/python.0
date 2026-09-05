from __future__ import annotations

import pytest

from aegis.sentiment import (
    Aspect,
    Response,
    SuppressedError,
    analyse,
    detect_early_warnings,
    score_text,
)


def responses(group: str, texts: list[str]) -> list[Response]:
    return [Response(group=group, text=text) for text in texts]


def positive_team(group: str = "platform", size: int = 6) -> list[Response]:
    return responses(
        group,
        [
            "My manager is supportive and the team culture is great.",
            "Pay is fair and the tooling is excellent.",
            "Leadership is clear and I feel valued.",
            "Good learning and collaborative colleagues.",
            "Growth here has been excellent and my lead is helpful.",
            "Workload balance is good and I am happy.",
        ][:size],
    )


def struggling_team(group: str = "support", size: int = 6) -> list[Response]:
    return responses(
        group,
        [
            "The workload is chaotic and I am exhausted every weekend.",
            "My manager ignored my concerns and leadership is unclear.",
            "Pay is unfair and I feel underpaid for the hours.",
            "Overtime is constant and the balance is toxic.",
            "I am frustrated and demoralised by the tooling.",
            "Career progression is stuck and I am thinking of leaving.",
        ][:size],
    )


class TestScoring:
    def test_positive_language_scores_positive(self) -> None:
        overall, _ = score_text("The team culture is great and my manager is supportive.")

        assert overall > 0

    def test_negative_language_scores_negative(self) -> None:
        overall, _ = score_text("The workload is chaotic and I am exhausted.")

        assert overall < 0

    def test_a_negation_flips_the_polarity(self) -> None:
        plain, _ = score_text("Leadership is clear.")
        negated, _ = score_text("Leadership is not clear.")

        assert plain > 0
        assert negated < 0

    def test_neutral_text_scores_zero(self) -> None:
        overall, _ = score_text("I joined the company in March and work on the API.")

        assert overall == 0.0

    def test_sentiment_is_attributed_to_the_aspect_it_concerns(self) -> None:
        _, aspects = score_text("My manager is supportive but the pay is unfair.")

        assert Aspect.LEADERSHIP in aspects
        assert Aspect.COMPENSATION in aspects


class TestAggregation:
    def test_a_healthy_team_reports_positive_sentiment(self) -> None:
        report = analyse(positive_team())
        group = report.group("platform")

        assert group is not None
        assert group.overall > 0
        assert group.respondents == 6

    def test_a_struggling_team_reports_negative_sentiment_and_concerns(self) -> None:
        report = analyse(struggling_team())
        group = report.group("support")

        assert group is not None
        assert group.overall < 0
        assert group.concerns

    def test_the_worst_group_is_named_in_the_summary(self) -> None:
        report = analyse(positive_team() + struggling_team())

        assert "support" in report.summary()

    def test_several_groups_are_reported_separately(self) -> None:
        report = analyse(positive_team() + struggling_team())

        assert len(report.groups) == 2


class TestPrivacyThreshold:
    def test_a_group_below_the_threshold_is_withheld_entirely(self) -> None:
        report = analyse(positive_team(size=6) + struggling_team("tiny_team", size=3))

        assert report.group("tiny_team") is None
        assert "tiny_team" in report.suppressed_groups
        assert report.was_suppressed

    def test_suppression_is_reported_rather_than_hidden(self) -> None:
        report = analyse(struggling_team("tiny_team", size=2))

        assert "withheld" in report.summary()

    def test_a_group_exactly_at_the_threshold_is_reported(self) -> None:
        report = analyse(positive_team(size=5), minimum_group_size=5)

        assert report.group("platform") is not None
        assert report.suppressed_groups == ()

    def test_a_threshold_that_cannot_protect_an_individual_is_refused(self) -> None:
        with pytest.raises(SuppressedError, match="protect an individual"):
            analyse(positive_team(), minimum_group_size=1)

    def test_no_individual_response_is_ever_returned(self) -> None:
        report = analyse(positive_team())
        group = report.group("platform")

        assert group is not None
        assert not hasattr(group, "responses")
        assert isinstance(group.overall, float)


class TestEarlyWarning:
    def test_a_sharp_drop_in_an_aspect_raises_a_warning(self) -> None:
        before = analyse(positive_team())
        after = analyse(struggling_team("platform"))

        warnings = detect_early_warnings(before, after)

        assert warnings
        assert warnings[0].drop > 0
        assert warnings[0].group == "platform"

    def test_a_stable_team_raises_nothing(self) -> None:
        before = analyse(positive_team())
        after = analyse(positive_team())

        assert detect_early_warnings(before, after) == ()

    def test_warnings_are_ordered_worst_first(self) -> None:
        warnings = detect_early_warnings(
            analyse(positive_team()), analyse(struggling_team("platform"))
        )

        drops = [warning.drop for warning in warnings]
        assert drops == sorted(drops, reverse=True)

    def test_a_group_absent_from_the_baseline_is_skipped(self) -> None:
        before = analyse(positive_team("platform"))
        after = analyse(struggling_team("brand_new"))

        assert detect_early_warnings(before, after) == ()

    def test_a_non_positive_threshold_is_refused(self) -> None:
        report = analyse(positive_team())

        with pytest.raises(SuppressedError, match="positive"):
            detect_early_warnings(report, report, drop_threshold=0.0)
