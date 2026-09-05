from __future__ import annotations

import itertools

import pytest

from aegis.verification import (
    DeterminismProbe,
    ProbeError,
    Stability,
    canonical_json,
    casefold_text,
    collapse_whitespace,
)


def constant(value: str):
    return lambda: value


def cycling(values: list[str]):
    iterator = itertools.cycle(values)
    return lambda: next(iterator)


class TestStableModels:
    def test_a_model_returning_one_answer_is_deterministic(self) -> None:
        report = DeterminismProbe(repetitions=8).probe("screening", constant("APPROVE"))

        assert report.stability is Stability.DETERMINISTIC
        assert report.is_deterministic
        assert report.distinct_outputs == 1
        assert report.modal_share == 1.0
        assert report.divergence_examples() == ()

    def test_one_stray_answer_in_twenty_is_near_deterministic_not_perfect(self) -> None:
        outputs = ["APPROVE"] * 19 + ["REJECT"]
        report = DeterminismProbe(repetitions=20).probe("screening", cycling(outputs))

        assert report.stability is Stability.NEAR_DETERMINISTIC
        assert report.passes
        assert not report.is_deterministic
        assert report.modal_output == "APPROVE"
        assert report.divergence_examples() == ("REJECT",)


class TestUnstableModels:
    def test_a_model_answering_differently_three_times_in_ten_is_unstable(self) -> None:
        report = DeterminismProbe(repetitions=10).probe(
            "screening", cycling(["APPROVE"] * 7 + ["REJECT"] * 3)
        )

        assert report.stability is Stability.UNSTABLE
        assert not report.passes

    def test_a_model_with_no_majority_answer_is_non_deterministic(self) -> None:
        report = DeterminismProbe(repetitions=9).probe(
            "screening", cycling(["APPROVE", "REJECT", "REVIEW"])
        )

        assert report.stability is Stability.NON_DETERMINISTIC
        assert report.distinct_outputs == 3
        assert report.modal_share == pytest.approx(1 / 3)

    def test_identical_input_producing_contrasting_evaluations_is_caught(self) -> None:
        report = DeterminismProbe(repetitions=10).probe(
            "same_candidate_twice", cycling(["score:0.81", "score:0.42"])
        )

        assert not report.passes
        assert report.distinct_outputs == 2


class TestNormalization:
    def test_whitespace_differences_alone_do_not_count_as_divergence(self) -> None:
        report = DeterminismProbe(repetitions=4, normalizer=collapse_whitespace).probe(
            "spacing", cycling(["APPROVE  NOW", "APPROVE NOW", "APPROVE\tNOW", "APPROVE\nNOW"])
        )

        assert report.is_deterministic

    def test_case_differences_alone_do_not_count_as_divergence(self) -> None:
        report = DeterminismProbe(repetitions=4, normalizer=casefold_text).probe(
            "casing", cycling(["Approve", "APPROVE", "approve", "ApPrOvE"])
        )

        assert report.is_deterministic

    def test_json_key_order_does_not_count_as_divergence(self) -> None:
        report = DeterminismProbe(repetitions=4, normalizer=canonical_json).probe(
            "json",
            cycling(['{"score":0.8,"band":"HIGH"}', '{"band":"HIGH","score":0.8}']),
        )

        assert report.is_deterministic

    def test_a_real_value_change_survives_normalization(self) -> None:
        report = DeterminismProbe(repetitions=4, normalizer=canonical_json).probe(
            "json",
            cycling(['{"score":0.8}', '{"score":0.9}']),
        )

        assert not report.is_deterministic
        assert report.distinct_outputs == 2

    def test_malformed_json_falls_back_to_text_comparison(self) -> None:
        report = DeterminismProbe(repetitions=2, normalizer=canonical_json).probe(
            "json", constant("not json at all")
        )

        assert report.is_deterministic


class TestFailureHandling:
    def test_an_exception_aborts_the_probe_by_default(self) -> None:
        def explode() -> str:
            raise RuntimeError("upstream timeout")

        with pytest.raises(ProbeError, match="upstream timeout"):
            DeterminismProbe(repetitions=3).probe("flaky", explode)

    def test_failures_can_be_tolerated_and_are_counted(self) -> None:
        calls = {"n": 0}

        def flaky() -> str:
            calls["n"] += 1
            if calls["n"] % 2 == 0:
                raise RuntimeError("upstream timeout")
            return "APPROVE"

        report = DeterminismProbe(repetitions=6, tolerate_failures=True).probe("flaky", flaky)

        assert report.failures == 3
        assert report.repetitions == 3
        assert report.is_deterministic

    def test_a_case_that_always_fails_is_an_error_not_a_pass(self) -> None:
        def explode() -> str:
            raise RuntimeError("down")

        with pytest.raises(ProbeError, match="no successful executions"):
            DeterminismProbe(repetitions=3, tolerate_failures=True).probe("dead", explode)


class TestConfiguration:
    def test_a_single_repetition_cannot_measure_determinism(self) -> None:
        with pytest.raises(ValueError, match="at least two repetitions"):
            DeterminismProbe(repetitions=1)

    def test_inverted_thresholds_are_rejected(self) -> None:
        with pytest.raises(ValueError, match="thresholds"):
            DeterminismProbe(near_threshold=0.5, unstable_threshold=0.9)

    def test_probe_all_reports_each_case_separately(self) -> None:
        reports = DeterminismProbe(repetitions=4).probe_all(
            {
                "stable": constant("APPROVE"),
                "unstable": cycling(["APPROVE", "REJECT"]),
            }
        )

        assert reports["stable"].is_deterministic
        assert not reports["unstable"].is_deterministic
