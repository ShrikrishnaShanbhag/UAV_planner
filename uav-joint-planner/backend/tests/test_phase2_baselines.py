"""Phase 2: routing heuristics, baselines B1–B3, simulator, metrics and CLI."""

from __future__ import annotations

import itertools
import json
from dataclasses import replace

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from conftest import make_scenario, make_task, make_uav
from uavplan.cli import main
from uavplan.core.allocation.base import PlanningContext, priority_order
from uavplan.core.allocation.greedy_energy import GreedyEnergyPlanner
from uavplan.core.allocation.independent import IndependentPlanner
from uavplan.core.allocation.nearest import NearestUAVPlanner
from uavplan.core.feasibility import FeasibilityChecker
from uavplan.core.generator import preset
from uavplan.core.metrics import optimality_gap, plan_metrics, simulation_metrics
from uavplan.core.models import ENERGY_VIOLATIONS, Obstacle, Priority, Station, ViolationType
from uavplan.core.registry import get_planner
from uavplan.core.routing.construct import cheapest_insertion, nearest_neighbour
from uavplan.core.routing.improve import inter_route_search, or_opt, two_opt
from uavplan.core.simulator import SimConfig, Simulator
from uavplan.core.state import TaskStatus


def euclid(a, b) -> float:
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5


def lower(a: float, b: float) -> bool:
    return a < b - 1e-9


# -- construction and improvement -------------------------------------------------------


def test_nearest_neighbour_order() -> None:
    stops = [("far", (9, 0)), ("near", (1, 0)), ("mid", (5, 0))]
    assert nearest_neighbour((0, 0), stops, euclid) == ("near", "mid", "far")


def test_cheapest_insertion_contains_all_stops() -> None:
    stops = [("a", (5, 0)), ("b", (1, 0)), ("c", (9, 1))]
    route = cheapest_insertion((0, 0), (0, 0), stops, euclid)
    assert sorted(route) == ["a", "b", "c"]


coords = st.tuples(st.integers(1, 19), st.integers(1, 19))


def tour_scenario(cells, battery=1000.0):
    unique = sorted(set(cells))
    tasks = [make_task(f"T{i}", c, energy=1.0) for i, c in enumerate(unique)]
    return make_scenario([make_uav(battery=battery, battery_max=1000.0)], tasks)


@settings(max_examples=60, deadline=None)
@given(st.lists(coords, min_size=2, max_size=8))
def test_two_opt_never_worsens_and_stays_feasible(cells) -> None:
    scenario = tour_scenario(cells)
    checker = FeasibilityChecker(scenario)
    seq = tuple(t.id for t in scenario.tasks)
    before = checker.evaluate("U1", seq)
    assert before.feasible
    score = lambda s: (
        len(checker.evaluate("U1", s).violations),
        checker.evaluate("U1", s).distance,
    )
    better = lambda a, b: a[0] < b[0] or (a[0] == b[0] and a[1] < b[1] - 1e-9)
    result = two_opt(seq, score, better)
    after = checker.evaluate("U1", result)
    assert sorted(result) == sorted(seq)
    assert after.distance <= before.distance + 1e-9
    assert after.feasible


@settings(max_examples=40, deadline=None)
@given(st.lists(coords, min_size=2, max_size=7))
def test_or_opt_never_worsens(cells) -> None:
    scenario = tour_scenario(cells)
    checker = FeasibilityChecker(scenario)
    seq = tuple(t.id for t in scenario.tasks)
    result = or_opt(seq, lambda s: checker.evaluate("U1", s).distance, lower)
    assert sorted(result) == sorted(seq)
    assert checker.evaluate("U1", result).distance <= checker.evaluate("U1", seq).distance + 1e-9


