import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "@/api/client";
import type { Metrics } from "@/api/client";
import { MetricBar } from "@/components/charts";
import { MapCanvas } from "@/components/map/MapCanvas";
import { WeightSliders } from "@/components/panels/WeightSliders";
import { Button, Card, Empty } from "@/components/ui";
import { ALGORITHM_ORDER } from "@/lib/colors";
import { fmt, METRIC_LABELS } from "@/lib/format";
import { useMission } from "@/store/mission";
import { reportError } from "@/store/ui";

const CHARTS = ["completion_rate", "energy_per_completed_task", "makespan"];

export default function Compare() {
  const { scenario, weights, reservePercent, setWeights, setReserve } = useMission();
  const algorithms = useQuery({ queryKey: ["algorithms"], queryFn: api.algorithms });
  const [chosen, setChosen] = useState(["nearest", "greedy_energy", "independent", "integrated"]);
  const compare = useMutation({
    mutationFn: () => {
      const ordered = ALGORITHM_ORDER.filter((a) => chosen.includes(a));
      return api.compare(scenario, ordered, weights, reservePercent / 100);
    },
    onError: reportError,
  });

  if (!scenario.uavs.length) {
    return (
      <Card>
        <Empty>
          No scenario loaded. Start from the <Link className="underline" to="/">Dashboard</Link>.
        </Empty>
      </Card>
    );
  }

  const label = (name: string) => algorithms.data?.find((a) => a.name === name)?.label ?? name;
  const results = compare.data?.results.filter((r) => r.plan) ?? [];
  const failures = compare.data?.results.filter((r) => r.error) ?? [];
  const metric = (entry: (typeof results)[number], name: string) => (entry.metrics as Metrics)[name] ?? null;
  const bestOf = (name: string) => {
    const values = results
      .filter((r) => r.plan?.report.feasible)
      .map((r) => metric(r, name))
      .filter((v): v is number => v !== null);
    if (!values.length) return null;
    return METRIC_LABELS[name].higherBetter ? Math.max(...values) : Math.min(...values);
  };

  return (
    <div className="space-y-4">
      <div className="grid gap-4 md:grid-cols-[1fr_300px]">
        <Card title="Algorithms">
          <div className="flex flex-wrap gap-3 text-sm">
            {algorithms.data?.map((a) => (
              <label key={a.name} className="flex items-center gap-1" title={a.description}>
                <input
                  type="checkbox"
                  checked={chosen.includes(a.name)}
                  onChange={(e) => setChosen(e.target.checked ? [...chosen, a.name] : chosen.filter((x) => x !== a.name))}
                />
                {a.label}
              </label>
            ))}
          </div>
          <Button variant="primary" className="mt-3" disabled={compare.isPending || !chosen.length} onClick={() => compare.mutate()} data-testid="run-compare">
            {compare.isPending ? "Running…" : "Compare"}
          </Button>
          {failures.map((f) => (
            <p key={f.algorithm} className="mt-2 text-xs text-red-700 dark:text-red-400">
              {label(f.algorithm)}: {f.error}
            </p>
          ))}
        </Card>
        <Card title="Weights (applied to all)">
          <WeightSliders weights={weights} reserve={reservePercent} onWeights={setWeights} onReserve={setReserve} />
        </Card>
      </div>
      {!results.length ? (
        <Card>
          <Empty>Choose algorithms and run a comparison.</Empty>
        </Card>
      ) : (
        <>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
            {results.map((r) => (
              <Card key={r.algorithm} title={label(r.algorithm)}>
                <MapCanvas scenario={scenario} routes={r.plan?.routes} label={`Routes from ${label(r.algorithm)}`} />
                <p className="mt-2 text-xs">
                  {r.plan?.report.feasible
                    ? "Feasible"
                    : `⚠ ${r.plan?.report.violations.length} violation${r.plan?.report.violations.length === 1 ? "" : "s"}`}
                </p>
              </Card>
            ))}
          </div>
          <Card title="Metrics">
            <div className="overflow-x-auto">
              <table className="w-full text-left text-xs" data-testid="compare-table">
                <thead className="text-stone-500 dark:text-stone-400">
                  <tr>
                    <th className="py-1 pr-3 font-medium">Metric</th>
                    {results.map((r) => (
                      <th key={r.algorithm} className="pr-3 font-medium">
                        {label(r.algorithm)}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(METRIC_LABELS).map(([name, info]) => {
                    const best = bestOf(name);
                    return (
                      <tr key={name} className="border-t border-stone-100 dark:border-stone-800">
                        <td className="py-1 pr-3">{info.label}</td>
                        {results.map((r) => {
                          const value = metric(r, name);
                          const isBest = best !== null && value === best && r.plan?.report.feasible;
                          return (
                            <td key={r.algorithm} className={`pr-3 tabular-nums ${isBest ? "font-bold" : ""}`}>
                              {fmt(value, info.digits)}
                              {isBest ? " ★" : ""}
                            </td>
                          );
                        })}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <p className="mt-2 text-xs text-stone-500 dark:text-stone-400">★ marks the best value among feasible plans.</p>
          </Card>
          <div className="grid gap-3 md:grid-cols-3">
            {CHARTS.map((name) => (
              <Card key={name} title={METRIC_LABELS[name].label}>
                <MetricBar
                  unit={METRIC_LABELS[name].label}
                  data={results.map((r) => ({ algorithm: r.algorithm, label: label(r.algorithm).split(" ")[0], value: metric(r, name) }))}
                />
              </Card>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
