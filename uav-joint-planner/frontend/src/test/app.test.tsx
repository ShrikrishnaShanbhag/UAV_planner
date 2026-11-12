import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api, ApiRequestError } from "@/api/client";
import type { Plan, Route, Scenario, SimFrame } from "@/api/client";
import { BoxPlot } from "@/components/charts";
import { EventLog } from "@/components/panels/EventLog";
import { ExplainPanel } from "@/components/panels/ExplainPanel";
import { FeasibilityPanel } from "@/components/panels/FeasibilityPanel";
import { UnassignedList } from "@/components/panels/TaskTable";
import { BatteryBar, RouteList, UAVPanel } from "@/components/panels/UAVPanel";
import { WeightSliders } from "@/components/panels/WeightSliders";
import { algorithmColor, seriesColor } from "@/lib/colors";
import { explainEnergy, fmt } from "@/lib/format";
import { emptyScenario, energyBreakdown, isBlocked, newTask, newUav, nextId, planLegs, pointInPolygon, regionCovers, routeEnergy, routesWithPaths, validateScenario } from "@/lib/scenario";
import { DEFAULT_WEIGHTS, planningInputs, useMission } from "@/store/mission";
import { useSimulation } from "@/store/simulation";
import { reportError, useUi } from "@/store/ui";
import Model from "@/pages/Model";

function route(overrides: Partial<Route> = {}): Route {
  return {
    uav_id: "U1",
    task_ids: ["T1"],
    legs: [],
    station_id: "S1",
    start_time: 0,
    completion_time: 100,
    distance: 500,
    travel_energy: 10,
    task_energy: 8,
    return_energy: 6,
    takeoff_energy: 1,
    landing_energy: 1,
    wait_energy: 0,
    reserve: 10,
    battery_start: 100,
    battery_end: 74,
    margin: 64,
    feasible: true,
    profile: [],
    recharged_energy: 0,
    ...overrides,
  };
}

function plan(overrides: Partial<Plan> = {}): Plan {
  return {
    algorithm: "integrated",
    scenario_id: "s",
    routes: [route()],
    unassigned: [],
    report: { feasible: true, violations: [] },
    objective: {
      z: 1,
      total_energy: 26,
      makespan: 100,
      total_distance: 500,
      load_variance: 0,
      unassigned_penalty: 0,
      energy_term: 0.3,
      time_term: 0.3,
      distance_term: 0.1,
      balance_term: 0.3,
    },
    compute_time_s: 0.01,
    explanations: [],
    config: {},
    ...overrides,
  };
}

function scenario(): Scenario {
  return {
    ...emptyScenario(),
    stations: [{ id: "S1", cell: [0, 0] }],
    uavs: [newUav("U1", [0, 0], "S1")],
    tasks: [newTask("T1", [5, 5])],
  };
}

