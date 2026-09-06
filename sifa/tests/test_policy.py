from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from sifa.core.types import ScoredItem
from sifa.policy.rules import (
    PolicyConfig,
    apply_freshness,
    cap_per_attribute,
    cosine_similarity_lookup,
    freshness_decay,
    maximal_marginal_relevance,
    similarity_matrix,
)

NOW = datetime(2026, 3, 1, tzinfo=UTC)


def item(item_id: str, score: float) -> ScoredItem:
    return ScoredItem(item_id=item_id, score=score, retrieval_score=score, source="tower")


def test_decay_is_one_at_zero_age() -> None:
    assert freshness_decay(timedelta(0), timedelta(days=3)) == pytest.approx(1.0)


def test_decay_halves_at_the_half_life() -> None:
    assert freshness_decay(timedelta(days=3), timedelta(days=3)) == pytest.approx(0.5)


def test_decay_keeps_falling() -> None:
    assert freshness_decay(timedelta(days=6), timedelta(days=3)) == pytest.approx(0.25)


def test_decay_treats_future_timestamps_as_fresh() -> None:
    assert freshness_decay(timedelta(days=-1), timedelta(days=3)) == pytest.approx(1.0)


def test_freshness_lifts_the_newer_of_two_equal_items() -> None:
    items = [item("old", 0.5), item("new", 0.5)]
    published = {"old": NOW - timedelta(days=9), "new": NOW}
    adjusted = {
        row.item_id: row.score
        for row in apply_freshness(items, published, NOW, PolicyConfig())
    }
    assert adjusted["new"] > adjusted["old"]


def test_freshness_leaves_order_alone_when_its_weight_is_zero() -> None:
    items = [item("old", 0.5), item("new", 0.4)]
    published = {"old": NOW - timedelta(days=30), "new": NOW}
    config = PolicyConfig(freshness_weight=0.0)
    adjusted = apply_freshness(items, published, NOW, config)
    assert [row.item_id for row in adjusted] == ["old", "new"]
    assert adjusted[0].score == pytest.approx(0.5)


def test_freshness_ignores_items_with_no_publication_date() -> None:
    items = [item("unknown", 0.5)]
    adjusted = apply_freshness(items, {}, NOW, PolicyConfig())
    assert adjusted[0].score == pytest.approx(0.5)


def test_similarity_matrix_is_symmetric_with_a_unit_diagonal() -> None:
    vectors = {
        "a": np.array([1.0, 0.0]),
        "b": np.array([0.0, 1.0]),
        "c": np.array([1.0, 1.0]),
    }
    items = [item(name, 1.0) for name in ("a", "b", "c")]
    matrix = similarity_matrix(items, vectors)

    assert matrix.shape == (3, 3)
    assert np.allclose(matrix, matrix.T)
    assert np.allclose(np.diag(matrix), 1.0)
    assert matrix[0, 1] == pytest.approx(0.0, abs=1e-6)
    assert matrix[0, 2] == pytest.approx(1.0 / np.sqrt(2.0), abs=1e-6)


def test_similarity_matrix_tolerates_a_missing_vector() -> None:
    items = [item("a", 1.0), item("ghost", 1.0)]
    matrix = similarity_matrix(items, {"a": np.array([1.0, 0.0])})
    assert matrix.shape == (2, 2)
    assert np.isfinite(matrix).all()


def test_mmr_keeps_the_best_item_first() -> None:
    vectors = {"a": np.array([1.0, 0.0]), "b": np.array([1.0, 0.0]), "c": np.array([0.0, 1.0])}
    items = [item("a", 0.9), item("b", 0.85), item("c", 0.4)]
    picked = maximal_marginal_relevance(items, similarity_matrix(items, vectors), k=2, lambda_=0.5)
    assert picked[0].item_id == "a"


