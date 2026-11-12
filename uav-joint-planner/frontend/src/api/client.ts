import type { components } from "./schema";

type S = components["schemas"];
export type Scenario = S["Scenario"];
export type UAV = S["UAV"];
export type Task = S["Task"];
export type Station = S["Station"];
export type Obstacle = S["Obstacle"];
export type Plan = S["Plan"];
export type Route = S["Route"];
export type Violation = S["Violation"];
export type TaskExplanation = S["TaskExplanation"];
export type CandidateTrace = S["CandidateTrace"];
export type AlgorithmMeta = S["AlgorithmMeta"];
export type PresetMeta = S["PresetMeta"];
export type GenerateParams = S["GenerateParams"];
export type ScenarioSummary = S["ScenarioSummary"];
export type RunSummary = S["RunSummary"];
export type PlanResult = S["PlanResult"];
export type CompareResult = S["CompareResult"];
export type SimulationState = S["SimulationState"];
export type JobInfo = S["JobInfo"];
export type ExperimentResults = S["ExperimentResults"];
export type ExperimentSummary = S["ExperimentSummary"];
export type CostWeights = S["CostWeights"];
export type PathCompareResult = S["PathCompareResult"];
export type LegRef = S["LegRef"];

export interface UavEnergy {
  uav_id: string;
  planned: number;
  actual: number;
  battery_end: number;
  margin_at_end: number;
  margin_alerts: number;
}
export type Metrics = Record<string, number | null>;
export type Cell = [number, number];

export interface UavFrame {
  id: string;
  x: number;
  y: number;
  battery: number;
  status: string;
}

export interface LogEntry {
  time: number;
  kind: string;
  uav_id?: string | null;
  task_id?: string | null;
  detail?: string;
}

export interface ReplanFrame {
  diff: Record<string, unknown> & {
    event_type: string;
    scope: string;
    escalated: boolean;
    escalation_reason: string | null;
    moved: { task_id: string; from_uav: string; to_uav: string }[];
    added: string[];
    dropped: string[];
  };
  plan: Plan;
  time_ms: number;
}

export interface SimFrame {
  type: "frame";
  t: number;
  uavs: UavFrame[];
  events: LogEntry[];
  end_time: number;
  playing?: boolean;
  seeked?: boolean;
  replan?: ReplanFrame;
}

export class ApiRequestError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly details: unknown,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: init?.body ? { "content-type": "application/json" } : undefined,
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    const error = body?.error;
    throw new ApiRequestError(
      response.status,
      error?.code ?? "request_failed",
      error?.message ?? `Request failed (${response.status})`,
      error?.details ?? null,
    );
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

const post = <T>(path: string, body: unknown) =>
  request<T>(path, { method: "POST", body: JSON.stringify(body) });

export interface PlanOptions {
  algorithm: string;
  weights?: CostWeights;
  reserve_ratio?: number;
  config?: Record<string, unknown>;
}

export const api = {
  algorithms: () => request<AlgorithmMeta[]>("/api/meta/algorithms"),
  presets: () => request<PresetMeta[]>("/api/meta/presets"),
  runs: () => request<RunSummary[]>("/api/runs"),
  scenarios: () => request<ScenarioSummary[]>("/api/scenarios"),
  scenario: (id: string) => request<Scenario>(`/api/scenarios/${id}`),
  generate: (body: { preset?: string; params?: Partial<GenerateParams>; seed?: number; save?: boolean }) =>
    post<Scenario>("/api/scenarios/generate", body),
  createScenario: (scenario: Scenario) => post<Scenario>("/api/scenarios", scenario),
  updateScenario: (scenario: Scenario) =>
    request<Scenario>(`/api/scenarios/${scenario.id}`, { method: "PUT", body: JSON.stringify(scenario) }),
  deleteScenario: (id: string) => request<void>(`/api/scenarios/${id}`, { method: "DELETE" }),
  plan: (scenario: Scenario, options: PlanOptions) => post<PlanResult>("/api/plans", { scenario, ...options }),
  compare: (scenario: Scenario, algorithms: string[], weights?: CostWeights, reserve_ratio?: number) =>
    post<CompareResult>("/api/plans/compare", { scenario, algorithms, weights, reserve_ratio }),
  comparePaths: (scenario: Scenario, legs: LegRef[]) => post<PathCompareResult>("/api/paths/compare", { scenario, legs }),
  feasibility: (scenario: Scenario, assignment: Record<string, string[]>) =>
    post<{ report: Plan["report"] }>("/api/feasibility/check", { scenario, assignment }),
  createSimulation: (
    scenario: Scenario,
    options: PlanOptions & { replan_mode?: string; noise_sigma?: number; seed?: number; early_rth?: boolean },
  ) => post<{ sim_id: string }>("/api/simulations", { scenario, ...options }),
  simulation: (id: string) => request<SimulationState>(`/api/simulations/${id}`),
  timeline: (id: string) =>
    request<{ end_time: number; frames: { t: number; uavs: UavFrame[] }[] }>(
      `/api/simulations/${id}/timeline?max_frames=400`,
    ),
  startExperiment: (body: { scenarios: string[]; algorithms: string[]; seeds: number; ablations: boolean }) =>
    post<{ job_id: string }>("/api/experiments", body),
  experiments: () => request<ExperimentSummary[]>("/api/experiments"),
  experimentResults: (id: string) => request<ExperimentResults>(`/api/experiments/${id}/results`),
  job: (id: string) => request<JobInfo>(`/api/jobs/${id}`),
  cancelJob: (id: string) => request<JobInfo>(`/api/jobs/${id}`, { method: "DELETE" }),
};
