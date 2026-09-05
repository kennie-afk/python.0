from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from aegis.skills.taxonomy import (
    Proficiency,
    SkillProfile,
    SkillRequirement,
    SkillTaxonomy,
    TaxonomyError,
)


@dataclass(frozen=True, slots=True)
class OpenRole:
    role_id: str
    title: str
    requirements: tuple[SkillRequirement, ...]
    family: str = "general"

    def __post_init__(self) -> None:
        if not self.requirements:
            raise TaxonomyError(f"role {self.role_id} has no skill requirements to match against")


@dataclass(frozen=True, slots=True)
class SkillDelta:
    skill: str
    held: Proficiency
    required: Proficiency

    @property
    def satisfied(self) -> bool:
        return self.held >= self.required

    @property
    def levels_short(self) -> int:
        return max(0, int(self.required) - int(self.held))


@dataclass(frozen=True, slots=True)
class MobilityMatch:
    subject_key: str
    role_id: str
    title: str
    score: float
    satisfied: tuple[SkillDelta, ...]
    missing: tuple[SkillDelta, ...]

    @property
    def ready_now(self) -> bool:
        return not self.missing

    @property
    def stretch(self) -> bool:
        return bool(self.missing) and all(delta.levels_short <= 1 for delta in self.missing)

    def development_path(self) -> tuple[str, ...]:
        ordered = sorted(self.missing, key=lambda delta: delta.levels_short)
        return tuple(
            f"{delta.skill}: {delta.held.name} to {delta.required.name}" for delta in ordered
        )


def match_role(
    profile: SkillProfile, role: OpenRole, taxonomy: SkillTaxonomy | None = None
) -> MobilityMatch:
    satisfied: list[SkillDelta] = []
    missing: list[SkillDelta] = []
    earned = 0.0
    available = 0.0

    for requirement in role.requirements:
        held = profile.level(requirement.skill)
        delta = SkillDelta(skill=requirement.skill, held=held, required=requirement.required)
        weight = float(requirement.required)
        available += weight
        earned += min(float(held), weight)

        if delta.satisfied:
            satisfied.append(delta)
        else:
            missing.append(delta)

    adjacency = 0.0
    if taxonomy is not None:
        families = {taxonomy.family_of(skill) for skill in profile.known()}
        if role.family in families:
            adjacency = 0.05

    score = min(1.0, (earned / available if available else 0.0) + adjacency)

    return MobilityMatch(
        subject_key=profile.subject_key,
        role_id=role.role_id,
        title=role.title,
        score=round(score, 4),
        satisfied=tuple(satisfied),
        missing=tuple(missing),
    )


def rank_roles(
    profile: SkillProfile,
    roles: Sequence[OpenRole],
    taxonomy: SkillTaxonomy | None = None,
    minimum_score: float = 0.5,
) -> tuple[MobilityMatch, ...]:
    matches = [match_role(profile, role, taxonomy) for role in roles]
    qualifying = [match for match in matches if match.score >= minimum_score]
    qualifying.sort(key=lambda match: (match.ready_now, match.score), reverse=True)
    return tuple(qualifying)


def internal_candidates(
    profiles: Sequence[SkillProfile],
    role: OpenRole,
    taxonomy: SkillTaxonomy | None = None,
    minimum_score: float = 0.6,
) -> tuple[MobilityMatch, ...]:
    matches = [match_role(profile, role, taxonomy) for profile in profiles]
    qualifying = [match for match in matches if match.score >= minimum_score]
    qualifying.sort(key=lambda match: match.score, reverse=True)
    return tuple(qualifying)
