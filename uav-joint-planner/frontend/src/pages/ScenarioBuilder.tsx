import { useMutation, useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { api } from "@/api/client";
import type { Cell, GenerateParams, Obstacle, Scenario, Task, UAV } from "@/api/client";
import { MapCanvas } from "@/components/map/MapCanvas";
import type { MapDraft } from "@/components/map/MapCanvas";
import { TaskTable } from "@/components/panels/TaskTable";
import { Button, Card, Empty, Field, inputClass } from "@/components/ui";
import { isBlocked, sameCell, validateScenario } from "@/lib/scenario";
import { useMission } from "@/store/mission";
import { reportError, useUi } from "@/store/ui";

type Tool = "select" | "rect" | "polygon" | "nofly" | "task" | "uav" | "station" | "erase";

const TOOLS: { id: Tool; label: string; key: string; hint: string }[] = [
  { id: "select", label: "Select", key: "v", hint: "Click to select; drag to pan" },
  { id: "rect", label: "Obstacle", key: "r", hint: "Drag to draw a rectangular obstacle" },
  { id: "polygon", label: "Polygon", key: "p", hint: "Click corners, double-click to finish" },
  { id: "nofly", label: "No-fly", key: "n", hint: "Drag to draw a no-fly zone" },
  { id: "task", label: "Task", key: "t", hint: "Click to place a task" },
  { id: "uav", label: "UAV", key: "u", hint: "Click to place a UAV" },
  { id: "station", label: "Station", key: "s", hint: "Click to place a station" },
  { id: "erase", label: "Erase", key: "e", hint: "Click an item to remove it" },
];
const CAPABILITIES = ["camera", "thermal", "lidar", "payload"];

function NumberInput({ value, onChange, min, step = 1 }: {
  value: number;
  onChange: (value: number) => void;
  min?: number;
  step?: number;
}) {
  return (
    <input
      type="number"
      className={inputClass}
      value={value}
      min={min}
      step={step}
      onChange={(e) => {
        if (e.target.value !== "") onChange(Number(e.target.value));
      }}
    />
  );
}

function TaskProperties({ task, update }: { task: Task; update: (patch: Partial<Task>) => void }) {
  return (
    <div className="grid grid-cols-2 gap-2">
      <Field label="Priority">
        <select className={inputClass} value={task.priority} onChange={(e) => update({ priority: Number(e.target.value) as Task["priority"] })}>
          <option value={1}>Low</option>
          <option value={2}>Medium</option>
          <option value={3}>High</option>
          <option value={4}>Critical</option>
        </select>
      </Field>
      <Field label="Capability">
        <select className={inputClass} value={task.capability ?? ""} onChange={(e) => update({ capability: e.target.value || null })}>
          <option value="">Any</option>
          {CAPABILITIES.map((c) => (
            <option key={c}>{c}</option>
          ))}
        </select>
      </Field>
      <Field label="Energy (EU)">
        <NumberInput value={task.energy} min={0} onChange={(energy) => update({ energy })} />
      </Field>
      <Field label="Duration (s)">
        <NumberInput value={task.duration} min={0} onChange={(duration) => update({ duration })} />
      </Field>
      <Field label="Deadline (s)" hint="Leave empty for no deadline">
        <input
          type="number"
          className={inputClass}
          value={task.deadline ?? ""}
          onChange={(e) => update({ deadline: e.target.value === "" ? null : Number(e.target.value) })}
        />
      </Field>
      <label className="flex items-end gap-2 pb-1 text-xs font-medium">
        <input type="checkbox" checked={task.mandatory} onChange={(e) => update({ mandatory: e.target.checked })} /> Mandatory
      </label>
    </div>
  );
}

function UavProperties({ uav, stations, update }: {
  uav: UAV;
  stations: string[];
  update: (patch: Partial<UAV>) => void;
}) {
  return (
    <div className="grid grid-cols-2 gap-2">
      <Field label="Battery (EU)">
        <NumberInput value={uav.battery} min={0} onChange={(battery) => update({ battery })} />
      </Field>
      <Field label="Capacity (EU)">
        <NumberInput value={uav.battery_max} min={1} onChange={(battery_max) => update({ battery_max })} />
      </Field>
      <Field label="Speed (m/s)">
        <NumberInput value={uav.speed} min={1} onChange={(speed) => update({ speed })} />
      </Field>
      <Field label="EU per metre">
        <NumberInput value={uav.energy_per_meter} min={0} step={0.001} onChange={(energy_per_meter) => update({ energy_per_meter })} />
      </Field>
      <Field label="Home station">
        <select className={inputClass} value={uav.home} onChange={(e) => update({ home: e.target.value })}>
          {stations.map((s) => (
            <option key={s}>{s}</option>
          ))}
        </select>
      </Field>
      <div className="col-span-2 flex flex-wrap gap-3 text-xs">
        {CAPABILITIES.map((c) => (
          <label key={c} className="flex items-center gap-1">
            <input
              type="checkbox"
              checked={uav.capabilities.includes(c)}
              onChange={(e) =>
                update({
                  capabilities: e.target.checked ? [...uav.capabilities, c] : uav.capabilities.filter((x) => x !== c),
                })
              }
            />
            {c}
          </label>
        ))}
      </div>
    </div>
  );
}

function RegionProperties({ region, update }: { region: Obstacle; update: (patch: Partial<Obstacle>) => void }) {
  return (
    <div className="grid grid-cols-2 gap-2">
      <Field label="Kind">
        <select className={inputClass} value={region.kind} onChange={(e) => update({ kind: e.target.value as Obstacle["kind"] })}>
          <option value="obstacle">Obstacle</option>
          <option value="no_fly">No-fly zone</option>
          <option value="energy_zone">High-energy zone</option>
        </select>
      </Field>
      {region.kind === "energy_zone" && (
        <Field label="Energy multiplier">
          <NumberInput value={region.energy_multiplier} min={1} step={0.1} onChange={(energy_multiplier) => update({ energy_multiplier })} />
        </Field>
      )}
    </div>
  );
}

function GeneratorForm({ onGenerate, busy }: { onGenerate: (params: Partial<GenerateParams>, seed: number) => void; busy: boolean }) {
  const [seed, setSeed] = useState(42);
  const [uavs, setUavs] = useState(4);
  const [tasks, setTasks] = useState(20);
  const [density, setDensity] = useState(0.08);
  const [distribution, setDistribution] = useState<GenerateParams["distribution"]>("uniform");
  return (
    <div className="grid grid-cols-2 gap-2">
      <Field label="Seed">
        <NumberInput value={seed} onChange={setSeed} />
      </Field>
      <Field label="Distribution">
        <select className={inputClass} value={distribution} onChange={(e) => setDistribution(e.target.value as GenerateParams["distribution"])}>
          <option value="uniform">Uniform</option>
          <option value="clustered">Clustered</option>
          <option value="edge">Edge-heavy</option>
        </select>
      </Field>
      <Field label="UAVs">
        <NumberInput value={uavs} min={1} onChange={setUavs} />
      </Field>
      <Field label="Tasks">
        <NumberInput value={tasks} min={0} onChange={setTasks} />
      </Field>
      <Field label="Obstacle density">
        <NumberInput value={density} min={0} step={0.01} onChange={setDensity} />
      </Field>
      <div className="flex items-end">
        <Button
          className="w-full"
          disabled={busy}
          onClick={() => onGenerate({ n_uavs: uavs, n_tasks: tasks, obstacle_density: density, distribution }, seed)}
        >
          Generate
        </Button>
      </div>
    </div>
  );
}

export default function ScenarioBuilder() {
  const mission = useMission();
  const { scenario, selection, savedId } = mission;
  const notify = useUi((s) => s.notify);
  const [tool, setTool] = useState<Tool>("select");
  const [dragStart, setDragStart] = useState<Cell | null>(null);
  const [draft, setDraft] = useState<MapDraft | null>(null);
  const [serverIssues, setServerIssues] = useState<string[]>([]);
  const fileInput = useRef<HTMLInputElement>(null);
  const presets = useQuery({ queryKey: ["presets"], queryFn: api.presets });

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.target as HTMLElement).matches("input, select, textarea")) return;
      const match = TOOLS.find((t) => t.key === event.key.toLowerCase());
      if (match) setTool(match.id);
      if (event.key === "Delete" || event.key === "Backspace") useMission.getState().removeSelected();
      if (event.key === "Escape") {
        setDraft(null);
        setDragStart(null);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const load = useMutation({
    mutationFn: api.generate,
    onSuccess: (s) => {
      mission.setScenario(s);
      setServerIssues([]);
    },
    onError: reportError,
  });
  const save = useMutation({
    mutationFn: async () => (savedId ? api.updateScenario({ ...scenario, id: savedId }) : api.createScenario(scenario)),
    onSuccess: (stored) => {
      mission.setScenario(stored, stored.id);
      setServerIssues([]);
      notify("Scenario saved.", "info");
    },
    onError: (error) => {
      const details = (error as { details?: unknown }).details;
      if (Array.isArray(details) && details.every((d) => typeof d === "string")) setServerIssues(details as string[]);
      reportError(error);
    },
  });

  const issues = [...validateScenario(scenario), ...serverIssues];
  const selectedTask = selection?.kind === "task" ? scenario.tasks.find((t) => t.id === selection.id) : undefined;
  const selectedUav = selection?.kind === "uav" ? scenario.uavs.find((u) => u.id === selection.id) : undefined;
  const selectedRegion = selection?.kind === "region" ? scenario.obstacles.find((o) => o.id === selection.id) : undefined;

  const dragRect = (a: Cell, b: Cell): [number, number, number, number] => [
    Math.min(a[0], b[0]),
    Math.min(a[1], b[1]),
    Math.max(a[0], b[0]),
    Math.max(a[1], b[1]),
  ];

  const onDown = (cell: Cell) => {
    if (tool === "rect" || tool === "nofly") {
      setDragStart(cell);
      setDraft({ rect: dragRect(cell, cell) });
    } else if (tool === "polygon") {
      setDraft({ polygon: [...(draft?.polygon ?? []), cell] });
    } else if (tool === "erase") {
      mission.eraseAt(cell);
    } else if (tool === "task" || tool === "uav" || tool === "station") {
      if (isBlocked(scenario, cell)) {
        notify("That cell is inside an obstacle.");
      } else if (tool === "task") {
        mission.addTask(cell);
      } else if (tool === "uav") {
        mission.addUav(cell);
      } else {
        mission.addStation(cell);
      }
    } else {
      const station = scenario.stations.find((s) => sameCell(s.cell, cell));
      const uav = scenario.uavs.find((u) => sameCell(u.start, cell));
      if (uav) mission.select({ kind: "uav", id: uav.id });
      else if (station) mission.select({ kind: "station", id: station.id });
      else mission.select(null);
    }
  };
  const onMove = (cell: Cell) => {
    if (dragStart) setDraft({ rect: dragRect(dragStart, cell) });
  };
  const onUp = (cell: Cell) => {
    if (dragStart) {
      mission.addRegion({
        kind: tool === "nofly" ? "no_fly" : "obstacle",
        rect: dragRect(dragStart, cell),
        energy_multiplier: 1,
        label: "",
      });
      setDragStart(null);
      setDraft(null);
    }
  };
  const finishPolygon = () => {
    if (tool === "polygon" && draft?.polygon && draft.polygon.length >= 3) {
      mission.addRegion({ kind: "obstacle", polygon: draft.polygon, energy_multiplier: 1, label: "" });
    }
    if (tool === "polygon") setDraft(null);
  };

  const exportJson = () => {
    const url = URL.createObjectURL(new Blob([JSON.stringify(scenario, null, 1)], { type: "application/json" }));
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `${scenario.name.replace(/\W+/g, "-")}.json`;
    anchor.click();
    URL.revokeObjectURL(url);
  };
  const importJson = async (file: File) => {
    try {
      mission.setScenario(JSON.parse(await file.text()) as Scenario);
      setServerIssues([]);
    } catch {
      notify("That file is not valid scenario JSON.");
    }
  };

  return (
    <div className="grid gap-4 lg:grid-cols-[1fr_340px]">
      <div className="space-y-3">
        <Card>
          <div className="flex flex-wrap items-center gap-1" role="toolbar" aria-label="Drawing tools">
            {TOOLS.map((t) => (
              <Button key={t.id} variant={tool === t.id ? "primary" : "secondary"} title={`${t.hint} (${t.key.toUpperCase()})`} onClick={() => setTool(t.id)}>
                {t.label}
              </Button>
            ))}
          </div>
          <div className="mt-3">
            <MapCanvas
              scenario={scenario}
              label="Scenario editor map"
              pan={tool === "select"}
              draft={draft}
              selectedRegion={selectedRegion?.id}
              highlightTask={selectedTask?.id}
              onCellDown={onDown}
              onCellMove={onMove}
              onCellUp={onUp}
              onDoubleClick={finishPolygon}
              onTask={(id) => (tool === "erase" ? undefined : mission.select({ kind: "task", id }))}
            />
          </div>
          <p className="mt-2 text-xs text-stone-500 dark:text-stone-400">
            Scroll to zoom and drag to pan with the Select tool. Keys: V, R, P, N, T, U, S, E; Delete removes the selection.
          </p>
        </Card>
        <Card title={`Tasks (${scenario.tasks.length})`}>
          <TaskTable scenario={scenario} onSelect={(id) => mission.select({ kind: "task", id })} />
        </Card>
      </div>
      <div className="space-y-3">
        <Card title="Scenario">
          <Field label="Name">
            <input className={inputClass} value={scenario.name} onChange={(e) => mission.patchScenario({ name: e.target.value })} />
          </Field>
          <div className="mt-3 flex flex-wrap gap-2">
            <Button variant="primary" disabled={save.isPending} onClick={() => save.mutate()}>
              {savedId ? "Save" : "Save new"}
            </Button>
            <Button onClick={exportJson}>Export JSON</Button>
            <Button onClick={() => fileInput.current?.click()}>Import JSON</Button>
            <input
              ref={fileInput}
              type="file"
              accept="application/json"
              hidden
              aria-label="Import scenario file"
              onChange={(e) => e.target.files?.[0] && importJson(e.target.files[0])}
            />
          </div>
        </Card>
        <Card title="Validation">
          {issues.length ? (
            <ul className="space-y-1 text-xs text-red-700 dark:text-red-400" data-testid="validation-issues">
              {issues.map((issue) => (
                <li key={issue}>⚠ {issue}</li>
              ))}
            </ul>
          ) : (
            <p className="text-xs text-green-800 dark:text-green-400">✓ No problems found.</p>
          )}
        </Card>
        <Card title="Properties">
          {selectedTask ? (
            <>
              <p className="mb-2 text-xs font-medium">Task {selectedTask.id}</p>
              <TaskProperties task={selectedTask} update={(patch) => mission.updateTask(selectedTask.id, patch)} />
            </>
          ) : selectedUav ? (
            <>
              <p className="mb-2 text-xs font-medium">UAV {selectedUav.id}</p>
              <UavProperties uav={selectedUav} stations={scenario.stations.map((s) => s.id)} update={(patch) => mission.updateUav(selectedUav.id, patch)} />
            </>
          ) : selectedRegion ? (
            <>
              <p className="mb-2 text-xs font-medium">Region {selectedRegion.id}</p>
              <RegionProperties region={selectedRegion} update={(patch) => mission.updateRegion(selectedRegion.id, patch)} />
            </>
          ) : (
            <Empty>Select something on the map to edit it.</Empty>
          )}
        </Card>
        <Card title="Presets">
          <div className="flex flex-wrap gap-1">
            {presets.data?.map((p) => (
              <Button key={p.id} title={p.description} onClick={() => load.mutate({ preset: p.id })}>
                {p.id === "DEMO" ? "Demo" : p.id}
              </Button>
            ))}
          </div>
        </Card>
        <Card title="Random generator">
          <GeneratorForm busy={load.isPending} onGenerate={(params, seed) => load.mutate({ params, seed })} />
        </Card>
      </div>
    </div>
  );
}
