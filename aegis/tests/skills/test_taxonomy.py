from __future__ import annotations

import pytest

from aegis.skills import (
    OpenRole,
    Proficiency,
    Skill,
    SkillRequirement,
    SkillTaxonomy,
    TaxonomyError,
    forecast_gaps,
    internal_candidates,
    match_role,
    rank_roles,
)


def taxonomy() -> SkillTaxonomy:
    return SkillTaxonomy(
        [
            Skill("python", frozenset({"py", "python3"}), family="engineering"),
            Skill("kubernetes", frozenset({"k8s"}), family="platform"),
            Skill("postgresql", frozenset({"postgres", "psql"}), family="data"),
            Skill("leadership", family="management"),
        ]
    )


def profile(subject: str = "subj_1", years: dict[str, float] | None = None):
    return taxonomy().extract(
        subject,
        [
            "Built services in Python with Postgres behind them.",
            "Deployed to k8s and tuned python performance.",
            "Ran python migrations against postgres nightly.",
        ],
        years or {"python": 6.0, "postgresql": 4.0, "kubernetes": 2.0},
    )


class TestTaxonomy:
    def test_aliases_resolve_to_the_canonical_skill(self) -> None:
        assert taxonomy().canonical("k8s") == "kubernetes"
        assert taxonomy().canonical("PY") == "python"

    def test_an_unknown_token_resolves_to_nothing(self) -> None:
        assert taxonomy().canonical("cobol") is None

    def test_duplicate_skill_names_are_rejected(self) -> None:
        with pytest.raises(TaxonomyError, match="unique"):
            SkillTaxonomy([Skill("python"), Skill("python")])

    def test_an_alias_claimed_twice_is_rejected(self) -> None:
        with pytest.raises(TaxonomyError, match="more than one skill"):
            SkillTaxonomy([Skill("python", frozenset({"py"})), Skill("pypy", frozenset({"py"}))])

    def test_an_empty_taxonomy_is_rejected(self) -> None:
        with pytest.raises(TaxonomyError, match="at least one skill"):
            SkillTaxonomy([])


class TestExtraction:
    def test_skills_are_extracted_from_free_text_evidence(self) -> None:
        assert "python" in profile().known()
        assert "postgresql" in profile().known()

    def test_aliases_in_prose_count_toward_the_canonical_skill(self) -> None:
        assert profile().level("kubernetes") > Proficiency.NONE

    def test_proficiency_rises_with_evidence_and_tenure(self) -> None:
        assert profile().level("python") is Proficiency.EXPERT

    def test_a_skill_with_no_evidence_is_none(self) -> None:
        assert profile().level("leadership") is Proficiency.NONE

    def test_a_single_mention_without_tenure_is_only_awareness(self) -> None:
        sparse = taxonomy().extract("subj_2", ["Some exposure to kubernetes."], {})

        assert sparse.level("kubernetes") is Proficiency.AWARENESS


class TestGapForecasting:
    def test_a_covered_requirement_reports_no_shortfall(self) -> None:
        forecast = forecast_gaps(
            [profile("a"), profile("b")],
            [SkillRequirement("python", Proficiency.PRACTITIONER, headcount=2)],
        )

        assert forecast.uncovered == ()
        assert "all requirements covered" in forecast.summary()

    def test_an_uncovered_requirement_reports_the_shortfall(self) -> None:
        forecast = forecast_gaps(
            [profile("a")],
            [SkillRequirement("leadership", Proficiency.PRACTITIONER, headcount=3)],
        )

        assert forecast.total_shortfall == 3
        assert "leadership" in forecast.summary()

    def test_attrition_erodes_supply_and_can_open_a_gap(self) -> None:
        requirement = [SkillRequirement("python", Proficiency.PRACTITIONER, headcount=2)]
        profiles = [profile("a"), profile("b")]

        assert forecast_gaps(profiles, requirement, attrition_rate=0.0).uncovered == ()
        assert forecast_gaps(profiles, requirement, attrition_rate=0.5).uncovered

    def test_a_negative_horizon_is_rejected(self) -> None:
        with pytest.raises(TaxonomyError, match="positive"):
            forecast_gaps([profile()], [], horizon_months=0)

    def test_an_impossible_attrition_rate_is_rejected(self) -> None:
        with pytest.raises(TaxonomyError, match="within"):
            forecast_gaps([profile()], [], attrition_rate=1.5)


class TestMobility:
    def test_a_fully_qualified_employee_is_ready_now(self) -> None:
        role = OpenRole(
            "role-1",
            "Backend Engineer",
            (SkillRequirement("python", Proficiency.PRACTITIONER),),
        )

        match = match_role(profile(), role)

        assert match.ready_now
        assert match.score == 1.0
        assert match.development_path() == ()

    def test_a_near_miss_is_a_stretch_role_with_a_development_path(self) -> None:
        role = OpenRole(
            "role-2",
            "Engineering Lead",
            (
                SkillRequirement("python", Proficiency.PRACTITIONER),
                SkillRequirement("leadership", Proficiency.AWARENESS),
            ),
        )

        match = match_role(profile(), role)

        assert not match.ready_now
        assert match.stretch
        assert any("leadership" in step for step in match.development_path())

    def test_roles_are_ranked_with_ready_ones_first(self) -> None:
        ready = OpenRole("ready", "Backend", (SkillRequirement("python", Proficiency.WORKING),))
        stretch = OpenRole(
            "stretch",
            "Lead",
            (
                SkillRequirement("python", Proficiency.WORKING),
                SkillRequirement("leadership", Proficiency.WORKING),
            ),
        )

        ranked = rank_roles(profile(), [stretch, ready], minimum_score=0.4)

        assert ranked[0].role_id == "ready"

    def test_a_role_far_from_the_profile_is_filtered_out(self) -> None:
        unrelated = OpenRole(
            "far", "Head of People", (SkillRequirement("leadership", Proficiency.EXPERT),)
        )

        assert rank_roles(profile(), [unrelated], minimum_score=0.5) == ()

    def test_internal_candidates_are_ranked_for_a_role(self) -> None:
        role = OpenRole(
            "role-3", "Platform Engineer", (SkillRequirement("kubernetes", Proficiency.WORKING),)
        )
        strong = profile("strong", {"kubernetes": 5.0})
        weak = taxonomy().extract("weak", ["only python here"], {"python": 5.0})

        candidates = internal_candidates([weak, strong], role, minimum_score=0.1)

        assert candidates[0].subject_key == "strong"

    def test_family_adjacency_gives_a_small_boost(self) -> None:
        role = OpenRole(
            "role-4",
            "Platform Engineer",
            (SkillRequirement("kubernetes", Proficiency.PRACTITIONER),),
            family="platform",
        )

        without = match_role(profile(), role)
        with_taxonomy = match_role(profile(), role, taxonomy())

        assert with_taxonomy.score > without.score

    def test_a_role_with_no_requirements_is_rejected(self) -> None:
        with pytest.raises(TaxonomyError, match="no skill requirements"):
            OpenRole("empty", "Nothing", ())