beforeEach(() => {
  useMission.setState({ scenario: emptyScenario(), savedId: null, selection: null, plan: null, metrics: null, weights: DEFAULT_WEIGHTS, reservePercent: 10 });
  useUi.setState({ toasts: [], theme: "light" });
  useSimulation.setState({ log: [], plan: null, oldPlan: null, lastReplan: null, simId: null });
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("lib", () => {
  it("formats missing values", () => {
    expect(fmt(null)).toBe("n/a");
    expect(fmt(Number.NaN)).toBe("n/a");
    expect(fmt(1.25, 1)).toBe("1.3");
  });

  it("reproduces the proposal's 25 vs 30 EU example", () => {
    expect(explainEnergy(25, 20, 10, 0)).toEqual({ needed: 30, margin: -5, feasible: false });
    expect(explainEnergy(30, 20, 10, 0).feasible).toBe(true);
  });

  it("keeps colours attached to entities and folds overflow to a neutral", () => {
    expect(algorithmColor("integrated", "light")).toBe(seriesColor(3, "light"));
    expect(seriesColor(8, "light")).toBe("#898781");
    expect(seriesColor(0, "dark")).not.toBe(seriesColor(0, "light"));
  });

  it("tests polygon and region membership", () => {
    const triangle: [number, number][] = [[0, 0], [6, 0], [0, 6]];
    expect(pointInPolygon(1, 1, triangle)).toBe(true);
    expect(pointInPolygon(6, 6, triangle)).toBe(false);
    expect(pointInPolygon(0, 0, triangle)).toBe(true);
    const region = { id: "r", kind: "obstacle" as const, rect: [1, 1, 2, 2] as [number, number, number, number], energy_multiplier: 1, label: "" };
    expect(regionCovers(region, [2, 2])).toBe(true);
    expect(regionCovers(region, [3, 2])).toBe(false);
  });

  it("does not treat energy zones as blocked", () => {
    const s = scenario();
    s.obstacles = [{ id: "z", kind: "energy_zone", rect: [4, 4, 6, 6], energy_multiplier: 2, label: "" }];
    expect(isBlocked(s, [5, 5])).toBe(false);
    s.obstacles[0].kind = "obstacle";
    expect(isBlocked(s, [5, 5])).toBe(true);
  });

  it("finds scenario problems for live validation", () => {
    expect(validateScenario(scenario())).toEqual([]);
    const bad = scenario();
    bad.stations = [];
    bad.tasks[0].capability = "sonar";
    bad.uavs[0].battery = 5;
    const text = validateScenario(bad).join(" ");
    expect(text).toContain("at least one station");
    expect(text).toContain("no valid home");
    expect(text).toContain("above the reserve");
    expect(text).toContain("no UAV has");
  });

  it("creates the next free id", () => {
    expect(nextId("T", ["T1", "T2"])).toBe("T3");
    expect(nextId("T", ["T2"])).toBe("T1");
  });

  it("groups overhead energy under travel", () => {
    expect(routeEnergy(route())).toEqual({ travel: 12, task: 8, ret: 6, total: 26 });
  });
});

describe("energy helpers", () => {
  const leg = { kind: "to_task" as const, start: [0, 0] as [number, number], end: [2, 0] as [number, number], task_id: "T1", path: [[0, 0], [1, 0], [2, 0]] as [number, number][], distance: 40, time: 4, energy: 1, edge_distances: [], edge_efforts: [], charge_energy: 0, charge_time: 0 };

  it("splits a route into the five energy parts", () => {
    expect(energyBreakdown(route({ wait_energy: 3 }))).toEqual({ uav: "U1", travel: 10, task: 11, ret: 6, takeoffLanding: 2, reserve: 10 });
  });

  it("lists legs and swaps in the chosen path metric", () => {
    const routes = [route({ legs: [leg] })];
    expect(planLegs(routes)).toEqual([{ uav_id: "U1", start: [0, 0], end: [2, 0] }]);
    const alternatives = [
      {
        uav_id: "U1",
        energy_optimal: { cells: [[0, 0], [2, 0]] as [number, number][], distance: 40, energy: 1 },
        distance_optimal: { cells: [[0, 0], [1, 1], [2, 0]] as [number, number][], distance: 38, energy: 2 },
      },
    ];
    expect(routesWithPaths(routes, alternatives, "distance")[0].legs[0].path).toEqual([[0, 0], [1, 1], [2, 0]]);
    expect(routesWithPaths(routes, alternatives, "energy")[0].legs[0].path).toEqual([[0, 0], [2, 0]]);
  });

  it("sends the energy options to the planner", () => {
    useMission.getState().setScenario(scenario());
    useMission.getState().setAllowRecharge(true);
    useMission.getState().setAdaptiveReserve(true);
    const inputs = planningInputs(useMission.getState());
    expect(inputs.options.config).toEqual({ allow_recharge: true });
    expect(inputs.scenario.energy?.adaptive_reserve).toBe(true);
    useMission.getState().setAllowRecharge(false);
    useMission.getState().setAdaptiveReserve(false);
    expect(planningInputs(useMission.getState()).options.config).toEqual({});
  });

  it("mentions a recharge on the battery bar", () => {
    render(<BatteryBar route={route({ recharged_energy: 42 })} batteryMax={100} index={0} />);
    expect(screen.getByText(/recharged 42.0/)).toBeInTheDocument();
  });
});

describe("api client", () => {
  it("raises the API's error shape", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ error: { code: "invalid_scenario", message: "Bad", details: ["x"] } }), { status: 422 }),
    );
    const failure = await api.scenarios().catch((e: unknown) => e);
    expect(failure).toBeInstanceOf(ApiRequestError);
    expect((failure as ApiRequestError).code).toBe("invalid_scenario");
    expect((failure as ApiRequestError).details).toEqual(["x"]);
  });

  it("handles non-JSON failures and empty responses", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(new Response("oops", { status: 500 }));
    await expect(api.runs()).rejects.toThrow("Request failed (500)");
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(new Response(null, { status: 204 }));
    await expect(api.deleteScenario("a")).resolves.toBeUndefined();
  });
});

