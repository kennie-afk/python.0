"use server";

import { api, describeError } from "@/lib/api";
import { requireSession } from "@/lib/session";
import { revalidatePath } from "next/cache";
import type {
  EarlyWarningView,
  GapForecastView,
  MobilityMatchView,
  SentimentView,
  SkillHolding,
  VerificationReport,
  WorkforceResult
} from "@/lib/types";

export interface ResultState<T> {
  error: string | null;
  result: T | null;
}

function fail<T>(error: unknown): ResultState<T> {
  return { error: describeError(error), result: null };
}

function text(form: FormData, key: string): string {
  return String(form.get(key) ?? "").trim();
}

/** "1, 2.5  3" -> [1, 2.5, 3]; anything that is not a number is an error, not a silent zero. */
function numbers(raw: string, label: string): number[] {
  const parts = raw.split(/[\s,;]+/).filter(Boolean);
  const values = parts.map(Number);
  if (values.some((value) => Number.isNaN(value))) {
    throw new Error(`${label} must be numbers separated by commas or spaces.`);
  }
  return values;
}

function words(raw: string): string[] {
  return raw.split(/[\n,;]+/).map((part) => part.trim()).filter(Boolean);
}

/** "python=6, k8s=2" -> { python: 6, k8s: 2 } */
function years(raw: string): Record<string, number> {
  const out: Record<string, number> = {};
  for (const part of words(raw)) {
    const [name, value] = part.split("=").map((piece) => piece.trim());
    const parsed = Number(value);
    if (!name || Number.isNaN(parsed)) {
      throw new Error(`"${part}" is not skill=years, for example python=6.`);
    }
    out[name] = parsed;
  }
  return out;
}

/** "group | response" per line. */
function responses(raw: string, label: string): { group: string; text: string }[] {
  return raw
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const cut = line.indexOf("|");
      if (cut < 1) {
        throw new Error(`${label}: each line must look like "group | what they said".`);
      }
      return { group: line.slice(0, cut).trim(), text: line.slice(cut + 1).trim() };
    });
}

// ---- verification ---------------------------------------------------------------------------
export async function runDriftCheck(
  _state: ResultState<VerificationReport>,
  form: FormData
): Promise<ResultState<VerificationReport>> {
  const session = await requireSession();
  try {
    const kind = text(form, "kind") || "psi";
    const baseline = kind === "categorical" ? words(text(form, "baseline")) : numbers(text(form, "baseline"), "Baseline");
    const candidate = kind === "categorical" ? words(text(form, "candidate")) : numbers(text(form, "candidate"), "Candidate");
    const result = await api.post<VerificationReport>(
      "/v1/verification/drift",
      {
        label: text(form, "label") || "Feature drift",
        features: [{ name: text(form, "feature") || "feature", kind, baseline, candidate }]
      },
      session.token
    );
    revalidatePath("/verification");
    return { error: null, result };
  } catch (error) {
    return fail(error);
  }
}

export async function runDeterminismCheck(
  _state: ResultState<VerificationReport>,
  form: FormData
): Promise<ResultState<VerificationReport>> {
  const session = await requireSession();
  try {
    let record: unknown;
    try {
      record = JSON.parse(text(form, "record"));
    } catch {
      throw new Error("The candidate record has to be valid JSON.");
    }
    const result = await api.post<VerificationReport>(
      "/v1/verification/determinism",
      {
        label: text(form, "label") || "Screening determinism",
        repetitions: Number(text(form, "repetitions") || 10),
        cases: [{ name: "case", record, requirement: text(form, "requirement") }]
      },
      session.token
    );
    revalidatePath("/verification");
    return { error: null, result };
  } catch (error) {
    return fail(error);
  }
}

// ---- skills ---------------------------------------------------------------------------------
export async function extractSkills(
  _state: ResultState<{ subject_key: string; holdings: SkillHolding[] }>,
  form: FormData
): Promise<ResultState<{ subject_key: string; holdings: SkillHolding[] }>> {
  const session = await requireSession();
  try {
    const result = await api.post<{ subject_key: string; holdings: SkillHolding[] }>(
      "/v1/skills/extract",
      {
        subject_key: text(form, "subject_key") || "employee",
        evidence: text(form, "evidence").split(/\n{2,}/).map((p) => p.trim()).filter(Boolean),
        years_by_skill: years(text(form, "years"))
      },
      session.token
    );
    return { error: null, result };
  } catch (error) {
    return fail(error);
  }
}

