from __future__ import annotations

import math

import pytest

from sifa.evaluation.metrics import (
    average_precision,
    dcg,
    expected_calibration_error,
    intra_list_diversity,
    mean_reciprocal_rank,
    ndcg,
    precision_at_k,
    recall_at_k,
)


def test_dcg_discounts_later_positions() -> None:
    assert dcg([1.0, 0.0]) > dcg([0.0, 1.0])


def test_dcg_uses_exponential_gain() -> None:
    expected = (2.0**3 - 1) / math.log2(2) + (2.0**1 - 1) / math.log2(3)
    assert dcg([3.0, 1.0]) == pytest.approx(expected)


def test_dcg_truncates_at_k() -> None:
    assert dcg([1.0, 1.0, 1.0], k=1) == pytest.approx(1.0)


def test_ndcg_is_one_for_perfect_order() -> None:
    assert ndcg([3.0, 2.0, 1.0]) == pytest.approx(1.0)


def test_ndcg_penalises_reversed_order() -> None:
    assert ndcg([1.0, 2.0, 3.0]) < 1.0


def test_ndcg_of_all_zero_relevance_is_zero() -> None:
    assert ndcg([0.0, 0.0, 0.0]) == 0.0


def test_ndcg_is_bounded() -> None:
    assert 0.0 <= ndcg([2.0, 0.0, 3.0, 1.0]) <= 1.0


def test_recall_counts_only_relevant_within_k() -> None:
    assert recall_at_k(["a", "b", "c"], {"a", "d"}, 2) == pytest.approx(0.5)


def test_recall_of_empty_relevant_set_is_zero() -> None:
    assert recall_at_k(["a"], set(), 5) == 0.0


def test_recall_reaches_one_when_all_found() -> None:
    assert recall_at_k(["a", "b"], {"a", "b"}, 2) == pytest.approx(1.0)


def test_precision_divides_by_k_not_by_length() -> None:
    assert precision_at_k(["a"], {"a"}, 4) == pytest.approx(0.25)


def test_precision_of_zero_k_is_zero() -> None:
    assert precision_at_k(["a"], {"a"}, 0) == 0.0


def test_reciprocal_rank_uses_first_hit() -> None:
    assert mean_reciprocal_rank(["x", "y", "a"], {"a", "y"}) == pytest.approx(0.5)


def test_reciprocal_rank_is_zero_without_a_hit() -> None:
    assert mean_reciprocal_rank(["x", "y"], {"a"}) == 0.0


def test_average_precision_rewards_early_hits() -> None:
    early = average_precision(["a", "x", "b"], {"a", "b"})
    late = average_precision(["x", "a", "b"], {"a", "b"})
    assert early > late


def test_average_precision_of_perfect_ranking_is_one() -> None:
    assert average_precision(["a", "b"], {"a", "b"}) == pytest.approx(1.0)


def test_average_precision_without_relevant_items_is_zero() -> None:
    assert average_precision(["a", "b"], set()) == 0.0


def test_calibration_error_is_zero_for_honest_probabilities() -> None:
    probabilities = [0.0] * 50 + [1.0] * 50
    outcomes = [0] * 50 + [1] * 50
    assert expected_calibration_error(probabilities, outcomes) == pytest.approx(0.0)


def test_calibration_error_detects_overconfidence() -> None:
    probabilities = [0.9] * 100
    outcomes = [0] * 100
    assert expected_calibration_error(probabilities, outcomes) == pytest.approx(0.9)


def test_calibration_error_of_empty_input_is_zero() -> None:
    assert expected_calibration_error([], []) == 0.0


def test_calibration_error_rejects_mismatched_lengths() -> None:
    with pytest.raises(ValueError):
        expected_calibration_error([0.5], [1, 0])


def test_intra_list_diversity_is_one_for_orthogonal_items() -> None:
    assert intra_list_diversity([[1.0, 0.0], [0.0, 1.0]]) == pytest.approx(1.0)


def test_intra_list_diversity_is_zero_for_identical_items() -> None:
    assert intra_list_diversity([[1.0, 1.0], [1.0, 1.0]]) == pytest.approx(0.0)


def test_intra_list_diversity_of_single_item_is_zero() -> None:
    assert intra_list_diversity([[1.0]]) == 0.0
