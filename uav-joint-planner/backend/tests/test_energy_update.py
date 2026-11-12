"""Energy-management update: audit gaps and energy extras."""

from __future__ import annotations

import pytest

from conftest import make_scenario, make_task, make_uav
from uavplan.core.allocation.base import PlanningContext
from uavplan.core.allocation.integrated import IntegratedConfig, IntegratedSearch
from uavplan.core.cost import fixed_scales
from uavplan.core.energy import EnergyEstimator
from uavplan.core.environment import EnvironmentManager
from uavplan.core.feasibility import FeasibilityChecker
from uavplan.core.generator import GeneratorParams, generate, preset, with_energy
from uavplan.core.metrics import simulation_metrics
from uavplan.core.models import (
    EnergyConfig,
    GridSpec,
    Obstacle,
    Plan,
    Priority,
    Scenario,
    Station,
    UAVStatus,
    ViolationType,
    charge_stop,
    is_charge_stop,
)
from uavplan.core.registry import get_planner
from uavplan.core.routing.paths import compare_leg_paths
from uavplan.core.simulator import SimConfig, Simulator
from uavplan.core.state import TaskStatus
from uavplan.experiments.ablation import STATIC_ABLATIONS, integrated_config


def test_fixed_scale_energy_comes_from_the_estimator() -> None:
    uavs = [make_uav("U1", energy_per_meter=0.01), make_uav("U2", energy_per_meter=0.03)]
    scenario = make_scenario(uavs, [make_task("T", (1, 1), energy=5.0)])
    distance = fixed_scales(scenario).distance
    assert fixed_scales(scenario).energy == pytest.approx(5.0 + 0.02 * distance)
    assert EnergyEstimator().nominal_energy([], [], 100.0) == 0.0


def test_physical_mode_changes_the_fixed_scale() -> None:
    physical = make_scenario(
        [make_uav()], [make_task("T", (1, 1), energy=5.0)], energy=EnergyConfig(mode="physical")
    )
    normalized = make_scenario([make_uav()], [make_task("T", (1, 1), energy=5.0)])
    assert fixed_scales(physical).energy > fixed_scales(normalized).energy


def test_no_prefix_safety_is_in_the_ablation_batch() -> None:
    assert "no_prefix_safety" in STATIC_ABLATIONS
    assert not integrated_config("no_prefix_safety", 0).strict_prefix_safety


# -- planned vs actual energy -----------------------------------------------------------


@pytest.mark.parametrize("seed", range(3))
def test_planned_equals_actual_without_noise(seed: int) -> None:
    scenario = preset("B", seed)
    plan = get_planner("integrated").plan(scenario)
    result = Simulator(scenario, plan).run()
    for route in plan.routes:
        outcome = result.outcomes[route.uav_id]
        assert outcome.planned_energy == pytest.approx(route.total_energy, abs=1e-9)
        assert outcome.margin_alerts == 0
    assert simulation_metrics(result, scenario)["energy_estimation_error"] == pytest.approx(
        0.0, abs=1e-9
    )


def test_noise_shows_up_as_estimation_error() -> None:
    scenario = preset("A", 1)
    plan = get_planner("integrated").plan(scenario)
    result = Simulator(scenario, plan, SimConfig(noise_sigma=0.2, seed=3)).run()
    metrics = simulation_metrics(result, scenario)
    assert metrics["energy_estimation_error"] > 0.0
    planned = sum(o.planned_energy for o in result.outcomes.values())
    assert planned == pytest.approx(sum(r.total_energy for r in plan.routes), abs=1e-9)


def test_margin_alert_when_actual_energy_eats_the_reserve() -> None:
    uav = make_uav(battery=30.0, battery_max=100.0, energy_per_meter=0.1)
    tasks = [make_task("A", (10, 0), energy=1.0), make_task("B", (15, 0), energy=1.0)]
    scenario = make_scenario([uav], tasks, energy=EnergyConfig(reserve_ratio=0.2))
    plan = get_planner("nearest").plan(scenario)  # no energy check while planning
    result = Simulator(scenario, plan).run()
    alerts = [e for e in result.log if e.kind == "MARGIN_ALERT"]
    assert len(alerts) == 1 and alerts[0].uav_id == "U1"
    assert result.outcomes["U1"].margin_alerts == 1
    assert simulation_metrics(result, scenario)["margin_alerts"] == 1.0


