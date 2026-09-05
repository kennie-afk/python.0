from __future__ import annotations

import numpy as np
import pytest

from aegis.verification import (
    DriftError,
    DriftSeverity,
    categorical_drift,
    distribution_shift,
    population_stability_index,
)

RNG = np.random.default_rng(20260905)


def normal(mean: float, size: int = 1000, sigma: float = 1.0) -> list[float]:
    return list(RNG.normal(mean, sigma, size))


class TestNumericDrift:
    def test_a_resampled_population_shows_no_drift(self) -> None:
        report = population_stability_index("tenure", normal(5.0), normal(5.0))

        assert report.severity is DriftSeverity.STABLE
        assert not report.has_drifted
        assert report.statistic < 0.10

    def test_a_shifted_population_is_flagged_as_significant(self) -> None:
        report = population_stability_index("tenure", normal(5.0), normal(9.0))

        assert report.severity is DriftSeverity.SIGNIFICANT
        assert report.has_drifted
        assert report.statistic > 0.25

    def test_drift_severity_increases_with_the_size_of_the_shift(self) -> None:
        small = population_stability_index("tenure", normal(5.0), normal(5.3)).statistic
        large = population_stability_index("tenure", normal(5.0), normal(8.0)).statistic

        assert large > small

    def test_the_worst_bucket_is_identifiable_for_investigation(self) -> None:
        report = population_stability_index("tenure", normal(5.0), normal(9.0))
        contributors = report.top_contributors(limit=2)

        assert len(contributors) == 2
        assert contributors[0][1] >= contributors[1][1]

    def test_a_constant_baseline_cannot_drift(self) -> None:
        report = population_stability_index("tenure", [3.0] * 50, [3.0] * 50)

        assert report.severity is DriftSeverity.STABLE

    def test_too_little_baseline_data_is_an_error_not_a_silent_pass(self) -> None:
        with pytest.raises(DriftError, match="at least"):
            population_stability_index("tenure", [1.0, 2.0], [1.0])


class TestCategoricalDrift:
    def test_an_unchanged_mix_shows_no_drift(self) -> None:
        baseline = ["ENGINEERING"] * 60 + ["SALES"] * 40
        candidate = ["ENGINEERING"] * 30 + ["SALES"] * 20

        assert categorical_drift("department", baseline, candidate).severity is DriftSeverity.STABLE

    def test_an_inverted_mix_is_flagged(self) -> None:
        baseline = ["ENGINEERING"] * 90 + ["SALES"] * 10
        candidate = ["ENGINEERING"] * 10 + ["SALES"] * 90

        report = categorical_drift("department", baseline, candidate)

        assert report.severity is DriftSeverity.SIGNIFICANT
        assert report.has_drifted

    def test_a_brand_new_category_is_detected(self) -> None:
        baseline = ["ENGINEERING"] * 100
        candidate = ["ENGINEERING"] * 50 + ["CONTRACTOR"] * 50

        report = categorical_drift("department", baseline, candidate)

        assert report.has_drifted
        assert any(name == "CONTRACTOR" for name, _ in report.contributions)

    def test_a_category_that_disappears_is_detected(self) -> None:
        baseline = ["ENGINEERING"] * 50 + ["INTERN"] * 50
        candidate = ["ENGINEERING"] * 100

        assert categorical_drift("department", baseline, candidate).has_drifted

    def test_empty_samples_are_an_error(self) -> None:
        with pytest.raises(DriftError, match="non-empty"):
            categorical_drift("department", [], ["ENGINEERING"])


class TestDistributionShift:
    def test_samples_from_one_population_are_not_shifted(self) -> None:
        report = distribution_shift("score", normal(0.5, 500, 0.1), normal(0.5, 500, 0.1))

        assert report.severity is DriftSeverity.STABLE
        assert report.p_value is not None
        assert report.p_value >= 0.05

    def test_a_clearly_different_population_is_shifted(self) -> None:
        report = distribution_shift("score", normal(0.5, 500, 0.1), normal(0.9, 500, 0.1))

        assert report.severity is DriftSeverity.SIGNIFICANT
        assert report.p_value is not None
        assert report.p_value < 0.05

    def test_the_metric_and_sizes_are_reported_for_the_audit_trail(self) -> None:
        report = distribution_shift("score", normal(0.5, 200), normal(0.5, 300))

        assert report.metric == "ks_2samp"
        assert report.baseline_size == 200
        assert report.candidate_size == 300

    def test_two_observations_are_not_enough_to_compare(self) -> None:
        with pytest.raises(DriftError, match="at least two"):
            distribution_shift("score", [0.5], [0.6])
