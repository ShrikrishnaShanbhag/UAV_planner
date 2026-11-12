"""Phase 1: environment, energy, feasibility, cost and generator."""

from __future__ import annotations

import json
import math
from dataclasses import replace
from pathlib import Path

import networkx as nx
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from conftest import make_scenario, make_task, make_uav
from uavplan.core.cost import (
    CostWeights,
    InsertionCandidate,
    ObjectiveScales,
    ObjectiveWeights,
    PlanScore,
    coefficient_of_variation,
    compare_scores,
    compute_objective,
    fixed_scales,
    score_candidates,
)
from uavplan.core.energy import EnergyEstimator
from uavplan.core.environment import EnvironmentManager, octile, point_in_polygon, polygon_cells
from uavplan.core.feasibility import FeasibilityChecker
from uavplan.core.generator import (
    PRESET_IDS,
    GeneratorParams,
    generate,
    preset,
    validate_scenario,
)
from uavplan.core.models import (
    EnergyConfig,
    Event,
    EventType,
    GridSpec,
    Obstacle,
    PathInfo,
    Priority,
    Scenario,
    Station,
    Task,
    UAVStatus,
    ViolationType,
)
from uavplan.core.routing.paths import astar, dijkstra, energy_astar
from uavplan.core.state import MissionState, TaskManager, TaskStatus

GRID = GridSpec(10, 10, 20.0)


def types_of(report_or_eval) -> set[ViolationType]:
    return {v.type for v in report_or_eval.violations}


# -- environment ------------------------------------------------------------------------


def test_graph_excludes_obstacle_cells() -> None:
    env = EnvironmentManager(GRID, [Obstacle("o", rect=(2, 2, 4, 4))])
    assert not env.is_free((3, 3))
    assert env.is_free((1, 1))
    graph = env.to_networkx()
    assert (3, 3) not in graph
    assert graph.number_of_nodes() == 100 - 9


def test_polygon_and_no_fly_are_removed() -> None:
    triangle = ((1.0, 1.0), (7.0, 1.0), (1.0, 7.0))
    env = EnvironmentManager(GRID, [Obstacle("p", "no_fly", polygon=triangle)])
    assert not env.is_free((2, 2))
    assert env.is_free((8, 8))
    assert point_in_polygon(1.0, 1.0, triangle)
    assert not point_in_polygon(9.0, 9.0, triangle)
    assert (2, 2) in polygon_cells(triangle, 10, 10)


def test_diagonal_edges_cost_sqrt2_cell_size() -> None:
    env = EnvironmentManager(GRID)
    distance, _ = env.edge((0, 0), (1, 1))
    assert distance == pytest.approx(math.sqrt(2) * 20.0)


def test_corner_cutting_is_forbidden() -> None:
    env = EnvironmentManager(GRID, [Obstacle("o", rect=(1, 0, 1, 0))])
    assert (1, 1) not in [n for n, _, _ in env.neighbors((0, 0))]
    path = env.path((0, 0), (1, 1))
    assert path is not None
    assert path.cells == ((0, 0), (0, 1), (1, 1))
    assert path.distance == pytest.approx(40.0)


def test_unreachable_returns_none() -> None:
    wall = Obstacle("wall", rect=(5, 0, 5, 9))
    env = EnvironmentManager(GRID, [wall])
    assert env.path((0, 0), (9, 9)) is None
    assert env.path((5, 5), (0, 0)) is None
    assert dijkstra(env, (0, 0), (9, 9)) is None


def test_path_symmetry_and_trivial_path() -> None:
    env = EnvironmentManager(GRID)
    forward, backward = env.path((0, 0), (9, 4)), env.path((9, 4), (0, 0))
    assert forward is not None and backward is not None
    assert forward.distance == backward.distance
    assert forward.cells == backward.cells[::-1]
    same = env.path((3, 3), (3, 3))
    assert same is not None and same.distance == 0.0


def test_block_invalidates_cache() -> None:
    env = EnvironmentManager(GRID)
    before = env.path((0, 5), (9, 5))
    assert before is not None
    version = env.version
    env.block_cells([(5, y) for y in range(10)])
    assert env.version > version
    assert env.path((0, 5), (9, 5)) is None


def test_escape_cell_finds_nearest_free() -> None:
    env = EnvironmentManager(GRID, [Obstacle("o", rect=(2, 2, 4, 4))])
    assert env.escape_cell((1, 1)) == (1, 1)
    escaped = env.escape_cell((3, 3))
    assert escaped is not None and env.is_free(escaped)


