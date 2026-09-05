from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import IntEnum

TOKEN = re.compile(r"[a-z0-9][a-z0-9+#.\-]*")
TRAILING = ".-+#"


class Proficiency(IntEnum):
    NONE = 0
    AWARENESS = 1
    WORKING = 2
    PRACTITIONER = 3
    EXPERT = 4

    @classmethod
    def from_evidence(cls, mentions: int, years: float) -> Proficiency:
        if mentions == 0:
            return cls.NONE
        if years >= 5 and mentions >= 2:
            return cls.EXPERT
        if years >= 3:
            return cls.PRACTITIONER
        if years >= 1:
            return cls.WORKING
        return cls.AWARENESS


class TaxonomyError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Skill:
    name: str
    aliases: frozenset[str] = field(default_factory=frozenset)
    family: str = "general"

    def matches(self, token: str) -> bool:
        lowered = token.lower()
        return lowered == self.name.lower() or lowered in {a.lower() for a in self.aliases}


@dataclass(frozen=True, slots=True)
class SkillHolding:
    skill: str
    proficiency: Proficiency
    mentions: int
    years: float


@dataclass(frozen=True, slots=True)
class SkillProfile:
    subject_key: str
    holdings: tuple[SkillHolding, ...]

    def level(self, skill: str) -> Proficiency:
        for holding in self.holdings:
            if holding.skill == skill:
                return holding.proficiency
        return Proficiency.NONE

    def known(self) -> frozenset[str]:
        return frozenset(
            holding.skill for holding in self.holdings if holding.proficiency > Proficiency.NONE
        )


class SkillTaxonomy:
    def __init__(self, skills: Sequence[Skill]) -> None:
        if not skills:
            raise TaxonomyError("a taxonomy needs at least one skill")

        names = [skill.name for skill in skills]
        if len(names) != len(set(names)):
            raise TaxonomyError("skill names must be unique")

        self._skills = tuple(skills)
        self._lookup: dict[str, str] = {}
        for skill in skills:
            self._lookup[skill.name.lower()] = skill.name
            for alias in skill.aliases:
                if alias.lower() in self._lookup:
                    raise TaxonomyError(f"alias {alias!r} is claimed by more than one skill")
                self._lookup[alias.lower()] = skill.name

    @property
    def skills(self) -> tuple[Skill, ...]:
        return self._skills

    def canonical(self, token: str) -> str | None:
        return self._lookup.get(token.lower())

    def family_of(self, skill_name: str) -> str:
        for skill in self._skills:
            if skill.name == skill_name:
                return skill.family
        raise TaxonomyError(f"unknown skill {skill_name!r}")

    def extract(
        self,
        subject_key: str,
        evidence: Iterable[str],
        years_by_skill: Mapping[str, float] | None = None,
    ) -> SkillProfile:
        counts: Counter[str] = Counter()

        for document in evidence:
            for raw in TOKEN.findall(document.lower()):
                token = raw.rstrip(TRAILING)
                canonical = self.canonical(token)
                if canonical:
                    counts[canonical] += 1

        years = years_by_skill or {}
        holdings = tuple(
            SkillHolding(
                skill=name,
                proficiency=Proficiency.from_evidence(mentions, float(years.get(name, 0.0))),
                mentions=mentions,
                years=float(years.get(name, 0.0)),
            )
            for name, mentions in sorted(counts.items())
        )
        return SkillProfile(subject_key=subject_key, holdings=holdings)


@dataclass(frozen=True, slots=True)
class SkillRequirement:
    skill: str
    required: Proficiency
    headcount: int = 1


@dataclass(frozen=True, slots=True)
class Gap:
    skill: str
    required: Proficiency
    supply: int
    demand: int

    @property
    def shortfall(self) -> int:
        return max(0, self.demand - self.supply)

    @property
    def covered(self) -> bool:
        return self.shortfall == 0


@dataclass(frozen=True, slots=True)
class GapForecast:
    horizon_months: int
    gaps: tuple[Gap, ...]

    @property
    def uncovered(self) -> tuple[Gap, ...]:
        return tuple(gap for gap in self.gaps if not gap.covered)

    @property
    def total_shortfall(self) -> int:
        return sum(gap.shortfall for gap in self.gaps)

    def summary(self) -> str:
        if not self.uncovered:
            return f"all requirements covered over {self.horizon_months} months"
        worst = max(self.uncovered, key=lambda gap: gap.shortfall)
        return (
            f"{self.total_shortfall} unmet requirements over {self.horizon_months} months, "
            f"worst is {worst.skill} short by {worst.shortfall}"
        )


def forecast_gaps(
    profiles: Sequence[SkillProfile],
    requirements: Sequence[SkillRequirement],
    horizon_months: int = 12,
    attrition_rate: float = 0.0,
) -> GapForecast:
    if horizon_months <= 0:
        raise TaxonomyError("a forecast horizon must be positive")
    if not 0.0 <= attrition_rate < 1.0:
        raise TaxonomyError("attrition rate must be within [0,1)")

    gaps: list[Gap] = []
    for requirement in requirements:
        qualified = sum(
            1 for profile in profiles if profile.level(requirement.skill) >= requirement.required
        )
        retained = int(qualified * (1.0 - attrition_rate))
        gaps.append(
            Gap(
                skill=requirement.skill,
                required=requirement.required,
                supply=retained,
                demand=requirement.headcount,
            )
        )

    return GapForecast(horizon_months=horizon_months, gaps=tuple(gaps))
