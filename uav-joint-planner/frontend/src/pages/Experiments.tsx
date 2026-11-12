import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "@/api/client";
import { BoxPlot, ParetoScatter } from "@/components/charts";
import type { BoxStat } from "@/components/charts";
import { Button, Card, Empty, Field, inputClass, Spinner, Tabs } from "@/components/ui";
import { ALGORITHM_ORDER } from "@/lib/colors";
import { fmt } from "@/lib/format";
import { reportError } from "@/store/ui";

const SCENARIOS = ["A", "B", "C", "D", "E", "F", "G"];
const METRICS: Record<string, { label: string; unit: string }> = {
  sim_completion_rate: { label: "Tasks completed", unit: "%" },
  sim_completion_rate_mandatory: { label: "Mandatory tasks completed", unit: "%" },
  sim_energy_per_completed_task: { label: "Energy per completed task", unit: "EU" },
  sim_makespan: { label: "Mission time", unit: "s" },
  sim_workload_cv: { label: "Workload imbalance", unit: "CV" },
  plan_objective_z: { label: "Objective Z", unit: "" },
};
type Row = Record<string, string>;
type View = "summary" | "significance" | "pareto" | "figures" | "report";

function Results({ id, labels }: { id: string; labels: (name: string) => string }) {
  const [scenario, setScenario] = useState("A");
  const [metric, setMetric] = useState("sim_completion_rate");
  const [view, setView] = useState<View>("summary");
  const query = useQuery({ queryKey: ["experiment", id], queryFn: () => api.experimentResults(id) });
  if (query.isLoading) return <Spinner label="Loading results" />;
  const data = query.data;
  if (!data) return <Empty>Results are not available.</Empty>;
  const summary = data.summary as Row[];
  const stats = data.stats as Row[];
  const present = [...new Set(summary.map((r) => r.scenario))].filter((s) => s.length === 1);
  const current = present.includes(scenario) ? scenario : (present[0] ?? "A");
  const rows = summary
    .filter((r) => r.scenario === current && r.metric === metric && r.variant === "full" && Number(r.noise) === 0 && r.mean !== "")
    .sort((a, b) => ALGORITHM_ORDER.indexOf(a.algorithm) - ALGORITHM_ORDER.indexOf(b.algorithm));
  const boxes: BoxStat[] = ALGORITHM_ORDER.flatMap((algorithm) => {
    const row = rows.find((r) => r.algorithm === algorithm);
    if (!row || row.q1 === "" || row.q1 === "nan") return [];
    return [
      {
        algorithm,
        label: labels(algorithm),
        q1: Number(row.q1),
        median: Number(row.median),
        q3: Number(row.q3),
        low: Number(row.ci_low),
        high: Number(row.ci_high),
      },
    ];
  });
  const tests = stats.filter((r) => r.scenario === current && r.metric === metric);
  const shares = [...new Set((data.pareto as Row[]).map((r) => r.energy_share))];
  const pareto = shares.map((share) => {
    const group = (data.pareto as Row[]).filter((r) => r.energy_share === share);
    const mean = (name: string) => group.reduce((sum, r) => sum + Number(r[name]), 0) / group.length;
    return { share: Number(share), energy: mean("total_energy"), makespan: mean("makespan") };
  });

  return (
    <Card
      title="Results"
      actions={
        <a className="text-sm underline" href={`/api/experiments/${id}/download`} download>
          Download all (CSV, figures, REPORT.md)
        </a>
      }
    >
      <Tabs
        value={view}
        onChange={setView}
        options={[
          { value: "summary", label: "Summary" },
          { value: "significance", label: "Significance" },
          { value: "pareto", label: "Pareto" },
          { value: "figures", label: "Figures" },
          { value: "report", label: "Report" },
        ]}
      />
      {(view === "summary" || view === "significance") && (
        <div className="mt-3 flex flex-wrap gap-3">
          <Field label="Scenario">
            <select className={inputClass} value={current} onChange={(e) => setScenario(e.target.value)}>
              {present.map((s) => (
                <option key={s}>{s}</option>
              ))}
            </select>
          </Field>
          <Field label="Metric">
            <select className={inputClass} value={metric} onChange={(e) => setMetric(e.target.value)}>
              {Object.entries(METRICS).map(([key, info]) => (
                <option key={key} value={key}>
                  {info.label}
                </option>
              ))}
            </select>
          </Field>
        </div>
      )}
      <div className="mt-4">
        {view === "summary" && (
          <div className="space-y-4">
            <BoxPlot stats={boxes} unit={METRICS[metric].unit} />
            <div className="overflow-x-auto">
              <table className="w-full text-left text-xs" data-testid="summary-table">
                <thead className="text-stone-500 dark:text-stone-400">
                  <tr>
                    {["Algorithm", "n", "Mean", "Std", "Median", "95% CI"].map((h) => (
                      <th key={h} className="py-1 pr-3 font-medium">
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r) => (
                    <tr key={r.algorithm} className="border-t border-stone-100 tabular-nums dark:border-stone-800">
                      <td className="py-1 pr-3">{labels(r.algorithm)}</td>
                      <td className="pr-3">{fmt(Number(r.n), 0)}</td>
                      <td className="pr-3">{fmt(Number(r.mean), 2)}</td>
                      <td className="pr-3">{fmt(Number(r.std), 2)}</td>
                      <td className="pr-3">{fmt(Number(r.median), 2)}</td>
                      <td>
                        {fmt(Number(r.ci_low), 2)} – {fmt(Number(r.ci_high), 2)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
        {view === "significance" &&
          (tests.length ? (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-xs" data-testid="stats-table">
                <thead className="text-stone-500 dark:text-stone-400">
                  <tr>
                    {["Proposed vs", "Win/tie/loss", "p (Holm)", "Effect", "Significant"].map((h) => (
                      <th key={h} className="py-1 pr-3 font-medium">
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {tests.map((t) => (
                    <tr key={t.baseline} className="border-t border-stone-100 tabular-nums dark:border-stone-800">
                      <td className="py-1 pr-3">{t.baseline.includes(":") ? t.baseline.split(":")[1].replaceAll("_", " ") : labels(t.baseline)}</td>
                      <td className="pr-3">
                        {t.wins}/{t.ties}/{t.losses}
                      </td>
                      <td className="pr-3">{fmt(Number(t.p_holm), 4)}</td>
                      <td className="pr-3">{fmt(Number(t.effect_size), 2)}</td>
                      <td>{t.significant === "True" ? "yes" : "no"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <Empty>Significance tests need the proposed method and a baseline in this scenario.</Empty>
          ))}
        {view === "pareto" && (pareto.length ? <ParetoScatter points={pareto} /> : <Empty>No weight sweep in this run.</Empty>)}
        {view === "figures" && (
          <div className="grid gap-3 md:grid-cols-2">
            {data.figures.map((name) => (
              <a key={name} href={`/api/experiments/${id}/figures/${name}`} target="_blank" rel="noreferrer">
                <img src={`/api/experiments/${id}/figures/${name}`} alt={name.replace(".png", "").replaceAll("_", " ")} className="w-full rounded border border-stone-200 dark:border-stone-800" loading="lazy" />
              </a>
            ))}
          </div>
        )}
        {view === "report" && <pre className="max-h-[60vh] overflow-auto whitespace-pre-wrap text-xs">{data.report}</pre>}
      </div>
    </Card>
  );
}

export default function Experiments() {
  const queryClient = useQueryClient();
  const algorithms = useQuery({ queryKey: ["algorithms"], queryFn: api.algorithms });
  const history = useQuery({ queryKey: ["experiments"], queryFn: api.experiments });
  const [scenarios, setScenarios] = useState(["A", "B", "C"]);
  const [chosen, setChosen] = useState(["nearest", "greedy_energy", "independent", "integrated"]);
  const [seeds, setSeeds] = useState(5);
  const [ablations, setAblations] = useState(false);
  const [jobId, setJobId] = useState<string | null>(null);
  const [resultId, setResultId] = useState<string | null>(null);

  const labels = (name: string) => algorithms.data?.find((a) => a.name === name)?.label ?? name;
  const job = useQuery({
    queryKey: ["job", jobId],
    queryFn: () => api.job(jobId as string),
    enabled: !!jobId,
    refetchInterval: (q) => {
      const status = q.state.data?.status;
      if (status === "finished" || status === "cancelled" || status === "failed") {
        if (status === "finished" && resultId !== jobId) setResultId(jobId);
        queryClient.invalidateQueries({ queryKey: ["experiments"] });
        return false;
      }
      return 1000;
    },
  });
  const launch = useMutation({
    mutationFn: () => api.startExperiment({ scenarios, algorithms: chosen, seeds, ablations }),
    onSuccess: ({ job_id }) => {
      setResultId(null);
      setJobId(job_id);
    },
    onError: reportError,
  });
  const cancel = useMutation({ mutationFn: () => api.cancelJob(jobId as string), onError: reportError });

  const toggle = (list: string[], value: string, on: boolean) => (on ? [...list, value] : list.filter((x) => x !== value));
  const info = job.data;
  const running = info && (info.status === "queued" || info.status === "running");
  const percent = info?.total ? Math.round((info.done / info.total) * 100) : 0;

  return (
    <div className="space-y-4">
      <div className="grid gap-4 md:grid-cols-[1fr_300px]">
        <Card title="Set up a batch">
          <p className="mb-1 text-xs font-medium">Scenarios</p>
          <div className="flex flex-wrap gap-3 text-sm">
            {SCENARIOS.map((s) => (
              <label key={s} className="flex items-center gap-1">
                <input type="checkbox" checked={scenarios.includes(s)} onChange={(e) => setScenarios(toggle(scenarios, s, e.target.checked))} />
                {s}
              </label>
            ))}
          </div>
          <p className="mb-1 mt-3 text-xs font-medium">Algorithms</p>
          <div className="flex flex-wrap gap-3 text-sm">
            {algorithms.data?.map((a) => (
              <label key={a.name} className="flex items-center gap-1" title={a.description}>
                <input type="checkbox" checked={chosen.includes(a.name)} onChange={(e) => setChosen(toggle(chosen, a.name, e.target.checked))} />
                {a.label}
              </label>
            ))}
          </div>
          <div className="mt-3 flex flex-wrap items-end gap-4">
            <Field label="Seeds" hint="At least 6 are needed for p < 0.05 to be reachable">
              <input type="number" className={inputClass} min={1} max={100} value={seeds} onChange={(e) => setSeeds(Number(e.target.value))} />
            </Field>
            <label className="flex items-center gap-1 pb-1 text-sm">
              <input type="checkbox" checked={ablations} onChange={(e) => setAblations(e.target.checked)} /> Include ablations
            </label>
            <Button variant="primary" disabled={launch.isPending || !!running || !scenarios.length || !chosen.length} onClick={() => launch.mutate()}>
              Launch
            </Button>
          </div>
          {info && (
            <div className="mt-4" data-testid="experiment-progress">
              <div className="mb-1 flex justify-between text-xs">
                <span>
                  {info.status}
                  {info.message && info.message !== info.status ? ` · ${info.message}` : ""}
                  {info.error ? ` · ${info.error}` : ""}
                </span>
                <span className="tabular-nums">
                  {info.done}/{info.total} runs
                </span>
              </div>
              <div className="h-2 overflow-hidden rounded bg-stone-100 dark:bg-stone-800">
                <div className="h-full bg-blue-600 transition-all" style={{ width: `${percent}%` }} />
              </div>
              {running && (
                <Button className="mt-2" onClick={() => cancel.mutate()}>
                  Cancel
                </Button>
              )}
            </div>
          )}
        </Card>
        <Card title="Previous runs">
          {!history.data?.length ? (
            <Empty>No experiments yet.</Empty>
          ) : (
            <ul className="space-y-1 text-xs">
              {history.data.map((e) => (
                <li key={e.id}>
                  <button className="hover:underline" disabled={e.status !== "finished"} onClick={() => setResultId(e.id)}>
                    {new Date(e.created_at).toLocaleString()} · {e.status}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
      {resultId && <Results key={resultId} id={resultId} labels={labels} />}
    </div>
  );
}