def test_inter_route_search_moves_tasks_to_the_better_uav() -> None:
    positions = {"a": 1, "b": 2, "x": 100, "y": 101}
    homes = {"U1": 0, "U2": 100}

    def score(assignment) -> float:
        return sum(abs(positions[t] - homes[u]) for u, seq in assignment.items() for t in seq)

    start = {"U1": ("x", "a"), "U2": ("b", "y")}
    result = inter_route_search(start, score, lower, lambda _u, _t: True)
    assert set(result["U1"]) == {"a", "b"} and set(result["U2"]) == {"x", "y"}


def test_inter_route_respects_capability_filter() -> None:
    start = {"U1": ("a",), "U2": ()}
    result = inter_route_search(
        start, lambda a: 0.0 if a["U2"] else 1.0, lower, lambda u, _t: u == "U1"
    )
    assert result == start


def test_inter_route_two_opt_star_swaps_tails() -> None:
    # Only exchanging both tails together improves this objective.
    def score(a) -> float:
        return 0.0 if a == {"U1": ("a", "d"), "U2": ("c", "b")} else 1.0

    start = {"U1": ("a", "b"), "U2": ("c", "d")}
    result = inter_route_search(start, score, lower, lambda _u, _t: True)
    assert result == {"U1": ("a", "d"), "U2": ("c", "b")}


# -- baselines --------------------------------------------------------------------------


def starving_scenario():
    """All tasks sit next to U1, whose battery cannot serve them all."""
    uavs = [make_uav("U1", battery=30.0), make_uav("U2", start=(19, 0), battery=100.0)]
    tasks = [make_task(f"T{i}", (1 + i, 1), energy=10.0) for i in range(6)]
    stations = [Station("S1", (0, 0)), Station("S2", (19, 0))]
    return make_scenario(uavs, tasks, stations)


def test_priority_order() -> None:
    tasks = [
        make_task("low", (1, 1), priority=Priority.LOW),
        make_task("late", (1, 1), priority=Priority.CRITICAL, deadline=50.0),
        make_task("early", (1, 1), priority=Priority.CRITICAL, deadline=10.0),
        make_task("none", (1, 1), priority=Priority.CRITICAL),
    ]
    assert [t.id for t in priority_order(tasks)] == ["early", "late", "none", "low"]


def test_nearest_may_be_infeasible_but_reports_it() -> None:
    plan = NearestUAVPlanner().plan(starving_scenario())
    assert not plan.report.feasible
    assert {v.type for v in plan.report.violations} & ENERGY_VIOLATIONS
    assert not plan.routes[0].feasible


def test_nearest_respects_capability_and_reports_unassigned() -> None:
    scenario = make_scenario([make_uav()], [make_task("T", (1, 1), capability="lidar")])
    plan = NearestUAVPlanner().plan(scenario)
    assert plan.unassigned[0].task_id == "T"
    assert "capability" in plan.unassigned[0].reason


@pytest.mark.parametrize("seed", range(8))
@pytest.mark.parametrize("preset_id", ["A", "B", "C"])
def test_greedy_energy_never_violates_energy(preset_id: str, seed: int) -> None:
    plan = GreedyEnergyPlanner().plan(preset(preset_id, seed))
    assert not [v for v in plan.report.violations if v.type in ENERGY_VIOLATIONS]


def test_greedy_energy_uses_the_other_uav_when_one_is_exhausted() -> None:
    plan = GreedyEnergyPlanner().plan(starving_scenario())
    assignment = plan.assignment()
    assert assignment["U2"]
    assert not [v for v in plan.report.violations if v.type in ENERGY_VIOLATIONS]


def test_greedy_energy_reports_task_that_cannot_be_done() -> None:
    uav = make_uav(battery=12.0)
    scenario = make_scenario([uav], [make_task("T", (1, 1), energy=50.0)])
    plan = GreedyEnergyPlanner().plan(scenario)
    assert plan.unassigned[0].task_id == "T"
    plan = GreedyEnergyPlanner().plan(
        make_scenario([make_uav()], [make_task("T", (1, 1), capability="lidar")])
    )
    assert "capability" in plan.unassigned[0].reason


