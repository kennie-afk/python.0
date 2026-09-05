"use server";

import { revalidatePath } from "next/cache";
import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { api, describeError } from "@/lib/api";
import { failed, succeeded, type FormState } from "@/lib/form-state";
import {
  subjectCookieName,
  tenantCookieName,
  tokenCookieName,
  requireSession
} from "@/lib/session";
import type {
  AdverseImpactResponse,
  AttritionScoreView,
  RunView,
  ScreeningView,
  TokenResponse,
  TrainResponse
} from "@/lib/types";

function text(form: FormData, key: string): string {
  return String(form.get(key) ?? "").trim();
}

const LIST_FIELDS = new Set(["attendees"]);

function collect(form: FormData, prefix: string): Record<string, unknown> {
  const collected: Record<string, unknown> = {};

  for (const key of new Set(form.keys())) {
    if (!key.startsWith(prefix)) {
      continue;
    }
    const field = key.slice(prefix.length);
    const values = form
      .getAll(key)
      .map((value) => String(value).trim())
      .filter((value) => value.length > 0);

    if (values.length === 0) {
      continue;
    }
    collected[field] = LIST_FIELDS.has(field) ? values : values[0];
  }

  return collected;
}

export async function signIn(_state: FormState, form: FormData): Promise<FormState> {
  const apiKey = text(form, "api_key");
  if (!apiKey) {
    return failed("Enter the API key issued for your tenant.");
  }

  let session: TokenResponse;
  try {
    session = await api.post<TokenResponse>("/v1/auth/token", { api_key: apiKey });
  } catch (error) {
    return failed(describeError(error));
  }

  const store = await cookies();
  const options = {
    httpOnly: true,
    sameSite: "lax" as const,
    path: "/",
    maxAge: 60 * 55,
    secure: process.env.NODE_ENV === "production"
  };
  store.set(tokenCookieName, session.token, options);
  store.set(tenantCookieName, session.tenant_id, options);
  store.set(subjectCookieName, session.subject, options);

  redirect("/");
}

export async function signOut(): Promise<void> {
  const store = await cookies();
  for (const name of [tokenCookieName, tenantCookieName, subjectCookieName]) {
    store.delete(name);
  }
  redirect("/login");
}

export async function startRun(_state: FormState, form: FormData): Promise<FormState> {
  const session = await requireSession();
  const workflow = text(form, "workflow");
  const subjectId = text(form, "subject_id");

  const context = collect(form, "context.");

  let run: RunView;
  try {
    run = await api.post<RunView>(
      "/v1/runs",
      { workflow, subject_id: subjectId, context },
      session.token
    );
  } catch (error) {
    return failed(describeError(error));
  }

  revalidatePath("/runs");
  redirect(`/runs/${run.run_id}`);
}

async function actOnStep(
  path: string,
  body: unknown,
  runId: string
): Promise<FormState> {
  const session = await requireSession();
  try {
    await api.post<RunView>(path, body, session.token);
  } catch (error) {
    return failed(describeError(error));
  }
  revalidatePath(`/runs/${runId}`);
  revalidatePath("/runs");
  revalidatePath("/ledger");
  revalidatePath("/");
  return succeeded("Done.");
}

export async function approveStep(_state: FormState, form: FormData): Promise<FormState> {
  const runId = text(form, "run_id");
  const stepKey = text(form, "step_key");
  const approver = text(form, "approver");
  if (!approver) {
    return failed("An approval has to name who approved it.");
  }
  return actOnStep(`/v1/runs/${runId}/steps/${stepKey}/approve`, { approver }, runId);
}

export async function rejectStep(_state: FormState, form: FormData): Promise<FormState> {
  const runId = text(form, "run_id");
  const stepKey = text(form, "step_key");
  const approver = text(form, "approver");
  const reason = text(form, "reason");
  if (!approver || !reason) {
    return failed("A rejection needs both a name and a reason.");
  }
  return actOnStep(`/v1/runs/${runId}/steps/${stepKey}/reject`, { approver, reason }, runId);
}

export async function retryStep(_state: FormState, form: FormData): Promise<FormState> {
  const runId = text(form, "run_id");
  const stepKey = text(form, "step_key");
  const actor = text(form, "actor");
  if (!actor) {
    return failed("A retry has to name who asked for it.");
  }

  const amendments = collect(form, "amend.");

  return actOnStep(`/v1/runs/${runId}/steps/${stepKey}/retry`, { actor, amendments }, runId);
}

export interface ScreeningState extends FormState {
  result: ScreeningView | null;
}

export async function screenCandidate(
  _state: ScreeningState,
  form: FormData
): Promise<ScreeningState> {
  const session = await requireSession();
  const requirement = text(form, "requirement");
  if (!requirement) {
    return { error: "Describe the role you are screening against.", message: null, result: null };
  }

  const record: Record<string, unknown> = {};
  for (const field of [
    "full_name",
    "email",
    "national_id",
    "gender",
    "date_of_birth",
    "university"
  ]) {
    const value = text(form, field);
    if (value) {
      record[field] = value;
    }
  }
  for (const field of ["years_experience", "skill_match"]) {
    const value = text(form, field);
    if (value) {
      record[field] = Number(value);
    }
  }

  if (!record.full_name && !record.national_id) {
    return {
      error: "Give at least a name or an identifier so the record can be pseudonymised.",
      message: null,
      result: null
    };
  }

  try {
    const result = await api.post<ScreeningView>(
      "/v1/screen",
      { record, requirement },
      session.token
    );
    return { error: null, message: null, result };
  } catch (error) {
    return { error: describeError(error), message: null, result: null };
  }
}

