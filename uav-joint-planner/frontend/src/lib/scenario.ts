import type { Cell, LegRef, Obstacle, PathCompareResult, Plan, Route, Scenario, Task, UAV } from "@/api/client";

export function pointInPolygon(x: number, y: number, polygon: [number, number][]): boolean {
  let inside = false;
  for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i++) {
    const [ax, ay] = polygon[i];
    const [bx, by] = polygon[j];
    const cross = (bx - ax) * (y - ay) - (by - ay) * (x - ax);
    const within = Math.min(ax, bx) <= x && x <= Math.max(ax, bx) && Math.min(ay, by) <= y && y <= Math.max(ay, by);
    if (Math.abs(cross) < 1e-9 && within) return true;
    if (ay > y !== by > y && x < ((bx - ax) * (y - ay)) / (by - ay) + ax) inside = !inside;
  }
  return inside;
}

export function regionCovers(region: Obstacle, cell: Cell): boolean {
  const [x, y] = cell;
  if (region.rect && x >= region.rect[0] && x <= region.rect[2] && y >= region.rect[1] && y <= region.rect[3]) {
    return true;
  }
  if (region.polygon && pointInPolygon(x, y, region.polygon as [number, number][])) return true;
  return !!region.cells?.some((c) => c[0] === x && c[1] === y);
}

export function isBlocked(scenario: Scenario, cell: Cell): boolean {
  return scenario.obstacles.some((r) => r.kind !== "energy_zone" && regionCovers(r, cell));
}

export function inBounds(scenario: Scenario, cell: Cell): boolean {
  return cell[0] >= 0 && cell[1] >= 0 && cell[0] < scenario.grid.width && cell[1] < scenario.grid.height;
}

export function sameCell(a: readonly number[], b: readonly number[]): boolean {
  return a[0] === b[0] && a[1] === b[1];
}

/** Fast checks for live feedback in the builder; the server performs the full validation. */
export function validateScenario(scenario: Scenario): string[] {
  const problems: string[] = [];
  const free = (cell: Cell) => inBounds(scenario, cell) && !isBlocked(scenario, cell);
  if (!scenario.stations.length) problems.push("Add at least one station.");
  if (!scenario.uavs.length) problems.push("Add at least one UAV.");
  const stationIds = new Set(scenario.stations.map((s) => s.id));
  const capabilities = new Set(scenario.uavs.flatMap((u) => u.capabilities));
  scenario.stations.forEach((s) => {
    if (!free(s.cell)) problems.push(`Station ${s.id} is inside an obstacle.`);
  });
  scenario.uavs.forEach((u) => {
    if (!free(u.start)) problems.push(`UAV ${u.id} starts inside an obstacle.`);
    if (!stationIds.has(u.home)) problems.push(`UAV ${u.id} has no valid home station.`);
    if (u.battery > u.battery_max) problems.push(`UAV ${u.id} battery exceeds its capacity.`);
    if (u.battery <= (scenario.energy?.reserve_ratio ?? 0.1) * u.battery_max) {
      problems.push(`UAV ${u.id} battery must be above the reserve.`);
    }
  });
  scenario.tasks.forEach((t) => {
    if (!free(t.cell)) problems.push(`Task ${t.id} is inside an obstacle.`);
    if (t.capability && !capabilities.has(t.capability)) {
      problems.push(`Task ${t.id} needs "${t.capability}", which no UAV has.`);
    }
  });
  return problems;
}

export function nextId(prefix: string, existing: string[]): string {
  const used = new Set(existing);
  let n = 1;
  while (used.has(`${prefix}${n}`)) n += 1;
  return `${prefix}${n}`;
}

export function newTask(id: string, cell: Cell): Task {
  return {
    id,
    cell,
    priority: 2,
    capability: "camera",
    energy: 8,
    duration: 30,
    deadline: null,
    mandatory: false,
    release_time: 0,
    label: "",
  };
}

export function newUav(id: string, cell: Cell, home: string): UAV {
  return {
    id,
    start: cell,
    battery: 100,
    home,
    capabilities: ["camera"],
    battery_max: 100,
    speed: 10,
    energy_per_meter: 0.017,
    power_cruise: 100,
    power_hover: 120,
    hover_rate: 0,
    takeoff_energy: 0,
    landing_energy: 0,
    status: "idle",
    ready_time: 0,
    airborne: false,
  };
}

export function emptyScenario(): Scenario {
  return {
    id: "draft",
    name: "Untitled scenario",
    grid: { width: 50, height: 50, cell_size: 20 },
    uavs: [],
    tasks: [],
    stations: [],
    obstacles: [],
    events: [],
    energy: {
      mode: "normalized",
      reserve_ratio: 0.1,
      home_only_return: false,
      strict_prefix_safety: true,
      turn_energy_per_rad: 0,
      path_metric: "energy",
      adaptive_reserve: false,
      adaptive_k: 1,
      adaptive_sigma: 0.1,
      charge_rate: 0.5,
      charge_time: 120,
    },
    seed: 0,
    description: "",
  };
}

/** Energy shown beyond travel, task and return, grouped under travel for the battery bar. */
export function routeEnergy(route: Route) {
  const travel = route.travel_energy + route.takeoff_energy + route.landing_energy + route.wait_energy;
  const total = travel + route.task_energy + route.return_energy;
  return { travel, task: route.task_energy, ret: route.return_energy, total };
}

export function energyBreakdown(route: Route) {
  return {
    uav: route.uav_id,
    travel: route.travel_energy,
    task: route.task_energy + route.wait_energy,
    ret: route.return_energy,
    takeoffLanding: route.takeoff_energy + route.landing_energy,
    reserve: route.reserve,
  };
}

export function planLegs(routes: Route[]): LegRef[] {
  return routes.flatMap((route) =>
    route.legs.map((leg) => ({ uav_id: route.uav_id, start: leg.start, end: leg.end })),
  );
}

/** The same routes drawn along the other path metric; legs are matched by position. */
export function routesWithPaths(routes: Route[], legs: PathCompareResult["legs"], metric: "energy" | "distance"): Route[] {
  let index = 0;
  return routes.map((route) => ({
    ...route,
    legs: route.legs.map((leg) => {
      const option = legs[index++];
      return { ...leg, path: (metric === "energy" ? option.energy_optimal : option.distance_optimal).cells };
    }),
  }));
}

export function planAssignment(plan: Plan): Record<string, string[]> {
  return Object.fromEntries(plan.routes.map((r) => [r.uav_id, [...r.task_ids]]));
}

export const PRIORITY_LABEL: Record<number, string> = { 1: "Low", 2: "Medium", 3: "High", 4: "Critical" };
