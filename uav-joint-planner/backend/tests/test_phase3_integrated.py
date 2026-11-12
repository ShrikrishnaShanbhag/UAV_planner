"""Phase 3: the proposed integrated planner."""

from __future__ import annotations

from dataclasses import replace

import pytest

from conftest import make_scenario, make_task, make_uav
from uavplan.core.allocation.base import PlannerConfig, PlanningContext
from uavplan.core.allocation.integrated import (
    IntegratedConfig,
    IntegratedPlanner,
    IntegratedSearch,
    as_integrated,
)
from uavplan.core.cost import CostWeights, compare_plans
from uavplan.core.generator import GeneratorParams, generate, preset
from uavplan.core.models import (
    EnergyConfig,
    GridSpec,
    Obstacle,
    Priority,
    Scenario,
    Station,
)
from uavplan.core.registry import get_planner

planner = IntegratedPlanner()


def random_scenario(seed: int) -> Scenario:
    """Varied small and medium instances, including tight batteries."""
    n_uavs = 2 + seed % 4
    n_tasks = 5 + seed % 16
    distribution = ("uniform", "clustered", "edge")[seed % 3]
    low = 35.0 if seed % 2 else 100.0
    params = GeneratorParams(
        n_uavs, n_tasks, distribution=distribution, battery_min=low, deadline_fraction=0.5
    )
    return generate(params, seed, f"prop-{seed}")


@pytest.mark.parametrize("seed", range(200))
def test_proposed_plans_are_always_feasible(seed: int) -> None:
    scenario = random_scenario(seed)
    plan = planner.plan(scenario)
    assert plan.report.feasible, plan.report.violations
    placed = [t for r in plan.routes for t in r.task_ids]
    assert len(placed) == len(set(placed))
    assert sorted(placed + [u.task_id for u in plan.unassigned]) == sorted(
        t.id for t in scenario.tasks
    )


@pytest.mark.parametrize("preset_id", ["A", "B", "C"])
def test_proposed_is_deterministic(preset_id: str) -> None:
    scenario = preset(preset_id, 3)
    assert planner.plan(scenario).canonical_json() == planner.plan(scenario).canonical_json()


def test_lns_is_deterministic_for_a_seed() -> None:
    scenario = preset("B", 3)
    cfg = IntegratedConfig(seed=5, use_lns=True, lns_iterations=30)
    assert (
        planner.plan(scenario, cfg).canonical_json() == planner.plan(scenario, cfg).canonical_json()
    )


@pytest.mark.parametrize("seed", range(6))
def test_lns_never_worsens_the_plan(seed: int) -> None:
    scenario = preset("B", seed)
    plain = planner.plan(scenario)
    improved = planner.plan(scenario, IntegratedConfig(use_lns=True, lns_iterations=40))
    tasks = {t.id: t for t in scenario.tasks}
    assert compare_plans(improved, plain, tasks) <= 0


@pytest.mark.parametrize(
    "flag",
    [
        "use_energy_in_cost",
        "use_workload_term",
        "use_insertion",
        "use_local_search",
        "use_inter_route",
    ],
)
def test_each_search_flag_changes_behaviour(flag: str) -> None:
    changed = 0
    for seed in range(10):
        scenario = preset("B", seed)
        full = planner.plan(scenario).assignment()
        off = planner.plan(scenario, IntegratedConfig(**{flag: False})).assignment()
        changed += full != off
    assert changed > 0, flag


def repair_scenario() -> Scenario:
    """Only U1 can do X, and A must move to U2 to make room for it."""
    u1 = make_uav("U1", battery=40.0, capabilities=frozenset({"camera", "lidar"}))
    u2 = make_uav("U2", start=(19, 0), battery=100.0)
    tasks = [
        make_task("A", (1, 0), energy=15.0, priority=Priority.CRITICAL),
        make_task("X", (2, 0), energy=20.0, capability="lidar", priority=Priority.LOW),
    ]
    stations = [Station("S1", (0, 0)), Station("S2", (19, 0))]
    return make_scenario([u1, u2], tasks, stations)


