from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from scipy import stats

FOUR_FIFTHS = 0.80


class ImpactVerdict(StrEnum):
    NO_ADVERSE_IMPACT = "NO_ADVERSE_IMPACT"
    ADVERSE_IMPACT = "ADVERSE_IMPACT"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class AdverseImpactError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class GroupOutcome:
    group: str
    selected: int
    total: int

    def __post_init__(self) -> None:
        if self.total <= 0:
            raise AdverseImpactError(f"group {self.group!r} has no applicants")
        if self.selected < 0 or self.selected > self.total:
            raise AdverseImpactError(
                f"group {self.group!r} selected {self.selected} of {self.total}"
            )

    @property
    def selection_rate(self) -> float:
        return self.selected / self.total


@dataclass(frozen=True, slots=True)
class GroupImpact:
    group: str
    selection_rate: float
    impact_ratio: float
    total: int
    selected: int
    adversely_impacted: bool


@dataclass(frozen=True, slots=True)
class AdverseImpactReport:
    verdict: ImpactVerdict
    reference_group: str
    reference_rate: float
    groups: tuple[GroupImpact, ...]
    p_value: float | None = None
    note: str | None = None

    @property
    def failing_groups(self) -> tuple[GroupImpact, ...]:
        return tuple(group for group in self.groups if group.adversely_impacted)

    @property
    def passed(self) -> bool:
        return self.verdict is ImpactVerdict.NO_ADVERSE_IMPACT

    def summary(self) -> str:
        if self.verdict is ImpactVerdict.INSUFFICIENT_DATA:
            return self.note or "insufficient data"
        if self.passed:
            return (
                f"no adverse impact; lowest impact ratio "
                f"{min(group.impact_ratio for group in self.groups):.2f}"
            )
        failing = ", ".join(
            f"{group.group} at {group.impact_ratio:.2f}" for group in self.failing_groups
        )
        return f"adverse impact against {failing} (four-fifths threshold {FOUR_FIFTHS:.2f})"


def four_fifths_test(
    outcomes: Sequence[GroupOutcome],
    minimum_group_size: int = 30,
    threshold: float = FOUR_FIFTHS,
) -> AdverseImpactReport:
    if len(outcomes) < 2:
        raise AdverseImpactError("adverse impact requires at least two groups to compare")

    names = [outcome.group for outcome in outcomes]
    if len(names) != len(set(names)):
        raise AdverseImpactError("group names must be unique")

    eligible = [outcome for outcome in outcomes if outcome.total >= minimum_group_size]
    if len(eligible) < 2:
        return AdverseImpactReport(
            verdict=ImpactVerdict.INSUFFICIENT_DATA,
            reference_group="",
            reference_rate=0.0,
            groups=(),
            note=(
                f"fewer than two groups reach the minimum size of {minimum_group_size}; "
                "a ratio computed on small samples is not evidence"
            ),
        )

    reference = max(eligible, key=lambda outcome: outcome.selection_rate)
    reference_rate = reference.selection_rate

    if reference_rate == 0.0:
        return AdverseImpactReport(
            verdict=ImpactVerdict.INSUFFICIENT_DATA,
            reference_group=reference.group,
            reference_rate=0.0,
            groups=(),
            note="no group selected anyone; there is no selection process to assess",
        )

    groups = tuple(
        GroupImpact(
            group=outcome.group,
            selection_rate=outcome.selection_rate,
            impact_ratio=outcome.selection_rate / reference_rate,
            total=outcome.total,
            selected=outcome.selected,
            adversely_impacted=(outcome.selection_rate / reference_rate) < threshold,
        )
        for outcome in eligible
    )

    verdict = (
        ImpactVerdict.ADVERSE_IMPACT
        if any(group.adversely_impacted for group in groups)
        else ImpactVerdict.NO_ADVERSE_IMPACT
    )

    return AdverseImpactReport(
        verdict=verdict,
        reference_group=reference.group,
        reference_rate=reference_rate,
        groups=groups,
        p_value=_significance(eligible),
    )


def _significance(outcomes: Sequence[GroupOutcome]) -> float | None:
    table = [
        [outcome.selected for outcome in outcomes],
        [outcome.total - outcome.selected for outcome in outcomes],
    ]
    if any(sum(row) == 0 for row in table):
        return None
    try:
        result = stats.chi2_contingency(table)
    except ValueError:
        return None
    return float(result.pvalue)


def selection_outcomes(groups: Sequence[str], selected: Sequence[bool]) -> tuple[GroupOutcome, ...]:
    if len(groups) != len(selected):
        raise AdverseImpactError("groups and selections must be the same length")

    totals: Counter[str] = Counter(groups)
    picks: Counter[str] = Counter(
        group for group, chosen in zip(groups, selected, strict=True) if chosen
    )
    return tuple(
        GroupOutcome(group=group, selected=picks.get(group, 0), total=total)
        for group, total in sorted(totals.items())
    )
