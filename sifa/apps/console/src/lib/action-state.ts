import type { BenchmarkResult, SimulationResult } from "@/lib/types";

export interface ActionState {
  error: string | null;
  message: string | null;
}

export interface SimulationState extends ActionState {
  result: SimulationResult | null;
}

export const idleAction: ActionState = { error: null, message: null };
export const idleSimulation: SimulationState = { error: null, message: null, result: null };

export interface BenchmarkState extends ActionState {
  result: BenchmarkResult | null;
}

export const idleBenchmark: BenchmarkState = { error: null, message: null, result: null };
