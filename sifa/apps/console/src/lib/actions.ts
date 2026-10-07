"use server";

import { revalidatePath } from "next/cache";
import { api, describeError } from "@/lib/api";
import type { ActionState, SimulationState } from "@/lib/action-state";
import type { BenchmarkJob, SimulationResult } from "@/lib/types";

export async function promoteCandidate(): Promise<ActionState> {
  try {
    const body = await api.post<{ promoted: string; stage: string }>("/v1/registry/promote");
    revalidatePath("/registry");
    revalidatePath("/");
    return { error: null, message: `${body.promoted} is now in ${body.stage}.` };
  } catch (error) {
    return { error: describeError(error), message: null };
  }
}

export async function advanceCanary(): Promise<ActionState> {
  try {
    const body = await api.post<{ now_live: string }>("/v1/registry/advance");
    revalidatePath("/registry");
    revalidatePath("/");
    return { error: null, message: `${body.now_live} is now live.` };
  } catch (error) {
    return { error: describeError(error), message: null };
  }
}

export async function rollbackServing(): Promise<ActionState> {
  try {
    const body = await api.post<{ rolled_back: string; now_live: string | null }>(
      "/v1/registry/rollback"
    );
    revalidatePath("/registry");
    revalidatePath("/");
    return {
      error: null,
      message: `${body.rolled_back} rolled back, ${body.now_live ?? "nothing"} is live.`
    };
  } catch (error) {
    return { error: describeError(error), message: null };
  }
}

export async function runLoadTest(
  _state: SimulationState,
  form: FormData
): Promise<SimulationState> {
  const requests = Number(form.get("requests") ?? 200);
  try {
    const result = await api.post<SimulationResult>(`/v1/simulate?requests=${requests}`);
    revalidatePath("/");
    return { error: null, message: null, result };
  } catch (error) {
    return { error: describeError(error), message: null, result: null };
  }
}

export async function sendFeedback(
  requestId: string,
  itemId: string,
  clicked: boolean
): Promise<ActionState> {
  try {
    const body = await api.post<{ duplicate: boolean; credited: boolean; outcome_mode: string }>(
      "/v1/feedback",
      { request_id: requestId, item_id: itemId, clicked }
    );
    if (body.duplicate) {
      return { error: null, message: "Already recorded for this request and item; nothing changed." };
    }
    const where =
      body.outcome_mode === "feedback"
        ? body.credited
          ? "It counted as this request's success in the experiment."
          : "The experiment and guard are fed by feedback."
        : "The platform is in simulated mode, so only the exploration bandit learns from it.";
    return { error: null, message: `${clicked ? "Click" : "No click"} recorded. ${where}` };
  } catch (error) {
    return { error: describeError(error), message: null };
  }
}

// The console offers sizes the index can build in tens of seconds. The API accepts far more, but
// building an HNSW graph is worse than quadratic (4,000 vectors is about 25 s, 40,000 is minutes).
const BENCHMARK_SIZES = [1000, 2000, 4000];

export async function startBenchmark(
  corpus: number
): Promise<{ error: string | null; jobId: string | null }> {
  if (!BENCHMARK_SIZES.includes(corpus)) {
    return { error: "Choose one of the listed corpus sizes.", jobId: null };
  }
  try {
    const job = await api.post<{ job_id: string }>(`/v1/retrieval/benchmark?corpus=${corpus}`);
    return { error: null, jobId: job.job_id };
  } catch (error) {
    return { error: describeError(error), jobId: null };
  }
}

export async function pollBenchmark(jobId: string): Promise<BenchmarkJob | { error: string }> {
  try {
    return await api.get<BenchmarkJob>(`/v1/retrieval/benchmark/${jobId}`);
  } catch (error) {
    return { error: describeError(error) };
  }
}