def test_repair_move_makes_room_and_flag_disables_it() -> None:
    scenario = repair_scenario()
    with_repair = planner.plan(scenario)
    assert with_repair.assignment() == {"U1": ("X",), "U2": ("A",)}
    assert with_repair.report.feasible
    note = next(e.note for e in with_repair.explanations if e.task_id == "X")
    assert "relocating" in note
    without = planner.plan(scenario, IntegratedConfig(use_repair_move=False))
    assert [u.task_id for u in without.unassigned] == ["X"]


def test_strict_prefix_flag_changes_behaviour() -> None:
    """Uses the construction from the Phase 1 prefix-safety test."""
    zone = Obstacle("z", "energy_zone", rect=(1, 0, 9, 0), energy_multiplier=12.0)
    grid = GridSpec(11, 6, 10.0)
    energy = EnergyConfig(reserve_ratio=0.0, path_metric="distance")
    uav = make_uav(start=(0, 0), battery=20.0, battery_max=100.0, energy_per_meter=0.1)
    tasks = [make_task("T1", (0, 0), energy=0.0), make_task("T2", (5, 5), energy=0.0)]
    scenario = make_scenario(
        [uav], tasks, [Station("S1", (10, 0))], [zone], grid=grid, energy=energy
    )
    relaxed = planner.plan(scenario, IntegratedConfig(strict_prefix_safety=False))
    strict = planner.plan(scenario)
    assert relaxed.assignment() != strict.assignment()
    # A relaxed plan is still judged by the scenario's own strict rule.
    assert not relaxed.report.feasible
    assert strict.report.feasible


def test_insertion_beats_append_on_a_line() -> None:
    uav = make_uav(battery=1000.0, battery_max=1000.0)
    tasks = [
        make_task("far", (10, 0), priority=Priority.CRITICAL),
        make_task("near", (5, 0), priority=Priority.LOW),
    ]
    scenario = make_scenario([uav], tasks)
    cfg = IntegratedConfig(use_insertion=False, use_local_search=False, use_inter_route=False)
    assert planner.plan(scenario).assignment()["U1"] == ("near", "far")
    assert planner.plan(scenario, cfg).assignment()["U1"] == ("far", "near")


def test_explanations_cover_every_task_and_rejections() -> None:
    uav_a = make_uav("U1", battery=12.0)
    uav_b = make_uav("U2", capabilities=frozenset({"lidar"}))
    scenario = make_scenario([uav_a, uav_b], [make_task("T", (1, 1), energy=50.0)])
    plan = planner.plan(scenario)
    explanation = plan.explanations[0]
    assert explanation.chosen_uav is None
    reasons = {c.uav_id: c.rejected_reason for c in explanation.candidates}
    assert "ENERGY_TOTAL" in reasons["U1"]
    assert "capability" in reasons["U2"]
    assert "ENERGY_TOTAL" in plan.unassigned[0].reason


def test_explanation_records_costs_of_feasible_candidates() -> None:
    plan = planner.plan(preset("A", 1))
    assert {e.task_id for e in plan.explanations} == {f"T{i}" for i in range(1, 11)}
    chosen = plan.explanations[0]
    feasible = [c for c in chosen.candidates if c.feasible]
    assert feasible and all(c.cost is not None for c in feasible)
    best = min(feasible, key=lambda c: (c.cost, c.uav_id))
    assert chosen.chosen_uav == best.uav_id


def test_no_capable_uav_reason() -> None:
    plan = planner.plan(make_scenario([make_uav()], [make_task("T", (1, 1), capability="lidar")]))
    assert "capability" in plan.unassigned[0].reason


def test_higher_priority_task_wins_scarce_energy() -> None:
    uav = make_uav(battery=20.0)
    tasks = [
        make_task("low", (1, 0), energy=8.0, priority=Priority.LOW),
        make_task("high", (0, 1), energy=8.0, priority=Priority.CRITICAL),
    ]
    plan = planner.plan(make_scenario([uav], tasks))
    assert plan.assignment()["U1"] == ("high",)
    assert plan.unassigned[0].task_id == "low"


