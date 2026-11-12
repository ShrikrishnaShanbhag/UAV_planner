import { useMutation, useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { api } from "@/api/client";
import { Button, Card, Empty, Spinner } from "@/components/ui";
import { fmt } from "@/lib/format";
import { useMission } from "@/store/mission";
import { reportError } from "@/store/ui";

export default function Dashboard() {
  const navigate = useNavigate();
  const setScenario = useMission((s) => s.setScenario);
  const scenarios = useQuery({ queryKey: ["scenarios"], queryFn: api.scenarios });
  const runs = useQuery({ queryKey: ["runs"], queryFn: api.runs });

  const loadDemo = useMutation({
    mutationFn: () => api.generate({ preset: "DEMO", save: true }),
    onSuccess: (scenario) => {
      setScenario(scenario, scenario.id);
      navigate("/planner");
    },
    onError: reportError,
  });
  const openScenario = useMutation({
    mutationFn: api.scenario,
    onSuccess: (scenario) => {
      setScenario(scenario, scenario.id);
      navigate("/planner");
    },
    onError: reportError,
  });

  return (
    <div className="space-y-4">
      <Card>
        <h1 className="text-xl font-semibold">Optimizing Routing, Task Allocation, and Energy Management in Collaborative UAV Systems</h1>
        <p className="mt-1 max-w-3xl text-sm font-medium text-stone-700 dark:text-stone-200" data-testid="hero-subtitle">
          Energy-aware route planning, battery-feasibility checks and dynamic energy replanning for multi-UAV missions (simulation).
        </p>
        <p className="mt-2 max-w-3xl text-sm text-stone-600 dark:text-stone-300">
          Several UAVs with limited batteries must complete tasks spread across a map. Every route is checked against the
          UAV's battery, including the trip home and a safety reserve. When the mission changes or a battery runs low, the
          affected routes are repaired first and the scope widens only when that is not enough.
        </p>
        <p className="mt-2 max-w-3xl text-sm text-stone-600 dark:text-stone-300">
          The research question is <em>whether</em> this energy-aware planning helps. The Experiments page reports where it does and where it does not.
        </p>
        <div className="mt-4 flex flex-wrap gap-2">
          <Button variant="primary" onClick={() => loadDemo.mutate()} disabled={loadDemo.isPending} data-testid="load-demo">
            {loadDemo.isPending ? "Loading…" : "Load earthquake demo"}
          </Button>
          <Button onClick={() => navigate("/builder")}>Build a scenario</Button>
          <Button onClick={() => navigate("/model")}>How the model works</Button>
        </div>
      </Card>
      <div className="grid gap-4 md:grid-cols-2">
        <Card title="Recent scenarios">
          {scenarios.isLoading ? (
            <Spinner />
          ) : !scenarios.data?.length ? (
            <Empty>No saved scenarios yet.</Empty>
          ) : (
            <ul className="divide-y divide-stone-100 text-sm dark:divide-stone-800">
              {scenarios.data.slice(0, 8).map((s) => (
                <li key={s.id} className="flex items-center justify-between py-1.5">
                  <button className="truncate text-left hover:underline" onClick={() => openScenario.mutate(s.id)}>
                    {s.name}
                  </button>
                  <span className="shrink-0 text-xs text-stone-500 dark:text-stone-400">
                    {s.n_uavs} UAVs · {s.n_tasks} tasks
                  </span>
                </li>
              ))}
            </ul>
          )}
        </Card>
        <Card title="Recent runs">
          {runs.isLoading ? (
            <Spinner />
          ) : !runs.data?.length ? (
            <Empty>No runs yet. Plan or simulate a scenario to see it here.</Empty>
          ) : (
            <ul className="divide-y divide-stone-100 text-sm dark:divide-stone-800">
              {runs.data.map((run) => (
                <li key={run.id} className="flex items-center justify-between gap-2 py-1.5">
                  <span className="truncate">
                    {run.algorithm} · {run.scenario_name}
                  </span>
                  <span className="shrink-0 text-xs text-stone-500 dark:text-stone-400">
                    {run.kind} · {fmt(run.completion_rate, 0)}% tasks · {run.feasible ? "feasible" : "infeasible"}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </div>
  );
}
