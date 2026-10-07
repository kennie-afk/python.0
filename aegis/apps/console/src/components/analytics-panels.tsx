"use client";

import { useActionState } from "react";
import type { ReactNode } from "react";
import { SubmitButton } from "@/components/submit-button";
import { Badge, Card, Field, Notice, Select, Stat, Table, inputClass, rowClass } from "@/components/ui";
import {
  analyseSentiment,
  detectEarlyWarning,
  extractSkills,
  forecastGaps,
  matchRoles,
  runDeterminismCheck,
  runDriftCheck,
  simulateWorkforce,
  type ResultState
} from "@/lib/analytics-actions";
import type {
  EarlyWarningView,
  GapForecastView,
  MobilityMatchView,
  SentimentView,
  SkillHolding,
  VerificationReport,
  WorkforceResult
} from "@/lib/types";

const idle = { error: null, result: null };
const areaClass = `${inputClass} font-mono text-xs leading-relaxed`;

function Outcome({ error, children }: { error: string | null; children?: ReactNode }) {
  return (
    <>
      {error ? <Notice tone="danger">{error}</Notice> : null}
      {children}
    </>
  );
}

// ---- verification ---------------------------------------------------------------------------
type Feature = { feature: string; severity: string; statistic: number; metric: string };
type Case = { case: string; stability: string; distinct_outputs: number; repetitions: number; modal_share: number };

export function DriftPanel() {
  const [state, action] = useActionState<ResultState<VerificationReport>, FormData>(runDriftCheck, idle);
  const features = (state.result?.report.features ?? []) as Feature[];
  return (
    <Card
      title="Drift between two samples"
      description="Compare a baseline sample with a candidate sample of one feature. Numbers use PSI or a KS test; text uses category shares."
    >
      <form action={action} className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-3">
          <Field label="Feature">
            <input name="feature" defaultValue="years_experience" className={inputClass} />
          </Field>
          <Select
            label="Method"
            name="kind"
            placeholder="Method"
            defaultValue="psi"
            options={[
              { value: "psi", label: "PSI (numbers)" },
              { value: "ks", label: "KS test (numbers)" },
              { value: "categorical", label: "Categories (text)" }
            ]}
          />
          <Field label="Label" hint="Recorded in the audit trail.">
            <input name="label" defaultValue="Applicant pool" className={inputClass} />
          </Field>
        </div>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Baseline" hint="Separated by commas or spaces; at least 10 for PSI.">
            <textarea name="baseline" rows={4} className={areaClass} defaultValue="2 3 3 4 4 5 5 5 6 6 7 7 8 9 10" />
          </Field>
          <Field label="Candidate">
            <textarea name="candidate" rows={4} className={areaClass} defaultValue="6 7 7 8 8 9 9 10 10 11 12 12 13 14 15" />
          </Field>
        </div>
        <SubmitButton label="Compare" pendingLabel="Comparing…" />
      </form>
      <Outcome error={state.error}>
        {state.result ? (
          <div className="mt-4 space-y-3">
            <div className="flex items-center gap-2 text-sm">
              <Badge value={state.result.verdict} />
              <span className="text-[var(--color-muted)]">
                recorded in the audit trail at entry {state.result.ledger_sequence}
              </span>
            </div>
            <Table head={["Feature", "Method", "Statistic", "Verdict"]}>
              {features.map((row) => (
                <tr key={row.feature} className={rowClass}>
                  <td className="px-3 py-2.5">{row.feature}</td>
                  <td className="px-3 py-2.5 text-[var(--color-muted)]">{row.metric}</td>
                  <td className="px-3 py-2.5 tabular-nums">{row.statistic.toFixed(3)}</td>
                  <td className="px-3 py-2.5"><Badge value={row.severity} /></td>
                </tr>
              ))}
            </Table>
          </div>
        ) : null}
      </Outcome>
    </Card>
  );
}

const SAMPLE_RECORD = JSON.stringify(
  {
    full_name: "Amina Wanjiru",
    gender: "female",
    university: "University of Nairobi",
    years_experience: 7,
    skill_match: 0.88,
    summary: "Backend engineer with distributed systems experience."
  },
  null,
  2
);

