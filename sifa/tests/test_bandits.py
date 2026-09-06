from __future__ import annotations

import pytest

from sifa.bandits.thompson import ArmState, ThompsonSampler


def test_uniform_prior_starts_at_one_half() -> None:
    assert ArmState().mean == pytest.approx(0.5)


def test_trials_count_observations_not_the_prior() -> None:
    assert ArmState().trials == pytest.approx(0.0)
    assert ArmState(successes=4.0, failures=3.0).trials == pytest.approx(5.0)


def test_posterior_mean_moves_with_evidence() -> None:
    assert ArmState(successes=9.0, failures=1.0).mean > 0.8


def test_selection_is_reproducible_for_a_seed() -> None:
    left = ThompsonSampler(seed=5)
    right = ThompsonSampler(seed=5)
    arms = ["a", "b", "c"]
    assert [left.select(arms) for _ in range(30)] == [right.select(arms) for _ in range(30)]


def test_different_seeds_explore_differently() -> None:
    left = ThompsonSampler(seed=1)
    right = ThompsonSampler(seed=2)
    arms = ["a", "b", "c"]
    assert [left.select(arms) for _ in range(30)] != [right.select(arms) for _ in range(30)]


def test_selection_registers_unseen_arms() -> None:
    sampler = ThompsonSampler()
    sampler.select(["fresh"])
    assert "fresh" in sampler.arms


def test_sampler_converges_on_the_best_arm() -> None:
    import numpy as np

    rng = np.random.default_rng(3)
    rates = {"weak": 0.05, "middle": 0.2, "strong": 0.5}
    sampler = ThompsonSampler(seed=11)
    arms = list(rates)
    for _ in range(3_000):
        chosen = sampler.select(arms)
        sampler.update(chosen, float(rng.random() < rates[chosen]))

    pulls = {arm: sampler.arms[arm].trials for arm in arms}
    assert pulls["strong"] > pulls["middle"] > pulls["weak"]
    assert pulls["strong"] / sum(pulls.values()) > 0.8


def test_posterior_estimates_the_true_rate() -> None:
    import numpy as np

    rng = np.random.default_rng(4)
    sampler = ThompsonSampler(seed=13)
    for _ in range(4_000):
        sampler.update("only", float(rng.random() < 0.3))
    mean, deviation = sampler.posterior("only")
    assert mean == pytest.approx(0.3, abs=0.03)
    assert 0.0 < deviation < 0.02


def test_reward_must_be_a_proportion() -> None:
    sampler = ThompsonSampler()
    with pytest.raises(ValueError):
        sampler.update("arm", 1.5)


def test_decay_forgets_stale_evidence() -> None:
    sampler = ThompsonSampler(seed=17, decay=0.9)
    for _ in range(200):
        sampler.update("arm", 1.0)
    assert sampler.arms["arm"].trials < 200


def test_selection_needs_at_least_one_arm() -> None:
    with pytest.raises(ValueError):
        ThompsonSampler().select([])
