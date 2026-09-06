from __future__ import annotations

import pytest

from sifa.core.errors import ExperimentError
from sifa.experiments.assignment import Experiment, Variant, assign
from sifa.experiments.sequential import Decision, MixtureSprt


def build(holdout: float = 0.0) -> Experiment:
    return Experiment(
        key="feed_ranker",
        variants=(Variant(name="control", weight=1.0), Variant(name="treatment", weight=1.0)),
        holdout=holdout,
    )


def test_assignment_is_deterministic() -> None:
    experiment = build()
    assert assign(experiment, "user-77") == assign(experiment, "user-77")


def test_assignment_splits_close_to_the_declared_weights() -> None:
    experiment = build()
    counts = {"control": 0, "treatment": 0}
    for index in range(20_000):
        counts[assign(experiment, f"user-{index}")] += 1
    share = counts["treatment"] / 20_000
    assert 0.48 < share < 0.52


def test_unequal_weights_are_respected() -> None:
    experiment = Experiment(
        key="feed_ranker",
        variants=(Variant(name="control", weight=9.0), Variant(name="treatment", weight=1.0)),
    )
    treatment = sum(assign(experiment, f"user-{index}") == "treatment" for index in range(20_000))
    assert 0.08 < treatment / 20_000 < 0.12


def test_holdout_is_carved_out_before_the_split() -> None:
    experiment = build(holdout=0.2)
    held = sum(assign(experiment, f"user-{index}") == "holdout" for index in range(20_000))
    assert 0.18 < held / 20_000 < 0.22


def test_salt_changes_the_partition() -> None:
    left = build()
    right = Experiment(key=left.key, variants=left.variants, salt="other")
    differing = sum(assign(left, f"user-{i}") != assign(right, f"user-{i}") for i in range(500))
    assert differing > 100


def test_a_different_experiment_key_reshuffles_units() -> None:
    left = build()
    right = Experiment(key="other_surface", variants=left.variants)
    differing = sum(assign(left, f"user-{i}") != assign(right, f"user-{i}") for i in range(500))
    assert differing > 100


def test_assignment_requires_a_unit_id() -> None:
    with pytest.raises(ExperimentError):
        assign(build(), "")


def test_an_experiment_needs_two_variants() -> None:
    with pytest.raises(ExperimentError):
        Experiment(key="k", variants=(Variant(name="only", weight=1.0),))


def test_variant_names_must_be_unique() -> None:
    with pytest.raises(ExperimentError):
        Experiment(
            key="k",
            variants=(Variant(name="a", weight=1.0), Variant(name="a", weight=1.0)),
        )


def test_holdout_must_be_a_proportion() -> None:
    with pytest.raises(ExperimentError):
        build(holdout=1.0)


def test_sprt_waits_for_the_minimum_sample() -> None:
    result = MixtureSprt().evaluate(5, 50, 10, 50)
    assert result.decision is Decision.CONTINUE


def test_sprt_detects_a_real_lift() -> None:
    result = MixtureSprt().evaluate(1_000, 20_000, 1_200, 20_000)
    assert result.decision is Decision.TREATMENT_WINS
    assert result.likelihood_ratio > result.threshold
    assert result.lift == pytest.approx(0.2, abs=0.01)


def test_sprt_detects_a_regression() -> None:
    result = MixtureSprt().evaluate(1_200, 20_000, 1_000, 20_000)
    assert result.decision is Decision.CONTROL_WINS


def test_sprt_keeps_watching_when_the_arms_are_equal() -> None:
    result = MixtureSprt().evaluate(2_000, 40_000, 2_000, 40_000)
    assert result.decision is Decision.CONTINUE
    assert result.likelihood_ratio < result.threshold


def test_sprt_reports_the_observed_rates() -> None:
    result = MixtureSprt().evaluate(500, 5_000, 750, 5_000)
    assert result.control_rate == pytest.approx(0.1)
    assert result.treatment_rate == pytest.approx(0.15)
    assert result.samples == 10_000


def test_sprt_threshold_follows_alpha() -> None:
    assert MixtureSprt(alpha=0.05).threshold == pytest.approx(20.0)
    assert MixtureSprt(alpha=0.01).threshold == pytest.approx(100.0)


def test_sprt_rejects_negative_counts() -> None:
    with pytest.raises(ExperimentError):
        MixtureSprt().evaluate(-1, 10, 1, 10)


def test_sprt_rejects_more_successes_than_trials() -> None:
    with pytest.raises(ExperimentError):
        MixtureSprt().evaluate(11, 10, 1, 10)


def test_sprt_false_positive_rate_stays_near_alpha() -> None:
    import numpy as np

    rng = np.random.default_rng(7)
    sprt = MixtureSprt(minimum_samples=200)
    stopped = 0
    runs = 300
    for _ in range(runs):
        control = treatment = 0
        control_hits = treatment_hits = 0
        for _ in range(30):
            control += 100
            treatment += 100
            control_hits += int(rng.binomial(100, 0.1))
            treatment_hits += int(rng.binomial(100, 0.1))
            verdict = sprt.evaluate(control_hits, control, treatment_hits, treatment)
            if verdict.decision is not Decision.CONTINUE:
                stopped += 1
                break
    assert stopped / runs < 0.15
