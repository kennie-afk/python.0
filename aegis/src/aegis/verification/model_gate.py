"""The governance gate for a trained attrition model.

Before a model can serve it is checked three ways, and the verdict is stored with it:

* determinism: scoring the same employee repeatedly must give the same number, and training twice
  on the same data must give the same model (a model that is not reproducible cannot be audited);
* drift: the new training population against the reference kept with the active version, per
  feature (PSI); a retrain on a population that has moved wholesale is held for a person to look at;
* adverse impact: when the request supplies group labels (used only for this test, never as a
  feature), the four-fifths rule on who the model flags.

`FidelityScorer` turns determinism and drift into PASS, WARN or BLOCK; an adverse-impact finding
can lower PASS to WARN but never raises anything. BLOCK models are stored, so the record shows what
was refused and why, but they cannot be activated or served.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from typing import Any

import numpy as np

from aegis.attrition.features import FEATURE_NAMES, EmployeeSnapshot, RiskBand
from aegis.attrition.model import AttritionModel
from aegis.bias.adverse_impact import (
    AdverseImpactReport,
    ImpactVerdict,
    four_fifths_test,
    selection_outcomes,
)
from aegis.verification.determinism import DeterminismProbe, DeterminismReport
from aegis.verification.drift import DriftReport, population_stability_index
from aegis.verification.fidelity import FidelityReport, FidelityScorer, Gate

PROBE_SAMPLE = 5
PROBE_REPETITIONS = 10
# Retraining just to check reproducibility costs a full fit; skip it on very large inputs.
RETRAIN_PROBE_MAX_ROWS = 20_000
MIN_DRIFT_REFERENCE = 10


@dataclass(frozen=True, slots=True)
class ModelFidelity:
    report: FidelityReport
    determinism: tuple[DeterminismReport, ...]
    drift: tuple[DriftReport, ...]
    adverse_impact: AdverseImpactReport | None
    reference_version: int | None
    notes: tuple[str, ...]

    @property
    def gate(self) -> Gate:
        return self.report.gate

    def to_dict(self) -> dict[str, Any]:
        return {
            "gate": str(self.report.gate),
            "score": round(self.report.score, 4),
            "determinism_score": round(self.report.determinism_score, 4),
            "drift_score": round(self.report.drift_score, 4),
            "findings": list(self.report.findings),
            "determinism": [determinism_view(r) for r in self.determinism],
            "drift": [drift_view(r) for r in self.drift],
            "adverse_impact": impact_view(self.adverse_impact),
            "reference_version": self.reference_version,
            "notes": list(self.notes),
        }


def determinism_view(report: DeterminismReport) -> dict[str, Any]:
    return {
        "case": report.case,
        "stability": str(report.stability),
        "repetitions": report.repetitions,
        "distinct_outputs": report.distinct_outputs,
        "modal_share": round(report.modal_share, 4),
        "passes": report.passes,
    }


def drift_view(report: DriftReport) -> dict[str, Any]:
    return {
        "feature": report.feature,
        "metric": report.metric,
        "statistic": round(report.statistic, 4),
        "severity": str(report.severity),
        "p_value": None if report.p_value is None else round(report.p_value, 6),
        "baseline_size": report.baseline_size,
        "candidate_size": report.candidate_size,
    }


def impact_view(report: AdverseImpactReport | None) -> dict[str, Any] | None:
    if report is None:
        return None
    return {
        "verdict": str(report.verdict),
        "reference_group": report.reference_group,
        "p_value": report.p_value,
        "groups": [
            {
                "group": g.group,
                "selection_rate": round(g.selection_rate, 4),
                "impact_ratio": round(g.impact_ratio, 4),
                "total": g.total,
                "selected": g.selected,
                "adversely_impacted": g.adversely_impacted,
            }
            for g in report.groups
        ],
    }


def feature_drift(reference: np.ndarray, candidate: np.ndarray) -> tuple[DriftReport, ...]:
    """PSI per model feature, reference against candidate. Empty when there is no usable
    reference (a legacy model, or fewer rows than buckets)."""
    if len(reference) < MIN_DRIFT_REFERENCE or len(candidate) < 1:
        return ()
    return tuple(
        population_stability_index(name, reference[:, index].tolist(), candidate[:, index].tolist())
        for index, name in enumerate(FEATURE_NAMES)
    )


def assess_model(
    model: AttritionModel,
    snapshots: Sequence[EmployeeSnapshot],
    left: Sequence[bool],
    previous: AttritionModel | None = None,
    previous_version: int | None = None,
    groups: Sequence[str] | None = None,
    minimum_group_size: int = 30,
) -> ModelFidelity:
    notes: list[str] = []
    probe = DeterminismProbe(repetitions=PROBE_REPETITIONS)

    step = max(1, len(snapshots) // PROBE_SAMPLE)
    sample = list(snapshots[::step][:PROBE_SAMPLE])

    def scorer_for(snapshot: EmployeeSnapshot) -> Callable[[], str]:
        return lambda: f"{model.score(snapshot).probability:.8f}"

    reports = [
        probe.probe(f"score:{snapshot.subject_key}", scorer_for(snapshot)) for snapshot in sample
    ]

    if len(snapshots) <= RETRAIN_PROBE_MAX_ROWS:
        probe_rows = sample

        def retrain() -> str:
            twin = AttritionModel(model.algorithm)
            twin.train(snapshots, left)
            return ",".join(f"{twin.score(s).probability:.8f}" for s in probe_rows)

        reports.append(DeterminismProbe(repetitions=2).probe("training_reproducibility", retrain))
    else:
        notes.append("training reproducibility was not probed: more than 20,000 rows")

    drift: tuple[DriftReport, ...] = ()
    if previous is None:
        notes.append("no earlier version, so there is no reference to measure drift against")
    else:
        drift = feature_drift(previous.reference, model.reference)
        if not drift:
            notes.append("the earlier version kept no usable reference, so drift was not measured")

    scorer = FidelityScorer()
    report = scorer.score(reports, drift)

    impact: AdverseImpactReport | None = None
    if groups is not None:
        matrix = model.features_matrix(snapshots)
        flagged = [
            RiskBand.from_probability(float(p)) is RiskBand.HIGH
            for p in model.predict_matrix(matrix)
        ]
        impact = four_fifths_test(
            selection_outcomes(list(groups), flagged), minimum_group_size=minimum_group_size
        )
        if impact.verdict is ImpactVerdict.ADVERSE_IMPACT:
            finding = "adverse impact in who is flagged high risk: " + "; ".join(
                f"{g.group} at {g.impact_ratio:.2f} of {impact.reference_group}"
                for g in impact.groups
                if g.adversely_impacted
            )
            report = replace(
                report,
                gate=Gate.WARN if report.gate is Gate.PASS else report.gate,
                findings=(*report.findings, finding),
            )
        elif impact.verdict is ImpactVerdict.INSUFFICIENT_DATA:
            notes.append(
                "adverse impact could not be assessed: " + (impact.note or "too little data")
            )
    else:
        notes.append("no group labels supplied, so adverse impact was not tested")

    return ModelFidelity(
        report=report,
        determinism=tuple(reports),
        drift=drift,
        adverse_impact=impact,
        reference_version=previous_version if previous is not None else None,
        notes=tuple(notes),
    )
