from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from sifa.persistence.store import ArtifactError
from sifa.ranking.ranker import LearningToRank
from sifa.serving.platform import Platform
from sifa.simulation.world import build_world


def make(path: Path) -> Platform:
    return Platform(world=build_world(n_users=60, n_items=150, seed=7), state_dir=path)


@pytest.fixture(scope="module")
def first_boot(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, dict[str, object]]:
    """A platform that has done real work, and what it looked like before it stopped."""
    path = tmp_path_factory.mktemp("state")
    platform = make(path)
    assert platform.restored is False
    platform.promote_candidate(actor="admin:test")
    for user in platform.world.users[:40]:
        feed = platform.recommend(user)
    platform.record_feedback(feed["request_id"], feed["items"][0]["item_id"], True)
    live = platform.registry.live("ranker")
    assert live is not None
    snapshot: dict[str, object] = {
        "versions": [(v.version, v.stage.value) for v in platform.registry.versions("ranker")],
        "history": {
            v.version: [(s.value, r, a) for (_, s, r), a in zip(v.history, v.actors, strict=True)]
            for v in platform.registry.versions("ranker")
        },
        "counters": (platform.counters.control_trials, platform.counters.treatment_trials),
        "served": platform.live_window.impressions + platform.canary_window.impressions,
        "bandit": platform.pipeline.sampler.export(),
        "scores": live.payload.score(platform._rows[:200]).tolist(),
    }
    platform.store.close()
    return path, snapshot


def test_a_restart_keeps_versions_stages_history_and_counters(first_boot) -> None:  # type: ignore[no-untyped-def]
    path, before = first_boot
    again = make(path)

    assert again.restored is True
    stages = [(v.version, v.stage.value) for v in again.registry.versions("ranker")]
    assert stages == before["versions"]
    assert {
        v.version: [(s.value, r, a) for (_, s, r), a in zip(v.history, v.actors, strict=True)]
        for v in again.registry.versions("ranker")
    } == before["history"]
    assert (again.counters.control_trials, again.counters.treatment_trials) == before["counters"]
    assert again.live_window.impressions + again.canary_window.impressions == before["served"]
    assert again.pipeline.sampler.export() == before["bandit"]
    assert before["bandit"]  # the click above moved an arm, so this compares something


def test_a_restart_serves_the_same_live_model(first_boot) -> None:  # type: ignore[no-untyped-def]
    path, before = first_boot
    again = make(path)
    live = again.registry.live("ranker")

    assert live is not None
    restored = live.payload.score(again._rows[:200])
    assert np.allclose(restored, np.asarray(before["scores"]), atol=1e-9)
    assert again.registry.canary("ranker") is not None
    assert again.recommend(again.world.users[0])["items"]


def test_a_restart_does_not_retrain(first_boot, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    path, _ = first_boot

    def refuse(*_: object, **__: object) -> None:
        raise AssertionError("retrained although a matching artifact exists")

    monkeypatch.setattr(LearningToRank, "fit", refuse)
    monkeypatch.setattr("sifa.retrieval.two_tower.TwoTowerModel.fit", refuse)
    assert make(path).restored is True


def test_changed_data_retrains_and_sets_the_old_state_aside(tmp_path: Path) -> None:
    make(tmp_path).store.close()
    changed = Platform(world=build_world(n_users=60, n_items=150, seed=8), state_dir=tmp_path)

    assert changed.restored is False
    assert len(changed.registry.versions("ranker")) == 1
    assert list(tmp_path.glob("*.stale-*"))


def test_a_tampered_artifact_is_refused_never_loaded(tmp_path: Path) -> None:
    make(tmp_path).store.close()
    artifact = tmp_path / "artifacts" / "ranker-v1.npz"
    artifact.write_bytes(artifact.read_bytes() + b"x")

    with pytest.raises(ArtifactError, match="hash"):
        make(tmp_path)


def test_artifacts_are_plain_arrays_with_no_pickle(tmp_path: Path) -> None:
    make(tmp_path).store.close()
    for file in (tmp_path / "artifacts").glob("*.npz"):
        with np.load(file, allow_pickle=False) as archive:
            assert all(archive[name].dtype != object for name in archive.files)


def test_the_exported_ranker_scores_exactly_like_the_trained_one() -> None:
    platform = Platform(world=build_world(n_users=60, n_items=150, seed=3))
    frozen = LearningToRank.from_arrays(platform.ranker.to_arrays())
    rows = platform._rows[:400]

    assert np.allclose(frozen.score(rows), platform.ranker.score(rows), atol=1e-9)


def test_a_state_store_from_a_newer_schema_is_refused(tmp_path: Path) -> None:
    from sifa.core.errors import SifaError
    from sifa.persistence.store import Store

    store = Store(tmp_path)
    store.set_meta("schema_version", "99")
    store.close()
    with pytest.raises(SifaError, match="newer schema"):
        Store(tmp_path)