export function DeterminismPanel() {
  const [state, action] = useActionState<ResultState<VerificationReport>, FormData>(runDeterminismCheck, idle);
  const cases = ((state.result?.report as { cases?: Case[] } | undefined)?.cases ?? []) as Case[];
  return (
    <Card
      title="Same candidate, same answer?"
      description="Screens one record repeatedly through the live screener. A model that answers differently on a rerun fails."
    >
      <form action={action} className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Role requirement">
            <input name="requirement" defaultValue="5+ years of backend engineering" className={inputClass} />
          </Field>
          <Field label="Repetitions" hint="2 to 50.">
            <input name="repetitions" type="number" min="2" max="50" defaultValue="10" className={inputClass} />
          </Field>
        </div>
        <Field label="Candidate record" hint="Names and protected attributes are removed before the screener sees it.">
          <textarea name="record" rows={8} className={areaClass} defaultValue={SAMPLE_RECORD} />
        </Field>
        <SubmitButton label="Run the probe" pendingLabel="Probing…" />
      </form>
      <Outcome error={state.error}>
        {state.result ? (
          <div className="mt-4 space-y-3">
            <div className="flex items-center gap-2 text-sm">
              <Badge value={state.result.verdict} />
              <span className="text-[var(--color-muted)]">entry {state.result.ledger_sequence} in the audit trail</span>
            </div>
            <Table head={["Case", "Stability", "Distinct outputs", "Modal share"]}>
              {cases.map((row) => (
                <tr key={row.case} className={rowClass}>
                  <td className="px-3 py-2.5">{row.case}</td>
                  <td className="px-3 py-2.5"><Badge value={row.stability} /></td>
                  <td className="px-3 py-2.5 tabular-nums">{row.distinct_outputs} of {row.repetitions}</td>
                  <td className="px-3 py-2.5 tabular-nums">{(row.modal_share * 100).toFixed(0)}%</td>
                </tr>
              ))}
            </Table>
          </div>
        ) : null}
      </Outcome>
    </Card>
  );
}

// ---- skills ---------------------------------------------------------------------------------
export function ExtractPanel() {
  const [state, action] = useActionState<ResultState<{ subject_key: string; holdings: SkillHolding[] }>, FormData>(extractSkills, idle);
  return (
    <Card title="Skills from evidence" description="Proficiency comes from stated years, not from how often a word appears. Nothing is stored.">
      <form action={action} className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Employee reference" hint="A pseudonym.">
            <input name="subject_key" defaultValue="emp-1" className={inputClass} />
          </Field>
          <Field label="Years per skill" hint="skill=years, comma separated.">
            <input name="years" defaultValue="python=6, postgresql=4, kubernetes=2" className={inputClass} />
          </Field>
        </div>
        <Field label="Evidence" hint="CV text, project notes. Separate documents with a blank line.">
          <textarea name="evidence" rows={4} className={areaClass} defaultValue={"Built services in Python with Postgres behind them.\n\nRan python migrations on k8s nightly."} />
        </Field>
        <SubmitButton label="Extract" pendingLabel="Reading…" />
      </form>
      <Outcome error={state.error}>
        {state.result ? (
          <div className="mt-4">
            <Table head={["Skill", "Proficiency", "Mentions", "Years"]}>
              {state.result.holdings.map((holding) => (
                <tr key={holding.skill} className={rowClass}>
                  <td className="px-3 py-2.5">{holding.skill}</td>
                  <td className="px-3 py-2.5"><Badge value={holding.proficiency} /></td>
                  <td className="px-3 py-2.5 tabular-nums">{holding.mentions}</td>
                  <td className="px-3 py-2.5 tabular-nums">{holding.years}</td>
                </tr>
              ))}
            </Table>
          </div>
        ) : null}
      </Outcome>
    </Card>
  );
}

