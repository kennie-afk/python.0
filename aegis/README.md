# Aegis

An HR automation platform that governs itself.

Aegis automates the employment lifecycle — sourcing, screening, engagement, interview
scheduling, onboarding, provisioning, retention — with autonomous agents, and constrains those
agents with governance that cannot be configured away. Every company has HR, so the platform is
built horizontally: a five-person startup and a ten-thousand-person enterprise run the same
engine under different policy.

## The problem

Two things are true at once. Automating HR work is enormously valuable: a hiring pipeline that
takes fourteen days of recruiter coordination can complete in under a day. And HR decisions are
about people's livelihoods, made by probabilistic systems that fail in ways ordinary monitoring
cannot see:

- **Probabilistic inconsistency** — the same candidate, submitted twice, receives two different
  evaluations.
- **Unseen drift** — the applicant population moves and guardrails calibrated months ago quietly
  stop binding.
- **Inherited bias** — a model trained on historical hiring reproduces the disparities in it.

Neither raises an exception nor appears in an error rate. Aegis exists so that the automation and
the evidence that it behaved correctly are the same system.

## Architecture

| Module | Responsibility | State |
|---|---|---|
| `agents` | workflow runtime: perception, reasoning, action, durable multi-day state | implemented |
| `governance` | permission boundaries, human-in-the-loop checkpoints, escalation triggers | implemented |
| `ledger` | hash-chained, tamper-evident record of every decision and approval | implemented |
| `anonymization` | identity obfuscation, pedigree neutralisation, temporal flattening | implemented |
| `bias` | four-fifths-rule adverse impact testing with statistical significance | implemented |
| `verification` | determinism probing, drift detection, fidelity gating | implemented |
| `hr` | workflow definitions for hiring, onboarding, retention, offboarding | implemented |
| `attrition` | flight-risk pipeline: feature engineering, model, risk banding, drivers | implemented |
| `api` | HTTP surface over the whole platform, authenticated and tenant-scoped | implemented |
| `reasoning` | language-model layer: prompts, structured screening, response validation | implemented |
| `persistence` | PostgreSQL storage for runs, ledger, policies, models and API keys | implemented |
| `auth` | JWT tokens and hashed API keys carrying tenant and roles | implemented |
| `integrations` | email and calendar tools an agent actually acts through | implemented |
| `skills` | skill taxonomy, proficiency extraction, gap forecasting, mobility matching | implemented |
| `workforce` | headcount, attrition and capacity simulation with hiring ramp | implemented |
| `sentiment` | aspect-based sentiment with a privacy threshold and early warning | implemented |

## Governance is structural, not configurable

Seven action types are irreversible: extending or withdrawing an offer, terminating employment,
setting a performance rating, approving a promotion, adjusting compensation, granting equity.
The gate checks these **before** it consults tenant policy, so no configuration reaches them.

```python
policy = TenantPolicy(
    tenant_id=tenant,
    autonomous_actions=frozenset(ActionType),      # delegate everything
    readable_domains=frozenset(RestrictedDomain),  # grant every domain
    confidence_floor=0.0,                          # accept any confidence
)

GovernanceGate(policy).evaluate(offer).verdict
# Verdict.REQUIRE_HUMAN_APPROVAL
```

A customer cannot buy their way out of human review on a termination. There is a test for
exactly that, parameterised over every irreversible action.

Restricted domains — compensation bands, equity grants, health records, disciplinary history,
immigration status — are **denied** rather than escalated when an agent has not been granted
them. An agent that should not see a health record does not get one queued for approval; it is
refused.

Every action must carry a rationale. Construction fails without one, so an agent cannot take an
HR action it is unable to explain.

## Workflows

Hiring runs `Sourcing → Match Score → Engagement → Scheduling → Offer`. Under a conservative
policy the first four steps run autonomously and the offer stops for a human. Onboarding runs
documentation, verification, background check, hardware, provisioning, learning path and
milestone check-ins — where the background check parks the run for days awaiting a provider and
resumes when the result arrives, with the verdict flowing into the steps that follow.

```python
runtime = AgentRuntime(gate=GovernanceGate(policy), tools=registry, ledger=ledger)
run = runtime.start(ONBOARDING, tenant_id, "employee-7")
runtime.advance(run)

run.state("background_check").status     # AWAITING_EXTERNAL
runtime.resolve_external(run, "background_check", {"verdict": "CLEAR"})
run.status                               # COMPLETED
```

## Anonymisation before inference