@st.composite
def random_grids(draw):
    w = draw(st.integers(4, 14))
    h = draw(st.integers(4, 14))
    blocked = draw(
        st.lists(st.tuples(st.integers(0, w - 1), st.integers(0, h - 1)), max_size=w * h // 4)
    )
    a = draw(st.tuples(st.integers(0, w - 1), st.integers(0, h - 1)))
    b = draw(st.tuples(st.integers(0, w - 1), st.integers(0, h - 1)))
    grid = GridSpec(w, h, 10.0)
    env = EnvironmentManager(
        grid, [Obstacle("o", cells=tuple(c for c in blocked if c not in (a, b)))]
    )
    return env, a, b


@settings(max_examples=120, deadline=None)
@given(random_grids())
def test_astar_matches_dijkstra(case) -> None:
    env, a, b = case
    d, h = dijkstra(env, a, b), astar(env, a, b)
    assert (d is None) == (h is None)
    if d is not None and h is not None:
        assert h.cost == pytest.approx(d.cost)
        assert h.expanded <= d.expanded
        assert octile(a, b, 10.0) <= d.cost + 1e-9
        cached = env.path(a, b, "distance")
        assert cached is not None and cached.distance == pytest.approx(d.cost)


@settings(max_examples=60, deadline=None)
@given(random_grids(), st.floats(1.0, 3.0))
def test_energy_astar_matches_energy_dijkstra(case, multiplier) -> None:
    env, a, b = case
    env.apply_region(Obstacle("z", "energy_zone", rect=(0, 0, 3, 3), energy_multiplier=multiplier))
    d, h = dijkstra(env, a, b, use_effort=True), energy_astar(env, a, b)
    assert (d is None) == (h is None)
    if d is not None and h is not None:
        assert h.cost == pytest.approx(d.cost)


def test_networkx_agrees_with_cache() -> None:
    env = EnvironmentManager(GRID, [Obstacle("o", rect=(3, 0, 3, 7))])
    length = nx.shortest_path_length(env.to_networkx(), (0, 0), (9, 0), weight="distance")
    path = env.path((0, 0), (9, 0), "distance")
    assert path is not None and path.distance == pytest.approx(length)


def test_energy_path_avoids_expensive_zone() -> None:
    """The shortest path is not necessarily the lowest-energy path."""
    zone = Obstacle("z", "energy_zone", rect=(3, 0, 6, 4), energy_multiplier=5.0)
    env = EnvironmentManager(GridSpec(10, 10, 20.0), [zone])
    by_distance = env.path((0, 2), (9, 2), "distance")
    by_energy = env.path((0, 2), (9, 2), "energy")
    assert by_distance is not None and by_energy is not None
    assert by_energy.distance > by_distance.distance
    assert by_energy.effort < by_distance.effort


# -- energy -----------------------------------------------------------------------------


def _path(distance: float, effort: float | None = None, turn: float = 0.0) -> PathInfo:
    return PathInfo(
        ((0, 0), (1, 0)), distance, distance if effort is None else effort, turn, (), ()
    )


def test_normalized_travel_energy() -> None:
    est = EnergyEstimator()
    assert est.travel_energy(make_uav(energy_per_meter=0.05), _path(200.0)) == pytest.approx(10.0)
    assert est.unit == "EU"


def test_physical_100w_for_60s_is_6kj() -> None:
    est = EnergyEstimator(EnergyConfig(mode="physical"))
    uav = make_uav(power_cruise=100.0, speed=10.0)
    assert est.travel_energy(uav, _path(600.0)) == pytest.approx(6000.0)
    assert est.unit == "J"


def test_physical_task_energy_and_turn_penalty() -> None:
    est = EnergyEstimator(EnergyConfig(mode="physical", turn_energy_per_rad=50.0))
    uav = make_uav(power_hover=120.0, power_cruise=100.0, speed=10.0)
    task = make_task("T", (1, 1), energy=30.0, duration=10.0)
    assert est.task_energy(uav, task) == pytest.approx(1230.0)
    assert est.travel_energy(uav, _path(100.0, turn=math.pi)) == pytest.approx(
        1000.0 + 50.0 * math.pi
    )
    assert est.wait_energy(uav, 2.0) == pytest.approx(240.0)


def test_reserve_uses_capacity_not_current_battery() -> None:
    est = EnergyEstimator(EnergyConfig(reserve_ratio=0.2))
    assert est.reserve(make_uav(battery=50.0, battery_max=100.0)) == pytest.approx(20.0)


def test_return_uses_cheapest_station_or_home_only() -> None:
    stations = [Station("S1", (0, 0)), Station("S2", (9, 0))]
    uav = make_uav(start=(8, 0), home="S1")
    near = make_scenario([uav], [], stations)
    checker = FeasibilityChecker(near)
    option = checker.return_option(uav, (8, 0))
    assert option is not None and option.station_id == "S2"
    home_only = make_scenario([uav], [], stations, energy=EnergyConfig(home_only_return=True))
    option = FeasibilityChecker(home_only).return_option(uav, (8, 0))
    assert option is not None and option.station_id == "S1"


def test_takeoff_and_landing_counted_once() -> None:
    uav = make_uav(takeoff_energy=2.0, landing_energy=3.0)
    scenario = make_scenario([uav], [make_task("T1", (0, 3), energy=1.0)])
    ev = FeasibilityChecker(scenario).evaluate("U1", ("T1",))
    assert ev.takeoff_energy == 2.0 and ev.landing_energy == 3.0
    assert ev.total_energy == pytest.approx(2.0 + 3.0 + 1.0 + 2 * 30.0 * 0.01)


# -- feasibility ------------------------------------------------------------------------


def test_25eu_vs_30eu_example_is_infeasible() -> None:
    """Proposal example: 25 EU available, task 20 EU + return 10 EU = 30 EU."""
    # energy_per_meter makes the 100 m trip cost 10 EU; the outbound leg is free of charge
    # because the UAV is already above the task.
    uav = make_uav(start=(10, 0), battery=25.0, battery_max=100.0, energy_per_meter=0.1)
    task = make_task("J", (10, 0), energy=20.0)
    scenario = make_scenario(
        [uav], [task], [Station("S1", (0, 0))], energy=EnergyConfig(reserve_ratio=0.0)
    )
    ev = FeasibilityChecker(scenario).evaluate("U1", ("J",))
    assert ev.return_energy == pytest.approx(10.0)
    assert ev.task_energy == pytest.approx(20.0)
    assert ev.total_energy == pytest.approx(30.0)
    assert types_of(ev) == {ViolationType.ENERGY_TOTAL}
    assert ev.margin == pytest.approx(-5.0)


def test_25eu_example_feasible_with_closer_station() -> None:
    """The proposal notes a closer safe landing point makes the assignment feasible."""
    uav = make_uav(start=(10, 0), battery=25.0, battery_max=100.0, energy_per_meter=0.1)
    task = make_task("J", (10, 0), energy=20.0)
    scenario = make_scenario(
        [uav],
        [task],
        [Station("S1", (0, 0)), Station("S2", (12, 0))],
        energy=EnergyConfig(reserve_ratio=0.0),
    )
    assert FeasibilityChecker(scenario).evaluate("U1", ("J",)).feasible


def test_reserve_makes_a_tight_route_infeasible() -> None:
    uav = make_uav(start=(0, 0), battery=11.0, energy_per_meter=0.0)
    scenario = make_scenario([uav], [make_task("T", (1, 0), energy=5.0)])
    ev = FeasibilityChecker(scenario).evaluate("U1", ("T",))
    assert ViolationType.ENERGY_TOTAL in types_of(ev)  # 5 + reserve 10 > 11


def test_prefix_safety_catches_route_safe_in_total_only() -> None:
    """Feasible in total but unsafe in the middle.

    With distance-shortest paths, the only shortest return from T1 runs straight along
    a high-multiplier row (109 EU), while the diagonal route through T2 to the same
    station costs 14.14 EU. The route fits in a 20 EU battery, but turning back at T1
    would be unaffordable.
    """
    zone = Obstacle("z", "energy_zone", rect=(1, 0, 9, 0), energy_multiplier=12.0)
    grid = GridSpec(11, 6, 10.0)
    energy = EnergyConfig(reserve_ratio=0.0, path_metric="distance")
    uav = make_uav(start=(0, 0), battery=20.0, battery_max=100.0, energy_per_meter=0.1)
    tasks = [make_task("T1", (0, 0), energy=0.0), make_task("T2", (5, 5), energy=0.0)]
    scenario = make_scenario(
        [uav], tasks, [Station("S1", (10, 0))], [zone], grid=grid, energy=energy
    )
    strict = FeasibilityChecker(scenario, strict_prefix=True).evaluate("U1", ("T1", "T2"))
    relaxed = FeasibilityChecker(scenario, strict_prefix=False).evaluate("U1", ("T1", "T2"))
    assert relaxed.total_energy == pytest.approx(10 * math.sqrt(2) * 10 * 0.1)
    assert relaxed.feasible
    assert types_of(strict) == {ViolationType.ENERGY_PREFIX}
    assert strict.violations[0].margin == pytest.approx(20.0 - 0.0 - 109.0)


def test_non_strict_prefix_flags_battery_below_reserve() -> None:
    uav = make_uav(start=(0, 0), battery=20.0, energy_per_meter=0.0)
    tasks = [make_task("A", (1, 0), energy=15.0), make_task("B", (2, 0), energy=0.0)]
    scenario = make_scenario([uav], tasks)
    ev = FeasibilityChecker(scenario, strict_prefix=False).evaluate("U1", ("A", "B"))
    assert ViolationType.ENERGY_PREFIX in types_of(ev)


def test_capability_violation() -> None:
    scenario = make_scenario([make_uav()], [make_task("T", (1, 1), capability="lidar")])
    assert ViolationType.CAPABILITY in types_of(FeasibilityChecker(scenario).evaluate("U1", ("T",)))


def test_unreachable_and_no_return_violations() -> None:
    wall = Obstacle("w", rect=(5, 0, 5, 19))
    uav = make_uav(start=(0, 0))
    # The task is across the wall.
    scenario = make_scenario([uav], [make_task("T", (10, 0))], obstacles=[wall])
    assert ViolationType.UNREACHABLE in types_of(
        FeasibilityChecker(scenario).evaluate("U1", ("T",))
    )
    # The only station is across the wall.
    far = make_scenario([uav], [make_task("T", (1, 0))], [Station("S1", (10, 0))], obstacles=[wall])
    ev = FeasibilityChecker(far).evaluate("U1", ("T",))
    assert ViolationType.NO_RETURN in types_of(ev)


def test_mid_route_no_return_reported_under_strict() -> None:
    wall = Obstacle("w", rect=(5, 0, 5, 19))
    station = Station("S1", (0, 0))
    uav = make_uav(start=(0, 0))
    tasks = [make_task("A", (1, 0)), make_task("B", (2, 0))]
    scenario = make_scenario([uav], tasks, [station], obstacles=[wall])
    env = EnvironmentManager.from_scenario(scenario)
    checker = FeasibilityChecker(scenario, env=env)
    assert checker.evaluate("U1", ("A", "B")).feasible
    env.block_cells([(0, 0)])  # the station is destroyed; caches must refresh
    assert ViolationType.NO_RETURN in types_of(checker.evaluate("U1", ("A", "B")))


def test_deadline_violation_and_margin() -> None:
    task = make_task("T", (10, 0), duration=10.0, deadline=5.0)
    scenario = make_scenario([make_uav()], [task])
    ev = FeasibilityChecker(scenario).evaluate("U1", ("T",))
    assert ViolationType.DEADLINE in types_of(ev)


def test_unavailable_uav_cannot_take_tasks() -> None:
    uav = replace(make_uav(), status=UAVStatus.UNAVAILABLE)
    scenario = make_scenario([uav], [make_task("T", (1, 1))])
    checker = FeasibilityChecker(scenario)
    assert ViolationType.UAV_UNAVAILABLE in types_of(checker.evaluate("U1", ("T",)))
    assert checker.evaluate("U1", ()).feasible


def test_release_time_delays_start() -> None:
    task = make_task("T", (10, 0), duration=5.0, release_time=100.0)
    uav = make_uav(hover_rate=0.1)
    scenario = make_scenario([uav], [task])
    ev = FeasibilityChecker(scenario).evaluate("U1", ("T",))
    assert ev.wait_energy == pytest.approx((100.0 - 10.0) * 0.1)
    assert ev.completion_time == pytest.approx(105.0 + 10.0)


def test_idle_route_at_station_costs_nothing() -> None:
    scenario = make_scenario([make_uav(battery=5.0)], [])
    ev = FeasibilityChecker(scenario).evaluate("U1", ())
    assert ev.total_energy == 0.0 and ev.feasible


def test_airborne_empty_route_must_return() -> None:
    uav = replace(make_uav(start=(10, 0), battery=11.0), airborne=True)
    scenario = make_scenario([uav], [])
    ev = FeasibilityChecker(scenario).evaluate("U1", ())
    assert ev.return_energy == pytest.approx(1.0)
    assert ev.feasible


def test_build_route_matches_evaluate(small_scenario: Scenario) -> None:
    checker = FeasibilityChecker(small_scenario)
    ev = checker.evaluate("U1", ("T1", "T2"))
    route = checker.build_route("U1", ("T1", "T2"))
    assert route.total_energy == pytest.approx(ev.total_energy)
    assert [leg.kind for leg in route.legs] == ["to_task", "to_task", "return"]
    assert route.battery_end == pytest.approx(route.profile[-1].battery)
    assert route.feasible


def test_check_assignment_collects_violations(small_scenario: Scenario) -> None:
    checker = FeasibilityChecker(small_scenario)
    assert checker.check_assignment({"U1": ("T1",), "U2": ("T3",)}).feasible
    with pytest.raises(KeyError):
        checker.check_assignment({"nope": ()})


# -- cost -------------------------------------------------------------------------------


def _candidate(uav: str, d: float, e: float, t: float, b: float) -> InsertionCandidate:
    return InsertionCandidate(uav, 0, (), d, e, t, b)


def test_score_candidates_normalizes_by_max() -> None:
    a, b = _candidate("a", 100.0, 10.0, 50.0, 0.0), _candidate("b", 50.0, 20.0, 25.0, 0.2)
    score_candidates([a, b], CostWeights())
    assert a.cost == pytest.approx(0.2 * 1.0 + 0.4 * 0.5 + 0.3 * 1.0 + 0.0)
    assert b.cost == pytest.approx(0.2 * 0.5 + 0.4 * 1.0 + 0.3 * 0.5 + 0.1 * 1.0)


def test_score_candidates_minmax() -> None:
    a, b = _candidate("a", 100.0, 10.0, 50.0, 0.0), _candidate("b", 50.0, 20.0, 25.0, 0.2)
    score_candidates([a, b], CostWeights(normalization="minmax"))
    assert a.cost == pytest.approx(0.2 + 0.0 + 0.3 + 0.0)
    assert b.cost == pytest.approx(0.0 + 0.4 + 0.0 + 0.1)


def test_coefficient_of_variation() -> None:
    assert coefficient_of_variation([]) == 0.0
    assert coefficient_of_variation([0.0, 0.0]) == 0.0
    assert coefficient_of_variation([10.0, 10.0]) == 0.0
    assert coefficient_of_variation([5.0, 15.0]) == pytest.approx(0.5)


def test_objective_terms_and_penalty(small_scenario: Scenario) -> None:
    checker = FeasibilityChecker(small_scenario)
    evals = [checker.evaluate("U1", ("T1",)), checker.evaluate("U2", ("T3",))]
    weights = ObjectiveWeights()
    scales = ObjectiveScales(10.0, 100.0, 1000.0, 50.0)
    unassigned = [make_task("X", (2, 2), priority=Priority.CRITICAL)]
    objective = compute_objective(evals, unassigned, weights, scales)
    assert objective.unassigned_penalty == pytest.approx(40.0)
    assert objective.z == pytest.approx(
        objective.energy_term
        + objective.time_term
        + objective.distance_term
        + objective.balance_term
        + 40.0
    )
    assert objective.energy_term == pytest.approx(0.35 * objective.total_energy / 10.0)


def test_fixed_scales_are_positive(small_scenario: Scenario) -> None:
    scales = fixed_scales(small_scenario)
    assert min(scales.energy, scales.time, scales.distance, scales.load_mean) > 0


def test_lexicographic_order() -> None:
    best = PlanScore(0, 0, 5.0)
    assert compare_scores(best, PlanScore(1, 0, 0.1)) < 0  # hard constraints dominate
    assert compare_scores(best, PlanScore(0, 1, 0.1)) < 0  # then mandatory tasks
    assert compare_scores(best, PlanScore(0, 0, 6.0)) < 0  # then Z
    assert compare_scores(PlanScore(0, 0, 6.0), best) > 0
    assert compare_scores(best, PlanScore(0, 0, 5.0)) == 0


# -- state ------------------------------------------------------------------------------


def test_mission_state_derives_remaining_problem(small_scenario: Scenario) -> None:
    state = MissionState.from_scenario(small_scenario)
    state.tasks.set("T1", TaskStatus.COMPLETED)
    u1 = state.uavs.get("U1")
    u1.cell, u1.battery, u1.ready_time, u1.airborne = (5, 5), 77.0, 12.0, True
    state.blocked.append(Obstacle("x", cells=((9, 9),)))
    derived = state.derived_scenario()
    assert sorted(t.id for t in derived.tasks) == ["T2", "T3"]
    planned = next(u for u in derived.uavs if u.id == "U1")
    assert planned.start == (5, 5) and planned.battery == 77.0 and planned.airborne
    assert planned.ready_time == 12.0
    assert derived.obstacles[-1].id == "x"
    state.uavs.get("U2").status = UAVStatus.UNAVAILABLE
    assert state.uavs.available() == ["U1"]


def test_task_manager_lifecycle() -> None:
    manager = TaskManager((make_task("T", (1, 1)),))
    manager.update_duration("T", 99.0)
    assert manager.tasks["T"].duration == 99.0
    manager.set("T", TaskStatus.IN_PROGRESS)
    assert manager.pending() == []
    assert manager.with_status(TaskStatus.IN_PROGRESS) == ["T"]


# -- generator --------------------------------------------------------------------------


@pytest.mark.parametrize("preset_id", PRESET_IDS)
def test_presets_are_valid(preset_id: str) -> None:
    assert validate_scenario(preset(preset_id, 7)) == []


@pytest.mark.parametrize("preset_id", ["A", "B", "C", "D", "E"])
def test_generator_is_deterministic(preset_id: str) -> None:
    assert preset(preset_id, 11).to_json() == preset(preset_id, 11).to_json()
    assert preset(preset_id, 11).to_json() != preset(preset_id, 12).to_json()


def test_preset_shapes() -> None:
    assert (len(preset("A").uavs), len(preset("A").tasks)) == (3, 10)
    assert (len(preset("B").uavs), len(preset("B").tasks)) == (5, 30)
    assert all(40.0 <= u.battery <= 100.0 for u in preset("C").uavs)
    assert sum(e.type == EventType.NEW_TASK for e in preset("D").events) == 5
    assert sum(e.type == EventType.UAV_UNAVAILABLE for e in preset("E").events) == 1


@pytest.mark.parametrize("distribution", ["uniform", "clustered", "edge"])
def test_distributions_generate_valid_scenarios(distribution) -> None:
    params = GeneratorParams(2, 15, distribution=distribution)
    scenario = generate(params, 3)
    assert validate_scenario(scenario) == []
    assert len(scenario.tasks) == 15


def test_generated_tasks_are_reachable() -> None:
    scenario = preset("B", 5)
    env = EnvironmentManager.from_scenario(scenario)
    for task in scenario.tasks:
        assert env.path(scenario.stations[0].cell, task.cell) is not None


def test_scenario_json_round_trip() -> None:
    for preset_id in ("D", "E", "DEMO"):
        original = preset(preset_id, 3)
        loaded = Scenario.from_dict(json.loads(original.to_json()))
        assert loaded.to_json() == original.to_json()


def test_validation_reports_problems() -> None:
    bad = make_scenario(
        [make_uav("U1", battery=5.0, home="NOPE")],
        [make_task("T", (3, 3), capability="lidar"), Task("T", (50, 50))],
        obstacles=[Obstacle("o", rect=(3, 3, 3, 3))],
    )
    errors = " ".join(validate_scenario(bad))
    for expected in (
        "duplicate task",
        "inside an obstacle",
        "home station",
        "above the reserve",
        "no UAV has",
    ):
        assert expected in errors


def test_event_round_trip_preserves_fields() -> None:
    event = Event(EventType.BATTERY_DROP, 5.0, uav_id="U1", amount=12.0)
    assert (
        Event.from_dict(
            json.loads(
                json.dumps({"type": "BATTERY_DROP", "time": 5.0, "uav_id": "U1", "amount": 12.0})
            )
        )
        == event
    )


def test_preset_json_files_are_in_sync() -> None:
    folder = Path(__file__).resolve().parent.parent / "scenarios"
    for preset_id in PRESET_IDS:
        path = folder / f"{preset_id}.json"
        assert path.read_text(encoding="utf-8") == preset(preset_id).to_json() + "\n", preset_id