export interface ImpactState extends FormState {
  result: AdverseImpactResponse | null;
}

export async function checkAdverseImpact(
  _state: ImpactState,
  form: FormData
): Promise<ImpactState> {
  const session = await requireSession();
  const outcomes: { group: string; selected: number; total: number }[] = [];

  for (let index = 0; index < 6; index += 1) {
    const group = text(form, `group.${index}.name`);
    const selected = text(form, `group.${index}.selected`);
    const total = text(form, `group.${index}.total`);
    if (!group || !selected || !total) {
      continue;
    }
    outcomes.push({ group, selected: Number(selected), total: Number(total) });
  }

  if (outcomes.length < 2) {
    return { error: "Enter at least two groups to compare.", message: null, result: null };
  }

  try {
    const result = await api.post<AdverseImpactResponse>(
      "/v1/bias/adverse-impact",
      { outcomes, minimum_group_size: Number(text(form, "minimum_group_size") || 30) },
      session.token
    );
    return { error: null, message: null, result };
  } catch (error) {
    return { error: describeError(error), message: null, result: null };
  }
}

export interface ScoringState extends FormState {
  result: AttritionScoreView[] | null;
}

const EMPLOYEE_FIELDS = [
  "tenure_years",
  "months_since_promotion",
  "salary",
  "band_midpoint",
  "peer_median_salary",
  "manager_changes_24m",
  "commute_minutes",
  "engagement_score",
  "training_hours_12m",
  "overtime_hours_monthly",
  "internal_applications_12m"
] as const;

export async function scoreEmployee(
  _state: ScoringState,
  form: FormData
): Promise<ScoringState> {
  const session = await requireSession();
  const subject = text(form, "subject_key");
  if (!subject) {
    return { error: "Give the employee a reference so the score can be traced.", message: null, result: null };
  }

  const employee: Record<string, unknown> = { subject_key: subject };
  for (const field of EMPLOYEE_FIELDS) {
    const value = text(form, field);
    if (value) {
      employee[field] = Number(value);
    }
  }

  try {
    const result = await api.post<AttritionScoreView[]>(
      "/v1/attrition/score",
      { employees: [employee] },
      session.token
    );
    return { error: null, message: null, result };
  } catch (error) {
    return { error: describeError(error), message: null, result: null };
  }
}

export interface TrainingState extends FormState {
  result: TrainResponse | null;
}

export async function trainOnSampleCohort(
  _state: TrainingState,
  _form: FormData
): Promise<TrainingState> {
  const session = await requireSession();
  const employees: Record<string, number | string>[] = [];
  const left: boolean[] = [];

  for (let index = 0; index < 120; index += 1) {
    const atRisk = index % 3 === 0;
    const spread = ((index * 37) % 100) / 100;
    employees.push({
      subject_key: `sample-${1000 + index}`,
      tenure_years: atRisk ? 0.6 + spread * 2 : 3.5 + spread * 8,
      months_since_promotion: atRisk ? 26 + spread * 24 : 2 + spread * 14,
      salary: atRisk ? 52000 + spread * 8000 : 76000 + spread * 20000,
      band_midpoint: 80000,
      peer_median_salary: 82000,
      manager_changes_24m: atRisk ? 3 : 0,
      commute_minutes: atRisk ? 62 + spread * 30 : 5 + spread * 22,
      engagement_score: atRisk ? 1.2 + spread : 3.6 + spread,
      training_hours_12m: atRisk ? spread * 5 : 22 + spread * 34,
      overtime_hours_monthly: atRisk ? 30 + spread * 20 : spread * 7,
      internal_applications_12m: atRisk ? 3 : 0
    });
    left.push(atRisk);
  }

  try {
    const result = await api.post<TrainResponse>(
      "/v1/attrition/train",
      { algorithm: "gradient_boosting", employees, left },
      session.token
    );
    revalidatePath("/attrition");
    return {
      error: null,
      message: `Trained on ${result.rows} sample records with ${result.positives} leavers.`,
      result
    };
  } catch (error) {
    return { error: describeError(error), message: null, result: null };
  }
}

export async function trainAttritionModel(
  _state: TrainingState,
  form: FormData
): Promise<TrainingState> {
  const session = await requireSession();
  const raw = text(form, "records");

  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return {
      error: "The training set has to be valid JSON.",
      message: null,
      result: null
    };
  }

  if (!Array.isArray(parsed)) {
    return {
      error: "Expected an array of records, each with an employee and a left flag.",
      message: null,
      result: null
    };
  }

  const employees: unknown[] = [];
  const left: boolean[] = [];
  for (const item of parsed) {
    const row = item as { left?: unknown } & Record<string, unknown>;
    const { left: outcome, ...employee } = row;
    employees.push(employee);
    left.push(Boolean(outcome));
  }

  try {
    const result = await api.post<TrainResponse>(
      "/v1/attrition/train",
      { algorithm: text(form, "algorithm") || "gradient_boosting", employees, left },
      session.token
    );
    revalidatePath("/attrition");
    return { error: null, message: `Trained on ${result.rows} records.`, result };
  } catch (error) {
    return { error: describeError(error), message: null, result: null };
  }
}