def test_segments_complete_in_time_order_across_uavs() -> None:
    scenario = preset("B", 4)
    plan = get_planner("integrated").plan(scenario)
    result = Simulator(scenario, plan).run()
    times = [e.time for e in result.log]
    assert times == sorted(times)


# -- recharge stops ---------------------------------------------------------------------


def recharge_scenario() -> Scenario:
    """B is too expensive for the starting battery, but fine after one charge."""
    uav = make_uav(battery=40.0, battery_max=100.0, energy_per_meter=0.1)
    tasks = [
        make_task("B", (6, 0), energy=20.0, priority=Priority.CRITICAL),
        make_task("A", (5, 0), energy=10.0),
    ]
    return make_scenario([uav], tasks)


def test_recharge_is_off_by_default_and_changes_nothing() -> None:
    scenario = preset("C", 2)
    planner = get_planner("integrated")
    off = planner.plan(scenario, IntegratedConfig(allow_recharge=False))
    assert planner.plan(scenario).canonical_json().replace("0.0", "") != ""
    assert off.assignment() == planner.plan(scenario).assignment()
    assert all(leg.kind != "to_charge" for r in off.routes for leg in r.legs)


def test_recharge_stop_saves_a_task_that_would_be_dropped() -> None:
    scenario = recharge_scenario()
    planner = get_planner("integrated")
    without = planner.plan(scenario)
    assert "B" in {u.task_id for u in without.unassigned}
    plan = planner.plan(scenario, IntegratedConfig(allow_recharge=True))
    assert not plan.unassigned
    assert plan.report.feasible
    route = plan.routes[0]
    assert set(route.task_ids) == {"A", "B"}
    assert any(leg.kind == "to_charge" for leg in route.legs)
    assert route.recharged_energy > 0
    assert route.battery_end == pytest.approx(route.profile[-1].battery)
    note = next(e.note for e in plan.explanations if e.task_id == "B")
    assert "charging stop" in note


def test_simulator_executes_a_charging_stop() -> None:
    uav = make_uav(
        battery=40.0,
        battery_max=100.0,
        energy_per_meter=0.1,
        takeoff_energy=1.0,
        landing_energy=1.0,
    )
    tasks = [make_task("B", (6, 0), energy=25.0)]
    scenario = make_scenario([uav], tasks)
    plan = get_planner("integrated").plan(scenario, IntegratedConfig(allow_recharge=True))
    route = plan.routes[0]
    result = Simulator(scenario, plan).run()
    segments = result.segments["U1"]
    charge = next(s for s in segments if s.kind == "charge")
    assert charge.b1 > charge.b0
    assert charge.status() == UAVStatus.CHARGING
    outcome = result.outcomes["U1"]
    assert outcome.energy_used == pytest.approx(route.total_energy, abs=1e-9)
    assert outcome.battery_end == pytest.approx(route.battery_end, abs=1e-9)
    assert outcome.landed
    assert result.task_status["B"] == TaskStatus.COMPLETED


def test_charge_gain_is_linear_and_capped() -> None:
    estimator = EnergyEstimator(EnergyConfig(charge_rate=0.5, charge_time=100.0))
    uav = make_uav(battery_max=100.0)
    assert estimator.charge_gain(uav, 10.0) == pytest.approx(50.0)
    assert estimator.charge_gain(uav, 80.0) == pytest.approx(20.0)
    assert estimator.charge_gain(uav, 100.0) == 0.0


def test_charging_stop_must_be_reachable_with_the_reserve() -> None:
    uav = make_uav(start=(15, 0), battery=12.0, energy_per_meter=0.1, airborne=True)
    tasks = [make_task("T", (16, 0), energy=0.0)]
    scenario = make_scenario([uav], tasks)
    ev = FeasibilityChecker(scenario).evaluate("U1", (charge_stop("S1"), "T"))
    assert ViolationType.ENERGY_PREFIX in {v.type for v in ev.violations}


