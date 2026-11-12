import type { Plan, Violation } from "@/api/client";
import { fmt } from "@/lib/format";

export function FeasibilityPanel({ plan, onSelect }: { plan: Plan; onSelect?: (violation: Violation) => void }) {
  const { report } = plan;
  if (report.feasible) {
    return (
      <p className="flex items-center gap-2 text-sm font-medium text-green-800 dark:text-green-400" data-testid="feasibility-ok">
        <span aria-hidden>✓</span> Every route satisfies all hard constraints.
      </p>
    );
  }
  return (
    <ul className="space-y-2 text-xs" data-testid="violations">
      <li className="flex items-center gap-2 text-sm font-medium text-red-700 dark:text-red-400">
        <span aria-hidden>⚠</span> {report.violations.length} violation{report.violations.length === 1 ? "" : "s"}
      </li>
      {report.violations.map((v, index) => (
        <li key={index}>
          <button className="w-full rounded p-1 text-left hover:bg-stone-100 dark:hover:bg-stone-800" onClick={() => onSelect?.(v)}>
            <span className="font-semibold">{v.type}</span> · {v.uav_id}
            {v.task_id ? ` · ${v.task_id}` : ""}
            <span className="block text-stone-600 dark:text-stone-300">
              {v.detail}
              {v.margin ? ` (margin ${fmt(v.margin)})` : ""}
            </span>
          </button>
        </li>
      ))}
    </ul>
  );
}
