from __future__ import annotations

import re
import statistics
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

MINIMUM_GROUP_SIZE = 5

TOKEN = re.compile(r"[a-z']+")

POSITIVE = frozenset(
    {
        "good",
        "great",
        "excellent",
        "supportive",
        "clear",
        "fair",
        "flexible",
        "respected",
        "valued",
        "growing",
        "learning",
        "collaborative",
        "trust",
        "recognised",
        "recognized",
        "happy",
        "proud",
        "improving",
        "helpful",
    }
)

NEGATIVE = frozenset(
    {
        "burnout",
        "burnt",
        "exhausted",
        "overworked",
        "unclear",
        "unfair",
        "ignored",
        "stuck",
        "underpaid",
        "toxic",
        "micromanaged",
        "stressed",
        "frustrated",
        "leaving",
        "demoralised",
        "demoralized",
        "chaotic",
        "blocked",
    }
)

NEGATORS = frozenset({"not", "never", "no", "hardly", "barely", "without"})


class Aspect(StrEnum):
    LEADERSHIP = "LEADERSHIP"
    CULTURE = "CULTURE"
    COMPENSATION = "COMPENSATION"
    WORK_LIFE_BALANCE = "WORK_LIFE_BALANCE"
    TOOLING = "TOOLING"
    CAREER_GROWTH = "CAREER_GROWTH"
    UNCATEGORISED = "UNCATEGORISED"


ASPECT_TERMS: dict[Aspect, frozenset[str]] = {
    Aspect.LEADERSHIP: frozenset({"manager", "lead", "leadership", "director", "management"}),
    Aspect.CULTURE: frozenset({"culture", "team", "colleagues", "values", "inclusion"}),
    Aspect.COMPENSATION: frozenset({"pay", "salary", "compensation", "bonus", "raise", "equity"}),
    Aspect.WORK_LIFE_BALANCE: frozenset(
        {"hours", "workload", "overtime", "balance", "weekend", "leave", "holiday"}
    ),
    Aspect.TOOLING: frozenset({"tools", "tooling", "laptop", "software", "systems", "ci"}),
    Aspect.CAREER_GROWTH: frozenset(
        {"promotion", "career", "growth", "training", "development", "progression"}
    ),
}


class SuppressedError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Response:
    group: str
    text: str


@dataclass(frozen=True, slots=True)
class AspectScore:
    aspect: Aspect
    score: float
    mentions: int

    @property
    def negative(self) -> bool:
        return self.score < -0.15


@dataclass(frozen=True, slots=True)
class GroupSentiment:
    group: str
    respondents: int
    overall: float
    aspects: tuple[AspectScore, ...]

    def aspect(self, aspect: Aspect) -> AspectScore | None:
        for score in self.aspects:
            if score.aspect is aspect:
                return score
        return None

    @property
    def concerns(self) -> tuple[AspectScore, ...]:
        return tuple(sorted((s for s in self.aspects if s.negative), key=lambda s: s.score))


@dataclass(frozen=True, slots=True)
class SentimentReport:
    groups: tuple[GroupSentiment, ...]
    suppressed_groups: tuple[str, ...]
    minimum_group_size: int

    def group(self, name: str) -> GroupSentiment | None:
        for entry in self.groups:
            if entry.group == name:
                return entry
        return None

    @property
    def was_suppressed(self) -> bool:
        return bool(self.suppressed_groups)

    def summary(self) -> str:
        parts: list[str] = []
        if self.groups:
            worst = min(self.groups, key=lambda group: group.overall)
            parts.append(f"lowest group sentiment is {worst.group} at {worst.overall:+.2f}")
        if self.suppressed_groups:
            parts.append(
                f"{len(self.suppressed_groups)} group(s) withheld for having fewer than "
                f"{self.minimum_group_size} respondents"
            )
        return "; ".join(parts) if parts else "no reportable sentiment"


def score_text(text: str) -> tuple[float, dict[Aspect, list[float]]]:
    tokens = TOKEN.findall(text.lower())
    per_aspect: dict[Aspect, list[float]] = defaultdict(list)
    polarities: list[float] = []

    for index, token in enumerate(tokens):
        polarity = 0.0
        if token in POSITIVE:
            polarity = 1.0
        elif token in NEGATIVE:
            polarity = -1.0

        if polarity == 0.0:
            continue

        window = tokens[max(0, index - 3) : index]
        if any(word in NEGATORS for word in window):
            polarity = -polarity

        polarities.append(polarity)

        aspect = _aspect_for(tokens, index)
        per_aspect[aspect].append(polarity)

    overall = statistics.fmean(polarities) if polarities else 0.0
    return overall, per_aspect


def _aspect_for(tokens: Sequence[str], index: int) -> Aspect:
    window = tokens[max(0, index - 6) : index + 7]
    for aspect, terms in ASPECT_TERMS.items():
        if any(token in terms for token in window):
            return aspect
    return Aspect.UNCATEGORISED


def analyse(
    responses: Sequence[Response], minimum_group_size: int = MINIMUM_GROUP_SIZE
) -> SentimentReport:
    if minimum_group_size < 2:
        raise SuppressedError(
            "a minimum group size below two cannot protect an individual's response"
        )

    grouped: dict[str, list[str]] = defaultdict(list)
    for response in responses:
        grouped[response.group].append(response.text)

    reported: list[GroupSentiment] = []
    suppressed: list[str] = []

    for group, texts in sorted(grouped.items()):
        if len(texts) < minimum_group_size:
            suppressed.append(group)
            continue

        overalls: list[float] = []
        aspect_polarities: dict[Aspect, list[float]] = defaultdict(list)

        for text in texts:
            overall, per_aspect = score_text(text)
            overalls.append(overall)
            for aspect, values in per_aspect.items():
                aspect_polarities[aspect].extend(values)

        reported.append(
            GroupSentiment(
                group=group,
                respondents=len(texts),
                overall=round(statistics.fmean(overalls), 4),
                aspects=tuple(
                    AspectScore(
                        aspect=aspect,
                        score=round(statistics.fmean(values), 4),
                        mentions=len(values),
                    )
                    for aspect, values in sorted(aspect_polarities.items())
                ),
            )
        )

    return SentimentReport(
        groups=tuple(reported),
        suppressed_groups=tuple(suppressed),
        minimum_group_size=minimum_group_size,
    )


@dataclass(frozen=True, slots=True)
class EarlyWarning:
    group: str
    aspect: Aspect
    previous: float
    current: float

    @property
    def drop(self) -> float:
        return self.previous - self.current


def detect_early_warnings(
    previous: SentimentReport, current: SentimentReport, drop_threshold: float = 0.35
) -> tuple[EarlyWarning, ...]:
    if drop_threshold <= 0:
        raise SuppressedError("a drop threshold must be positive")

    warnings: list[EarlyWarning] = []

    for group in current.groups:
        before = previous.group(group.group)
        if before is None:
            continue

        for aspect_score in group.aspects:
            prior = before.aspect(aspect_score.aspect)
            if prior is None:
                continue
            if prior.score - aspect_score.score >= drop_threshold:
                warnings.append(
                    EarlyWarning(
                        group=group.group,
                        aspect=aspect_score.aspect,
                        previous=prior.score,
                        current=aspect_score.score,
                    )
                )

    warnings.sort(key=lambda warning: warning.drop, reverse=True)
    return tuple(warnings)