@pytest.mark.parametrize("seed", range(8))
@pytest.mark.parametrize("preset_id", ["A", "B", "C"])
def test_independent_is_always_feasible(preset_id: str, seed: int) -> None:
    scenario = preset(preset_id, seed)
    plan = IndependentPlanner().plan(scenario)
    assert plan.report.feasible
    placed = [t for r in plan.routes for t in r.task_ids]
    assert sorted(placed + [u.task_id for u in plan.unassigned]) == sorted(
        t.id for t in scenario.tasks
    )


def test_independent_balances_task_counts() -> None:
    uavs = [
        make_uav("U1", battery=1000.0, battery_max=1000.0),
        make_uav("U2", start=(0, 1), battery=1000.0, battery_max=1000.0),
    ]
    tasks = [make_task(f"T{i}", (i + 1, 1), energy=1.0) for i in range(6)]
    plan = IndependentPlanner().plan(make_scenario(uavs, tasks, [Station("S1", (0, 0))]))
    assert sorted(len(r.task_ids) for r in plan.routes) == [3, 3]


def test_independent_drops_lowest_priority_first() -> None:
    uav = make_uav(battery=25.0)
    tasks = [
        make_task("keep", (1, 0), energy=10.0, priority=Priority.CRITICAL),
        make_task("drop", (2, 0), energy=10.0, priority=Priority.LOW),
    ]
    plan = IndependentPlanner().plan(make_scenario([uav], tasks))
    assert plan.assignment()["U1"] == ("keep",)
    assert plan.unassigned[0].task_id == "drop"


def test_independent_reports_unreachable_tasks() -> None:
    wall = Obstacle("w", rect=(5, 0, 5, 19))
    scenario = make_scenario([make_uav()], [make_task("T", (10, 0))], obstacles=[wall])
    plan = IndependentPlanner().plan(scenario)
    assert "reach" in plan.unassigned[0].reason


@pytest.mark.parametrize("name", ["nearest", "greedy_energy", "independent"])
def test_baselines_are_deterministic(name: str) -> None:
    scenario = preset("B", 3)
    planner = get_planner(name)
    assert planner.plan(scenario).canonical_json() == planner.plan(scenario).canonical_json()


def test_registry_rejects_unknown() -> None:
    with pytest.raises(KeyError):
        get_planner("nope")


def test_context_with_relaxed_checker_keeps_strict_report() -> None:
    scenario = preset("A", 1)
    ctx = PlanningContext(scenario, strict_prefix=False)
    assert ctx.checker is not ctx.report_checker
    assert ctx.report_checker.strict_prefix


# -- simulator --------------------------------------------------------------------------


@pytest.mark.parametrize("seed", range(5))
@pytest.mark.parametrize("name", ["greedy_energy", "independent"])
def test_simulated_energy_equals_planned_energy(name: str, seed: int) -> None:
    scenario = preset("B", seed)
    plan = get_planner(name).plan(scenario)
    result = Simulator(scenario, plan).run()
    for route in plan.routes:
        outcome = result.outcomes[route.uav_id]
        assert outcome.energy_used == pytest.approx(route.total_energy, abs=1e-9)
        assert outcome.distance == pytest.approx(route.distance, abs=1e-9)
        if route.task_ids:
            assert outcome.work_end == pytest.approx(route.completion_time, abs=1e-9)


def test_simulator_completes_planned_tasks_and_lands() -> None:
    scenario = preset("A", 2)
    plan = get_planner("independent").plan(scenario)
    result = Simulator(scenario, plan).run()
    planned = {t for r in plan.routes for t in r.task_ids}
    done = {t for t, s in result.task_status.items() if s == TaskStatus.COMPLETED}
    assert planned == done
    assert all(o.landed for o in result.outcomes.values() if o.flew)
    kinds = {e.kind for e in result.log}
    assert {"TASK_COMPLETED", "LANDED"} <= kinds


