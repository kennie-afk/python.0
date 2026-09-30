"""Synthetic people for the demo. Everything here is invented and generated from a fixed seed, so
two runs produce the same candidates, the same scores and the same findings."""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass
from typing import Any

SEED = 20260930

REQUIREMENT = "Senior backend engineer with 5+ years of Python and distributed systems experience"

FIRST_NAMES = (
    "Achieng", "Wanjiru", "Amina", "Njeri", "Akinyi", "Zawadi", "Mwende", "Halima", "Atieno",
    "Nyambura", "Faith", "Grace", "Esther", "Naliaka", "Wairimu", "Brian", "Kevin", "Otieno",
    "Mutua", "Kamau", "Omondi", "Juma", "Kiprop", "Barasa", "Mwangi", "Hassan", "Peter", "Ian",
    "Dennis", "Felix", "Collins", "Victor", "Samuel", "Ibrahim", "Joseph", "Daniel",
)
LAST_NAMES = (
    "Kamau", "Otieno", "Wanjala", "Mwangi", "Njoroge", "Achieng", "Kiptoo", "Mutiso", "Abdi",
    "Ochieng", "Kariuki", "Wekesa", "Cheruiyot", "Nyaga", "Mohamed", "Onyango", "Kimani",
    "Waweru", "Langat", "Mbugua", "Ali", "Odhiambo", "Kiprotich", "Maina", "Wafula",
)
UNIVERSITIES = (
    "University of Nairobi", "Strathmore University", "Kenyatta University", "JKUAT",
    "Moi University", "Egerton University", "Maseno University", "USIU-Africa",
    "Technical University of Kenya", "Dedan Kimathi University",
)
NATIONALITIES = ("Kenyan", "Kenyan", "Kenyan", "Kenyan", "Ugandan", "Tanzanian", "Rwandan")


def age_band(age: int) -> str:
    if age < 30:
        return "Under 30"
    if age < 45:
        return "30 to 44"
    return "45 and over"


@dataclass(frozen=True, slots=True)
class Candidate:
    index: int
    record: dict[str, Any]
    gender: str
    age: int
    discloses_disability: bool

    @property
    def age_group(self) -> str:
        return age_band(self.age)

    def without(self, *fields: str) -> dict[str, Any]:
        return {key: value for key, value in self.record.items() if key not in fields}


def _clamp(value: float) -> float:
    return round(min(max(value, 0.0), 1.0), 2)


def build_candidates(count: int = 200, seed: int = SEED) -> list[Candidate]:
    """A plausible applicant pool with one built-in flaw.

    `recent_tooling` (familiarity with the current stack) is a fair-looking signal that happens to
    track age: older applicants have the same skills and assessment results but less exposure to
    the newest tooling. A screener that weighs it reproduces an age disparity without ever seeing
    anyone's age, which is exactly what adverse impact testing exists to catch.
    """
    rng = random.Random(seed)
    candidates: list[Candidate] = []

    for index in range(count):
        roll = rng.random()
        age = (
            rng.randint(23, 29)
            if roll < 0.26
            else rng.randint(30, 44)
            if roll < 0.68
            else rng.randint(45, 58)
        )
        older = age >= 45
        gender = "Female" if rng.random() < 0.46 else "Male"

        years = max(0.5, rng.gauss(14.0 if older else 9.5 if age >= 30 else 5.6, 2.2))
        skill = _clamp(rng.gauss(0.74, 0.13))
        assessment = _clamp(rng.gauss(0.70, 0.14))
        tooling = _clamp(rng.gauss(0.40 if older else 0.84, 0.13))

        first, last = rng.choice(FIRST_NAMES), rng.choice(LAST_NAMES)
        record = {
            "name": f"{first} {last}",
            "email": f"{first}.{last}{index}@candidates.example.org".lower(),
            "phone": f"+2547{rng.randint(10000000, 99999999)}",
            "gender": gender,
            "age": age,
            "nationality": rng.choice(NATIONALITIES),
            "university": rng.choice(UNIVERSITIES),
            "years_experience": round(years, 1),
            "skill_match": skill,
            "assessment_score": assessment,
            "recent_tooling": tooling,
            "summary": (
                "Backend developer with production experience building services and data "
                "pipelines, comfortable owning systems from design through operation."
            ),
        }
        candidates.append(
            Candidate(
                index=index,
                record=record,
                gender=gender,
                age=age,
                discloses_disability=index % 34 == 0,
            )
        )
    return candidates


