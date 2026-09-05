from __future__ import annotations

import hashlib
import json
import re

from aegis.reasoning.provider import Completion, Prompt

_NUMBER = re.compile(r"([a-z_]+)\s*[:=]\s*(-?\d+(?:\.\d+)?)")


class DeterministicModel:
    def __init__(self, name: str = "aegis-deterministic-v1") -> None:
        self._name = name
        self.calls: list[Prompt] = []

    @property
    def name(self) -> str:
        return self._name

    def complete(self, prompt: Prompt) -> Completion:
        self.calls.append(prompt)

        seed = int(hashlib.sha256(prompt.user.encode("utf-8")).hexdigest()[:8], 16)
        signals = {key: float(value) for key, value in _NUMBER.findall(prompt.user.lower())}

        score = self._score(signals, seed)
        payload = {
            "score": round(score, 4),
            "recommendation": self._recommendation(score),
            "rationale": self._rationale(signals, score),
            "signals_considered": sorted(signals),
        }

        return Completion(
            text=json.dumps(payload),
            model=self._name,
            prompt_fingerprint=prompt.fingerprint(),
            usage={"prompt_chars": len(prompt.user), "completion_chars": len(json.dumps(payload))},
        )

    def _score(self, signals: dict[str, float], seed: int) -> float:
        if not signals:
            return round((seed % 1000) / 1000.0, 4)

        weighted = 0.0
        total = 0.0
        for key, value in signals.items():
            weight = 2.0 if "match" in key or "skill" in key else 1.0
            weighted += min(max(value, 0.0), 1.0) * weight if value <= 1.0 else weight
            total += weight

        return weighted / total if total else 0.0

    def _recommendation(self, score: float) -> str:
        if score >= 0.75:
            return "ADVANCE"
        if score >= 0.45:
            return "REVIEW"
        return "HOLD"

    def _rationale(self, signals: dict[str, float], score: float) -> str:
        if not signals:
            return "no numeric signals were present in the brief, so the score is not evidential"
        strongest = max(signals.items(), key=lambda item: item[1])
        return (
            f"weighted {len(signals)} signals to {score:.2f}; "
            f"{strongest[0]} at {strongest[1]:.2f} carried the most weight"
        )


class ScriptedModel:
    def __init__(self, responses: list[str], name: str = "scripted") -> None:
        if not responses:
            raise ValueError("a scripted model needs at least one response")
        self._responses = responses
        self._name = name
        self._index = 0

    @property
    def name(self) -> str:
        return self._name

    def complete(self, prompt: Prompt) -> Completion:
        text = self._responses[self._index % len(self._responses)]
        self._index += 1
        return Completion(text=text, model=self._name, prompt_fingerprint=prompt.fingerprint())