def test_log_has_one_landing_per_uav_and_no_false_strandings() -> None:
    """Regression: a whole route completing at once must not look like many endings."""
    scenario = preset("B", 2)
    plan = get_planner("integrated").plan(scenario)
    result = Simulator(scenario, plan).run()
    flown = sorted(u for u, o in result.outcomes.items() if o.flew)
    assert sorted(e.uav_id for e in result.log if e.kind == "LANDED") == flown
    assert not [e for e in result.log if e.kind == "STRANDED"]


def test_infeasible_plan_depletes_battery_and_fails_tasks() -> None:
    scenario = starving_scenario()
    plan = NearestUAVPlanner().plan(scenario)
    result = Simulator(scenario, plan).run()
    assert result.outcomes["U1"].depleted
    assert not result.outcomes["U1"].landed
    assert TaskStatus.FAILED in result.task_status.values()
    assert any(e.kind == "BATTERY_DEPLETED" for e in result.log)
    metrics = simulation_metrics(result, scenario)
    assert metrics["depleted_uavs"] == 1.0
    assert metrics["completion_rate"] < 100.0


def test_depletion_partway_along_a_leg_stops_early() -> None:
    uav = make_uav(battery=2.0, battery_max=100.0, energy_per_meter=0.1)
    # Infeasible on purpose: the planner does not check energy.
    scenario = make_scenario([uav], [make_task("T", (19, 0), energy=0.0)])
    plan = NearestUAVPlanner().plan(scenario)
    result = Simulator(scenario, plan).run()
    outcome = result.outcomes["U1"]
    assert outcome.depleted
    assert 0 < outcome.distance < 190.0
    assert result.segments["U1"][-1].b1 == 0.0


def test_timeline_interpolates_and_is_monotone_in_battery() -> None:
    scenario = preset("A", 4)
    plan = get_planner("independent").plan(scenario)
    result = Simulator(scenario, plan).run()
    frames = result.timeline(tick=1.0)
    assert frames[0]["t"] == 0.0
    assert frames[-1]["t"] == pytest.approx(result.end_time)
    for uav_index in range(len(scenario.uavs)):
        batteries = [f["uavs"][uav_index]["battery"] for f in frames]
        assert all(b <= a + 1e-9 for a, b in itertools.pairwise(batteries))
    assert len(result.timeline(tick=1.0, max_frames=20)) <= 21


def test_timeline_reports_start_and_end_states() -> None:
    scenario = preset("A", 4)
    plan = get_planner("independent").plan(scenario)
    result = Simulator(scenario, plan).run()
    uav = scenario.uavs[0]
    assert result.sample(uav.id, -1.0)["status"] == "idle"
    assert result.sample(uav.id, result.end_time + 100.0)["status"] == "charging"
    assert result.to_dict()["scenario_id"] == "A"


def test_noise_changes_energy_and_is_seeded() -> None:
    scenario = preset("A", 5)
    plan = get_planner("independent").plan(scenario)
    clean = Simulator(scenario, plan).run()
    noisy_a = Simulator(scenario, plan, SimConfig(noise_sigma=0.2, seed=9)).run()
    noisy_b = Simulator(scenario, plan, SimConfig(noise_sigma=0.2, seed=9)).run()
    other = Simulator(scenario, plan, SimConfig(noise_sigma=0.2, seed=10)).run()
    energy = lambda r: [o.energy_used for o in r.outcomes.values()]
    assert energy(noisy_a) == energy(noisy_b)
    assert energy(noisy_a) != energy(clean)
    assert energy(noisy_a) != energy(other)