describe("mission store", () => {
  it("adds entities with unique ids and selects them", () => {
    const s = useMission.getState();
    s.addStation([0, 0]);
    s.addUav([1, 1]);
    s.addTask([2, 2]);
    s.addTask([3, 3]);
    const state = useMission.getState();
    expect(state.scenario.tasks.map((t) => t.id)).toEqual(["T1", "T2"]);
    expect(state.scenario.uavs[0].home).toBe("S1");
    expect(state.selection).toEqual({ kind: "task", id: "T2" });
  });

  it("erases what is at a cell, tasks before regions", () => {
    const s = useMission.getState();
    s.addRegion({ kind: "energy_zone", rect: [0, 0, 5, 5], energy_multiplier: 2, label: "" });
    s.addTask([2, 2]);
    s.eraseAt([2, 2]);
    expect(useMission.getState().scenario.tasks).toHaveLength(0);
    expect(useMission.getState().scenario.obstacles).toHaveLength(1);
    s.eraseAt([2, 2]);
    expect(useMission.getState().scenario.obstacles).toHaveLength(0);
    s.eraseAt([9, 9]);
    expect(useMission.getState().selection).toBeNull();
  });

  it("reassigns a UAV's home when its station is removed", () => {
    const s = useMission.getState();
    s.addStation([0, 0]);
    s.addStation([9, 9]);
    s.addUav([1, 1]);
    useMission.getState().select({ kind: "station", id: "S1" });
    useMission.getState().removeSelected();
    expect(useMission.getState().scenario.uavs[0].home).toBe("S2");
  });

  it("clears a stale plan when the scenario changes", () => {
    useMission.getState().setPlan(plan());
    useMission.getState().updateTask("T1", { energy: 1 });
    expect(useMission.getState().plan).toBeNull();
  });

  it("takes the reserve from a loaded scenario", () => {
    const s = scenario();
    s.energy!.reserve_ratio = 0.25;
    useMission.getState().setScenario(s, "abc");
    expect(useMission.getState().reservePercent).toBe(25);
    expect(useMission.getState().savedId).toBe("abc");
  });
});

describe("ui store", () => {
  it("adds and dismisses toasts", () => {
    useUi.getState().notify("hello", "info");
    const [toast] = useUi.getState().toasts;
    expect(toast.message).toBe("hello");
    useUi.getState().dismiss(toast.id);
    expect(useUi.getState().toasts).toHaveLength(0);
  });

  it("reports errors as toasts", () => {
    reportError(new Error("broken"));
    reportError("not an error");
    expect(useUi.getState().toasts.map((t) => t.message)).toEqual(["broken", "Something went wrong."]);
  });

  it("toggles the document's dark class", () => {
    useUi.getState().toggleTheme();
    expect(useUi.getState().theme).toBe("dark");
    expect(document.documentElement.classList.contains("dark")).toBe(true);
    useUi.getState().toggleTheme();
    expect(document.documentElement.classList.contains("dark")).toBe(false);
  });
});

describe("simulation store", () => {
  const frame = (overrides: Partial<SimFrame> = {}): SimFrame => ({ type: "frame", t: 5, uavs: [], events: [], end_time: 100, ...overrides });

  it("accumulates log entries but not on a seek", () => {
    const { receive } = useSimulation.getState();
    receive(frame({ events: [{ time: 1, kind: "TASK_COMPLETED" }] }));
    receive(frame({ events: [{ time: 2, kind: "LANDED" }], seeked: true }));
    expect(useSimulation.getState().log.map((e) => e.kind)).toEqual(["TASK_COMPLETED"]);
    expect(useSimulation.getState().t).toBe(5);
  });

  it("keeps the old route when a replan arrives", () => {
    const first = plan({ algorithm: "first" });
    const second = plan({ algorithm: "second" });
    useSimulation.setState({ plan: first });
    useSimulation.getState().receive(
      frame({
        replan: {
          time_ms: 3,
          plan: second,
          diff: { event_type: "UAV_UNAVAILABLE", scope: "local", escalated: false, escalation_reason: null, moved: [], added: [], dropped: [] },
        },
      }),
    );
    const state = useSimulation.getState();
    expect(state.plan).toBe(second);
    expect(state.oldPlan).toBe(first);
    expect(state.lastReplan?.time_ms).toBe(3);
  });
});