export function GapPanel() {
  const [state, action] = useActionState<ResultState<GapForecastView>, FormData>(forecastGaps, idle);
  return (
    <Card title="Skill gaps" description="Qualified supply against required headcount over a horizon, eroded by expected attrition.">
      <form action={action} className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Requirements" hint="skill:level:headcount, one per line. Levels: awareness, working, practitioner, expert.">
            <textarea name="requirements" rows={3} className={areaClass} defaultValue={"python:expert:3\nkubernetes:working:1"} />
          </Field>
          <Field label="People" hint="One evidence block per person, separated by a blank line.">
            <textarea name="people" rows={3} className={areaClass} defaultValue={"Senior python engineer, python python for 6 years, k8s\n\nPlatform engineer, kubernetes, terraform"} />
          </Field>
        </div>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Horizon (months)"><input name="horizon" type="number" min="1" max="60" defaultValue="12" className={inputClass} /></Field>
          <Field label="Attrition over the horizon (%)"><input name="attrition" type="number" min="0" max="99" defaultValue="15" className={inputClass} /></Field>
        </div>
        <SubmitButton label="Forecast" pendingLabel="Forecasting…" />
      </form>
      <Outcome error={state.error}>
        {state.result ? (
          <div className="mt-4 space-y-3">
            <Notice tone={state.result.total_shortfall > 0 ? "warn" : "good"}>{state.result.summary}</Notice>
            <Table head={["Skill", "Needs", "Supply", "Demand", "Short by"]}>
              {state.result.gaps.map((gap) => (
                <tr key={gap.skill} className={rowClass}>
                  <td className="px-3 py-2.5">{gap.skill}</td>
                  <td className="px-3 py-2.5 text-[var(--color-muted)]">{gap.required.toLowerCase()}</td>
                  <td className="px-3 py-2.5 tabular-nums">{gap.supply}</td>
                  <td className="px-3 py-2.5 tabular-nums">{gap.demand}</td>
                  <td className="px-3 py-2.5 tabular-nums">{gap.shortfall}</td>
                </tr>
              ))}
            </Table>
          </div>
        ) : null}
      </Outcome>
    </Card>
  );
}

export function MobilityPanel() {
  const [state, action] = useActionState<ResultState<MobilityMatchView[]>, FormData>(matchRoles, idle);
  return (
    <Card title="Internal mobility" description="How close an employee is to a role, and the exact steps between them and it.">
      <form action={action} className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-3">
          <Field label="Employee reference"><input name="subject_key" defaultValue="emp-1" className={inputClass} /></Field>
          <Field label="Target role"><input name="title" defaultValue="Platform lead" className={inputClass} /></Field>
          <Field label="Years per skill"><input name="years" defaultValue="python=6, kubernetes=2" className={inputClass} /></Field>
        </div>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Role requirements" hint="skill:level, one per line.">
            <textarea name="requirements" rows={3} className={areaClass} defaultValue={"kubernetes:practitioner\npython:expert"} />
          </Field>
          <Field label="Evidence">
            <textarea name="evidence" rows={3} className={areaClass} defaultValue="Python services, k8s deployments, python tooling." />
          </Field>
        </div>
        <SubmitButton label="Match" pendingLabel="Matching…" />
      </form>
      <Outcome error={state.error}>
        {state.result ? (
          state.result.length === 0 ? (
            <div className="mt-4"><Notice tone="info">No role scored high enough to match.</Notice></div>
          ) : (
            <div className="mt-4">
              <Table head={["Role", "Fit", "Readiness", "Development path"]}>
                {state.result.map((match) => (
                  <tr key={match.role_id} className={rowClass}>
                    <td className="px-3 py-2.5">{match.title}</td>
                    <td className="px-3 py-2.5 tabular-nums">{(match.score * 100).toFixed(0)}%</td>
                    <td className="px-3 py-2.5"><Badge value={match.ready_now ? "ready now" : match.stretch ? "stretch" : "further"} /></td>
                    <td className="px-3 py-2.5 text-xs text-[var(--color-muted)]">{match.development_path.join("; ") || "none needed"}</td>
                  </tr>
                ))}
              </Table>
            </div>
          )
        ) : null}
      </Outcome>
    </Card>
  );
}

