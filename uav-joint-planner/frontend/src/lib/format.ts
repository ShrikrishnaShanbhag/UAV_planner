export function fmt(value: number | null | undefined, digits = 1): string {
  return value === null || value === undefined || Number.isNaN(value) ? "n/a" : value.toFixed(digits);
}

export const METRIC_LABELS: Record<string, { label: string; digits: number; higherBetter: boolean }> = {
  completion_rate: { label: "Tasks assigned %", digits: 1, higherBetter: true },
  completion_rate_mandatory: { label: "Mandatory assigned %", digits: 1, higherBetter: true },
  total_energy: { label: "Total energy (EU)", digits: 1, higherBetter: false },
  energy_per_completed_task: { label: "EU per task", digits: 2, higherBetter: false },
  makespan: { label: "Mission time (s)", digits: 0, higherBetter: false },
  total_distance: { label: "Distance (m)", digits: 0, higherBetter: false },
  energy_feasibility_rate: { label: "Energy-feasible routes %", digits: 0, higherBetter: true },
  min_battery_margin: { label: "Min battery margin (EU)", digits: 1, higherBetter: true },
  workload_cv: { label: "Workload imbalance (CV)", digits: 2, higherBetter: false },
  violations: { label: "Violations", digits: 0, higherBetter: false },
  objective_z: { label: "Objective Z", digits: 2, higherBetter: false },
  compute_time: { label: "Compute time (s)", digits: 3, higherBetter: false },
};

export function explainEnergy(available: number, task: number, ret: number, reserve: number) {
  const needed = task + ret + reserve;
  return { needed, margin: available - needed, feasible: needed <= available };
}
