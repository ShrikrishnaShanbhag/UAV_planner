import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "@/api/client";
import type { Metrics, Plan, PlanResult, Violation } from "@/api/client";
import { EnergyBreakdownChart } from "@/components/charts";
import { MapCanvas } from "@/components/map/MapCanvas";
import { FeasibilityPanel } from "@/components/panels/FeasibilityPanel";
import { UnassignedList } from "@/components/panels/TaskTable";
import { RouteList, UAVPanel } from "@/components/panels/UAVPanel";
import { WeightSliders } from "@/components/panels/WeightSliders";
import { Button, Card, Empty, Tabs } from "@/components/ui";
import { fmt } from "@/lib/format";
import { energyBreakdown, planLegs, routesWithPaths } from "@/lib/scenario";
import { planningInputs, useMission } from "@/store/mission";
import { reportError } from "@/store/ui";

async function waitForJob(jobId: string): Promise<PlanResult> {
  for (;;) {
    const job = await api.job(jobId);
    if (job.status === "finished") return job.result as PlanResult;
    if (job.status === "failed" || job.status === "cancelled") {
      throw new Error(job.error ?? `Planning job ${job.status}.`);
    }
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
}

function ObjectiveBreakdown({ plan, metrics }: { plan: Plan; metrics: Metrics | null }) {
  const o = plan.objective;
  const rows: [string, string][] = [
    ["Objective Z", fmt(o.z, 3)],
    ["Energy term", fmt(o.energy_term, 3)],
    ["Time term", fmt(o.time_term, 3)],
    ["Distance term", fmt(o.distance_term, 3)],
    ["Balance term", fmt(o.balance_term, 3)],
    ["Unassigned penalty", fmt(o.unassigned_penalty, 1)],
    ["Total energy", `${fmt(o.total_energy)} EU`],
    ["Mission time", `${fmt(o.makespan, 0)} s`],
    ["Distance", `${fmt(o.total_distance, 0)} m`],
    ["Tasks assigned", `${fmt(metrics?.completion_rate ?? null, 0)}%`],
    ["Compute time", `${fmt(plan.compute_time_s * 1000, 0)} ms`],
  ];
  return (
    <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs" data-testid="objective">
      {rows.map(([label, value]) => (
        <div key={label} className="flex justify-between gap-2">
          <dt className="text-stone-500 dark:text-stone-400">{label}</dt>
          <dd className="tabular-nums">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

export default function Planner() {
  const mission = useMission();
  const { scenario, plan, metrics, weights, reservePercent, allowRecharge, adaptiveReserve } = mission;
  const [pathView, setPathView] = useState<"energy" | "distance">("energy");
  const [selectedTask, setSelectedTask] = useState<string | null>(null);
  const [highlightUav, setHighlightUav] = useState<string | null>(null);

  const run = useMutation({
    mutationFn: async () => {
      const inputs = planningInputs(useMission.getState());
      const result = await api.plan(inputs.scenario, inputs.options);
      return result.job_id ? waitForJob(result.job_id) : result;
    },
    onSuccess: (result) => {
      mission.setPlan(result.plan ?? null, (result.metrics as Metrics) ?? null);
      setSelectedTask(null);
    },
    onError: reportError,
  });

  const paths = useQuery({
    queryKey: ["paths", plan?.compute_time_s],
    queryFn: () => api.comparePaths(scenario, planLegs(plan!.routes)),
    enabled: !!plan,
  });
  const shownRoutes = plan && paths.data ? routesWithPaths(plan.routes, paths.data.legs, pathView) : plan?.routes;

  const onViolation = (violation: Violation) => {
    setHighlightUav(violation.uav_id);
    setSelectedTask(violation.task_id ?? null);
  };

  if (!scenario.uavs.length) {
    return (
      <Card>
        <Empty>
          No scenario loaded. Start from the <Link className="underline" to="/">Dashboard</Link> or the{" "}
          <Link className="underline" to="/builder">Scenario Builder</Link>.
        </Empty>
      </Card>
    );
  }

  return (
    <div className="grid gap-4 lg:grid-cols-[300px_1fr_340px]">
      <div className="space-y-3">
        <Card title="Energy settings">
          <WeightSliders energyOnly weights={weights} reserve={reservePercent} onWeights={mission.setWeights} onReserve={mission.setReserve} />
          <label className="mt-3 flex items-center gap-2 text-xs" title="Try a charging stop before dropping a task">
            <input type="checkbox" checked={allowRecharge} onChange={(e) => mission.setAllowRecharge(e.target.checked)} data-testid="allow-recharge" />
            Allow charging stops
          </label>
          <label className="mt-1 flex items-center gap-2 text-xs" title="Hold back more energy while more of the route is still ahead">
            <input type="checkbox" checked={adaptiveReserve} onChange={(e) => mission.setAdaptiveReserve(e.target.checked)} data-testid="adaptive-reserve" />
            Adaptive reserve
          </label>
          <Button variant="primary" className="mt-3 w-full" disabled={run.isPending} onClick={() => run.mutate()} data-testid="run-plan">
            {run.isPending ? "Planning…" : "Run planner"}
          </Button>
        </Card>
        {plan && (
          <Card title="Objective">
            <ObjectiveBreakdown plan={plan} metrics={metrics} />
          </Card>
        )}
      </div>
      <div className="space-y-3">
        <Card>
          <MapCanvas
            scenario={scenario}
            routes={shownRoutes}
            pan
            highlightTask={selectedTask}
            highlightUav={highlightUav}
            label="Planned routes"
            onTask={setSelectedTask}
          />
          <p className="mt-2 text-xs text-stone-500 dark:text-stone-400">
            Numbers show visit order on each route. Hollow tasks are unassigned. Faded lines are return legs.
          </p>
          {plan && paths.data && (
            <div className="mt-3 flex flex-wrap items-center gap-3">
              <Tabs
                value={pathView}
                onChange={setPathView}
                options={[
                  { value: "energy", label: "Energy-optimal paths" },
                  { value: "distance", label: "Shortest-distance paths" },
                ]}
              />
              <p className="text-xs text-stone-600 dark:text-stone-300" data-testid="path-summary">
                Energy-optimal: {fmt(paths.data.energy_optimal.energy)} EU over {fmt(paths.data.energy_optimal.distance, 0)} m. Shortest-distance:{" "}
                {fmt(paths.data.distance_optimal.energy)} EU over {fmt(paths.data.distance_optimal.distance, 0)} m (travel only).
              </p>
            </div>
          )}
        </Card>
      </div>
      <div className="space-y-3">
        <Card title="Battery use">
          <UAVPanel scenario={scenario} plan={plan} />
        </Card>
        {plan && (
          <>
            <Card title="Energy breakdown">
              <EnergyBreakdownChart rows={plan.routes.map(energyBreakdown)} />
            </Card>
            <Card title="Routes">
              <RouteList plan={plan} />
            </Card>
            <Card title="Feasibility">
              <FeasibilityPanel plan={plan} onSelect={onViolation} />
            </Card>
            <Card title={`Unassigned (${plan.unassigned.length})`}>
              <UnassignedList scenario={scenario} plan={plan} onSelect={setSelectedTask} />
            </Card>
          </>
        )}
      </div>
    </div>
  );
}
