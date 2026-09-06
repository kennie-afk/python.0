"use server";

import { revalidatePath } from "next/cache";
import { api, describeError } from "@/lib/api";
import type { ActionState, SimulationState } from "@/lib/action-state";
import type { SimulationResult } from "@/lib/types";

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