// ---- workforce ------------------------------------------------------------------------------
export function WorkforcePanel() {
  const [state, action] = useActionState<ResultState<WorkforceResult>, FormData>(simulateWorkforce, idle);
  return (
    <div className="space-y-6">
      <Card
        title="Headcount and capacity"
        description="New hires ramp to productivity over a period instead of counting in full from day one, so capacity trails headcount."
      >
        <form action={action} className="space-y-4">
          <div className="grid gap-4 sm:grid-cols-3">
            <Field label="Starting headcount"><input name="headcount" type="number" min="0" defaultValue="120" className={inputClass} /></Field>
            <Field label="Monthly attrition (%)"><input name="attrition" type="number" step="0.1" min="0" max="50" defaultValue="1.5" className={inputClass} /></Field>
            <Field label="Hires per month"><input name="hires" type="number" min="0" defaultValue="3" className={inputClass} /></Field>
            <Field label="Ramp (months)"><input name="ramp" type="number" min="0" max="24" defaultValue="3" className={inputClass} /></Field>
            <Field label="Capacity needed"><input name="demand" type="number" min="0" defaultValue="125" className={inputClass} /></Field>
            <Field label="Months to project"><input name="months" type="number" min="1" max="120" defaultValue="12" className={inputClass} /></Field>
          </div>
          <div className="max-w-xs">
            <Field label="Target headcount" hint="Optional. Solves for the monthly hiring rate that reaches it (up to 5,000).">
              <input name="target" type="number" min="0" max="5000" className={inputClass} />
            </Field>
          </div>
          <SubmitButton label="Project" pendingLabel="Projecting…" />
        </form>
      </Card>
      <Outcome error={state.error}>
        {state.result
          ? state.result.results.map((scenario) => {
              const hires = state.result?.hires_required[scenario.scenario];
              return (
                <Card key={scenario.scenario} title={scenario.scenario} description={scenario.summary}>
                  <div className="grid gap-3 sm:grid-cols-4">
                    <Stat label="Final headcount" value={scenario.final_headcount.toFixed(0)} />
                    <Stat label="Leavers" value={scenario.total_leavers.toFixed(0)} />
                    <Stat label="Hires" value={String(scenario.total_hires)} />
                    <Stat label="First shortfall" value={scenario.first_shortfall_month ? `Month ${scenario.first_shortfall_month}` : "None"} />
                  </div>
                  {hires !== undefined ? (
                    <p className="mt-3 text-xs text-[var(--color-muted)]">
                      To reach the target headcount: {typeof hires === "number" ? `${hires} hires a month` : hires}.
                    </p>
                  ) : null}
                  <div className="mt-4">
                    <Table head={["Month", "Headcount", "Capacity", "Leavers", "Joiners", "Short by"]}>
                      {scenario.timeline.map((month) => (
                        <tr key={month.month} className={rowClass}>
                          <td className="px-3 py-2 tabular-nums">{month.month}</td>
                          <td className="px-3 py-2 tabular-nums">{month.headcount.toFixed(1)}</td>
                          <td className="px-3 py-2 tabular-nums">{month.effective_capacity.toFixed(1)}</td>
                          <td className="px-3 py-2 tabular-nums">{month.leavers.toFixed(1)}</td>
                          <td className="px-3 py-2 tabular-nums">{month.joiners}</td>
                          <td className="px-3 py-2 tabular-nums">{month.shortfall > 0 ? month.shortfall.toFixed(1) : "—"}</td>
                        </tr>
                      ))}
                    </Table>
                  </div>
                </Card>
              );
            })
          : null}
      </Outcome>
    </div>
  );
}

// ---- sentiment ------------------------------------------------------------------------------
const GOOD = [
  "operations | the manager is supportive and leadership is clear",
  "operations | great team culture and good pay",
  "operations | pay is fair and the work is good",
  "operations | supportive manager, good tools",
  "operations | good culture here"
].join("\n");
const BAD = [
  "operations | leadership is not clear and the manager is poor",
  "operations | pay is bad and hours are long",
  "operations | poor management, terrible workload",
  "operations | the manager is not supportive",
  "operations | bad culture, poor tools"
].join("\n");

