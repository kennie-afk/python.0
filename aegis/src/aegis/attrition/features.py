from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from aegis.anonymization.engine import PROTECTED_ATTRIBUTES


class FeatureError(ValueError):
    pass


class ProtectedFeatureError(FeatureError):
    pass


FEATURE_NAMES: tuple[str, ...] = (
    "tenure_years",
    "months_since_promotion",
    "compensation_ratio_to_band",
    "compensation_ratio_to_peers",
    "manager_changes_24m",
    "commute_minutes",
    "engagement_score",
    "training_hours_12m",
    "overtime_hours_monthly",
    "internal_applications_12m",
)


class RiskBand(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"

    @classmethod
    def from_probability(cls, probability: float) -> RiskBand:
        if probability >= 0.60:
            return cls.HIGH
        if probability >= 0.30:
            return cls.MEDIUM
        return cls.LOW


@dataclass(frozen=True, slots=True)
class EmployeeSnapshot:
    subject_key: str
    tenure_years: float
    months_since_promotion: float
    salary: float
    band_midpoint: float
    peer_median_salary: float
    manager_changes_24m: int
    commute_minutes: float
    engagement_score: float
    training_hours_12m: float
    overtime_hours_monthly: float
    internal_applications_12m: int

    def __post_init__(self) -> None:
        if self.band_midpoint <= 0 or self.peer_median_salary <= 0:
            raise FeatureError(
                f"{self.subject_key}: band midpoint and peer median must be positive to "
                "compute a relative compensation position"
            )
        if self.tenure_years < 0 or self.months_since_promotion < 0:
            raise FeatureError(f"{self.subject_key}: durations cannot be negative")


def assert_no_protected_attributes(columns: Sequence[str]) -> None:
    offending = sorted(column for column in columns if column.lower() in PROTECTED_ATTRIBUTES)
    if offending:
        raise ProtectedFeatureError(
            "attrition features must never include protected attributes, found: "
            + ", ".join(offending)
        )


def build_features(snapshot: EmployeeSnapshot) -> dict[str, float]:
    return {
        "tenure_years": snapshot.tenure_years,
        "months_since_promotion": snapshot.months_since_promotion,
        "compensation_ratio_to_band": snapshot.salary / snapshot.band_midpoint,
        "compensation_ratio_to_peers": snapshot.salary / snapshot.peer_median_salary,
        "manager_changes_24m": float(snapshot.manager_changes_24m),
        "commute_minutes": snapshot.commute_minutes,
        "engagement_score": snapshot.engagement_score,
        "training_hours_12m": snapshot.training_hours_12m,
        "overtime_hours_monthly": snapshot.overtime_hours_monthly,
        "internal_applications_12m": float(snapshot.internal_applications_12m),
    }


def from_mapping(subject_key: str, record: Mapping[str, float]) -> EmployeeSnapshot:
    assert_no_protected_attributes(list(record))

    required = (
        "tenure_years",
        "months_since_promotion",
        "salary",
        "band_midpoint",
        "peer_median_salary",
    )
    missing = [field for field in required if field not in record]
    if missing:
        raise FeatureError(f"{subject_key}: missing required fields {missing}")

    return EmployeeSnapshot(
        subject_key=subject_key,
        tenure_years=float(record["tenure_years"]),
        months_since_promotion=float(record["months_since_promotion"]),
        salary=float(record["salary"]),
        band_midpoint=float(record["band_midpoint"]),
        peer_median_salary=float(record["peer_median_salary"]),
        manager_changes_24m=int(record.get("manager_changes_24m", 0)),
        commute_minutes=float(record.get("commute_minutes", 0.0)),
        engagement_score=float(record.get("engagement_score", 3.0)),
        training_hours_12m=float(record.get("training_hours_12m", 0.0)),
        overtime_hours_monthly=float(record.get("overtime_hours_monthly", 0.0)),
        internal_applications_12m=int(record.get("internal_applications_12m", 0)),
    )
