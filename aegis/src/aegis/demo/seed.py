"""Populates a tenant by calling the platform's own API, the way a real deployment would be
used. Nothing is written to the database directly: runs go through the governance gate, every
step lands in the hash-chained ledger, and screening verdicts come from the actual scorer."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from aegis.demo.data import (
    REQUIREMENT,
    Candidate,
    build_candidates,
    build_cohort,
)

HR_PARTNER = "Wanjiru Kamau (HR partner)"
HIRING_MANAGER = "Peter Otieno (Engineering)"
IT_LEAD = "Amina Yusuf (IT)"
RETAINED_AFTER = "Amina Yusuf (IT)"

Say = Callable[[str], None]


@dataclass
class Summary:
    screened: int = 0
    advance: int = 0
    runs: int = 0
    reports: list[tuple[str, str]] = field(default_factory=list)
    employees_scored: int = 0
    chain_intact: bool = False
    chain_entries: int = 0


class DemoSeeder:
    def __init__(self, client: httpx.Client, say: Say = print) -> None:
        self._http = client
        self._say = say
        self.summary = Summary()
        # Interview slots must never collide: the calendar refuses a double-booking.
        self._slot = (datetime.now(UTC) + timedelta(days=3)).replace(
            hour=8, minute=0, second=0, microsecond=0
        )

    # -- plumbing -------------------------------------------------------------------------

    def _call(self, method: str, path: str, **kwargs: Any) -> Any:
        response = self._http.request(method, path, **kwargs)
        if response.status_code >= 400:
            raise RuntimeError(f"{method} {path} -> {response.status_code}: {response.text[:300]}")
        return response.json()

    def _run(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        result: dict[str, Any] = self._call(method, path, **kwargs)
        return result

    def _next_slot(self) -> str:
        self._slot += timedelta(minutes=45)
        if self._slot.hour >= 17:
            self._slot = (self._slot + timedelta(days=1)).replace(hour=8, minute=0)
        if self._slot.weekday() >= 5:
            self._slot += timedelta(days=7 - self._slot.weekday())
        return self._slot.isoformat()

    def _start(self, workflow: str, subject: str, context: dict[str, Any]) -> dict[str, Any]:
        self.summary.runs += 1
        body = {"workflow": workflow, "subject_id": subject, "context": context}
        return self._run("POST", "/v1/runs", json=body)

    def _approve(self, run: dict[str, Any], step: str, who: str) -> dict[str, Any]:
        return self._run(
            "POST", f"/v1/runs/{run['run_id']}/steps/{step}/approve", json={"approver": who}
        )

    # -- screening and compliance ---------------------------------------------------------

    def screen_pool(self) -> tuple[list[Candidate], dict[int, dict[str, Any]]]:
        candidates = build_candidates()
        self._say(f"screening {len(candidates)} applicants against the requisition")
        verdicts: dict[int, dict[str, Any]] = {}
        for candidate in candidates:
            verdicts[candidate.index] = self._call(
                "POST", "/v1/screen", json={"record": candidate.record, "requirement": REQUIREMENT}
            )
        self.summary.screened = len(verdicts)
        self.summary.advance = sum(v["recommendation"] == "ADVANCE" for v in verdicts.values())
        return candidates, verdicts

    def _impact(
        self,
        label: str,
        candidates: list[Candidate],
        advanced: dict[int, bool],
        key: Callable[[Candidate], str],
        minimum: int = 30,
    ) -> None:
        totals: dict[str, list[int]] = defaultdict(lambda: [0, 0])
        for candidate in candidates:
            slot = totals[key(candidate)]
            slot[0] += 1 if advanced[candidate.index] else 0
            slot[1] += 1
        report = self._call(
            "POST",
            "/v1/bias/adverse-impact",
            json={
                "label": label,
                "minimum_group_size": minimum,
                "outcomes": [
                    {"group": group, "selected": sel, "total": total}
                    for group, (sel, total) in totals.items()
                ],
            },
        )
        self.summary.reports.append((label, report["verdict"]))
        self._say(f"  {report['verdict']:<18} {label}")

    def compliance(self, candidates: list[Candidate], verdicts: dict[int, dict[str, Any]]) -> None:
        self._say("testing the shortlist for adverse impact")
        advanced = {i: v["recommendation"] == "ADVANCE" for i, v in verdicts.items()}
        by_age = lambda c: c.age_group  # noqa: E731
        by_gender = lambda c: c.gender  # noqa: E731
        by_disability = lambda c: "Discloses a disability" if c.discloses_disability else "Does not"  # noqa: E731

        self._impact("Backend engineer shortlist by age band", candidates, advanced, by_age)
        self._impact("Backend engineer shortlist by gender", candidates, advanced, by_gender)
        self._impact("Shortlist by disability disclosure", candidates, advanced, by_disability)

        # The remediation: take the signal that tracked age out of the brief and screen everyone
        # again. The second analysis is the evidence that the fix worked.
        self._say("re-screening with the recent-tooling signal removed")
        again: dict[int, bool] = {}
        for candidate in candidates:
            result = self._call(
                "POST",
                "/v1/screen",
                json={
                    "record": candidate.without("recent_tooling"),
                    "requirement": REQUIREMENT + " (recent-tooling signal removed)",
                },
            )
            again[candidate.index] = result["recommendation"] == "ADVANCE"
        self._impact(
            "Backend engineer shortlist by age band, after removing the recent-tooling signal",
            candidates,
            again,
            by_age,
        )

    # -- hiring ---------------------------------------------------------------------------

    def hiring(self, candidates: list[Candidate], verdicts: dict[int, dict[str, Any]]) -> None:
        self._say("running hiring workflows")
        ranked = sorted(candidates, key=lambda c: -verdicts[c.index]["score"])
        advance = [c for c in ranked if verdicts[c.index]["recommendation"] == "ADVANCE"]
        review = [c for c in ranked if verdicts[c.index]["recommendation"] == "REVIEW"]
        if len(advance) < 12 or len(review) < 2:
            raise RuntimeError("the synthetic pool did not yield enough candidates to script runs")

        def context(candidate: Candidate, email: str | None = None) -> dict[str, Any]:
            return {
                "recipient_email": email or candidate.record["email"],
                "subject": "Interview invitation - Senior Backend Engineer",
                "body": "We enjoyed your profile and would like to invite you to interview.",
                "attendees": [f"interviewer{candidate.index % 4 + 1}@kijani.example.org"],
                "starts_at": self._next_slot(),
            }

        def subject(candidate: Candidate) -> str:
            return str(verdicts[candidate.index]["subject_key"])

        # Held at shortlisting: deciding who continues is never delegated, even before any offer.
        for candidate in advance[0:4] + review[0:2]:
            self._start("talent_acquisition", subject(candidate), context(candidate))

        # Shortlisted by a person; outreach and scheduling ran themselves; the offer is irreversible
        # and so waits for a human however the tenant is configured.
        for candidate in advance[4:7]:
            run = self._start("talent_acquisition", subject(candidate), context(candidate))
            self._approve(run, "shortlist", HR_PARTNER)

        # A complete hire: shortlist approved, offer approved by the hiring manager.
        run = self._start("talent_acquisition", subject(advance[7]), context(advance[7]))
        run = self._approve(run, "shortlist", HR_PARTNER)
        self._approve(run, "offer", HIRING_MANAGER)

        # A person declined to shortlist, and said why.
        run = self._start("talent_acquisition", subject(advance[8]), context(advance[8]))
        self._call(
            "POST",
            f"/v1/runs/{run['run_id']}/steps/shortlist/reject",
            json={"approver": HR_PARTNER, "reason": "Requisition filled by an internal move"},
        )

        # An outreach that failed for a real reason (a mistyped address) and is left for a person.
        run = self._start(
            "talent_acquisition", subject(advance[9]), context(advance[9], "not-an-email-address")
        )
        self._approve(run, "shortlist", HR_PARTNER)

        # The same failure, recovered: retried with the corrected address, then carried on.
        run = self._start(
            "talent_acquisition", subject(advance[10]), context(advance[10], "typo@@example")
        )
        run = self._approve(run, "shortlist", HR_PARTNER)
        self._call(
            "POST",
            f"/v1/runs/{run['run_id']}/steps/engage/retry",
            json={
                "actor": HR_PARTNER,
                "amendments": {"recipient_email": advance[10].record["email"]},
            },
        )

    # -- onboarding, retention, offboarding -----------------------------------------------

    def onboarding(self) -> None:
        self._say("running onboarding")

        def context() -> dict[str, Any]:
            return {"attendees": ["buddy@kijani.example.org"], "starts_at": self._next_slot()}

        # Ordering a background check or hardware is not delegated to the agent, so a person
        # approves the order and only then does the run wait on the outside provider.
        self._start("onboarding", "hire_8f2a1c", context())

        hardware = self._start("onboarding", "hire_51be09", context())
        self._approve(hardware, "order_hardware", IT_LEAD)  # now waiting on the courier

        cleared = self._start("onboarding", "hire_c07d33", context())
        self._approve(cleared, "background_check", HR_PARTNER)
        self._approve(cleared, "order_hardware", IT_LEAD)
        for step, result in (
            ("background_check", {"verdict": "CLEAR"}),
            ("order_hardware", {"hardware_tracking": "DHL-KE-204119"}),
        ):
            cleared = self._run(
                "POST",
                f"/v1/runs/{cleared['run_id']}/steps/{step}/external",
                json={"succeeded": True, "result": result},
            )
        self._approve(cleared, "provision_access", IT_LEAD)

        failed = self._start("onboarding", "hire_a94e72", context())
        self._approve(failed, "background_check", HR_PARTNER)
        self._call(
            "POST",
            f"/v1/runs/{failed['run_id']}/steps/background_check/external",
            json={"succeeded": False, "result": {"verdict": "UNABLE_TO_VERIFY"}},
        )

    def attrition(self) -> None:
        history, current = build_cohort()
        self._say(f"training the attrition model on {len(history)} past employees")
        self._call(
            "POST",
            "/v1/attrition/train",
            json={
                "algorithm": "gradient_boosting",
                "employees": [{"subject_key": e.subject_key, **e.features} for e in history],
                "left": [e.left for e in history],
            },
        )
        scores = self._call(
            "POST",
            "/v1/attrition/score",
            json={"employees": [{"subject_key": e.subject_key, **e.features} for e in current]},
        )
        self.summary.employees_scored = len(scores)
        high = sorted((s for s in scores if s["band"] == "HIGH"), key=lambda s: -s["probability"])
        self._say(f"  {len(high)} of {len(scores)} current employees are in the high-risk band")

        runs = []
        for score in high[:5]:
            drivers = score["drivers"]
            driver = drivers[0]["feature"].replace("_", " ") if drivers else "risk"
            run = self._start(
                "retention_intervention",
                score["subject_key"],
                {
                    "attendees": ["line.manager@kijani.example.org"],
                    "starts_at": self._next_slot(),
                    "primary_driver": driver,
                },
            )
            runs.append(run)
        for run in runs[:2]:
            self._approve(run, "recommend_move", RETAINED_AFTER)

    def offboarding(self) -> None:
        self._say("running offboarding")
        self._start("offboarding", "emp_leaving_01", {})
        run = self._start("offboarding", "emp_leaving_02", {})
        run = self._approve(run, "terminate", HR_PARTNER)
        self._approve(run, "revoke_access", IT_LEAD)

    # -- whole thing ----------------------------------------------------------------------

    def run(self) -> Summary:
        candidates, verdicts = self.screen_pool()
        self.compliance(candidates, verdicts)
        self.hiring(candidates, verdicts)
        self.onboarding()
        self.attrition()
        self.offboarding()
        integrity = self._call("GET", "/v1/ledger/verify")
        self.summary.chain_intact = bool(integrity["intact"])
        self.summary.chain_entries = int(integrity["entries_checked"])
        return self.summary


__all__ = ["DemoSeeder", "Summary"]
