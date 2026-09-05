from __future__ import annotations

import random

import pytest

from aegis.attrition import (
    AttritionModel,
    EmployeeSnapshot,
    FeatureError,
    ModelError,
    ProtectedFeatureError,
    RiskBand,
    assert_no_protected_attributes,
    build_features,
    from_mapping,
)

RANDOM = random.Random(20260905)


def snapshot(
    key: str = "subj_1",
    *,
    tenure: float = 3.0,
    months_since_promotion: float = 12.0,
    salary: float = 100_000.0,
    peer_median: float = 100_000.0,
    manager_changes: int = 0,
    engagement: float = 4.0,
    overtime: float = 5.0,
    internal_applications: int = 0,
) -> EmployeeSnapshot:
    return EmployeeSnapshot(
        subject_key=key,
        tenure_years=tenure,
        months_since_promotion=months_since_promotion,
        salary=salary,
        band_midpoint=100_000.0,
        peer_median_salary=peer_median,
        manager_changes_24m=manager_changes,
        commute_minutes=30.0,
        engagement_score=engagement,
        training_hours_12m=20.0,
        overtime_hours_monthly=overtime,
        internal_applications_12m=internal_applications,
    )


def training_set(size: int = 200) -> tuple[list[EmployeeSnapshot], list[bool]]:
    snapshots: list[EmployeeSnapshot] = []
    outcomes: list[bool] = []

    for index in range(size):
        leaving = index % 2 == 0
        snapshots.append(
            snapshot(
                key=f"subj_{index}",
                months_since_promotion=RANDOM.uniform(30, 60)
                if leaving
                else RANDOM.uniform(1, 12),
                salary=RANDOM.uniform(70_000, 85_000)
                if leaving
                else RANDOM.uniform(100_000, 120_000),
                peer_median=100_000.0,
                engagement=RANDOM.uniform(1.0, 2.5) if leaving else RANDOM.uniform(4.0, 5.0),
                manager_changes=RANDOM.randint(2, 4) if leaving else 0,
                internal_applications=RANDOM.randint(1, 4) if leaving else 0,
            )
        )
        outcomes.append(leaving)

    return snapshots, outcomes


def trained(algorithm: str = "gradient_boosting") -> AttritionModel:
    model = AttritionModel(algorithm)
    model.train(*training_set())
    return model


class TestFeatureEngineering:
    def test_compensation_becomes_a_relative_position_not_an_absolute(self) -> None:
        features = build_features(snapshot(salary=80_000.0, peer_median=100_000.0))

        assert features["compensation_ratio_to_band"] == pytest.approx(0.8)
        assert features["compensation_ratio_to_peers"] == pytest.approx(0.8)
        assert "salary" not in features

    def test_every_declared_feature_is_produced(self) -> None:
        from aegis.attrition import FEATURE_NAMES

        assert set(build_features(snapshot())) == set(FEATURE_NAMES)

    def test_a_zero_band_midpoint_is_rejected_rather_than_dividing_by_zero(self) -> None:
        with pytest.raises(FeatureError, match="must be positive"):
            EmployeeSnapshot(
                subject_key="subj_1",
                tenure_years=1.0,
                months_since_promotion=1.0,
                salary=100.0,
                band_midpoint=0.0,
                peer_median_salary=100.0,
                manager_changes_24m=0,
                commute_minutes=0.0,
                engagement_score=3.0,
                training_hours_12m=0.0,
                overtime_hours_monthly=0.0,
                internal_applications_12m=0,
            )

    def test_negative_durations_are_rejected(self) -> None:
        with pytest.raises(FeatureError, match="negative"):
            snapshot(tenure=-1.0)

    def test_a_mapping_missing_required_fields_names_them(self) -> None:
        with pytest.raises(FeatureError, match="missing required fields"):
            from_mapping("subj_1", {"tenure_years": 3.0})


class TestProtectedAttributeGuard:
    def test_a_protected_attribute_in_the_input_is_refused(self) -> None:
        with pytest.raises(ProtectedFeatureError, match="gender"):
            from_mapping(
                "subj_1",
                {
                    "tenure_years": 3.0,
                    "months_since_promotion": 6.0,
                    "salary": 100_000.0,
                    "band_midpoint": 100_000.0,
                    "peer_median_salary": 100_000.0,
                    "gender": 1.0,
                },
            )

    def test_every_offending_attribute_is_named(self) -> None:
        with pytest.raises(ProtectedFeatureError) as raised:
            assert_no_protected_attributes(["tenure_years", "age", "ethnicity"])

        assert "age" in str(raised.value)
        assert "ethnicity" in str(raised.value)

    def test_the_declared_feature_set_contains_nothing_protected(self) -> None:
        from aegis.attrition import FEATURE_NAMES

        assert_no_protected_attributes(FEATURE_NAMES)


