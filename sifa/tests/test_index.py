from __future__ import annotations

import numpy as np
import pytest

from sifa.core.errors import VectorIndexError
from sifa.index.hnsw import HnswConfig, HnswIndex, cosine_distance, normalise


@pytest.fixture
def rng() -> np.random.Generator:
    return np.random.default_rng(11)


def build(rng: np.random.Generator, n: int = 600, d: int = 32) -> tuple[HnswIndex, np.ndarray]:
    vectors = rng.normal(size=(n, d)).astype(np.float32)
    index = HnswIndex(d, HnswConfig(m=12, ef_construction=100, ef_search=60, seed=3))
    for i, vector in enumerate(vectors):
        index.add(f"item-{i}", vector)
    return index, vectors


class TestGeometry:
    def test_a_vector_is_identical_to_itself(self) -> None:
        vector = np.array([1.0, 2.0, 3.0], dtype=np.float32)
        assert cosine_distance(vector, vector) == pytest.approx(0.0, abs=1e-6)

    def test_opposite_vectors_are_maximally_distant(self) -> None:
        vector = np.array([1.0, 0.0], dtype=np.float32)
        assert cosine_distance(vector, -vector) == pytest.approx(2.0, abs=1e-6)

    def test_magnitude_does_not_change_direction(self) -> None:
        a = np.array([3.0, 4.0], dtype=np.float32)
        assert cosine_distance(a, a * 10) == pytest.approx(0.0, abs=1e-6)

    def test_a_zero_vector_normalises_without_dividing_by_zero(self) -> None:
        assert np.all(normalise(np.zeros(4, dtype=np.float32)) == 0.0)


class TestRecall:
    def test_it_finds_almost_everything_brute_force_finds(self, rng: np.random.Generator) -> None:
        index, _ = build(rng)
        queries = rng.normal(size=(40, 32)).astype(np.float32)

        hits = 0
        total = 0
        for query in queries:
            approximate = {key for key, _ in index.search(query, 10)}
            exact = {key for key, _ in index.brute_force(query, 10)}
            hits += len(approximate & exact)
            total += len(exact)

        assert hits / total >= 0.90

    def test_a_larger_search_budget_never_loses_recall(self, rng: np.random.Generator) -> None:
        index, _ = build(rng)
        query = rng.normal(size=32).astype(np.float32)
        exact = {key for key, _ in index.brute_force(query, 10)}

        narrow = len({k for k, _ in index.search(query, 10, ef=10)} & exact)
        wide = len({k for k, _ in index.search(query, 10, ef=200)} & exact)

        assert wide >= narrow

    def test_an_indexed_vector_retrieves_itself_first(self, rng: np.random.Generator) -> None:
        index, vectors = build(rng, n=300)
        for probe in (0, 42, 199):
            top = index.search(vectors[probe], 1)
            assert top[0][0] == f"item-{probe}"

    def test_scores_come_back_in_descending_order(self, rng: np.random.Generator) -> None:
        index, _ = build(rng, n=200)
        results = index.search(rng.normal(size=32).astype(np.float32), 10)
        scores = [score for _, score in results]
        assert scores == sorted(scores, reverse=True)


class TestBehaviour:
    def test_an_empty_index_returns_nothing_rather_than_failing(self) -> None:
        assert HnswIndex(8).search(np.ones(8, dtype=np.float32), 5) == []

    def test_re_adding_a_key_updates_it_instead_of_duplicating(self) -> None:
        index = HnswIndex(4)
        index.add("a", np.array([1, 0, 0, 0], dtype=np.float32))
        index.add("a", np.array([0, 1, 0, 0], dtype=np.float32))

        assert len(index) == 1

    def test_a_wrong_sized_vector_is_refused_on_insert(self) -> None:
        with pytest.raises(VectorIndexError, match="dimensions"):
            HnswIndex(8).add("a", np.ones(4, dtype=np.float32))

    def test_a_wrong_sized_query_is_refused(self) -> None:
        index = HnswIndex(8)
        index.add("a", np.ones(8, dtype=np.float32))
        with pytest.raises(VectorIndexError, match="dimensions"):
            index.search(np.ones(4, dtype=np.float32), 1)

    def test_a_degenerate_configuration_is_refused(self) -> None:
        with pytest.raises(VectorIndexError):
            HnswConfig(m=1)
        with pytest.raises(VectorIndexError):
            HnswConfig(m=16, ef_construction=4)

    def test_asking_for_more_than_exists_returns_what_exists(self) -> None:
        index = HnswIndex(4)
        for i in range(3):
            index.add(f"i{i}", np.eye(4, dtype=np.float32)[i])
        assert len(index.search(np.ones(4, dtype=np.float32), 50)) == 3
