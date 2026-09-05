from __future__ import annotations

import heapq
import math
import random
from dataclasses import dataclass, field

import numpy as np

from sifa.core.errors import VectorIndexError


def normalise(vector: np.ndarray) -> np.ndarray:
    array = np.asarray(vector, dtype=np.float32).reshape(-1)
    norm = float(np.linalg.norm(array))
    if norm == 0.0:
        return array
    return np.asarray(array / norm, dtype=np.float32)


def cosine_distance(left: np.ndarray, right: np.ndarray) -> float:
    return 1.0 - float(np.dot(normalise(left), normalise(right)))


@dataclass(slots=True)
class HnswConfig:
    m: int = 16
    ef_construction: int = 200
    ef_search: int = 64
    seed: int = 17

    def __post_init__(self) -> None:
        if self.m < 2:
            raise VectorIndexError("m must be at least 2 or the graph cannot connect")
        if self.ef_construction < self.m:
            raise VectorIndexError("ef_construction must be at least m")
        if self.ef_search < 1:
            raise VectorIndexError("ef_search must be at least 1")


@dataclass(slots=True)
class _Node:
    key: str
    neighbours: list[set[int]] = field(default_factory=list)


class HnswIndex:
    def __init__(self, dimension: int, config: HnswConfig | None = None) -> None:
        if dimension < 2:
            raise VectorIndexError("a vector index needs at least two dimensions")

        self._dimension = dimension
        self._config = config or HnswConfig()
        self._nodes: list[_Node] = []
        self._vectors = np.zeros((0, dimension), dtype=np.float32)
        self._capacity = 0
        self._by_key: dict[str, int] = {}
        self._entry: int | None = None
        self._top_level = -1
        self._random = random.Random(self._config.seed)
        self._level_scale = 1.0 / math.log(self._config.m)

    def __len__(self) -> int:
        return len(self._nodes)

    @property
    def dimension(self) -> int:
        return self._dimension

    def _random_level(self) -> int:
        return int(-math.log(max(self._random.random(), 1e-12)) * self._level_scale)

    def _distance(self, vector: np.ndarray, node_id: int) -> float:
        return 1.0 - float(self._vectors[node_id] @ vector)

    def _distances(self, vector: np.ndarray, node_ids: list[int]) -> np.ndarray:
        if not node_ids:
            return np.empty(0, dtype=np.float32)
        return np.asarray(1.0 - (self._vectors[node_ids] @ vector), dtype=np.float32)

    def _reserve(self, needed: int) -> None:
        if needed <= self._capacity:
            return
        capacity = max(16, self._capacity * 2, needed)
        grown = np.zeros((capacity, self._dimension), dtype=np.float32)
        grown[: len(self._nodes)] = self._vectors[: len(self._nodes)]
        self._vectors = grown
        self._capacity = capacity

    def add(self, key: str, vector: np.ndarray) -> None:
        raw = np.asarray(vector, dtype=np.float32).reshape(-1)
        if raw.shape[0] != self._dimension:
            raise VectorIndexError(
                f"vector for {key!r} has {raw.shape[0]} dimensions, index expects {self._dimension}"
            )
        array = normalise(raw)

        if key in self._by_key:
            self._vectors[self._by_key[key]] = array
            return

        level = self._random_level()
        node = _Node(key=key, neighbours=[set() for _ in range(level + 1)])
        node_id = len(self._nodes)
        self._reserve(node_id + 1)
        self._nodes.append(node)
        self._vectors[node_id] = array
        self._by_key[key] = node_id

        if self._entry is None:
            self._entry = node_id
            self._top_level = level
            return

        current = self._entry
        for layer in range(self._top_level, level, -1):
            current = self._greedy_descend(array, current, layer)

        for layer in range(min(level, self._top_level), -1, -1):
            candidates = self._search_layer(array, [current], layer, self._config.ef_construction)
            selected = self._select_neighbours(array, candidates, self._config.m)

            for other in selected:
                node.neighbours[layer].add(other)
                self._nodes[other].neighbours[layer].add(node_id)
                self._prune(other, layer)

            current = selected[0] if selected else current

        if level > self._top_level:
            self._top_level = level
            self._entry = node_id

    def _greedy_descend(self, vector: np.ndarray, start: int, layer: int) -> int:
        current = start
        best = self._distance(vector, current)
        improved = True

        while improved:
            improved = False
            neighbours = list(self._neighbours(current, layer))
            if not neighbours:
                break
            distances = self._distances(vector, neighbours)
            position = int(np.argmin(distances))
            if float(distances[position]) < best:
                best = float(distances[position])
                current = neighbours[position]
                improved = True

        return current

    def _neighbours(self, node_id: int, layer: int) -> set[int]:
        node = self._nodes[node_id]
        if layer >= len(node.neighbours):
            return set()
        return node.neighbours[layer]

    def _search_layer(
        self, vector: np.ndarray, entries: list[int], layer: int, ef: int
    ) -> list[int]:
        visited = set(entries)
        frontier: list[tuple[float, int]] = []
        found: list[tuple[float, int]] = []

        for entry in entries:
            distance = self._distance(vector, entry)
            heapq.heappush(frontier, (distance, entry))
            heapq.heappush(found, (-distance, entry))

        while frontier:
            distance, node_id = heapq.heappop(frontier)
            if found and distance > -found[0][0]:
                break

            fresh = [n for n in self._neighbours(node_id, layer) if n not in visited]
            if not fresh:
                continue
            visited.update(fresh)
            computed = self._distances(vector, fresh)

            for neighbour, neighbour_distance_raw in zip(fresh, computed, strict=True):
                neighbour_distance = float(neighbour_distance_raw)
                if len(found) < ef or neighbour_distance < -found[0][0]:
                    heapq.heappush(frontier, (neighbour_distance, neighbour))
                    heapq.heappush(found, (-neighbour_distance, neighbour))
                    if len(found) > ef:
                        heapq.heappop(found)

        return [node_id for _, node_id in sorted((-d, n) for d, n in found)]

    def _select_neighbours(self, vector: np.ndarray, candidates: list[int], m: int) -> list[int]:
        if not candidates:
            return []
        distances = self._distances(vector, candidates)
        order = np.argsort(distances)[:m]
        return [candidates[int(i)] for i in order]

    def _prune(self, node_id: int, layer: int) -> None:
        limit = self._config.m * 2 if layer == 0 else self._config.m
        neighbours = self._nodes[node_id].neighbours[layer]
        if len(neighbours) <= limit:
            return

        vector = self._vectors[node_id]
        ordered = list(neighbours)
        distances = self._distances(vector, ordered)
        keep = [ordered[int(i)] for i in np.argsort(distances)[:limit]]
        dropped = neighbours - set(keep)
        self._nodes[node_id].neighbours[layer] = set(keep)

        for other in dropped:
            self._nodes[other].neighbours[layer].discard(node_id)

    def search(self, vector: np.ndarray, k: int, ef: int | None = None) -> list[tuple[str, float]]:
        if self._entry is None:
            return []

        raw = np.asarray(vector, dtype=np.float32).reshape(-1)
        if raw.shape[0] != self._dimension:
            raise VectorIndexError(
                f"query has {raw.shape[0]} dimensions, index expects {self._dimension}"
            )
        array = normalise(raw)

        effective = max(ef or self._config.ef_search, k)

        current = self._entry
        for layer in range(self._top_level, 0, -1):
            current = self._greedy_descend(array, current, layer)

        candidates = self._search_layer(array, [current], 0, effective)
        scored = [
            (self._nodes[node_id].key, 1.0 - self._distance(array, node_id))
            for node_id in candidates
        ]
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return scored[:k]

    def brute_force(self, vector: np.ndarray, k: int) -> list[tuple[str, float]]:
        if not self._nodes:
            return []
        array = normalise(vector)
        similarities = self._vectors[: len(self._nodes)] @ array
        top = np.argsort(similarities)[::-1][:k]
        return [(self._nodes[int(i)].key, float(similarities[int(i)])) for i in top]