class TestTraining:
    def test_a_model_trains_on_a_realistic_cohort(self) -> None:
        model = AttritionModel()
        report = model.train(*training_set())

        assert model.is_trained
        assert report.rows == 200
        assert report.positive_rate == pytest.approx(0.5)
        assert report.algorithm == "gradient_boosting"

    def test_too_little_data_is_refused_rather_than_fitted(self) -> None:
        snapshots, outcomes = training_set(size=10)

        with pytest.raises(ModelError, match="at least"):
            AttritionModel().train(snapshots, outcomes)

    def test_training_on_one_outcome_class_is_refused(self) -> None:
        snapshots, _ = training_set()

        with pytest.raises(ModelError, match="both employees who left"):
            AttritionModel().train(snapshots, [False] * len(snapshots))

    def test_mismatched_lengths_are_refused(self) -> None:
        snapshots, outcomes = training_set()

        with pytest.raises(ModelError, match="same length"):
            AttritionModel().train(snapshots, outcomes[:-1])

    def test_scoring_before_training_is_an_error(self) -> None:
        with pytest.raises(ModelError, match="must be trained"):
            AttritionModel().score(snapshot())

    def test_an_unknown_algorithm_is_rejected(self) -> None:
        with pytest.raises(ModelError, match="unknown algorithm"):
            AttritionModel("magic").train(*training_set())

    @pytest.mark.parametrize(
        "algorithm", ["gradient_boosting", "random_forest", "logistic_regression"]
    )
    def test_each_supported_algorithm_trains_and_scores(self, algorithm: str) -> None:
        model = trained(algorithm)

        assert 0.0 <= model.score(snapshot()).probability <= 1.0


class TestScoring:
    def test_a_settled_well_paid_employee_scores_low(self) -> None:
        score = trained().score(
            snapshot(
                months_since_promotion=3.0,
                salary=115_000.0,
                engagement=4.8,
                manager_changes=0,
                internal_applications=0,
            )
        )

        assert score.band is RiskBand.LOW
        assert not score.needs_intervention

    def test_an_underpaid_disengaged_employee_scores_high(self) -> None:
        score = trained().score(
            snapshot(
                months_since_promotion=48.0,
                salary=72_000.0,
                engagement=1.4,
                manager_changes=3,
                internal_applications=3,
            )
        )

        assert score.band is RiskBand.HIGH
        assert score.needs_intervention
        assert score.probability > 0.6

    def test_the_score_names_the_factors_driving_it(self) -> None:
        score = trained().score(
            snapshot(months_since_promotion=48.0, salary=72_000.0, engagement=1.4)
        )

        assert score.drivers
        drivers = score.top_drivers(3)
        assert len(drivers) <= 3
        assert all(driver.direction in ("above cohort", "below cohort") for driver in drivers)

    def test_drivers_are_ordered_by_contribution(self) -> None:
        score = trained().score(snapshot(months_since_promotion=48.0, salary=72_000.0))

        contributions = [driver.contribution for driver in score.drivers]
        assert contributions == sorted(contributions, reverse=True)

    def test_a_cohort_can_be_scored_in_one_call(self) -> None:
        scores = trained().score_all([snapshot("a"), snapshot("b")])

        assert [score.subject_key for score in scores] == ["a", "b"]

    def test_the_subject_key_is_carried_through_so_it_stays_pseudonymous(self) -> None:
        assert trained().score(snapshot("subj_abc123")).subject_key == "subj_abc123"


class TestRiskBands:
    @pytest.mark.parametrize(
        ("probability", "expected"),
        [
            (0.05, RiskBand.LOW),
            (0.29, RiskBand.LOW),
            (0.30, RiskBand.MEDIUM),
            (0.59, RiskBand.MEDIUM),
            (0.60, RiskBand.HIGH),
            (0.99, RiskBand.HIGH),
        ],
    )
    def test_bands_are_assigned_at_the_documented_thresholds(
        self, probability: float, expected: RiskBand
    ) -> None:
        assert RiskBand.from_probability(probability) is expected


class TestExplainability:
    def test_feature_importance_is_available_and_normalised(self) -> None:
        importance = trained().feature_importance()

        assert len(importance) == 10
        assert sum(weight for _, weight in importance) == pytest.approx(1.0, abs=1e-6)

    def test_importance_before_training_is_an_error(self) -> None:
        with pytest.raises(ModelError, match="must be trained"):
            AttritionModel().feature_importance()

    def test_the_model_learns_that_time_since_promotion_matters(self) -> None:
        importance = dict(trained().feature_importance())

        assert importance["months_since_promotion"] > 0.0
        assert importance["engagement_score"] > 0.0
