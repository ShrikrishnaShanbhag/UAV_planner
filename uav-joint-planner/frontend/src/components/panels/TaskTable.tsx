import type { Plan, Scenario } from "@/api/client";
import { Empty } from "@/components/ui";
import { PRIORITY_LABEL } from "@/lib/scenario";

export function UnassignedList({ scenario, plan, onSelect }: {
  scenario: Scenario;
  plan: Plan;
  onSelect?: (taskId: string) => void;
}) {
  if (!plan.unassigned.length) return <Empty>Every task is assigned.</Empty>;
  const tasks = new Map(scenario.tasks.map((t) => [t.id, t]));
  return (
    <ul className="space-y-2 text-xs" data-testid="unassigned-list">
      {plan.unassigned.map((item) => {
        const task = tasks.get(item.task_id);
        return (
          <li key={item.task_id}>
            <button className="text-left hover:underline" onClick={() => onSelect?.(item.task_id)}>
              <span className="font-medium">{item.task_id}</span>
              {task && (
                <span className="text-stone-500 dark:text-stone-400">
                  {" "}
                  · {PRIORITY_LABEL[task.priority]}
                  {task.mandatory ? " · mandatory" : ""}
                </span>
              )}
              <span className="block text-stone-600 dark:text-stone-300">{item.reason}</span>
            </button>
          </li>
        );
      })}
    </ul>
  );
}

export function TaskTable({ scenario, onSelect }: { scenario: Scenario; onSelect?: (id: string) => void }) {
  if (!scenario.tasks.length) return <Empty>No tasks yet.</Empty>;
  return (
    <div className="max-h-64 overflow-auto">
      <table className="w-full text-left text-xs">
        <thead className="sticky top-0 bg-white text-stone-500 dark:bg-stone-900 dark:text-stone-400">
          <tr>
            <th className="py-1 pr-2 font-medium">Task</th>
            <th className="pr-2 font-medium">Priority</th>
            <th className="pr-2 font-medium">Needs</th>
            <th className="pr-2 font-medium">EU</th>
            <th className="font-medium">Deadline</th>
          </tr>
        </thead>
        <tbody>
          {scenario.tasks.map((task) => (
            <tr key={task.id} className="cursor-pointer hover:bg-stone-100 dark:hover:bg-stone-800" onClick={() => onSelect?.(task.id)}>
              <td className="py-1 pr-2 font-medium">{task.id}</td>
              <td className="pr-2">
                {PRIORITY_LABEL[task.priority]}
                {task.mandatory ? " *" : ""}
              </td>
              <td className="pr-2">{task.capability ?? "any"}</td>
              <td className="pr-2 tabular-nums">{task.energy}</td>
              <td className="tabular-nums">{task.deadline ?? "none"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
