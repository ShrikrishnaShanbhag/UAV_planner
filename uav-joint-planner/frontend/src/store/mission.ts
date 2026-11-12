import { create } from "zustand";
import type { Cell, CostWeights, Obstacle, Plan, Metrics, Scenario, Station, Task, UAV } from "@/api/client";
import { emptyScenario, nextId, newTask, newUav, regionCovers, sameCell } from "@/lib/scenario";

export type Selection =
  | { kind: "task" | "uav" | "station" | "region"; id: string }
  | null;

export const DEFAULT_WEIGHTS: CostWeights = { w_d: 0.2, w_e: 0.4, w_t: 0.3, w_l: 0.1, normalization: "max" };

interface MissionState {
  scenario: Scenario;
  savedId: string | null;
  selection: Selection;
  algorithm: string;
  weights: CostWeights;
  reservePercent: number;
  allowRecharge: boolean;
  adaptiveReserve: boolean;
  plan: Plan | null;
  metrics: Metrics | null;
  setScenario: (scenario: Scenario, savedId?: string | null) => void;
  patchScenario: (patch: Partial<Scenario>) => void;
  select: (selection: Selection) => void;
  addTask: (cell: Cell) => void;
  addUav: (cell: Cell) => void;
  addStation: (cell: Cell) => void;
  addRegion: (region: Omit<Obstacle, "id">) => void;
  updateTask: (id: string, patch: Partial<Task>) => void;
  updateUav: (id: string, patch: Partial<UAV>) => void;
  updateRegion: (id: string, patch: Partial<Obstacle>) => void;
  removeSelected: () => void;
  eraseAt: (cell: Cell) => void;
  setAlgorithm: (algorithm: string) => void;
  setWeights: (weights: CostWeights) => void;
  setReserve: (percent: number) => void;
  setAllowRecharge: (on: boolean) => void;
  setAdaptiveReserve: (on: boolean) => void;
  setPlan: (plan: Plan | null, metrics?: Metrics | null) => void;
}

function withoutStation(uavs: UAV[], removed: Station, stations: Station[]): UAV[] {
  const fallback = stations.find((s) => s.id !== removed.id)?.id ?? "";
  return uavs.map((u) => (u.home === removed.id ? { ...u, home: fallback } : u));
}

/** The scenario and planner options for the current energy settings. */
export function planningInputs(state: MissionState) {
  const { scenario, adaptiveReserve, allowRecharge, algorithm, weights, reservePercent } = state;
  return {
    scenario: { ...scenario, energy: { ...scenario.energy!, adaptive_reserve: adaptiveReserve } },
    options: {
      algorithm,
      weights,
      reserve_ratio: reservePercent / 100,
      config: allowRecharge && algorithm === "integrated" ? { allow_recharge: true } : {},
    },
  };
}

export const useMission = create<MissionState>((set, get) => ({
  scenario: emptyScenario(),
  savedId: null,
  selection: null,
  algorithm: "integrated",
  weights: DEFAULT_WEIGHTS,
  reservePercent: 10,
  allowRecharge: false,
  adaptiveReserve: false,
  plan: null,
  metrics: null,

  setScenario: (scenario, savedId = null) =>
    set({
      scenario,
      savedId,
      selection: null,
      plan: null,
      metrics: null,
      reservePercent: Math.round((scenario.energy?.reserve_ratio ?? 0.1) * 100),
    }),
  patchScenario: (patch) => set((s) => ({ scenario: { ...s.scenario, ...patch }, plan: null, metrics: null })),
  select: (selection) => set({ selection }),

  addTask: (cell) => {
    const { scenario, patchScenario } = get();
    const id = nextId("T", scenario.tasks.map((t) => t.id));
    patchScenario({ tasks: [...scenario.tasks, newTask(id, cell)] });
    set({ selection: { kind: "task", id } });
  },
  addUav: (cell) => {
    const { scenario, patchScenario } = get();
    const id = nextId("U", scenario.uavs.map((u) => u.id));
    patchScenario({ uavs: [...scenario.uavs, newUav(id, cell, scenario.stations[0]?.id ?? "")] });
    set({ selection: { kind: "uav", id } });
  },
  addStation: (cell) => {
    const { scenario, patchScenario } = get();
    const id = nextId("S", scenario.stations.map((s) => s.id));
    patchScenario({
      stations: [...scenario.stations, { id, cell }],
      uavs: scenario.uavs.map((u) => (u.home ? u : { ...u, home: id })),
    });
    set({ selection: { kind: "station", id } });
  },
  addRegion: (region) => {
    const { scenario, patchScenario } = get();
    const id = nextId("R", scenario.obstacles.map((o) => o.id));
    patchScenario({ obstacles: [...scenario.obstacles, { ...region, id }] });
    set({ selection: { kind: "region", id } });
  },
  updateTask: (id, patch) => {
    const { scenario, patchScenario } = get();
    patchScenario({ tasks: scenario.tasks.map((t) => (t.id === id ? { ...t, ...patch } : t)) });
  },
  updateUav: (id, patch) => {
    const { scenario, patchScenario } = get();
    patchScenario({ uavs: scenario.uavs.map((u) => (u.id === id ? { ...u, ...patch } : u)) });
  },
  updateRegion: (id, patch) => {
    const { scenario, patchScenario } = get();
    patchScenario({ obstacles: scenario.obstacles.map((o) => (o.id === id ? { ...o, ...patch } : o)) });
  },
  removeSelected: () => {
    const { selection, scenario, patchScenario } = get();
    if (!selection) return;
    if (selection.kind === "task") patchScenario({ tasks: scenario.tasks.filter((t) => t.id !== selection.id) });
    if (selection.kind === "uav") patchScenario({ uavs: scenario.uavs.filter((u) => u.id !== selection.id) });
    if (selection.kind === "region") {
      patchScenario({ obstacles: scenario.obstacles.filter((o) => o.id !== selection.id) });
    }
    if (selection.kind === "station") {
      const station = scenario.stations.find((s) => s.id === selection.id);
      if (station) {
        patchScenario({
          stations: scenario.stations.filter((s) => s.id !== station.id),
          uavs: withoutStation(scenario.uavs, station, scenario.stations),
        });
      }
    }
    set({ selection: null });
  },
  eraseAt: (cell) => {
    const { scenario, select, removeSelected } = get();
    const task = scenario.tasks.find((t) => sameCell(t.cell, cell));
    const uav = scenario.uavs.find((u) => sameCell(u.start, cell));
    const station = scenario.stations.find((s) => sameCell(s.cell, cell));
    const region = [...scenario.obstacles].reverse().find((r) => regionCovers(r, cell));
    if (task) select({ kind: "task", id: task.id });
    else if (uav) select({ kind: "uav", id: uav.id });
    else if (station) select({ kind: "station", id: station.id });
    else if (region) select({ kind: "region", id: region.id });
    else return;
    removeSelected();
  },
  setAlgorithm: (algorithm) => set({ algorithm }),
  setWeights: (weights) => set({ weights }),
  setReserve: (reservePercent) => set({ reservePercent }),
  setAllowRecharge: (allowRecharge) => set({ allowRecharge, plan: null, metrics: null }),
  setAdaptiveReserve: (adaptiveReserve) => set({ adaptiveReserve, plan: null, metrics: null }),
  setPlan: (plan, metrics = null) => set({ plan, metrics }),
}));
