from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Protocol


class ReasoningError(RuntimeError):
    pass


class UnparseableResponseError(ReasoningError):
    pass


@dataclass(frozen=True, slots=True)
class Prompt:
    system: str
    user: str
    temperature: float = 0.0
    max_tokens: int = 512
    schema_hint: str | None = None

    def __post_init__(self) -> None:
        if not 0.0 <= self.temperature <= 2.0:
            raise ValueError("temperature must be within [0,2]")
        if self.temperature > 0.0:
            raise ValueError(
                "reasoning that drives an employment decision must run at temperature 0; "
                "a stylistic sampler makes the same candidate score differently on a rerun"
            )

    def fingerprint(self) -> str:
        canonical = json.dumps(
            {
                "system": self.system,
                "user": self.user,
                "temperature": self.temperature,
                "schema_hint": self.schema_hint,
            },
            sort_keys=True,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True, slots=True)
class Completion:
    text: str
    model: str
    prompt_fingerprint: str
    usage: dict[str, int] = field(default_factory=dict)

    def as_json(self) -> dict[str, Any]:
        cleaned = self.text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[-1]
            if cleaned.endswith("```"):
                cleaned = cleaned[: cleaned.rfind("```")]

        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError as error:
            raise UnparseableResponseError(
                f"model {self.model} did not return valid JSON: {error}"
            ) from error

        if not isinstance(parsed, dict):
            raise UnparseableResponseError(
                f"model {self.model} returned {type(parsed).__name__}, expected an object"
            )
        return parsed


class LanguageModel(Protocol):
    @property
    def name(self) -> str: ...

    def complete(self, prompt: Prompt) -> Completion: ...