def test_unreachable_charging_stop_is_reported() -> None:
    wall = Obstacle("w", rect=(5, 0, 5, 19))
    scenario = make_scenario(
        [make_uav()],
        [make_task("T", (1, 0))],
        [Station("S1", (0, 0)), Station("S2", (10, 0))],
        [wall],
    )
    ev = FeasibilityChecker(scenario).evaluate("U1", (charge_stop("S2"), "T"))
    assert ViolationType.UNREACHABLE in {v.type for v in ev.violations}


def test_unnecessary_charging_stop_is_pruned() -> None:
    scenario = make_scenario([make_uav()], [make_task("T", (1, 0))])
    ctx = PlanningContext(scenario)
    search = IntegratedSearch(ctx, IntegratedConfig(allow_recharge=True))
    assignment = {"U1": (charge_stop("S1"), "T")}
    search._prune_stops(assignment)
    assert assignment == {"U1": ("T",)}


@pytest.mark.parametrize("seed", range(40))
def test_recharge_and_adaptive_plans_are_always_feasible(seed: int) -> None:
    params = GeneratorParams(2 + seed % 3, 8 + seed % 12, battery_min=40.0)
    scenario = with_energy(generate(params, seed, f"e-{seed}"), adaptive_reserve=bool(seed % 2))
    plan = get_planner("integrated").plan(scenario, IntegratedConfig(allow_recharge=True))
    assert plan.report.feasible, plan.report.violations
    placed = [t for r in plan.routes for t in r.task_ids]
    assert sorted(placed + [u.task_id for u in plan.unassigned]) == sorted(
        t.id for t in scenario.tasks
    )
    assert not [t for t in placed if is_charge_stop(t)]


@pytest.mark.parametrize("seed", range(5))
def test_recharge_replans_stay_consistent(seed: int) -> None:
    scenario = preset("E", seed)
    config = IntegratedConfig(allow_recharge=True)
    plan = get_planner("integrated").plan(scenario, config)
    result = Simulator(scenario, plan, planner_config=config).run()
    assert all(r.plan.report.feasible for r in result.replans)


# -- adaptive reserve -------------------------------------------------------------------


def test_adaptive_reserve_formula() -> None:
    config = EnergyConfig(
        reserve_ratio=0.1, adaptive_reserve=True, adaptive_k=1.0, adaptive_sigma=0.2
    )
    estimator = EnergyEstimator(config)
    uav = make_uav(battery_max=100.0)
    assert estimator.reserve(uav) == pytest.approx(30.0)
    assert estimator.reserve(uav, 0.25) == pytest.approx(20.0)
    assert estimator.reserve(uav, 0.0) == pytest.approx(10.0)
    assert EnergyEstimator(EnergyConfig(reserve_ratio=0.1)).reserve(uav, 0.25) == pytest.approx(
        10.0
    )


def test_adaptive_reserve_rejects_a_route_the_fixed_reserve_accepts() -> None:
    uav = make_uav(battery=25.0, battery_max=100.0, energy_per_meter=0.0)
    tasks = [make_task("T", (1, 0), energy=10.0)]
    fixed = make_scenario([uav], tasks)
    assert FeasibilityChecker(fixed).evaluate("U1", ("T",)).feasible
    adaptive = make_scenario(
        [uav], tasks, energy=EnergyConfig(adaptive_reserve=True, adaptive_sigma=0.2)
    )
    ev = FeasibilityChecker(adaptive).evaluate("U1", ("T",))
    assert ViolationType.ENERGY_TOTAL in {v.type for v in ev.violations}


def test_adaptive_reserve_relaxes_later_in_the_route() -> None:
    uav = make_uav(battery=100.0, battery_max=100.0, energy_per_meter=0.1)
    tasks = [make_task("A", (5, 0), energy=0.0), make_task("B", (10, 0), energy=0.0)]
    energy = EnergyConfig(adaptive_reserve=True, adaptive_sigma=0.2)
    checker = FeasibilityChecker(make_scenario([uav], tasks, energy=energy))
    total = checker._route_length(uav, ("A", "B"))
    assert total == pytest.approx(200.0)
    assert checker._reserve_at(uav, 50.0, total) < checker.estimator.reserve(uav)
    assert checker._reserve_at(uav, 200.0, total) == pytest.approx(10.0)


