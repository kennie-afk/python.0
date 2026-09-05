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
| `api` | HTTP surface over the whole platform, tenant-scoped | implemented |

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

 tests. Passes `mypy --strict` and `ruff` with zero findings.

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

## Not yet built

Skill taxonomy and gap forecasting, internal mobility matching, workforce planning simulation,
and aggregated sentiment analysis. Persistence is in-memory; runs, ledgers and models do not yet
survive a restart.
