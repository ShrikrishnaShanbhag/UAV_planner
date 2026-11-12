import type { Plan } from "@/api/client";
import { Empty } from "@/components/ui";
import { fmt } from "@/lib/format";

export function ExplainPanel({ plan, taskId }: { plan: Plan; taskId: string | null }) {
  if (!taskId) return <Empty>Click a task on the map to see why it was assigned this way.</Empty>;
  const explanation = plan.explanations.find((e) => e.task_id === taskId);
  if (!explanation) {
    return <Empty>{plan.algorithm} does not record candidate costs. Use the proposed method to explain {taskId}.</Empty>;
  }
  return (
    <div data-testid="explain-panel">
      <p className="mb-2 text-sm">
        <span className="font-semibold">{taskId}</span>:{" "}
        {explanation.chosen_uav ? `assigned to ${explanation.chosen_uav}` : "not assigned"}.{" "}
        <span className="text-stone-500 dark:text-stone-400">{explanation.note}.</span>
      </p>
      <div className="overflow-x-auto">
        <table className="w-full text-left text-xs">
          <thead className="text-stone-500 dark:text-stone-400">
            <tr>
              <th className="py-1 pr-2 font-medium">UAV</th>
              <th className="pr-2 font-medium" title="Incremental distance (m)">ΔD</th>
              <th className="pr-2 font-medium" title="Incremental energy (EU)">ΔE</th>
              <th className="pr-2 font-medium" title="Increase in completion time (s)">ΔT</th>
              <th className="pr-2 font-medium" title="Change in workload imbalance">ΔL</th>
              <th className="pr-2 font-medium" title="Assignment cost; lowest wins">C</th>
              <th className="font-medium">Outcome</th>
            </tr>
          </thead>
          <tbody>
            {explanation.candidates.map((c) => (
              <tr key={c.uav_id} className={c.uav_id === explanation.chosen_uav ? "font-semibold" : ""}>
                <td className="py-1 pr-2">{c.uav_id}</td>
                {c.feasible ? (
                  <>
                    <td className="pr-2 tabular-nums">{fmt(c.delta_distance, 0)}</td>
                    <td className="pr-2 tabular-nums">{fmt(c.delta_energy)}</td>
                    <td className="pr-2 tabular-nums">{fmt(c.delta_time, 0)}</td>
                    <td className="pr-2 tabular-nums">{fmt(c.delta_imbalance, 3)}</td>
                    <td className="pr-2 tabular-nums">{fmt(c.cost, 3)}</td>
                    <td>{c.uav_id === explanation.chosen_uav ? "chosen" : "higher cost"}</td>
                  </>
                ) : (
                  <td colSpan={6} className="text-stone-600 dark:text-stone-300">
                    rejected: {c.rejected_reason}
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
