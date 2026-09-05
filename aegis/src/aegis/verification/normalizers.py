from __future__ import annotations

import json
import re
from collections.abc import Callable

Normalizer = Callable[[str], str]

_WHITESPACE = re.compile(r"\s+")


def identity(value: str) -> str:
    return value


def collapse_whitespace(value: str) -> str:
    return _WHITESPACE.sub(" ", value).strip()


def casefold_text(value: str) -> str:
    return collapse_whitespace(value).casefold()


def canonical_json(value: str) -> str:
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return collapse_whitespace(value)
    return json.dumps(parsed, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def chain(*normalizers: Normalizer) -> Normalizer:
    def apply(value: str) -> str:
        for normalizer in normalizers:
            value = normalizer(value)
        return value

    return apply


BUILTIN: dict[str, Normalizer] = {
    "identity": identity,
    "collapse_whitespace": collapse_whitespace,
    "casefold": casefold_text,
    "canonical_json": canonical_json,
}