describe("panels", () => {
  it("shows the energy budget split, with the margin", () => {
    render(<BatteryBar route={route()} batteryMax={100} index={0} />);
    expect(screen.getByRole("img")).toHaveAccessibleName("U1: travel 12.0, task 8.0, return 6.0, reserve 10.0 EU");
    expect(screen.getByText(/margin 64.0/)).toBeInTheDocument();
  });

  it("renders one battery bar per route, or an empty state", () => {
    const { rerender } = render(<UAVPanel scenario={scenario()} plan={null} />);
    expect(screen.getByText(/Run a planner/)).toBeInTheDocument();
    rerender(<UAVPanel scenario={scenario()} plan={plan()} />);
    expect(screen.getByTestId("battery-U1")).toBeInTheDocument();
  });

  it("lists visit order and idle UAVs", () => {
    render(<RouteList plan={plan({ routes: [route({ task_ids: ["T2", "T1"] }), route({ uav_id: "U2", task_ids: [] })] })} />);
    expect(screen.getByText("T2 → T1", { exact: false })).toBeInTheDocument();
    expect(screen.getByText("idle")).toBeInTheDocument();
  });

  it("confirms a feasible plan", () => {
    render(<FeasibilityPanel plan={plan()} />);
    expect(screen.getByTestId("feasibility-ok")).toBeInTheDocument();
  });

  it("makes violations clickable", async () => {
    const violation = { type: "ENERGY_TOTAL" as const, uav_id: "U1", detail: "too far", margin: -5, task_id: "T1" };
    const onSelect = vi.fn();
    render(<FeasibilityPanel plan={plan({ report: { feasible: false, violations: [violation] } })} onSelect={onSelect} />);
    await userEvent.click(screen.getByRole("button"));
    expect(onSelect).toHaveBeenCalledWith(violation);
    expect(screen.getByText(/1 violation$/)).toBeInTheDocument();
  });

  it("explains candidates and rejections", () => {
    const explained = plan({
      explanations: [
        {
          task_id: "T1",
          chosen_uav: "U1",
          note: "lowest-cost feasible insertion",
          candidates: [
            { uav_id: "U1", position: 0, feasible: true, delta_distance: 100, delta_energy: 5, delta_time: 20, delta_imbalance: 0.1, cost: 0.4, rejected_reason: null },
            { uav_id: "U2", position: null, feasible: false, delta_distance: 0, delta_energy: 0, delta_time: 0, delta_imbalance: 0, cost: null, rejected_reason: "ENERGY_TOTAL: too far" },
          ],
        },
      ],
    });
    render(<ExplainPanel plan={explained} taskId="T1" />);
    expect(screen.getByText("chosen")).toBeInTheDocument();
    expect(screen.getByText("rejected: ENERGY_TOTAL: too far")).toBeInTheDocument();
  });

  it("says when an algorithm has no explanations", () => {
    const { rerender } = render(<ExplainPanel plan={plan()} taskId={null} />);
    expect(screen.getByText(/Click a task/)).toBeInTheDocument();
    rerender(<ExplainPanel plan={plan()} taskId="T1" />);
    expect(screen.getByText(/does not record/)).toBeInTheDocument();
  });

  it("shows why tasks are unassigned", () => {
    const s = scenario();
    render(<UnassignedList scenario={s} plan={plan({ unassigned: [{ task_id: "T1", reason: "no energy" }] })} />);
    expect(screen.getByText("no energy")).toBeInTheDocument();
  });

  it("shows the newest event first", () => {
    render(<EventLog entries={[{ time: 1, kind: "TASK_COMPLETED", task_id: "T1" }, { time: 9, kind: "REPLAN", detail: "local" }]} />);
    const items = screen.getAllByRole("listitem");
    expect(items[0]).toHaveTextContent("REPLAN (local)");
    expect(items[1]).toHaveTextContent("TASK COMPLETED · T1");
  });

  it("reports slider changes", () => {
    const onWeights = vi.fn();
    render(<WeightSliders weights={DEFAULT_WEIGHTS} reserve={10} onWeights={onWeights} onReserve={vi.fn()} />);
    fireEvent.change(screen.getByLabelText("Energy w_e"), { target: { value: "0.8" } });
    expect(onWeights).toHaveBeenCalledWith({ ...DEFAULT_WEIGHTS, w_e: 0.8 });
  });

  it("shows only the energy controls when energyOnly is set", () => {
    render(<WeightSliders energyOnly weights={DEFAULT_WEIGHTS} reserve={10} onWeights={vi.fn()} onReserve={vi.fn()} />);
    expect(screen.getByLabelText("Energy w_e")).toBeInTheDocument();
    expect(screen.getByLabelText("Reserve")).toBeInTheDocument();
    expect(screen.queryByLabelText("Distance w_d")).toBeNull();
    expect(screen.queryByLabelText("Time w_t")).toBeNull();
    expect(screen.queryByLabelText("Workload w_l")).toBeNull();
  });

  it("draws a box plot row per algorithm", () => {
    render(<BoxPlot unit="%" stats={[{ algorithm: "integrated", label: "Proposed", q1: 80, median: 85, q3: 90, low: 82, high: 88 }]} />);
    expect(screen.getByText("Proposed")).toBeInTheDocument();
    expect(screen.getByText("85.0")).toBeInTheDocument();
  });
});

describe("Model page", () => {
  it("opens on the proposal's infeasible example", () => {
    render(<Model />);
    expect(screen.getByTestId("example-result")).toHaveTextContent("Infeasible");
    expect(screen.getByTestId("example-result")).toHaveTextContent("20 + 10 + 0 = 30 EU");
  });

  it("collapses the assignment cost into one sentence", () => {
    render(<Model />);
    expect(screen.getByTestId("assignment-note")).toHaveTextContent(
      "Tasks are assigned by an internal energy-feasible insertion heuristic.",
    );
    expect(screen.queryByText("Assignment cost")).toBeNull();
  });
});