function GroupTable({ view }: { view: SentimentView }) {
  return (
    <div className="space-y-3">
      <p className="text-xs text-[var(--color-muted)]">{view.summary}</p>
      <Table head={["Group", "Respondents", "Overall", "Concerns"]}>
        {view.groups.map((group) => (
          <tr key={group.group} className={rowClass}>
            <td className="px-3 py-2.5">{group.group}</td>
            <td className="px-3 py-2.5 tabular-nums">{group.respondents}</td>
            <td className="px-3 py-2.5 tabular-nums">{group.overall > 0 ? "+" : ""}{group.overall.toFixed(2)}</td>
            <td className="px-3 py-2.5 text-xs text-[var(--color-muted)]">
              {group.aspects.filter((a) => a.negative).map((a) => a.aspect.replaceAll("_", " ").toLowerCase()).join(", ") || "none"}
            </td>
          </tr>
        ))}
      </Table>
      {view.suppressed_groups.length > 0 ? (
        <Notice tone="info">
          Withheld, fewer than {view.minimum_group_size} respondents: {view.suppressed_groups.join(", ")}. At that size an aggregate is a thin disguise for one person&apos;s answer.
        </Notice>
      ) : null}
    </div>
  );
}

export function SentimentPanel() {
  const [state, action] = useActionState<ResultState<SentimentView>, FormData>(analyseSentiment, idle);
  return (
    <Card title="Sentiment by group" description="Group aggregates only. No individual response is ever returned, and small groups are withheld.">
      <form action={action} className="space-y-4">
        <Field label="Responses" hint={'One per line: "group | what they said".'}>
          <textarea name="responses" rows={6} className={areaClass} defaultValue={GOOD} />
        </Field>
        <div className="max-w-xs">
          <Field label="Minimum group size" hint="At least 2."><input name="minimum" type="number" min="2" defaultValue="5" className={inputClass} /></Field>
        </div>
        <SubmitButton label="Analyse" pendingLabel="Analysing…" />
      </form>
      <Outcome error={state.error}>{state.result ? <div className="mt-4"><GroupTable view={state.result} /></div> : null}</Outcome>
    </Card>
  );
}

export function EarlyWarningPanel() {
  const [state, action] = useActionState<ResultState<EarlyWarningView>, FormData>(detectEarlyWarning, idle);
  return (
    <Card title="Early warning" description="Aspects that dropped sharply between two periods, worst first. A finding is written to the audit trail and queued as an alert.">
      <form action={action} className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Earlier period"><textarea name="previous" rows={6} className={areaClass} defaultValue={GOOD} /></Field>
          <Field label="Latest period"><textarea name="current" rows={6} className={areaClass} defaultValue={BAD} /></Field>
        </div>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Label" hint="Recorded in the audit trail."><input name="label" defaultValue="Operations, last quarter" className={inputClass} /></Field>
          <Field label="Minimum group size"><input name="minimum" type="number" min="2" defaultValue="5" className={inputClass} /></Field>
        </div>
        <SubmitButton label="Compare periods" pendingLabel="Comparing…" />
      </form>
      <Outcome error={state.error}>
        {state.result ? (
          <div className="mt-4 space-y-3">
            {state.result.warnings.length === 0 ? (
              <Notice tone="good">No aspect dropped past the threshold.</Notice>
            ) : (
              <Table head={["Group", "Aspect", "Before", "Now", "Drop"]}>
                {state.result.warnings.map((warning) => (
                  <tr key={`${warning.group}-${warning.aspect}`} className={rowClass}>
                    <td className="px-3 py-2.5">{warning.group}</td>
                    <td className="px-3 py-2.5">{warning.aspect.replaceAll("_", " ").toLowerCase()}</td>
                    <td className="px-3 py-2.5 tabular-nums">{warning.previous.toFixed(2)}</td>
                    <td className="px-3 py-2.5 tabular-nums">{warning.current.toFixed(2)}</td>
                    <td className="px-3 py-2.5 tabular-nums">{warning.drop.toFixed(2)}</td>
                  </tr>
                ))}
              </Table>
            )}
          </div>
        ) : null}
      </Outcome>
    </Card>
  );
}
