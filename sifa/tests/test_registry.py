from __future__ import annotations

import pytest

from sifa.core.errors import RegistryError
from sifa.registry.models import ModelRegistry, Stage


def promote(registry: ModelRegistry, name: str, version: int) -> None:
    registry.transition(name, version, Stage.SHADOW)
    registry.transition(name, version, Stage.CANARY)
    registry.transition(name, version, Stage.LIVE)


def test_registration_starts_in_draft_with_no_traffic() -> None:
    registry = ModelRegistry()
    version = registry.register("ranker", payload={"weights": [1.0]})
    assert version.stage is Stage.DRAFT
    assert version.traffic == 0.0
    assert version.version == 1


def test_versions_increment_per_model_name() -> None:
    registry = ModelRegistry()
    registry.register("ranker", payload=None)
    registry.register("ranker", payload=None)
    registry.register("tower", payload=None)
    assert [v.version for v in registry.versions("ranker")] == [1, 2]
    assert [v.version for v in registry.versions("tower")] == [1]


def test_registration_records_its_metrics() -> None:
    registry = ModelRegistry()
    version = registry.register("ranker", payload=None, metrics={"auc": 0.81})
    assert version.metrics["auc"] == pytest.approx(0.81)


def test_a_model_must_be_shadowed_before_it_can_go_live() -> None:
    registry = ModelRegistry()
    registry.register("ranker", payload=None)
    with pytest.raises(RegistryError):
        registry.transition("ranker", 1, Stage.LIVE)


def test_canary_serves_partial_traffic() -> None:
    registry = ModelRegistry()
    registry.register("ranker", payload=None)
    registry.transition("ranker", 1, Stage.SHADOW)
    canary = registry.transition("ranker", 1, Stage.CANARY)
    assert 0.0 < canary.traffic < 1.0
    assert registry.canary("ranker") is canary


def test_shadow_serves_no_traffic() -> None:
    registry = ModelRegistry()
    registry.register("ranker", payload=None)
    shadow = registry.transition("ranker", 1, Stage.SHADOW)
    assert shadow.traffic == 0.0
    assert registry.shadow("ranker") is shadow


def test_going_live_takes_all_traffic() -> None:
    registry = ModelRegistry()
    registry.register("ranker", payload=None)
    promote(registry, "ranker", 1)
    assert registry.live("ranker").traffic == pytest.approx(1.0)


def test_a_new_live_model_archives_the_previous_one() -> None:
    registry = ModelRegistry()
    registry.register("ranker", payload=None)
    registry.register("ranker", payload=None)
    promote(registry, "ranker", 1)
    promote(registry, "ranker", 2)

    assert registry.get("ranker", 1).stage is Stage.ARCHIVED
    assert registry.get("ranker", 1).traffic == 0.0
    assert registry.live("ranker").version == 2


def test_rollback_restores_the_last_archived_version() -> None:
    registry = ModelRegistry()
    registry.register("ranker", payload=None)
    registry.register("ranker", payload=None)
    promote(registry, "ranker", 1)
    promote(registry, "ranker", 2)

    withdrawn = registry.rollback("ranker", "calibration drifted")

    assert withdrawn.version == 2
    assert withdrawn.stage is Stage.ROLLED_BACK
    assert withdrawn.traffic == 0.0

    restored = registry.live("ranker")
    assert restored is not None
    assert restored.version == 1
    assert restored.traffic == pytest.approx(1.0)


def test_rollback_prefers_the_canary_over_the_live_model() -> None:
    registry = ModelRegistry()
    registry.register("ranker", payload=None)
    registry.register("ranker", payload=None)
    promote(registry, "ranker", 1)
    registry.transition("ranker", 2, Stage.SHADOW)
    registry.transition("ranker", 2, Stage.CANARY)

    registry.rollback("ranker", "canary regressed")

    assert registry.get("ranker", 2).stage is Stage.ROLLED_BACK
    assert registry.live("ranker").version == 1


def test_rollback_without_anything_serving_is_refused() -> None:
    registry = ModelRegistry()
    registry.register("ranker", payload=None)
    with pytest.raises(RegistryError):
        registry.rollback("ranker", "nothing to undo")


def test_a_rolled_back_model_cannot_be_promoted_again() -> None:
    registry = ModelRegistry()
    registry.register("ranker", payload=None)
    promote(registry, "ranker", 1)
    registry.rollback("ranker", "bad")
    with pytest.raises(RegistryError):
        registry.transition("ranker", 1, Stage.LIVE)


def test_an_archived_model_is_terminal() -> None:
    registry = ModelRegistry()
    registry.register("ranker", payload=None)
    registry.transition("ranker", 1, Stage.ARCHIVED)
    with pytest.raises(RegistryError):
        registry.transition("ranker", 1, Stage.SHADOW)


def test_every_transition_is_recorded_with_its_reason() -> None:
    registry = ModelRegistry()
    registry.register("ranker", payload=None)
    registry.transition("ranker", 1, Stage.SHADOW, reason="offline auc cleared the bar")

    history = registry.get("ranker", 1).history
    assert [entry[1] for entry in history] == [Stage.DRAFT, Stage.SHADOW]
    assert history[-1][2] == "offline auc cleared the bar"


def test_history_survives_a_rollback() -> None:
    registry = ModelRegistry()
    registry.register("ranker", payload=None)
    promote(registry, "ranker", 1)
    registry.rollback("ranker", "latency regression")
    stages = [entry[1] for entry in registry.get("ranker", 1).history]
    assert stages == [Stage.DRAFT, Stage.SHADOW, Stage.CANARY, Stage.LIVE, Stage.ROLLED_BACK]


def test_an_unknown_version_is_reported() -> None:
    registry = ModelRegistry()
    with pytest.raises(RegistryError):
        registry.get("ranker", 9)


def test_label_identifies_name_and_version() -> None:
    registry = ModelRegistry()
    assert "ranker" in registry.register("ranker", payload=None).label
