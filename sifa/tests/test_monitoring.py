from __future__ import annotations

import numpy as np
import pytest

from sifa.monitoring.drift import detect_drift, population_stability_index
from sifa.monitoring.guard import GuardThresholds, RolloutGuard, ServingWindow
from sifa.registry.models import ModelRegistry, Stage
from sifa.core.errors import RegistryError


@pytest.fixture
def rng() -> np.random.Generator:
    return np.random.default_rng(2)


class TestPopulationStabilityIndex:
    def test_a_distribution_against_itself_scores_zero(self, rng: np.random.Generator) -> None:
        sample = rng.normal(0, 1, 4000)
        assert population_stability_index(sample, sample.copy()) == pytest.approx(0.0, abs=1e-9)

    def test_a_mean_shift_registers(self, rng: np.random.Generator) -> None:
        sample = rng.normal(0, 1, 4000)
        assert population_stability_index(sample, sample + 1.0) > 0.25

    def test_float_noise_on_a_binary_feature_is_not_drift(self, rng: np.random.Generator) -> None:
        binary = (rng.random(4000) < 0.5).astype(float)
        noisy = binary + rng.normal(0, 1e-6, 4000)
        assert population_stability_index(binary, noisy) < 0.01

    def test_a_real_shift_in_a_binary_feature_registers(self, rng: np.random.Generator) -> None:
        balanced = (rng.random(4000) < 0.5).astype(float)
        skewed = (rng.random(4000) < 0.85).astype(float)
        assert population_stability_index(balanced, skewed) > 0.25

    def test_categorical_reweighting_registers(self, rng: np.random.Generator) -> None:
        categories = rng.integers(0, 5, 4000).astype(float)
        reweighted = np.where(rng.random(4000) < 0.5, 4.0, categories)
        assert population_stability_index(categories, reweighted) > 0.25

    def test_too_little_data_reports_nothing_rather_than_guessing(self) -> None:
        assert population_stability_index([1.0], [2.0]) == 0.0


class TestDriftReports:
    def test_a_stable_feature_is_reported_stable(self, rng: np.random.Generator) -> None:
        sample = rng.normal(0, 1, 3000)
        report = detect_drift("f", sample, sample + rng.normal(0, 1e-9, 3000))
        assert report.severity == "stable"
        assert report.drifted is False

    def test_a_shifted_feature_raises_an_alert(self, rng: np.random.Generator) -> None:
        sample = rng.normal(0, 1, 3000)
        report = detect_drift("f", sample, rng.normal(1.5, 1, 3000))
        assert report.severity == "alert"
        assert report.drifted is True

    def test_the_verdict_is_a_plain_bool_so_it_can_be_serialised(
        self, rng: np.random.Generator
    ) -> None:
        report = detect_drift("f", rng.normal(0, 1, 500), rng.normal(3, 1, 500))
        assert type(report.drifted) is bool


class TestRegistryLifecycle:
    def test_a_model_walks_draft_to_live(self) -> None:
        registry = ModelRegistry()
        registry.register("ranker", object())
        for stage in (Stage.SHADOW, Stage.CANARY, Stage.LIVE):
            registry.transition("ranker", 1, stage)

        live = registry.live("ranker")
        assert live is not None
        assert live.traffic == 1.0

    def test_a_canary_carries_only_a_slice_of_traffic(self) -> None:
        registry = ModelRegistry(canary_traffic=0.1)
        registry.register("ranker", object())
        registry.transition("ranker", 1, Stage.SHADOW)
        registry.transition("ranker", 1, Stage.CANARY)

        assert registry.get("ranker", 1).traffic == pytest.approx(0.1)

    def test_a_model_cannot_skip_straight_from_draft_to_live(self) -> None:
        registry = ModelRegistry()
        registry.register("ranker", object())
        with pytest.raises(RegistryError, match="cannot move"):
            registry.transition("ranker", 1, Stage.LIVE)

    def test_promoting_a_new_version_archives_the_old_one(self) -> None:
        registry = ModelRegistry()
        for _ in range(2):
            registry.register("ranker", object())
        for version in (1, 2):
            registry.transition("ranker", version, Stage.SHADOW)
            registry.transition("ranker", version, Stage.CANARY)
            registry.transition("ranker", version, Stage.LIVE)

        assert registry.get("ranker", 1).stage is Stage.ARCHIVED
        assert registry.get("ranker", 2).stage is Stage.LIVE

    def test_rolling_back_restores_the_previous_version(self) -> None:
        registry = ModelRegistry()
        for _ in range(2):
            registry.register("ranker", object())
        for version in (1, 2):
            registry.transition("ranker", version, Stage.SHADOW)
            registry.transition("ranker", version, Stage.CANARY)
            registry.transition("ranker", version, Stage.LIVE)

        registry.rollback("ranker", "worse click through")

        assert registry.get("ranker", 2).stage is Stage.ROLLED_BACK
        assert registry.get("ranker", 1).stage is Stage.LIVE


class TestRolloutGuard:
    def _window(self, impressions: int, click_every: int) -> ServingWindow:
        window = ServingWindow()
        for i in range(impressions):
            window.record(i % click_every == 0, 0.2, 40.0)
        return window

    def test_a_healthy_canary_is_left_alone(self) -> None:
        guard = RolloutGuard()
        verdict = guard.assess(self._window(1000, 5), self._window(1000, 5))
        assert verdict.healthy

    def test_a_canary_that_halves_click_through_is_caught(self) -> None:
        guard = RolloutGuard()
        verdict = guard.assess(self._window(1000, 5), self._window(1000, 20))
        assert not verdict.healthy
        assert any("click through" in reason for reason in verdict.reasons)

    def test_a_slow_canary_is_caught(self) -> None:
        guard = RolloutGuard(GuardThresholds(max_latency_ms=50.0))
        slow = ServingWindow()
        for i in range(500):
            slow.record(i % 5 == 0, 0.2, 400.0)
        verdict = guard.assess(self._window(500, 5), slow)
        assert not verdict.healthy
        assert any("latency" in reason for reason in verdict.reasons)

    def test_it_waits_for_traffic_before_judging(self) -> None:
        guard = RolloutGuard()
        verdict = guard.assess(self._window(1000, 5), self._window(10, 100))
        assert verdict.healthy
        assert "not enough traffic" in verdict.reasons[0]

    def test_an_unhealthy_canary_is_rolled_back_automatically(self) -> None:
        registry = ModelRegistry()
        for _ in range(2):
            registry.register("ranker", object())
        registry.transition("ranker", 1, Stage.SHADOW)
        registry.transition("ranker", 1, Stage.CANARY)
        registry.transition("ranker", 1, Stage.LIVE)
        registry.transition("ranker", 2, Stage.SHADOW)
        registry.transition("ranker", 2, Stage.CANARY)

        RolloutGuard().enforce(
            registry, "ranker", self._window(1000, 5), self._window(1000, 40)
        )

        assert registry.get("ranker", 2).stage is Stage.ROLLED_BACK
        assert registry.get("ranker", 1).stage is Stage.LIVE
