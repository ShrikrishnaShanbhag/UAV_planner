import type { LogEntry } from "@/api/client";
import { Empty } from "@/components/ui";
import { fmt } from "@/lib/format";

export function EventLog({ entries }: { entries: LogEntry[] }) {
  if (!entries.length) return <Empty>Events appear here as the mission runs.</Empty>;
  return (
    <ul className="max-h-64 space-y-1 overflow-auto text-xs" data-testid="event-log">
      {[...entries].reverse().map((entry, index) => (
        <li key={index} className="flex gap-2">
          <span className="w-12 shrink-0 tabular-nums text-stone-500 dark:text-stone-400">{fmt(entry.time, 0)}s</span>
          <span className={entry.kind === "REPLAN" ? "font-semibold" : ""}>
            {entry.kind.replaceAll("_", " ")}
            {entry.uav_id ? ` · ${entry.uav_id}` : ""}
            {entry.task_id ? ` · ${entry.task_id}` : ""}
            {entry.detail ? ` (${entry.detail})` : ""}
          </span>
        </li>
      ))}
    </ul>
  );
}
