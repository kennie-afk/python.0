from __future__ import annotations

import numpy as np
import pytest

from sifa.core.errors import NotTrainedError
from sifa.index.hnsw import HnswConfig
from sifa.retrieval.two_tower import Retriever, TwoTowerConfig, TwoTowerModel


def build_interactions() -> list[tuple[str, str]]:
    topics = {"sport": range(20), "food": range(20, 40), "code": range(40, 60)}
    interactions: list[tuple[str, str]] = []
    for topic, items in topics.items():
        for user in range(12):
            for offset in items:
                interactions.append((f"{topic}-user-{user}", f"item-{offset}"))
    return interactions


@pytest.fixture(scope="module")
def model() -> TwoTowerModel:
    tower = TwoTowerModel(TwoTowerConfig(dimension=32, epochs=14, seed=5))
    tower.fit(build_interactions())
    return tower


def test_an_untrained_tower_reports_itself_untrained() -> None:
    assert TwoTowerModel(TwoTowerConfig()).is_trained is False


def test_training_reports_its_loss(model: TwoTowerModel) -> None:
    report = TwoTowerModel(TwoTowerConfig(dimension=16, epochs=4, seed=6)).fit(build_interactions())
    assert "final_loss" in report
    assert report["final_loss"] > 0.0


def test_training_reduces_the_loss() -> None:
    tower = TwoTowerModel(TwoTowerConfig(dimension=16, epochs=10, seed=7))
    report = tower.fit(build_interactions())
    assert report["final_loss"] < report["first_loss"]


def test_a_trained_tower_reports_itself_trained(model: TwoTowerModel) -> None:
    assert model.is_trained is True
    assert model.dimension == 32


def test_vectors_are_unit_length(model: TwoTowerModel) -> None:
    user = model.user_vector("sport-user-1")
    item = model.item_vector("item-3")
    assert np.linalg.norm(user) == pytest.approx(1.0, abs=1e-5)
    assert np.linalg.norm(item) == pytest.approx(1.0, abs=1e-5)


def test_an_unknown_user_gets_a_zero_vector(model: TwoTowerModel) -> None:
    assert not np.any(model.user_vector("nobody"))


def test_users_who_share_a_topic_sit_close_together(model: TwoTowerModel) -> None:
    same = float(model.user_vector("sport-user-1") @ model.user_vector("sport-user-2"))
    other = float(model.user_vector("sport-user-1") @ model.user_vector("food-user-2"))
    assert same > other


def test_an_item_sits_closest_to_the_users_who_read_it(model: TwoTowerModel) -> None:
    aligned = float(model.user_vector("code-user-3") @ model.item_vector("item-45"))
    unrelated = float(model.user_vector("code-user-3") @ model.item_vector("item-5"))
    assert aligned > unrelated


def test_the_tower_builds_a_searchable_index(model: TwoTowerModel) -> None:
    index = model.build_index(HnswConfig(m=8, ef_construction=60, seed=3))
    assert len(index) == 60


def test_retrieval_returns_the_requested_count(model: TwoTowerModel) -> None:
    retriever = Retriever(model, model.build_index(HnswConfig(m=8, ef_construction=60, seed=3)))
    assert len(retriever.retrieve("sport-user-0", k=10)) == 10


def test_retrieval_favours_the_users_own_topic(model: TwoTowerModel) -> None:
    retriever = Retriever(model, model.build_index(HnswConfig(m=12, ef_construction=100, seed=3)))
    results = retriever.retrieve("food-user-0", k=10, ef=120)
    in_topic = sum(20 <= int(row.item_id.split("-")[1]) < 40 for row in results)
    assert in_topic >= 7


def test_retrieval_scores_fall_monotonically(model: TwoTowerModel) -> None:
    retriever = Retriever(model, model.build_index(HnswConfig(m=8, ef_construction=60, seed=3)))
    scores = [row.retrieval_score for row in retriever.retrieve("sport-user-0", k=10)]
    assert scores == sorted(scores, reverse=True)


def test_retrieval_honours_the_exclusion_set(model: TwoTowerModel) -> None:
    retriever = Retriever(model, model.build_index(HnswConfig(m=8, ef_construction=60, seed=3)))
    seen = {row.item_id for row in retriever.retrieve("sport-user-0", k=5)}
    fresh = retriever.retrieve("sport-user-0", k=5, exclude=seen)
    assert len(fresh) == 5
    assert not (seen & {row.item_id for row in fresh})


def test_retrieval_for_an_unknown_user_is_empty(model: TwoTowerModel) -> None:
    retriever = Retriever(model, model.build_index(HnswConfig(m=8, ef_construction=60, seed=3)))
    assert retriever.retrieve("nobody", k=10) == []


def test_every_candidate_names_its_source(model: TwoTowerModel) -> None:
    retriever = Retriever(model, model.build_index(HnswConfig(m=8, ef_construction=60, seed=3)))
    assert {row.source for row in retriever.retrieve("code-user-1", k=5)} == {"two_tower"}


def test_training_needs_interactions() -> None:
    with pytest.raises(NotTrainedError):
        TwoTowerModel(TwoTowerConfig()).fit([])