def test_rescue_finds_mandatory_assignment_greedy_misses() -> None:
    """A low-priority mandatory task needs the UAV that a critical task took first.

    Both UAVs fit one task. Greedy gives the critical task (which either UAV can do) to
    the cheaper U1, and repair is off, so only the backtracking rescue can recover.
    """
    u1 = make_uav("U1", battery=30.0, capabilities=frozenset({"camera", "lidar"}))
    u2 = make_uav("U2", battery=30.0, energy_per_meter=0.1)
    tasks = [
        make_task("C", (1, 0), energy=15.0, priority=Priority.CRITICAL, mandatory=True),
        make_task(
            "L", (2, 0), energy=15.0, capability="lidar", priority=Priority.LOW, mandatory=True
        ),
    ]
    scenario = make_scenario([u1, u2], tasks)
    cfg = IntegratedConfig(use_repair_move=False, use_inter_route=False, use_local_search=False)
    plan = planner.plan(scenario, cfg)
    assert not plan.unassigned
    assert plan.assignment() == {"U1": ("L",), "U2": ("C",)}


def test_rescue_keeps_result_when_budget_is_exhausted() -> None:
    scenario = repair_scenario()
    mandatory = tuple(replace(t, mandatory=True) for t in scenario.tasks)
    scenario = replace(scenario, tasks=mandatory)
    cfg = IntegratedConfig(use_repair_move=False, use_inter_route=False, rescue_budget=0)
    plan = planner.plan(scenario, cfg)
    assert [u.task_id for u in plan.unassigned] == ["X"]


def test_rescue_does_nothing_when_truly_infeasible() -> None:
    uav = make_uav(battery=20.0)
    tasks = [make_task("A", (1, 0), energy=50.0, mandatory=True)]
    plan = planner.plan(make_scenario([uav], tasks))
    assert plan.unassigned[0].task_id == "A"


def test_weights_change_the_chosen_uav() -> None:
    near_costly = make_uav(
        "U1", start=(3, 0), energy_per_meter=0.5, battery=1000.0, battery_max=1000.0
    )
    far_cheap = make_uav(
        "U2", start=(0, 0), energy_per_meter=0.001, battery=1000.0, battery_max=1000.0
    )
    stations = [Station("S1", (0, 0)), Station("S2", (3, 0))]
    scenario = make_scenario([near_costly, far_cheap], [make_task("T", (6, 0))], stations)
    off = IntegratedConfig(use_local_search=False, use_inter_route=False)
    energy_only = replace(off, weights=CostWeights(w_d=0.0, w_e=1.0, w_t=0.0, w_l=0.0))
    distance_only = replace(off, weights=CostWeights(w_d=1.0, w_e=0.0, w_t=0.0, w_l=0.0))
    assert planner.plan(scenario, energy_only).assignment()["U2"] == ("T",)
    assert planner.plan(scenario, distance_only).assignment()["U1"] == ("T",)


def test_as_integrated_keeps_shared_settings() -> None:
    base = PlannerConfig(seed=9)
    assert as_integrated(base).seed == 9
    config = IntegratedConfig(use_lns=True)
    assert as_integrated(config) is config
    assert as_integrated(None).use_insertion


def test_search_uses_ablated_objective_but_plan_reports_full_z() -> None:
    scenario = preset("A", 1)
    cfg = IntegratedConfig(use_energy_in_cost=False, use_workload_term=False)
    ctx = PlanningContext(scenario, cfg)
    IntegratedSearch(ctx, cfg)
    assert ctx.search_weights.alpha == 0.0 and ctx.search_weights.delta == 0.0
    assert ctx.objective_weights.alpha > 0.0
    plan = planner.plan(scenario, cfg)
    assert plan.objective.energy_term > 0.0


def test_registry_has_integrated_and_records_config() -> None:
    plan = get_planner("integrated").plan(preset("A", 2))
    assert plan.algorithm == "integrated"
    assert plan.config["use_insertion"] is True
    assert "scales" not in plan.config


@pytest.mark.parametrize("preset_id", ["A", "B", "C"])
def test_integrated_is_at_least_as_good_as_nearest_when_nearest_is_feasible(preset_id: str) -> None:
    """A sanity check on ordering only; the real comparison belongs to the experiments."""
    tasks = None
    for seed in range(5):
        scenario = preset(preset_id, seed)
        tasks = {t.id: t for t in scenario.tasks}
        nearest = get_planner("nearest").plan(scenario)
        integrated = planner.plan(scenario)
        if not nearest.report.feasible:
            assert compare_plans(integrated, nearest, tasks) < 0