def test_takeoff_landing_and_waiting_are_simulated() -> None:
    uav = make_uav(takeoff_energy=2.0, landing_energy=3.0, hover_rate=0.1)
    task = make_task("T", (10, 0), release_time=50.0, energy=1.0)
    scenario = make_scenario([uav], [task])
    plan = NearestUAVPlanner().plan(scenario)
    result = Simulator(scenario, plan).run()
    kinds = [s.kind for s in result.segments["U1"]]
    assert kinds == ["takeoff", "move", "wait", "work", "move", "landing"]
    assert result.outcomes["U1"].energy_used == pytest.approx(plan.routes[0].total_energy)


def test_deadline_miss_is_recorded() -> None:
    scenario = make_scenario([make_uav()], [make_task("T", (10, 0), deadline=1.0)])
    plan = NearestUAVPlanner().plan(scenario)
    assert any(v.type == ViolationType.DEADLINE for v in plan.report.violations)
    result = Simulator(scenario, plan).run()
    assert result.deadlines_missed == ["T"]


def test_uav_without_reachable_station_is_stranded() -> None:
    wall = Obstacle("w", rect=(5, 0, 5, 19))
    uav = replace(make_uav(start=(1, 0)), airborne=True)
    scenario = make_scenario(
        [uav], [make_task("T", (2, 0))], [Station("S1", (10, 0))], obstacles=[wall]
    )
    plan = NearestUAVPlanner().plan(scenario)
    result = Simulator(scenario, plan).run()
    assert any(e.kind == "STRANDED" for e in result.log)
    assert result.outcomes["U1"].flew and not result.outcomes["U1"].landed


# -- metrics ----------------------------------------------------------------------------


def test_plan_metrics_values(small_scenario) -> None:
    plan = get_planner("independent").plan(small_scenario)
    metrics = plan_metrics(plan, small_scenario)
    assert metrics["completion_rate"] == 100.0
    assert metrics["total_energy"] == pytest.approx(sum(r.total_energy for r in plan.routes))
    assert metrics["energy_feasibility_rate"] == 100.0
    assert metrics["min_battery_margin"] == pytest.approx(
        min(r.margin for r in plan.routes if r.task_ids)
    )


def test_plan_metrics_flag_energy_infeasible_routes() -> None:
    scenario = starving_scenario()
    metrics = plan_metrics(NearestUAVPlanner().plan(scenario), scenario)
    assert metrics["energy_feasibility_rate"] < 100.0
    assert metrics["plan_feasible"] == 0.0


def test_simulation_metrics_match_plan_when_noise_free() -> None:
    scenario = preset("A", 6)
    plan = get_planner("independent").plan(scenario)
    planned = plan_metrics(plan, scenario)
    simulated = simulation_metrics(Simulator(scenario, plan).run(), scenario)
    for name in ("total_energy", "total_distance", "makespan", "completion_rate"):
        assert simulated[name] == pytest.approx(planned[name], abs=1e-6), name
    assert simulated["stranded_uavs"] == 0.0


def test_optimality_gap() -> None:
    assert optimality_gap(11.0, 10.0) == pytest.approx(10.0)
    assert optimality_gap(0.0, 0.0) == 0.0
    assert optimality_gap(1.0, 0.0) == float("inf")


# -- CLI --------------------------------------------------------------------------------


def test_cli_plan_simulate_generate(tmp_path, capsys) -> None:
    scenario_path = tmp_path / "scenario.json"
    assert main(["generate", "--preset", "A", "--seed", "1", "--out", str(scenario_path)]) == 0
    plan_path = tmp_path / "plan.json"
    code = main(
        [
            "plan",
            "--scenario",
            str(scenario_path),
            "--algorithm",
            "nearest",
            "--out",
            str(plan_path),
        ]
    )
    assert code == 0
    assert json.loads(plan_path.read_text(encoding="utf-8"))["algorithm"] == "nearest"
    sim_path = tmp_path / "sim.json"
    assert main(["simulate", "--scenario", "A", "--noise", "0.05", "--out", str(sim_path)]) == 0
    assert "simulated" in capsys.readouterr().out
