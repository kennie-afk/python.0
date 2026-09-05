from __future__ import annotations

from datetime import date

import pytest

from aegis.anonymization import AnonymizationEngine, LeakageError

SALT = "a-sufficiently-long-tenant-salt"


def engine(reference_year: int = 2026) -> AnonymizationEngine:
    return AnonymizationEngine(salt=SALT, reference_year=reference_year)


def candidate() -> dict[str, object]:
    return {
        "national_id": "12345678",
        "full_name": "Amina Wanjiru",
        "email": "amina@example.com",
        "phone": "+254712345678",
        "gender": "female",
        "date_of_birth": date(1996, 4, 12),
        "ethnicity": "Kikuyu",
        "university": "University of Nairobi",
        "graduation_date": date(2018, 7, 1),
        "years_experience": 7,
        "skills": ["python", "kubernetes"],
        "summary": "Reach me at amina@example.com or +254 712 345 678.",
    }


class TestIdentityObfuscation:
    def test_identifying_attributes_never_reach_the_output(self) -> None:
        result = engine().anonymize(candidate())

        for field in ("full_name", "email", "phone", "national_id"):
            assert field not in result.attributes

    def test_protected_attributes_are_dropped_entirely(self) -> None:
        result = engine().anonymize(candidate())

        for field in ("gender", "ethnicity", "date_of_birth"):
            assert field not in result.attributes
        assert set(result.report.dropped) >= {"gender", "ethnicity", "date_of_birth"}

    def test_job_relevant_attributes_survive_untouched(self) -> None:
        result = engine().anonymize(candidate())

        assert result["years_experience"] == 7
        assert result["skills"] == ["python", "kubernetes"]

    def test_the_same_person_always_gets_the_same_pseudonym(self) -> None:
        first = engine().anonymize(candidate())
        second = engine().anonymize(candidate())

        assert first.subject_key == second.subject_key
        assert first.subject_key.startswith("subj_")

    def test_a_different_salt_produces_a_different_pseudonym(self) -> None:
        other = AnonymizationEngine(salt="a-completely-different-salt-value")

        assert (
            engine().anonymize(candidate()).subject_key != other.anonymize(candidate()).subject_key
        )

    def test_a_record_with_no_identifier_cannot_be_pseudonymised(self) -> None:
        with pytest.raises(ValueError, match="no stable identifier"):
            engine().anonymize({"skills": ["python"]})

    def test_a_short_salt_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="at least 16"):
            AnonymizationEngine(salt="short")


class TestPedigreeNeutralisation:
    def test_the_institution_name_is_replaced_with_an_opaque_label(self) -> None:
        result = engine().anonymize(candidate())

        assert result["university"] != "University of Nairobi"
        assert result["university"].startswith("inst_")

    def test_the_same_institution_maps_consistently_so_it_stays_a_usable_feature(self) -> None:
        first = engine().anonymize(candidate())
        second = engine().anonymize({**candidate(), "national_id": "87654321"})

        assert first["university"] == second["university"]

    def test_different_institutions_map_differently(self) -> None:
        harvard = engine().anonymize({**candidate(), "university": "Harvard"})

        assert harvard["university"] != engine().anonymize(candidate())["university"]

    def test_a_missing_institution_becomes_unspecified(self) -> None:
        result = engine().anonymize({**candidate(), "university": ""})

        assert result["university"] == "UNSPECIFIED"


class TestTemporalFlattening:
    def test_a_graduation_date_becomes_a_duration(self) -> None:
        result = engine().anonymize(candidate())

        assert "graduation_date" not in result.attributes
        assert result["graduation_years_ago"] == pytest.approx(7.5, abs=0.1)

    def test_flattening_removes_the_age_signal_a_date_carries(self) -> None:
        older = engine().anonymize({**candidate(), "graduation_date": date(1998, 7, 1)})
        younger = engine().anonymize({**candidate(), "graduation_date": date(2018, 7, 1)})

        assert older["graduation_years_ago"] > younger["graduation_years_ago"]
        assert "date_of_birth" not in older.attributes

    def test_an_iso_string_date_is_flattened_too(self) -> None:
        result = engine().anonymize({**candidate(), "started_on": "2020-01-01"})

        assert result["started_years_ago"] == pytest.approx(6.0, abs=0.1)

    def test_an_unparseable_date_flattens_to_none_rather_than_leaking(self) -> None:
        result = engine().anonymize({**candidate(), "started_on": "not a date"})

        assert result["started_years_ago"] is None


class TestFreeTextScrubbing:
    def test_contact_details_in_prose_are_scrubbed(self) -> None:
        result = engine().anonymize(candidate())

        assert "amina@example.com" not in result["summary"]
        assert "[email]" in result["summary"]
        assert "[phone]" in result["summary"]

    def test_scrubbing_is_reported_so_it_is_auditable(self) -> None:
        assert "summary" in engine().anonymize(candidate()).report.scrubbed_free_text

    def test_clean_prose_is_left_alone(self) -> None:
        result = engine().anonymize({**candidate(), "summary": "Backend engineer."})

        assert result["summary"] == "Backend engineer."
        assert "summary" not in result.report.scrubbed_free_text


class TestLeakageGuard:
    def test_a_payload_still_carrying_protected_data_is_refused(self) -> None:
        with pytest.raises(LeakageError, match="gender"):
            engine().assert_clean({"gender": "female", "skills": ["python"]})

    def test_an_anonymised_payload_passes_the_guard(self) -> None:
        result = engine().anonymize(candidate())

        engine().assert_clean(result.attributes)

    def test_the_guard_names_every_leaking_field(self) -> None:
        with pytest.raises(LeakageError) as raised:
            engine().assert_clean({"gender": "female", "email": "a@b.com"})

        assert "gender" in str(raised.value)
        assert "email" in str(raised.value)

    def test_the_report_records_everything_it_touched(self) -> None:
        report = engine().anonymize(candidate()).report

        assert not report.is_clean
        assert "gender" in report.touched
        assert "university" in report.touched