@dataclass(frozen=True, slots=True)
class EmployeeRecord:
    subject_key: str
    features: dict[str, float | int | str]
    left: bool


def _employee_key(index: int) -> str:
    digest = hashlib.sha256(f"aegis-demo-employee-{index}".encode()).hexdigest()
    return f"emp_{digest[:10]}"


def _employee(rng: random.Random, index: int) -> tuple[dict[str, float | int | str], float]:
    tenure = round(rng.uniform(0.5, 11.0), 1)
    since_promotion = round(rng.uniform(1, min(84.0, tenure * 12 + 6)), 1)
    midpoint = rng.choice((90_000.0, 130_000.0, 190_000.0, 260_000.0, 380_000.0))
    compa = rng.uniform(0.72, 1.22)
    salary = round(midpoint * compa, 0)
    peer = round(midpoint * rng.uniform(0.95, 1.05), 0)
    managers = rng.choice((0, 0, 0, 1, 1, 2, 3))
    engagement = round(min(5.0, max(1.0, rng.gauss(3.6, 0.8))), 1)
    overtime = round(max(0.0, rng.gauss(14, 10)), 1)
    training = round(max(0.0, rng.gauss(22, 14)), 1)
    internal = rng.choice((0, 0, 0, 1, 1, 2, 4))
    commute = round(rng.uniform(10, 95), 0)

    # What makes people leave, as a business would recognise it: stalled promotion, pay well below
    # the peer median, churn in management, burnout, disengagement and already looking around.
    z = (
        -3.0
        + 0.028 * since_promotion
        + 3.2 * max(0.0, 1.0 - salary / peer)
        + 0.45 * managers
        + 0.035 * overtime
        - 0.55 * (engagement - 3.0)
        + 0.55 * internal
        - 0.012 * training
        + 0.006 * commute
    )
    features: dict[str, float | int | str] = {
        "tenure_years": tenure,
        "months_since_promotion": since_promotion,
        "salary": salary,
        "band_midpoint": midpoint,
        "peer_median_salary": peer,
        "manager_changes_24m": managers,
        "commute_minutes": commute,
        "engagement_score": engagement,
        "training_hours_12m": training,
        "overtime_hours_monthly": overtime,
        "internal_applications_12m": internal,
    }
    return features, z


def build_cohort(history: int = 420, current: int = 64, seed: int = SEED) -> tuple[
    list[EmployeeRecord], list[EmployeeRecord]
]:
    """Past employees with a known outcome to train on, and current ones to score."""
    rng = random.Random(seed + 1)
    past: list[EmployeeRecord] = []
    now: list[EmployeeRecord] = []

    for index in range(history):
        features, z = _employee(rng, index)
        left = rng.random() < 1.0 / (1.0 + 2.718281828 ** (-z))
        past.append(EmployeeRecord(_employee_key(index), features, left))
    for index in range(history, history + current):
        features, _ = _employee(rng, index)
        now.append(EmployeeRecord(_employee_key(index), features, False))
    return past, now


def simulate_screening(candidates: list[Candidate], drop: tuple[str, ...] = ()) -> list[bool]:
    """Who the deterministic scorer advances, without any HTTP. The demo's findings are a
    property of the data, so the tests check them here rather than by driving a server."""
    from aegis.anonymization.engine import AnonymizationEngine
    from aegis.reasoning.deterministic import DeterministicModel
    from aegis.reasoning.screening import CandidateScreener

    engine = AnonymizationEngine(salt="demo-salt-0123456789")
    screener = CandidateScreener(DeterministicModel(), engine)
    return [screener.screen(c.without(*drop), REQUIREMENT).advances for c in candidates]
