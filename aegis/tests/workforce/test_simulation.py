from __future__ import annotations

import pytest

from aegis.workforce import Scenario, SimulationError, compare, hires_required, simulate


def scenario(**overrides: object) -> Scenario:
    base = {
        "name": "baseline",
        "starting_headcount": 100,
        "monthly_attrition_rate": 0.01,
        "monthly_hires": 2,
        "hire_ramp_months": 3,
        "monthly_demand": 0.0,
    }
    base.update(overrides)
    return Scenario(**base)  # type: ignore[arg-type]


class TestProjection:
    def test_a_stable_team_holds_its_headcount(self) -> None:
        result = simulate(scenario(monthly_attrition_rate=0.02, monthly_hires=2), months=12)

        assert 95 <= result.final_headcount <= 105

    def test_attrition_without_hiring_shrinks_the_team(self) -> None:
        result = simulate(scenario(monthly_attrition_rate=0.05, monthly_hires=0), months=12)

        assert result.final_headcount < 60
        assert result.total_hires == 0

    def test_hiring_above_attrition_grows_the_team(self) -> None:
        result = simulate(scenario(monthly_attrition_rate=0.01, monthly_hires=5), months=12)

        assert result.final_headcount > 100
        assert result.total_hires == 60

    def test_every_month_is_reported(self) -> None:
        assert len(simulate(scenario(), months=18).months) == 18

    def test_leavers_accumulate_across_the_horizon(self) -> None:
        assert simulate(scenario(monthly_attrition_rate=0.05), months=12).total_leavers > 0


class TestRamp:
    def test_new_hires_are_not_immediately_productive(self) -> None:
        result = simulate(
            scenario(starting_headcount=0, monthly_hires=10, hire_ramp_months=3), months=1
        )

        assert result.months[0].headcount == 10
        assert result.months[0].effective_capacity < 10

    def test_capacity_catches_up_once_hiring_stops(self) -> None:
        ramping = simulate(
            scenario(
                starting_headcount=0,
                monthly_hires=10,
                hire_ramp_months=3,
                monthly_attrition_rate=0.0,
            ),
            months=3,
        )
        assert ramping.months[-1].effective_capacity < ramping.months[-1].headcount

        settled = simulate(
            scenario(
                starting_headcount=30,
                monthly_hires=0,
                hire_ramp_months=3,
                monthly_attrition_rate=0.0,
            ),
            months=6,
        )
        last = settled.months[-1]

        assert last.effective_capacity == pytest.approx(last.headcount)

    def test_a_zero_ramp_makes_hires_productive_at_once(self) -> None:
        result = simulate(
            scenario(starting_headcount=0, monthly_hires=10, hire_ramp_months=0), months=1
        )

        assert result.months[0].effective_capacity == pytest.approx(10.0)


class TestDemand:
    def test_capacity_below_demand_is_a_shortfall(self) -> None:
        result = simulate(
            scenario(starting_headcount=10, monthly_hires=0, monthly_demand=50.0), months=6
        )

        assert result.first_shortfall_month == 1
        assert result.peak_shortfall > 0
        assert "falls short" in result.summary()

    def test_ample_capacity_reports_no_shortfall(self) -> None:
        result = simulate(
            scenario(starting_headcount=100, monthly_demand=10.0), months=6
        )

        assert result.first_shortfall_month is None
        assert "capacity holds" in result.summary()


class TestPlanning:
    def test_the_hires_needed_to_reach_a_target_are_computed(self) -> None:
        needed = hires_required(
            scenario(starting_headcount=100, monthly_attrition_rate=0.02, monthly_hires=0),
            target_headcount=120,
            months=12,
        )

        assert needed > 0
        grown = simulate(scenario(monthly_attrition_rate=0.02, monthly_hires=needed), months=12)
        assert grown.final_headcount >= 120

    def test_a_target_already_met_needs_no_hires(self) -> None:
        assert (
            hires_required(
                scenario(starting_headcount=100, monthly_attrition_rate=0.0, monthly_hires=0),
                target_headcount=90,
            )
            == 0
        )

    def test_scenarios_can_be_compared_side_by_side(self) -> None:
        results = compare(
            [
                scenario(name="freeze", monthly_hires=0),
                scenario(name="growth", monthly_hires=5),
            ],
            months=12,
        )

        assert len(results) == 2
        assert results[1].final_headcount > results[0].final_headcount


class TestValidation:
    def test_an_impossible_attrition_rate_is_rejected(self) -> None:
        with pytest.raises(SimulationError, match="within"):
            scenario(monthly_attrition_rate=1.0)

    def test_negative_headcount_is_rejected(self) -> None:
        with pytest.raises(SimulationError, match="negative"):
            scenario(starting_headcount=-1)

    def test_a_zero_month_simulation_is_rejected(self) -> None:
        with pytest.raises(SimulationError, match="at least one month"):
            simulate(scenario(), months=0)

    def test_comparing_nothing_is_rejected(self) -> None:
        with pytest.raises(SimulationError, match="nothing to compare"):
            compare([])
