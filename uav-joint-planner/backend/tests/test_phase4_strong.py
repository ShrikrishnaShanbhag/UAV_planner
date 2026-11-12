"""Phase 4: OR-Tools VRP, genetic algorithm and the exact CP-SAT model."""

from __future__ import annotations

from dataclasses import replace

import pytest

from conftest import make_scenario, make_task, make_uav
from uavplan.core.allocation.exact_cpsat import ExactConfig, ExactCPSATPlanner
from uavplan.core.allocation.genetic import GeneticConfig, GeneticPlanner
from uavplan.core.allocation.integrated import IntegratedPlanner
from uavplan.core.allocation.ortools_vrp import ORToolsConfig, ORToolsVRPPlanner
from uavplan.core.cost import plan_score
from uavplan.core.feasibility import FeasibilityChecker
from uavplan.core.generator import GeneratorParams, generate, preset
from uavplan.core.metrics import optimality_gap
from uavplan.core.models import Priority, Scenario, Station
from uavplan.core.registry import ALGORITHMS, get_planner

HEURISTICS = ["greedy_energy", "independent", "integrated", "ortools_vrp", "genetic"]
exact = ExactCPSATPlanner()


def tiny(seed: int) -> Scenario:
    return generate(GeneratorParams(2, 6, battery_min=45.0), seed, f"tiny-{seed}")


# -- exact ------------------------------------------------------------------------------


@pytest.mark.slow
@pytest.mark.parametrize("seed", range(6))
def test_exact_is_no_worse_than_any_heuristic(seed: int) -> None:
    scenario = tiny(seed)
    tasks = {t.id: t for t in scenario.tasks}
    solved = exact.plan(scenario)
    assert solved.config["proven_optimal"], solved.config
    assert solved.report.feasible
    best = plan_score(solved, tasks)
    for name in HEURISTICS:
        plan = get_planner(name).plan(scenario)
        other = plan_score(plan, tasks)
        if other.violations:
            continue
        assert best.mandatory_unassigned <= other.mandatory_unassigned, name
        if best.mandatory_unassigned == other.mandatory_unassigned:
            # The CP-SAT model rounds energy and time up, hence the small tolerance.
            assert best.z <= other.z + 2e-3, (name, best.z, other.z)


@pytest.mark.slow
@pytest.mark.parametrize("seed", range(10))
def test_proposed_never_misses_a_mandatory_task_the_exact_solver_can_serve(seed: int) -> None:
    params = GeneratorParams(3, 10, battery_min=40.0, priority_weights=(0.1, 0.2, 0.3, 0.4))
    scenario = generate(params, seed, f"mand-{seed}")
    tasks = {t.id: t for t in scenario.tasks}
    solved = exact.plan(scenario)
    if not solved.config["proven_optimal"]:
        pytest.skip("solver did not prove optimality within its limit")
    if plan_score(solved, tasks).mandatory_unassigned == 0:
        plan = IntegratedPlanner().plan(scenario)
        assert plan_score(plan, tasks).mandatory_unassigned == 0


def test_exact_model_rejects_large_and_mid_mission_instances() -> None:
    with pytest.raises(ValueError, match="limited"):
        exact.plan(preset("B", 1))
    airborne = replace(make_uav(), airborne=True)
    with pytest.raises(ValueError, match="static"):
        exact.plan(make_scenario([airborne], [make_task("T", (1, 1))]))


def test_exact_solves_the_25eu_example_as_unassigned() -> None:
    uav = make_uav(start=(10, 0), battery=25.0, energy_per_meter=0.1)
    scenario = make_scenario([uav], [make_task("J", (10, 0), energy=20.0)], [Station("S1", (0, 0))])
    plan = exact.plan(scenario)
    assert plan.config["proven_optimal"]
    assert [u.task_id for u in plan.unassigned] == ["J"]


def test_exact_respects_capability_and_unreachable_tasks() -> None:
    scenario = make_scenario(
        [make_uav("U1"), make_uav("U2", capabilities=frozenset({"lidar"}))],
        [make_task("A", (3, 3), capability="lidar"), make_task("B", (4, 4))],
    )
    plan = exact.plan(scenario)
    assert plan.assignment() == {"U1": ("B",), "U2": ("A",)}
    assert plan.report.feasible


def test_exact_is_deterministic() -> None:
    scenario = tiny(1)
    assert exact.plan(scenario).canonical_json() == exact.plan(scenario).canonical_json()


def test_exact_prefers_critical_when_energy_is_scarce() -> None:
    uav = make_uav(battery=20.0)
    tasks = [
        make_task("low", (1, 0), energy=8.0, priority=Priority.LOW),
        make_task("high", (0, 1), energy=8.0, priority=Priority.CRITICAL),
    ]
    assert exact.plan(make_scenario([uav], tasks)).assignment()["U1"] == ("high",)


