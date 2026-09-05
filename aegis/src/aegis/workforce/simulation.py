from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass


class SimulationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Scenario:
    name: str
    starting_headcount: int
    monthly_attrition_rate: float
    monthly_hires: int
    hire_ramp_months: int = 3
    monthly_demand: float = 0.0

    def __post_init__(self) -> None:
        if self.starting_headcount < 0:
            raise SimulationError("starting headcount cannot be negative")
        if not 0.0 <= self.monthly_attrition_rate < 1.0:
            raise SimulationError("monthly attrition rate must be within [0,1)")
        if self.monthly_hires < 0:
            raise SimulationError("monthly hires cannot be negative")
        if self.hire_ramp_months < 0:
            raise SimulationError("ramp cannot be negative")


@dataclass(frozen=True, slots=True)
class MonthState:
    month: int
    headcount: float
    effective_capacity: float
    leavers: float
    joiners: int
    demand: float

    @property
    def shortfall(self) -> float:
        return max(0.0, self.demand - self.effective_capacity)

    @property
    def covered(self) -> bool:
        return self.shortfall == 0.0


@dataclass(frozen=True, slots=True)
class SimulationResult:
    scenario: str
    months: tuple[MonthState, ...]

    @property
    def final_headcount(self) -> float:
        return self.months[-1].headcount if self.months else 0.0

    @property
    def total_leavers(self) -> float:
        return sum(month.leavers for month in self.months)

    @property
    def total_hires(self) -> int:
        return sum(month.joiners for month in self.months)

    @property
    def first_shortfall_month(self) -> int | None:
        for month in self.months:
            if not month.covered:
                return month.month
        return None

    @property
    def peak_shortfall(self) -> float:
        return max((month.shortfall for month in self.months), default=0.0)

    def summary(self) -> str:
        if self.first_shortfall_month is None:
            return (
                f"{self.scenario}: capacity holds for {len(self.months)} months, "
                f"ending at {self.final_headcount:.1f} heads"
            )
        return (
            f"{self.scenario}: capacity falls short from month "
            f"{self.first_shortfall_month}, peaking at {self.peak_shortfall:.1f} unmet"
        )


def simulate(scenario: Scenario, months: int = 12) -> SimulationResult:
    if months <= 0:
        raise SimulationError("a simulation must cover at least one month")

    headcount = float(scenario.starting_headcount)
    ramping: list[tuple[int, int]] = []
    states: list[MonthState] = []

    for month in range(1, months + 1):
        leavers = headcount * scenario.monthly_attrition_rate
        headcount = max(0.0, headcount - leavers)

        joiners = scenario.monthly_hires
        if joiners:
            ramping.append((month, joiners))
        headcount += joiners

        still_ramping: list[tuple[int, int]] = []
        effective = 0.0
        for started, count in ramping:
            elapsed = month - started
            if scenario.hire_ramp_months == 0 or elapsed >= scenario.hire_ramp_months:
                continue
            still_ramping.append((started, count))
            effective += count * (elapsed / scenario.hire_ramp_months)

        ramping = still_ramping
        tenured = max(0.0, headcount - sum(count for _, count in ramping))
        effective += tenured

        states.append(
            MonthState(
                month=month,
                headcount=round(headcount, 3),
                effective_capacity=round(effective, 3),
                leavers=round(leavers, 3),
                joiners=joiners,
                demand=scenario.monthly_demand,
            )
        )

    return SimulationResult(scenario=scenario.name, months=tuple(states))


def compare(scenarios: Sequence[Scenario], months: int = 12) -> tuple[SimulationResult, ...]:
    if not scenarios:
        raise SimulationError("nothing to compare")
    return tuple(simulate(scenario, months) for scenario in scenarios)


def hires_required(
    scenario: Scenario, target_headcount: int, months: int = 12
) -> int:
    if target_headcount < 0:
        raise SimulationError("target headcount cannot be negative")

    for candidate in range(0, target_headcount + scenario.starting_headcount + 1):
        trial = Scenario(
            name=scenario.name,
            starting_headcount=scenario.starting_headcount,
            monthly_attrition_rate=scenario.monthly_attrition_rate,
            monthly_hires=candidate,
            hire_ramp_months=scenario.hire_ramp_months,
            monthly_demand=scenario.monthly_demand,
        )
        if simulate(trial, months).final_headcount >= target_headcount:
            return candidate

    raise SimulationError(
        f"target of {target_headcount} is unreachable within {months} months at "
        f"{scenario.monthly_attrition_rate:.0%} monthly attrition"
    )
