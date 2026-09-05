from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from sifa.core.errors import LeakageError, SchemaError
from sifa.features.schema import FeatureKind, FeatureSpec, FeatureView
from sifa.features.store import FeatureStore

VIEW = FeatureView(
    name="user_activity",
    entity="user",
    features=(
        FeatureSpec("clicks_7d", FeatureKind.NUMERIC, default=0.0),
        FeatureSpec("dwell_mean", FeatureKind.NUMERIC, default=0.0),
    ),
    ttl=timedelta(days=30),
)


def at(day: int, hour: int = 12) -> datetime:
    return datetime(2026, 3, day, hour, tzinfo=UTC)


@pytest.fixture
def store() -> FeatureStore:
    return FeatureStore(VIEW)


class TestPointInTimeCorrectness:
    def test_a_join_never_sees_a_value_written_after_the_label(self, store: FeatureStore) -> None:
        store.write("u1", at(1), {"clicks_7d": 5.0})
        store.write("u1", at(10), {"clicks_7d": 500.0})

        examples = store.point_in_time_join([("u1", at(5), 1.0)])

        assert examples[0].features["clicks_7d"] == 5.0

    def test_a_label_before_any_feature_falls_back_to_the_default(
        self, store: FeatureStore
    ) -> None:
        store.write("u1", at(10), {"clicks_7d": 500.0})

        examples = store.point_in_time_join([("u1", at(5), 1.0)])

        assert examples[0].features["clicks_7d"] == 0.0

    def test_the_newest_value_at_or_before_the_cutoff_wins(self, store: FeatureStore) -> None:
        for day, value in [(1, 1.0), (3, 3.0), (5, 5.0), (9, 9.0)]:
            store.write("u1", at(day), {"clicks_7d": value})

        examples = store.point_in_time_join([("u1", at(6), 1.0)])

        assert examples[0].features["clicks_7d"] == 5.0

    def test_an_embargo_pushes_the_cutoff_further_back(self, store: FeatureStore) -> None:
        store.write("u1", at(1), {"clicks_7d": 1.0})
        store.write("u1", at(5, hour=11), {"clicks_7d": 99.0})

        without = store.point_in_time_join([("u1", at(5, hour=12), 1.0)])
        with_embargo = store.point_in_time_join(
            [("u1", at(5, hour=12), 1.0)], embargo=timedelta(hours=2)
        )

        assert without[0].features["clicks_7d"] == 99.0
        assert with_embargo[0].features["clicks_7d"] == 1.0

    def test_values_older_than_the_ttl_are_not_served(self) -> None:
        store = FeatureStore(
            FeatureView(
                name="short",
                entity="user",
                features=(FeatureSpec("clicks_7d", FeatureKind.NUMERIC),),
                ttl=timedelta(days=2),
            )
        )
        store.write("u1", at(1), {"clicks_7d": 7.0})

        examples = store.point_in_time_join([("u1", at(20), 1.0)])

        assert examples[0].features["clicks_7d"] == 0.0

    def test_entities_never_borrow_each_other_features(self, store: FeatureStore) -> None:
        store.write("u1", at(1), {"clicks_7d": 11.0})
        store.write("u2", at(1), {"clicks_7d": 22.0})

        examples = store.point_in_time_join([("u1", at(5), 1.0), ("u2", at(5), 1.0)])

        assert examples[0].features["clicks_7d"] == 11.0
        assert examples[1].features["clicks_7d"] == 22.0

    def test_out_of_order_writes_are_still_ordered_correctly(self, store: FeatureStore) -> None:
        store.write("u1", at(9), {"clicks_7d": 9.0})
        store.write("u1", at(1), {"clicks_7d": 1.0})
        store.write("u1", at(5), {"clicks_7d": 5.0})

        examples = store.point_in_time_join([("u1", at(6), 1.0)])

        assert examples[0].features["clicks_7d"] == 5.0


class TestLeakageDetection:
    def test_a_correct_join_passes_the_audit(self, store: FeatureStore) -> None:
        store.write("u1", at(1), {"clicks_7d": 5.0})
        store.write("u1", at(10), {"clicks_7d": 500.0})

        examples = store.point_in_time_join([("u1", at(5), 1.0)])
        store.assert_no_leakage(examples)

    def test_a_hand_built_future_feature_is_caught(self, store: FeatureStore) -> None:
        from sifa.features.store import TrainingExample

        store.write("u1", at(1), {"clicks_7d": 5.0})
        store.write("u1", at(10), {"clicks_7d": 500.0})

        cheated = [
            TrainingExample(
                entity_id="u1",
                label_time=at(5),
                label=1.0,
                features={"clicks_7d": 500.0, "dwell_mean": 0.0},
            )
        ]

        with pytest.raises(LeakageError, match="after the label"):
            store.assert_no_leakage(cheated)


class TestSchema:
    def test_an_undeclared_feature_is_refused_on_write(self, store: FeatureStore) -> None:
        with pytest.raises(SchemaError, match="does not declare"):
            store.write("u1", at(1), {"not_a_feature": 1.0})

    def test_a_view_with_duplicate_features_is_refused(self) -> None:
        with pytest.raises(SchemaError, match="duplicate"):
            FeatureView(
                name="bad",
                entity="user",
                features=(
                    FeatureSpec("x", FeatureKind.NUMERIC),
                    FeatureSpec("x", FeatureKind.NUMERIC),
                ),
            )

    def test_a_naive_timestamp_is_refused(self, store: FeatureStore) -> None:
        with pytest.raises(ValueError, match="timezone"):
            store.write("u1", datetime(2026, 3, 1, 12), {"clicks_7d": 1.0})