def test_exact_without_hint_matches_with_hint() -> None:
    scenario = tiny(2)
    tasks = {t.id: t for t in scenario.tasks}
    with_hint = plan_score(exact.plan(scenario), tasks)
    without = plan_score(exact.plan(scenario, ExactConfig(use_hint=False)), tasks)
    assert with_hint.z == pytest.approx(without.z, abs=1e-6)


def test_heuristic_gap_is_non_negative_on_tiny_instance() -> None:
    scenario = tiny(3)
    solved = exact.plan(scenario)
    plan = IntegratedPlanner().plan(scenario)
    assert optimality_gap(plan.objective.z, solved.objective.z) >= -0.05


# -- OR-Tools ---------------------------------------------------------------------------


@pytest.mark.parametrize("seed", range(4))
@pytest.mark.parametrize("preset_id", ["A", "B", "C"])
def test_ortools_output_passes_our_checker(preset_id: str, seed: int) -> None:
    """Cross-check: the VRP model and the FeasibilityChecker must agree."""
    scenario = preset(preset_id, seed)
    plan = ORToolsVRPPlanner().plan(scenario, ORToolsConfig(solution_limit=100))
    assert plan.config["shed_tasks"] == 0
    checker = FeasibilityChecker(scenario)
    assert checker.check_assignment(plan.assignment()).feasible
    assert plan.report.feasible


def test_ortools_is_deterministic() -> None:
    scenario = preset("A", 7)
    planner = ORToolsVRPPlanner()
    config = ORToolsConfig(solution_limit=100)
    assert (
        planner.plan(scenario, config).canonical_json()
        == planner.plan(scenario, config).canonical_json()
    )


def test_ortools_handles_capability_impossible_deadline_and_no_tasks() -> None:
    scenario = make_scenario(
        [make_uav()],
        [
            make_task("cap", (1, 1), capability="lidar"),
            make_task("late", (2, 2), deadline=1.0, duration=30.0),
            make_task("ok", (3, 3)),
        ],
    )
    plan = ORToolsVRPPlanner().plan(scenario)
    reasons = {u.task_id: u.reason for u in plan.unassigned}
    assert "capability" in reasons["cap"] and "impossible" in reasons["late"]
    assert plan.assignment()["U1"] == ("ok",)
    assert ORToolsVRPPlanner().plan(make_scenario([make_uav()], [])).routes[0].task_ids == ()


def test_ortools_drops_lower_priority_when_energy_is_scarce() -> None:
    uav = make_uav(battery=20.0)
    tasks = [
        make_task("low", (1, 0), energy=8.0, priority=Priority.LOW),
        make_task("high", (0, 1), energy=8.0, priority=Priority.CRITICAL),
    ]
    assert ORToolsVRPPlanner().plan(make_scenario([uav], tasks)).assignment()["U1"] == ("high",)


def test_ortools_sheds_when_hover_waiting_breaks_the_model() -> None:
    """Hover-waiting energy is outside the VRP model, so the checker must catch it."""
    uav = make_uav(battery=30.0, hover_rate=1.0)
    task = make_task("T", (1, 0), energy=1.0, release_time=1000.0)
    plan = ORToolsVRPPlanner().plan(make_scenario([uav], [task]))
    assert plan.config["shed_tasks"] == 1
    assert plan.report.feasible


# -- genetic ----------------------------------------------------------------------------


@pytest.mark.parametrize("seed", range(4))
@pytest.mark.parametrize("preset_id", ["A", "C"])
def test_genetic_plans_are_feasible(preset_id: str, seed: int) -> None:
    config = GeneticConfig(population=16, generations=10, seed=seed)
    assert GeneticPlanner().plan(preset(preset_id, seed), config).report.feasible


def test_genetic_is_deterministic_for_a_seed() -> None:
    scenario = preset("A", 2)
    planner = GeneticPlanner()
    config = GeneticConfig(population=16, generations=10, seed=4)
    assert (
        planner.plan(scenario, config).canonical_json()
        == planner.plan(scenario, config).canonical_json()
    )


def test_genetic_reports_incapable_and_handles_empty() -> None:
    scenario = make_scenario([make_uav()], [make_task("T", (1, 1), capability="lidar")])
    assert "capability" in GeneticPlanner().plan(scenario).unassigned[0].reason
    assert GeneticPlanner().plan(make_scenario([make_uav()], [])).routes[0].task_ids == ()


def test_genetic_more_generations_do_not_hurt() -> None:
    scenario = preset("A", 6)
    tasks = {t.id: t for t in scenario.tasks}
    short = GeneticPlanner().plan(scenario, GeneticConfig(population=20, generations=1))
    long = GeneticPlanner().plan(scenario, GeneticConfig(population=20, generations=30))
    assert plan_score(long, tasks).z <= plan_score(short, tasks).z + 1e-9


def test_registry_lists_every_algorithm() -> None:
    assert set(ALGORITHMS) == {
        "nearest",
        "greedy_energy",
        "independent",
        "integrated",
        "ortools_vrp",
        "genetic",
        "exact_cpsat",
    }
