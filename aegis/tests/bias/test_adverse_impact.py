from __future__ import annotations

import pytest

from aegis.bias import (
    AdverseImpactError,
    GroupOutcome,
    ImpactVerdict,
    four_fifths_test,
    selection_outcomes,
)


class TestFourFifthsRule:
    def test_equal_selection_rates_show_no_adverse_impact(self) -> None:
        report = four_fifths_test(
            [
                GroupOutcome("group_a", selected=50, total=100),
                GroupOutcome("group_b", selected=50, total=100),
            ]
        )

        assert report.verdict is ImpactVerdict.NO_ADVERSE_IMPACT
        assert report.passed
        assert report.failing_groups == ()

    def test_a_ratio_below_four_fifths_is_adverse_impact(self) -> None:
        report = four_fifths_test(
            [
                GroupOutcome("group_a", selected=60, total=100),
                GroupOutcome("group_b", selected=30, total=100),
            ]
        )

        assert report.verdict is ImpactVerdict.ADVERSE_IMPACT
        assert not report.passed
        assert [group.group for group in report.failing_groups] == ["group_b"]
        assert report.failing_groups[0].impact_ratio == pytest.approx(0.5)

    def test_exactly_four_fifths_passes(self) -> None:
        report = four_fifths_test(
            [
                GroupOutcome("group_a", selected=50, total=100),
                GroupOutcome("group_b", selected=40, total=100),
            ]
        )

        assert report.passed
        assert report.groups[1].impact_ratio == pytest.approx(0.80)

    def test_just_below_four_fifths_fails(self) -> None:
        report = four_fifths_test(
            [
                GroupOutcome("group_a", selected=50, total=100),
                GroupOutcome("group_b", selected=39, total=100),
            ]
        )

        assert not report.passed

    def test_the_highest_selecting_group_is_the_reference(self) -> None:
        report = four_fifths_test(
            [
                GroupOutcome("group_a", selected=30, total=100),
                GroupOutcome("group_b", selected=70, total=100),
                GroupOutcome("group_c", selected=50, total=100),
            ]
        )

        assert report.reference_group == "group_b"
        assert report.reference_rate == pytest.approx(0.70)

    def test_every_failing_group_is_reported_not_just_the_worst(self) -> None:
        report = four_fifths_test(
            [
                GroupOutcome("group_a", selected=80, total=100),
                GroupOutcome("group_b", selected=40, total=100),
                GroupOutcome("group_c", selected=30, total=100),
            ]
        )

        assert len(report.failing_groups) == 2


class TestSmallSamples:
    def test_a_group_below_the_minimum_size_is_excluded(self) -> None:
        report = four_fifths_test(
            [
                GroupOutcome("group_a", selected=50, total=100),
                GroupOutcome("group_b", selected=45, total=100),
                GroupOutcome("tiny", selected=0, total=5),
            ]
        )

        assert [group.group for group in report.groups] == ["group_a", "group_b"]
        assert report.passed

    def test_too_few_eligible_groups_reports_insufficient_data_not_a_pass(self) -> None:
        report = four_fifths_test(
            [
                GroupOutcome("group_a", selected=5, total=10),
                GroupOutcome("group_b", selected=1, total=8),
            ]
        )

        assert report.verdict is ImpactVerdict.INSUFFICIENT_DATA
        assert not report.passed
        assert report.note is not None
        assert "minimum size" in report.note

    def test_a_process_selecting_nobody_is_insufficient_data(self) -> None:
        report = four_fifths_test(
            [
                GroupOutcome("group_a", selected=0, total=100),
                GroupOutcome("group_b", selected=0, total=100),
            ]
        )

        assert report.verdict is ImpactVerdict.INSUFFICIENT_DATA


class TestStatisticalSignificance:
    def test_a_large_clear_disparity_is_statistically_significant(self) -> None:
        report = four_fifths_test(
            [
                GroupOutcome("group_a", selected=400, total=500),
                GroupOutcome("group_b", selected=100, total=500),
            ]
        )

        assert report.p_value is not None
        assert report.p_value < 0.01

    def test_an_even_split_is_not_significant(self) -> None:
        report = four_fifths_test(
            [
                GroupOutcome("group_a", selected=250, total=500),
                GroupOutcome("group_b", selected=248, total=500),
            ]
        )

        assert report.p_value is not None
        assert report.p_value > 0.05


class TestInputValidation:
    def test_one_group_cannot_be_compared_against_itself(self) -> None:
        with pytest.raises(AdverseImpactError, match="at least two groups"):
            four_fifths_test([GroupOutcome("group_a", selected=10, total=100)])

    def test_duplicate_group_names_are_rejected(self) -> None:
        with pytest.raises(AdverseImpactError, match="unique"):
            four_fifths_test(
                [
                    GroupOutcome("group_a", selected=10, total=100),
                    GroupOutcome("group_a", selected=20, total=100),
                ]
            )

    def test_selecting_more_people_than_applied_is_rejected(self) -> None:
        with pytest.raises(AdverseImpactError, match="selected"):
            GroupOutcome("group_a", selected=101, total=100)

    def test_an_empty_group_is_rejected(self) -> None:
        with pytest.raises(AdverseImpactError, match="no applicants"):
            GroupOutcome("group_a", selected=0, total=0)


class TestOutcomeBuilding:
    def test_outcomes_are_derived_from_raw_decisions(self) -> None:
        groups = ["a"] * 100 + ["b"] * 100
        selected = [True] * 60 + [False] * 40 + [True] * 30 + [False] * 70

        outcomes = selection_outcomes(groups, selected)

        assert {outcome.group: outcome.selected for outcome in outcomes} == {"a": 60, "b": 30}
        assert not four_fifths_test(outcomes).passed

    def test_mismatched_lengths_are_rejected(self) -> None:
        with pytest.raises(AdverseImpactError, match="same length"):
            selection_outcomes(["a", "b"], [True])

    def test_the_summary_names_the_failing_group_and_its_ratio(self) -> None:
        report = four_fifths_test(
            [
                GroupOutcome("group_a", selected=60, total=100),
                GroupOutcome("group_b", selected=30, total=100),
            ]
        )

        assert "group_b" in report.summary()
        assert "0.50" in report.summary()
