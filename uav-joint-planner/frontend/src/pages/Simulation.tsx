import { useMutation } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "@/api/client";
import type { Cell, UavEnergy } from "@/api/client";
import { BatteryTimeline, GanttChart } from "@/components/charts";
import { MapCanvas } from "@/components/map/MapCanvas";
import type { MapDraft } from "@/components/map/MapCanvas";
import { EventLog } from "@/components/panels/EventLog";
import { Button, Card, Empty, Field, inputClass } from "@/components/ui";
import { fmt } from "@/lib/format";
import { planningInputs, useMission } from "@/store/mission";
import { useSimulation } from "@/store/simulation";
import { reportError } from "@/store/ui";

type InjectMode = "none" | "task" | "block";
const SPEEDS = [1, 5, 10, 30, 60];
const REPLAN_MODES = [
  { value: "local_then_global", label: "Local, then global" },
  { value: "local_only", label: "Local only" },
  { value: "always_global", label: "Always global" },
  { value: "none", label: "No replanning" },
];

export default function Simulation() {
  const { scenario } = useMission();
  const sim = useSimulation();
  const [mode, setMode] = useState("local_then_global");
  const [noise, setNoise] = useState(0);
  const [earlyRth, setEarlyRth] = useState(false);
  const [inject, setInject] = useState<InjectMode>("none");
  const [dragStart, setDragStart] = useState<Cell | null>(null);
  const [draft, setDraft] = useState<MapDraft | null>(null);
  const [uavId, setUavId] = useState("");
  const [drop, setDrop] = useState(30);
  const [taskCount, setTaskCount] = useState(1);

  useEffect(() => () => useSimulation.getState().disconnect(), []);

  const start = useMutation({
    mutationFn: async () => {
      const inputs = planningInputs(useMission.getState());
      const { sim_id } = await api.createSimulation(inputs.scenario, {
        ...inputs.options,
        replan_mode: mode,
        noise_sigma: noise,
        early_rth: earlyRth,
      });
      await sim.connect(sim_id);
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

  const uavIds = scenario.uavs.map((u) => u.id);
  const target = uavId || uavIds[0];
  const active = !!sim.simId;
  const replan = sim.lastReplan;

  const onDown = (cell: Cell) => {
    if (inject === "task") {
      sim.send("inject_event", {
        type: "NEW_TASK",
        task: {
          id: `N${taskCount}`,
          cell,
          priority: 4,
          capability: "camera",
          energy: 6,
          duration: 20,
          mandatory: true,
          release_time: sim.t,
          label: "Urgent task",
        },
      });
      setTaskCount(taskCount + 1);
      setInject("none");
    } else if (inject === "block") {
      setDragStart(cell);
      setDraft({ rect: [cell[0], cell[1], cell[0], cell[1]] });
    }
  };
  const bounds = (a: Cell, b: Cell): [number, number, number, number] => [
    Math.min(a[0], b[0]),
    Math.min(a[1], b[1]),
    Math.max(a[0], b[0]),
    Math.max(a[1], b[1]),
  ];
  const onMove = (cell: Cell) => {
    if (dragStart) setDraft({ rect: bounds(dragStart, cell) });
  };
  const onUp = (cell: Cell) => {
    if (!dragStart) return;
    const [x0, y0, x1, y1] = bounds(dragStart, cell);
    const cells: Cell[] = [];
    for (let x = x0; x <= x1; x++) for (let y = y0; y <= y1; y++) cells.push([x, y]);
    sim.send("inject_event", { type: "BLOCK_REGION", cells });
    setDragStart(null);
    setDraft(null);
    setInject("none");
  };

  return (
    <div className="grid gap-4 lg:grid-cols-[1fr_360px]">
      <div className="space-y-3">
        <Card>
          <MapCanvas
            scenario={scenario}
            routes={sim.plan?.routes}
            oldRoutes={sim.oldPlan?.routes}
            liveUavs={active && sim.uavs.length ? sim.uavs : undefined}
            draft={draft}
            label="Live simulation map"
            onCellDown={onDown}
            onCellMove={onMove}
            onCellUp={onUp}
          />
          {inject !== "none" && (
            <p className="mt-2 text-xs font-medium">
              {inject === "task" ? "Click the map to place an urgent task." : "Drag on the map to block a region."}
            </p>
          )}
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <Button variant="primary" disabled={!sim.connected} onClick={() => sim.send(sim.playing ? "pause" : "play")} data-testid="play">
              {sim.playing ? "Pause" : "Play"}
            </Button>
            <label className="flex items-center gap-1 text-xs">
              Speed
              <select
                className={inputClass}
                value={sim.speed}
                aria-label="Playback speed"
                onChange={(e) => sim.send("speed", { value: Number(e.target.value) })}
              >
                {SPEEDS.map((s) => (
                  <option key={s} value={s}>
                    {s}×
                  </option>
                ))}
              </select>
            </label>
            <input
              type="range"
              className="min-w-40 flex-1 accent-blue-600"
              min={0}
              max={sim.endTime || 1}
              step={1}
              value={sim.t}
              aria-label="Seek"
              disabled={!sim.connected}
              onChange={(e) => sim.send("seek", { t: Number(e.target.value) })}
            />
            <span className="text-xs tabular-nums" data-testid="sim-time">
              {fmt(sim.t, 0)} / {fmt(sim.endTime, 0)} s
            </span>
          </div>
        </Card>
        {active && (
          <>
            <Card title="Battery over time">
              <BatteryTimeline frames={sim.timeline} uavIds={uavIds} t={sim.t} reserve={sim.plan?.routes[0]?.reserve} />
            </Card>
            <Card title="UAV schedules">
              <GanttChart frames={sim.timeline} uavIds={uavIds} />
            </Card>
          </>
        )}
      </div>
      <div className="space-y-3">
        <Card title="Simulation">
          <div className="grid grid-cols-2 gap-2">
            <Field label="Replanning">
              <select className={inputClass} value={mode} onChange={(e) => setMode(e.target.value)}>
                {REPLAN_MODES.map((m) => (
                  <option key={m.value} value={m.value}>
                    {m.label}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Energy noise σ" hint="Random error applied to energy use">
              <input type="number" className={inputClass} min={0} max={0.5} step={0.05} value={noise} onChange={(e) => setNoise(Number(e.target.value))} />
            </Field>
          </div>
          <label className="mt-3 flex items-center gap-2 text-xs" title="Abort and fly home when actual energy no longer covers the return and the reserve">
            <input type="checkbox" checked={earlyRth} onChange={(e) => setEarlyRth(e.target.checked)} data-testid="early-rth" />
            Early return-to-home
          </label>
          <Button variant="primary" className="mt-3 w-full" disabled={start.isPending} onClick={() => start.mutate()} data-testid="start-sim">
            {start.isPending ? "Starting…" : active ? "Restart simulation" : "Start simulation"}
          </Button>
        </Card>
        <Card title="Inject event">
          <div className="flex flex-wrap gap-1">
            <Button disabled={!sim.connected} onClick={() => setInject(inject === "task" ? "none" : "task")}>
              Urgent task
            </Button>
            <Button disabled={!sim.connected} onClick={() => setInject(inject === "block" ? "none" : "block")}>
              Block region
            </Button>
          </div>
          <div className="mt-3 grid grid-cols-2 gap-2">
            <Field label="UAV">
              <select className={inputClass} value={target} onChange={(e) => setUavId(e.target.value)} data-testid="inject-uav">
                {uavIds.map((id) => (
                  <option key={id}>{id}</option>
                ))}
              </select>
            </Field>
            <Field label="Battery drop (EU)">
              <input type="number" className={inputClass} min={0} value={drop} onChange={(e) => setDrop(Number(e.target.value))} />
            </Field>
            <Button disabled={!sim.connected} onClick={() => sim.send("inject_event", { type: "UAV_UNAVAILABLE", uav_id: target })} data-testid="fail-uav">
              Fail UAV
            </Button>
            <Button disabled={!sim.connected} onClick={() => sim.send("inject_event", { type: "BATTERY_DROP", uav_id: target, amount: drop })}>
              Drop battery
            </Button>
          </div>
        </Card>
        {replan && (
          <Card title="Last replan">
            <dl className="space-y-1 text-xs" data-testid="replan-diff">
              <div>
                Trigger: <span className="font-medium">{replan.diff.event_type.replaceAll("_", " ")}</span>
              </div>
              <div>
                Scope: <span className="font-medium">{replan.diff.scope}</span>
                {replan.diff.escalated ? ` (escalated: ${replan.diff.escalation_reason})` : ""}
              </div>
              <div>Replanning time: {fmt(replan.time_ms, 1)} ms</div>
              <div>Moved: {replan.diff.moved.map((m) => `${m.task_id} ${m.from_uav}→${m.to_uav}`).join(", ") || "none"}</div>
              <div>Added: {replan.diff.added.join(", ") || "none"}</div>
              <div>Dropped: {replan.diff.dropped.join(", ") || "none"}</div>
            </dl>
          </Card>
        )}
        {sim.state && (
          <Card title="Planned vs actual energy">
            <table className="w-full text-left text-xs" data-testid="energy-table">
              <thead className="text-stone-500 dark:text-stone-400">
                <tr>
                  {["UAV", "Planned", "Actual", "Margin", "Alerts"].map((h) => (
                    <th key={h} className="py-1 pr-2 font-medium">
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {(sim.state.energy as unknown as UavEnergy[]).map((e) => (
                  <tr key={e.uav_id} className="border-t border-stone-100 tabular-nums dark:border-stone-800">
                    <td className="py-1 pr-2">{e.uav_id}</td>
                    <td className="pr-2">{fmt(e.planned)}</td>
                    <td className="pr-2">{fmt(e.actual)}</td>
                    <td className="pr-2">{fmt(e.margin_at_end)}</td>
                    <td>{e.margin_alerts ? `⚠ ${e.margin_alerts}` : "none"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="mt-2 text-xs text-stone-500 dark:text-stone-400">Energy in EU over the whole mission. Margin is battery at the end minus the reserve.</p>
          </Card>
        )}
        <Card title="Event log">
          <EventLog entries={sim.log} />
        </Card>
      </div>
    </div>
  );
}