def test_mmr_drops_a_near_duplicate_for_something_different() -> None:
    vectors = {"a": np.array([1.0, 0.0]), "b": np.array([1.0, 0.0]), "c": np.array([0.0, 1.0])}
    items = [item("a", 0.9), item("b", 0.85), item("c", 0.4)]
    picked = maximal_marginal_relevance(items, similarity_matrix(items, vectors), k=2, lambda_=0.6)
    assert [row.item_id for row in picked] == ["a", "c"]


def test_pure_diversity_avoids_the_duplicate() -> None:
    vectors = {"a": np.array([1.0, 0.0]), "b": np.array([1.0, 0.0]), "c": np.array([0.0, 1.0])}
    items = [item("a", 0.9), item("b", 0.85), item("c", 0.4)]
    picked = maximal_marginal_relevance(items, similarity_matrix(items, vectors), k=2, lambda_=0.0)
    assert [row.item_id for row in picked] == ["a", "c"]


def test_pure_relevance_keeps_the_duplicate() -> None:
    vectors = {"a": np.array([1.0, 0.0]), "b": np.array([1.0, 0.0]), "c": np.array([0.0, 1.0])}
    items = [item("a", 0.9), item("b", 0.85), item("c", 0.4)]
    picked = maximal_marginal_relevance(items, similarity_matrix(items, vectors), k=2, lambda_=1.0)
    assert [row.item_id for row in picked] == ["a", "b"]


def test_mmr_returns_at_most_k_items() -> None:
    vectors = {name: np.random.default_rng(2).normal(size=4) for name in "abcde"}
    items = [item(name, 1.0 - index * 0.1) for index, name in enumerate("abcde")]
    assert len(maximal_marginal_relevance(items, similarity_matrix(items, vectors), 3, 0.5)) == 3


def test_mmr_never_repeats_an_item() -> None:
    vectors = {name: np.random.default_rng(6).normal(size=4) for name in "abcde"}
    items = [item(name, 1.0) for name in "abcde"]
    picked = maximal_marginal_relevance(items, similarity_matrix(items, vectors), 5, 0.5)
    assert len({row.item_id for row in picked}) == 5


def test_mmr_accepts_a_similarity_callable() -> None:
    vectors = {"a": np.array([1.0, 0.0]), "b": np.array([1.0, 0.0]), "c": np.array([0.0, 1.0])}
    items = [item("a", 0.9), item("b", 0.85), item("c", 0.4)]
    lookup = cosine_similarity_lookup(vectors)
    picked = maximal_marginal_relevance(items, lookup, k=2, lambda_=0.6)
    assert [row.item_id for row in picked] == ["a", "c"]


def test_the_matrix_and_callable_paths_agree() -> None:
    rng = np.random.default_rng(8)
    vectors = {name: rng.normal(size=6) for name in "abcdefgh"}
    items = [item(name, float(rng.random())) for name in "abcdefgh"]
    by_matrix = maximal_marginal_relevance(items, similarity_matrix(items, vectors), 4, 0.5)
    by_callable = maximal_marginal_relevance(items, cosine_similarity_lookup(vectors), 4, 0.5)
    assert [row.item_id for row in by_matrix] == [row.item_id for row in by_callable]


def test_cap_limits_each_author() -> None:
    items = [item(name, 1.0) for name in ("a", "b", "c", "d")]
    author = {"a": "kim", "b": "kim", "c": "kim", "d": "ola"}
    kept = cap_per_attribute(items, author, limit=2)
    assert [row.item_id for row in kept] == ["a", "b", "d"]


def test_cap_keeps_the_highest_scoring_of_a_group() -> None:
    items = [item("best", 0.9), item("worst", 0.1)]
    kept = cap_per_attribute(items, {"best": "kim", "worst": "kim"}, limit=1)
    assert [row.item_id for row in kept] == ["best"]


def test_cap_passes_items_with_no_attribute_through() -> None:
    items = [item("a", 1.0), item("b", 1.0)]
    assert len(cap_per_attribute(items, {}, limit=1)) == 2