export async function forecastGaps(
  _state: ResultState<GapForecastView>,
  form: FormData
): Promise<ResultState<GapForecastView>> {
  const session = await requireSession();
  try {
    const requirements = words(text(form, "requirements")).map((line) => {
      const [skill, required, headcount] = line.split(":").map((piece) => piece.trim());
      return { skill, required: (required || "WORKING").toUpperCase(), headcount: Number(headcount || 1) };
    });
    const people = text(form, "people")
      .split(/\n{2,}/)
      .map((block, index) => ({
        subject_key: `person-${index + 1}`,
        evidence: [block.trim()],
        years_by_skill: {}
      }))
      .filter((person) => person.evidence[0]);
    const result = await api.post<GapForecastView>(
      "/v1/skills/gaps",
      {
        profiles: people,
        requirements,
        horizon_months: Number(text(form, "horizon") || 12),
        attrition_rate: Number(text(form, "attrition") || 0) / 100
      },
      session.token
    );
    return { error: null, result };
  } catch (error) {
    return fail(error);
  }
}

export async function matchRoles(
  _state: ResultState<MobilityMatchView[]>,
  form: FormData
): Promise<ResultState<MobilityMatchView[]>> {
  const session = await requireSession();
  try {
    const requirements = words(text(form, "requirements")).map((line) => {
      const [skill, required] = line.split(":").map((piece) => piece.trim());
      return { skill, required: (required || "WORKING").toUpperCase(), headcount: 1 };
    });
    const result = await api.post<MobilityMatchView[]>(
      "/v1/skills/mobility",
      {
        profile: {
          subject_key: text(form, "subject_key") || "employee",
          evidence: [text(form, "evidence")],
          years_by_skill: years(text(form, "years"))
        },
        roles: [{ role_id: "target", title: text(form, "title") || "Target role", requirements }],
        minimum_score: 0.3
      },
      session.token
    );
    return { error: null, result };
  } catch (error) {
    return fail(error);
  }
}

// ---- workforce ------------------------------------------------------------------------------
export async function simulateWorkforce(
  _state: ResultState<WorkforceResult>,
  form: FormData
): Promise<ResultState<WorkforceResult>> {
  const session = await requireSession();
  try {
    const base = {
      starting_headcount: Number(text(form, "headcount")),
      monthly_attrition_rate: Number(text(form, "attrition")) / 100,
      hire_ramp_months: Number(text(form, "ramp") || 3),
      monthly_demand: Number(text(form, "demand") || 0)
    };
    const target = text(form, "target");
    const result = await api.post<WorkforceResult>(
      "/v1/workforce/simulate",
      {
        scenarios: [
          { name: "Planned hiring", ...base, monthly_hires: Number(text(form, "hires") || 0) },
          { name: "Hiring freeze", ...base, monthly_hires: 0 }
        ],
        months: Number(text(form, "months") || 12),
        target_headcount: target ? Number(target) : null
      },
      session.token
    );
    return { error: null, result };
  } catch (error) {
    return fail(error);
  }
}

// ---- sentiment ------------------------------------------------------------------------------
export async function analyseSentiment(
  _state: ResultState<SentimentView>,
  form: FormData
): Promise<ResultState<SentimentView>> {
  const session = await requireSession();
  try {
    const result = await api.post<SentimentView>(
      "/v1/sentiment/analyse",
      {
        responses: responses(text(form, "responses"), "Responses"),
        minimum_group_size: Number(text(form, "minimum") || 5)
      },
      session.token
    );
    return { error: null, result };
  } catch (error) {
    return fail(error);
  }
}

export async function detectEarlyWarning(
  _state: ResultState<EarlyWarningView>,
  form: FormData
): Promise<ResultState<EarlyWarningView>> {
  const session = await requireSession();
  try {
    const result = await api.post<EarlyWarningView>(
      "/v1/sentiment/early-warning",
      {
        label: text(form, "label") || "Sentiment early warning",
        previous: responses(text(form, "previous"), "Earlier period"),
        current: responses(text(form, "current"), "Latest period"),
        minimum_group_size: Number(text(form, "minimum") || 5)
      },
      session.token
    );
    revalidatePath("/ledger");
    return { error: null, result };
  } catch (error) {
    return fail(error);
  }
}

// ---- model governance -----------------------------------------------------------------------
export async function activateModelVersion(version: number): Promise<{ error: string | null }> {
  const session = await requireSession();
  try {
    await api.post(`/v1/attrition/models/${version}/activate`, {}, session.token);
    revalidatePath("/attrition");
    return { error: null };
  } catch (error) {
    return { error: describeError(error) };
  }
}

export async function rollbackModelVersion(): Promise<{ error: string | null }> {
  const session = await requireSession();
  try {
    await api.post("/v1/attrition/models/rollback", {}, session.token);
    revalidatePath("/attrition");
    return { error: null };
  } catch (error) {
    return { error: describeError(error) };
  }
}

