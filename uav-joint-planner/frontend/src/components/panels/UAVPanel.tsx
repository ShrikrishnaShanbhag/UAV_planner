import type { Plan, Route, Scenario } from "@/api/client";
import { Empty, Legend } from "@/components/ui";
import { seriesColor } from "@/lib/colors";
import { fmt } from "@/lib/format";
import { routeEnergy } from "@/lib/scenario";
import { useUi } from "@/store/ui";

const RESERVE = "#898781";

export function BatteryBar({ route, batteryMax, index }: { route: Route; batteryMax: number; index: number }) {
  const theme = useUi((s) => s.theme);
  const { travel, task, ret, total } = routeEnergy(route);
  const free = Math.max(route.battery_start - total - route.reserve, 0);
  const parts = [
    { label: "Travel", value: travel, color: seriesColor(0, theme) },
    { label: "Task", value: task, color: seriesColor(1, theme) },
    { label: "Return", value: ret, color: seriesColor(2, theme) },
  ];
  const percent = (value: number) => `${Math.min((value / batteryMax) * 100, 100)}%`;
  return (
    <div data-testid={`battery-${route.uav_id}`}>
      <div className="mb-1 flex items-center justify-between text-xs">
        <span className="flex items-center gap-1.5 font-medium">
          <span className="inline-block h-2.5 w-2.5 rounded-full" style={{ background: seriesColor(index, theme) }} />
          {route.uav_id}
        </span>
        <span className={route.feasible ? "text-stone-500 dark:text-stone-400" : "font-semibold text-red-700 dark:text-red-400"}>
          uses {fmt(total)} of {fmt(route.battery_start)} EU · margin {fmt(route.margin)}
          {route.recharged_energy > 0 ? ` · recharged ${fmt(route.recharged_energy)}` : ""}
        </span>
      </div>
      <div
        className="flex h-3 gap-0.5 overflow-hidden rounded bg-stone-100 dark:bg-stone-800"
        role="img"
        aria-label={`${route.uav_id}: travel ${fmt(travel)}, task ${fmt(task)}, return ${fmt(ret)}, reserve ${fmt(route.reserve)} EU`}
      >
        {parts.map((p) => (
          <div key={p.label} title={`${p.label}: ${fmt(p.value)} EU`} style={{ width: percent(p.value), background: p.color }} />
        ))}
        <div title={`Unused: ${fmt(free)} EU`} className="flex-1" />
        <div title={`Reserve: ${fmt(route.reserve)} EU`} style={{ width: percent(route.reserve), background: RESERVE }} />
      </div>
    </div>
  );
}

export function UAVPanel({ scenario, plan }: { scenario: Scenario; plan: Plan | null }) {
  const theme = useUi((s) => s.theme);
  if (!plan) return <Empty>Run a planner to see each UAV's energy budget.</Empty>;
  return (
    <div className="space-y-3" data-testid="battery-bars">
      {plan.routes.map((route) => {
        const uav = scenario.uavs.find((u) => u.id === route.uav_id);
        const index = scenario.uavs.findIndex((u) => u.id === route.uav_id);
        return <BatteryBar key={route.uav_id} route={route} batteryMax={uav?.battery_max ?? 100} index={index} />;
      })}
      <Legend
        items={[
          { label: "Travel", color: seriesColor(0, theme) },
          { label: "Task", color: seriesColor(1, theme) },
          { label: "Return", color: seriesColor(2, theme) },
          { label: "Reserve", color: RESERVE },
        ]}
      />
    </div>
  );
}

export function RouteList({ plan }: { plan: Plan }) {
  return (
    <ol className="space-y-1 text-xs" data-testid="route-list">
      {plan.routes.map((route) => (
        <li key={route.uav_id}>
          <span className="font-medium">{route.uav_id}:</span>{" "}
          {route.task_ids.length ? route.task_ids.join(" → ") : <span className="text-stone-500">idle</span>}
        </li>
      ))}
    </ol>
  );
}
