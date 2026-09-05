from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Any

PROTECTED_ATTRIBUTES: frozenset[str] = frozenset(
    {
        "age",
        "date_of_birth",
        "gender",
        "sex",
        "ethnicity",
        "race",
        "religion",
        "marital_status",
        "disability",
        "pregnancy",
        "sexual_orientation",
        "national_origin",
        "nationality",
        "veteran_status",
    }
)

IDENTIFYING_ATTRIBUTES: frozenset[str] = frozenset(
    {
        "name",
        "full_name",
        "first_name",
        "last_name",
        "email",
        "phone",
        "address",
        "postcode",
        "national_id",
        "passport_number",
        "photo_url",
        "linkedin_url",
        "social_handle",
    }
)

PEDIGREE_ATTRIBUTES: frozenset[str] = frozenset(
    {"school", "university", "institution", "alma_mater", "employer_prestige"}
)

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE = re.compile(r"(?:\+?\d[\d\s().-]{7,}\d)")


class RedactionMode(StrEnum):
    DROP = "DROP"
    PSEUDONYMISE = "PSEUDONYMISE"
    GENERALISE = "GENERALISE"


@dataclass(frozen=True, slots=True)
class AnonymizationReport:
    dropped: tuple[str, ...]
    pseudonymised: tuple[str, ...]
    generalised: tuple[str, ...]
    scrubbed_free_text: tuple[str, ...]

    @property
    def touched(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                set(self.dropped)
                | set(self.pseudonymised)
                | set(self.generalised)
                | set(self.scrubbed_free_text)
            )
        )

    @property
    def is_clean(self) -> bool:
        return not self.touched


class LeakageError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class AnonymizedRecord:
    subject_key: str
    attributes: Mapping[str, Any]
    report: AnonymizationReport

    def __getitem__(self, key: str) -> Any:
        return self.attributes[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self.attributes.get(key, default)


class AnonymizationEngine:
    def __init__(
        self,
        salt: str,
        identifying: frozenset[str] = IDENTIFYING_ATTRIBUTES,
        protected: frozenset[str] = PROTECTED_ATTRIBUTES,
        pedigree: frozenset[str] = PEDIGREE_ATTRIBUTES,
        free_text_fields: Sequence[str] = ("summary", "cover_letter", "notes"),
        reference_year: int | None = None,
    ) -> None:
        if not salt or len(salt) < 16:
            raise ValueError("anonymisation salt must be at least 16 characters")
        self._salt = salt
        self._identifying = identifying
        self._protected = protected
        self._pedigree = pedigree
        self._free_text = tuple(free_text_fields)
        self._reference_year = reference_year

    def anonymize(self, record: Mapping[str, Any]) -> AnonymizedRecord:
        subject_key = self._pseudonym(record)

        attributes: dict[str, Any] = {}
        dropped: list[str] = []
        pseudonymised: list[str] = []
        generalised: list[str] = []
        scrubbed: list[str] = []

        for key, value in record.items():
            lowered = key.lower()

            if lowered in self._protected:
                dropped.append(key)
                continue

            if lowered in self._identifying:
                pseudonymised.append(key)
                continue

            if lowered in self._pedigree:
                attributes[key] = self._generalise_pedigree(value)
                generalised.append(key)
                continue

            if self._is_temporal(lowered, value):
                attributes[self._duration_key(key)] = self._flatten(value)
                generalised.append(key)
                continue

            if key in self._free_text and isinstance(value, str):
                cleaned = self._scrub(value)
                if cleaned != value:
                    scrubbed.append(key)
                attributes[key] = cleaned
                continue

            attributes[key] = value

        attributes["subject_key"] = subject_key

        return AnonymizedRecord(
            subject_key=subject_key,
            attributes=attributes,
            report=AnonymizationReport(
                dropped=tuple(dropped),
                pseudonymised=tuple(pseudonymised),
                generalised=tuple(generalised),
                scrubbed_free_text=tuple(scrubbed),
            ),
        )

    def assert_clean(self, payload: Mapping[str, Any]) -> None:
        leaks = sorted(
            key
            for key in payload
            if key.lower() in self._protected or key.lower() in self._identifying
        )
        if leaks:
            raise LeakageError(
                "payload still carries attributes that must never reach a model: "
                + ", ".join(leaks)
            )

    def _pseudonym(self, record: Mapping[str, Any]) -> str:
        for candidate in ("national_id", "email", "subject_id", "id"):
            value = record.get(candidate)
            if value:
                digest = hashlib.sha256(f"{self._salt}:{candidate}:{value}".encode())
                return f"subj_{digest.hexdigest()[:16]}"
        raise ValueError("record has no stable identifier to pseudonymise")

    def _generalise_pedigree(self, value: Any) -> str:
        if not isinstance(value, str) or not value.strip():
            return "UNSPECIFIED"
        digest = hashlib.sha256(f"{self._salt}:pedigree:{value.strip().lower()}".encode())
        return f"inst_{digest.hexdigest()[:8]}"

    def _is_temporal(self, key: str, value: Any) -> bool:
        if isinstance(value, date):
            return True
        return key.endswith(("_date", "_on", "_since")) and isinstance(value, str)

    def _duration_key(self, key: str) -> str:
        base = key
        for suffix in ("_date", "_on", "_since"):
            if base.lower().endswith(suffix):
                base = base[: -len(suffix)]
                break
        return f"{base}_years_ago"

    def _flatten(self, value: Any) -> float | None:
        parsed = value if isinstance(value, date) else self._parse(value)
        if parsed is None:
            return None
        reference = self._reference_year or date.today().year
        return round(reference - (parsed.year + (parsed.month - 1) / 12), 2)

    @staticmethod
    def _parse(value: Any) -> date | None:
        if not isinstance(value, str):
            return None
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None

    @staticmethod
    def _scrub(text: str) -> str:
        cleaned = _EMAIL.sub("[email]", text)
        return _PHONE.sub("[phone]", cleaned)