def test_adaptive_reserve_keeps_more_battery_in_hand() -> None:
    fixed = preset("B", 1)
    adaptive = with_energy(fixed, adaptive_reserve=True, adaptive_sigma=0.1)
    planner = get_planner("integrated")
    margin = lambda plan: min(r.battery_end for r in plan.routes if r.task_ids)
    assert margin(planner.plan(adaptive)) >= margin(planner.plan(fixed)) - 1e-9


# -- early return-to-home ---------------------------------------------------------------


def rth_scenario() -> tuple[Scenario, Plan]:
    """U1 is sent too far by a plan with no energy check; U2 has spare capacity."""
    u1 = make_uav("U1", battery=30.0, battery_max=100.0, energy_per_meter=0.1)
    u2 = make_uav("U2", battery=100.0, battery_max=100.0, energy_per_meter=0.01)
    tasks = [make_task("A", (10, 0), energy=1.0), make_task("B", (15, 0), energy=1.0)]
    scenario = make_scenario([u1, u2], tasks, energy=EnergyConfig(reserve_ratio=0.2))
    ctx = PlanningContext(scenario)
    return scenario, ctx.build_plan("manual", {"U1": ("A", "B"), "U2": ()}, {}, 0.0)


def test_early_rth_is_off_by_default() -> None:
    scenario, plan = rth_scenario()
    result = Simulator(scenario, plan).run()
    assert not [e for e in result.log if e.kind == "RTH"]
    assert result.outcomes["U1"].depleted


def test_early_rth_brings_the_uav_home_and_replans_its_tasks() -> None:
    scenario, plan = rth_scenario()
    result = Simulator(scenario, plan, SimConfig(early_rth=True)).run()
    rth = [e for e in result.log if e.kind == "RTH"]
    assert len(rth) == 1 and rth[0].uav_id == "U1"
    assert result.outcomes["U1"].landed and not result.outcomes["U1"].depleted
    assert result.replans[0].event_type == "RTH"
    assert result.task_status == {"A": TaskStatus.COMPLETED, "B": TaskStatus.COMPLETED}
    assert simulation_metrics(result, scenario)["rth_events"] == 1.0


def test_early_rth_without_replanning_only_saves_the_uav() -> None:
    scenario, plan = rth_scenario()
    result = Simulator(scenario, plan, SimConfig(early_rth=True, replan=None)).run()
    assert result.outcomes["U1"].landed
    assert TaskStatus.FAILED in result.task_status.values()


def test_early_rth_does_not_fire_on_a_feasible_noise_free_plan() -> None:
    scenario = preset("B", 2)
    plan = get_planner("integrated").plan(scenario)
    result = Simulator(scenario, plan, SimConfig(early_rth=True)).run()
    assert not [e for e in result.log if e.kind == "RTH"]


def test_early_rth_reduces_depletion_under_heavy_noise() -> None:
    depleted = {False: 0.0, True: 0.0}
    for seed in range(12):
        scenario = preset("C", seed)
        plan = get_planner("integrated").plan(scenario)
        for flag in depleted:
            config = SimConfig(noise_sigma=0.4, seed=seed, early_rth=flag)
            depleted[flag] += simulation_metrics(Simulator(scenario, plan, config).run(), scenario)[
                "depleted_uavs"
            ]
    assert depleted[True] <= depleted[False]


# -- path comparison --------------------------------------------------------------------


def test_compare_paths_shows_shortest_is_not_lowest_energy() -> None:
    zone = Obstacle("z", "energy_zone", rect=(3, 0, 6, 4), energy_multiplier=5.0)
    env = EnvironmentManager(GridSpec(10, 10, 20.0), [zone])
    legs = compare_leg_paths(env, EnergyEstimator(), make_uav(energy_per_meter=0.1), (0, 2), (9, 2))
    assert legs is not None
    assert legs.energy_optimal.distance > legs.distance_optimal.distance
    assert legs.energy_optimal.energy < legs.distance_optimal.energy


def test_compare_paths_reports_no_path() -> None:
    env = EnvironmentManager(GridSpec(10, 10, 20.0), [Obstacle("w", rect=(5, 0, 5, 9))])
    assert compare_leg_paths(env, EnergyEstimator(), make_uav(), (0, 0), (9, 0)) is None