Protected attributes are dropped, identifying attributes are replaced with a salted pseudonym,
institution names are mapped to opaque but stable labels, and dates become durations. The
pedigree mapping is deliberately consistent rather than random, so "did candidates from the same
institution fare differently" remains answerable while the institution's prestige is invisible.
Temporal flattening removes the age signal a graduation date carries.

`assert_clean` refuses a payload that still holds anything protected, so the guarantee is checked
at the boundary rather than assumed.

## Adverse impact

`four_fifths_test` computes selection rates per group against the highest-selecting group and
flags any ratio below 0.80, with a chi-squared p-value alongside. Groups below a minimum size are
excluded, and if fewer than two remain the verdict is `INSUFFICIENT_DATA` rather than a pass — a
ratio computed on eleven applicants is not evidence, and reporting it as compliance would be
worse than reporting nothing.

## Development

```bash
uv venv
uv pip install -e ".[dev]"
uv run pytest
uv run ruff check .
uv run mypy
```

314 tests. Passes `mypy --strict` and `ruff` with zero findings.

```bash
docker compose up
```

```bash
uv run uvicorn aegis.api.app:app --reload
```

Thirteen endpoints covering workflow runs, approvals, external results, anonymisation, adverse
impact testing, attrition training and scoring, and ledger inspection. Every request carries an
`X-Tenant-Id`; a request without one is refused rather than defaulted, and one tenant cannot
read another's runs, models or ledger.

## Attrition

The flight-risk pipeline follows ingestion, feature engineering, model scoring and action.
Absolute salary never becomes a feature; what the model sees is position relative to band
midpoint and to peer median, which is the signal that actually predicts leaving. Protected
attributes are refused at the feature boundary, so a model cannot be trained on them by
accident.

```python
model = AttritionModel("gradient_boosting")
model.train(snapshots, left)

score = model.score(snapshot)
score.band              # RiskBand.HIGH
score.top_drivers(3)    # months_since_promotion, above cohort
```

Gradient boosting, random forest and logistic regression are all supported. Training on fewer
than forty rows is refused, as is training where every outcome is the same, because a model
fitted on either is not evidence. Scores carry the factors driving them, ranked, so a manager
receives a reason rather than a number.

## Reasoning

The reasoning layer sits behind a `LanguageModel` interface with two implementations: a hosted
model over an OpenAI-compatible API, and a deterministic local model used by default and in
tests. Screening runs a candidate through anonymisation first, so the model never sees a name,
a protected attribute or an institution, and the response is validated rather than trusted — a
score outside `[0,1]`, an invented recommendation or malformed JSON is refused rather than
passed downstream as a decision.

Prompts refuse a non-zero temperature outright. A stylistic sampler makes the same candidate
score differently on a rerun, which is precisely the failure the verification module exists to
catch; allowing it here would mean shipping the defect and then measuring it.

## Authentication and persistence

Callers authenticate with a signed JWT carrying the tenant claim, or with an API key stored as
a SHA-256 hash. A tenant header is not authentication and is refused. Runs, ledger entries,
tenant policies, trained models and API keys live in PostgreSQL, so a restart loses nothing and
the hash chain continues from its stored head rather than restarting at genesis.

## Skills, mobility and planning

Skills are extracted from free-text evidence against a taxonomy with aliases, and proficiency
is inferred from tenure rather than how often a word appears — a candidate with six years of
Python is an expert whether their CV says it twice or ten times. Gap forecasting compares
qualified supply against required headcount over a horizon, eroded by expected attrition.

Mobility matching scores an employee against open roles and separates the ones they are ready
for from stretch roles, returning a development path naming the exact proficiency steps between
them and the role. The same function inverted ranks internal candidates for a vacancy.

Workforce simulation projects headcount, attrition and effective capacity month by month, with
new hires ramping to productivity over a configurable period rather than counting fully from
day one. `hires_required` solves the inverse: the monthly hiring rate needed to reach a target
headcount, or an error saying the target is unreachable at that attrition rate.

## Sentiment without surveillance

Aspect-based scoring categorises feedback into leadership, culture, compensation, work-life
balance, tooling and career growth, handling negation so "leadership is not clear" scores
negative rather than positive.

Groups below a minimum size are **withheld entirely** and reported as suppressed. A team of
three cannot be reported on, because at that size an aggregate is a thin disguise for an
individual's answer. A threshold below two is refused outright. No individual response is ever
returned — only group aggregates. Early warning compares two periods and raises the aspects
that dropped sharply, worst first.

## Migrations

```bash
alembic upgrade head
```

Schema is versioned rather than created implicitly, and the initial revision applies and
reverses cleanly.

## Not yet built

A web interface. The platform is API-first and every capability is reachable over HTTP, but
there is no front end.
