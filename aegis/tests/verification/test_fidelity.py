from __future__ import annotations

import pytest

from aegis.verification import (
    DeterminismProbe,
    DriftReport,
    DriftSeverity,
    FidelityScorer,
    Gate,
    Stability,
)
from aegis.verification.determinism import DeterminismReport


def determinism(stability: Stability, case: str = "screening") -> DeterminismReport:
    return DeterminismReport(
        case=case,
        repetitions=10,
        distinct_outputs=1 if stability is Stability.DETERMINISTIC else 3,
        modal_output="APPROVE",
        modal_share=1.0 if stability is Stability.DETERMINISTIC else 0.4,
        stability=stability,
    )


def drift(severity: DriftSeverity, feature: str = "tenure") -> DriftReport:
    return DriftReport(
        feature=feature,
        metric="psi",
        statistic=0.01 if severity is DriftSeverity.STABLE else 0.42,
        severity=severity,
        baseline_size=1000,
        candidate_size=1000,
    )


class TestGating:
    def test_a_deterministic_undrifted_system_passes(self) -> None:
        report = FidelityScorer().score(
            [determinism(Stability.DETERMINISTIC)], [drift(DriftSeverity.STABLE)]
        )

        assert report.gate is Gate.PASS
        assert report.deployable
        assert report.score == pytest.approx(1.0)
        assert report.findings == ()

    def test_a_non_deterministic_system_is_blocked_from_deployment(self) -> None:
        report = FidelityScorer().score(
            [determinism(Stability.NON_DETERMINISTIC)], [drift(DriftSeverity.STABLE)]
        )

        assert report.gate is Gate.BLOCK
        assert not report.deployable

    def test_significant_drift_alone_can_block(self) -> None:
        report = FidelityScorer().score([], [drift(DriftSeverity.SIGNIFICANT)])

        assert report.gate is Gate.BLOCK

    def test_a_moderate_problem_warns_rather_than_blocking(self) -> None:
        report = FidelityScorer().score(
            [determinism(Stability.NEAR_DETERMINISTIC)], [drift(DriftSeverity.MODERATE)]
        )

        assert report.gate is Gate.WARN
        assert report.deployable


class TestWeighting:
    def test_determinism_carries_more_weight_than_drift(self) -> None:
        scorer = FidelityScorer()

        bad_determinism = scorer.score(
            [determinism(Stability.NON_DETERMINISTIC)], [drift(DriftSeverity.STABLE)]
        )
        bad_drift = scorer.score(
            [determinism(Stability.DETERMINISTIC)], [drift(DriftSeverity.SIGNIFICANT)]
        )

        assert bad_determinism.score < bad_drift.score

    def test_one_bad_case_among_many_good_ones_lowers_but_does_not_zero_the_score(self) -> None:
        reports = [determinism(Stability.DETERMINISTIC, f"case_{i}") for i in range(9)]
        reports.append(determinism(Stability.NON_DETERMINISTIC, "case_9"))

        report = FidelityScorer().score(reports, [drift(DriftSeverity.STABLE)])

        assert 0.0 < report.determinism_score < 1.0
        assert report.gate is Gate.PASS


class TestFindings:
    def test_every_failing_case_is_named_in_the_findings(self) -> None:
        report = FidelityScorer().score(
            [
                determinism(Stability.DETERMINISTIC, "good"),
                determinism(Stability.NON_DETERMINISTIC, "bad"),
            ],
            [drift(DriftSeverity.SIGNIFICANT, "tenure")],
        )

        assert len(report.findings) == 2
        assert any("bad" in finding for finding in report.findings)
        assert any("tenure" in finding for finding in report.findings)
        assert not any("good" in finding for finding in report.findings)

    def test_scoring_nothing_is_an_error_rather_than_a_perfect_score(self) -> None:
        with pytest.raises(ValueError, match="at least one"):
            FidelityScorer().score([], [])

    def test_inverted_gate_thresholds_are_rejected(self) -> None:
        with pytest.raises(ValueError, match="thresholds"):
            FidelityScorer(warn_below=0.5, block_below=0.9)


class TestEndToEnd:
    def test_a_live_probe_feeds_straight_into_the_gate(self) -> None:
        import itertools

        flip = itertools.cycle(["APPROVE", "REJECT"])
        probe = DeterminismProbe(repetitions=10)

        report = FidelityScorer().score([probe.probe("live", lambda: next(flip))], [])

        assert report.gate is Gate.BLOCK
        assert report.findings
