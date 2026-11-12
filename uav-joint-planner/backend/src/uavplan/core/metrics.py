"""Evaluation metrics, computed for both planned and simulated results."""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence

from uavplan.core.cost import coefficient_of_variation
from uavplan.core.models import (
    ENERGY_VIOLATIONS,
    Event,
    EventType,
    Plan,
    Scenario,
    Task,
    ViolationType,
)
from uavplan.core.simulator import SimulationResult, UAVOutcome
from uavplan.core.state import TaskStatus

_NAN = float("nan")


def all_tasks(scenario: Scenario) -> list[Task]:
    """Initial tasks plus any that arrive through ``NEW_TASK`` events."""
    arriving = [e.task for e in scenario.events if e.type == EventType.NEW_TASK and e.task]
    return [*scenario.tasks, *arriving]


def _rates(tasks: Sequence[Task], done: set[str]) -> dict[str, float]:
    mandatory = [t for t in tasks if t.mandatory]
    total_priority = sum(int(t.priority) for t in tasks)
    return {
        "completion_rate": 100.0 * sum(t.id in done for t in tasks) / len(tasks)
        if tasks
        else 100.0,
        "completion_rate_mandatory": (
            100.0 * sum(t.id in done for t in mandatory) / len(mandatory) if mandatory else 100.0
        ),
        "completion_rate_weighted": (
            100.0 * sum(int(t.priority) for t in tasks if t.id in done) / total_priority
            if total_priority
            else 100.0
        ),
    }


def _workload(loads: Sequence[float]) -> dict[str, float]:
    return {
        "workload_cv": coefficient_of_variation(loads),
        "workload_range": max(loads) - min(loads) if loads else 0.0,
    }


def plan_metrics(plan: Plan, scenario: Scenario) -> dict[str, float]:
    """Metrics of a plan as designed, before any execution."""
    tasks = list(scenario.tasks)
    active = [r for r in plan.routes if r.task_ids]
    energy_bad = {v.uav_id for v in plan.report.violations if v.type in ENERGY_VIOLATIONS}
    margins = [r.margin for r in active]
    result: dict[str, float] = {
        "total_energy": math.fsum(r.total_energy for r in plan.routes),
        "makespan": max((r.completion_time for r in plan.routes), default=0.0),
        "total_distance": math.fsum(r.distance for r in plan.routes),
        "energy_feasibility_rate": (
            100.0 * sum(r.uav_id not in energy_bad for r in active) / len(active)
            if active
            else 100.0
        ),
        "min_battery_margin": min(margins) if margins else _NAN,
        "unassigned": float(len(plan.unassigned)),
        "deadline_violations": float(
            sum(v.type == ViolationType.DEADLINE for v in plan.report.violations)
        ),
        "violations": float(len(plan.report.violations)),
        "plan_feasible": 100.0 if plan.report.feasible else 0.0,
        "compute_time": plan.compute_time_s,
        "objective_z": plan.objective.z,
    }
    done = {t for r in plan.routes for t in r.task_ids}
    result["energy_per_completed_task"] = result["total_energy"] / len(done) if done else _NAN
    result.update(_rates(tasks, done))
    result.update(_workload([r.completion_time - r.start_time for r in plan.routes]))
    return result


def simulation_metrics(sim: SimulationResult, scenario: Scenario) -> dict[str, float]:
    """Metrics of what actually happened in simulation."""
    outcomes = list(sim.outcomes.values())
    flown = [o for o in outcomes if o.flew]
    cancelled = {t for t, s in sim.task_status.items() if s == TaskStatus.CANCELLED}
    tasks = [sim.tasks[t] for t in sorted(sim.tasks) if t not in cancelled]
    done = {t for t, s in sim.task_status.items() if s == TaskStatus.COMPLETED}
    safe = [o for o in flown if o.landed and o.battery_end >= o.reserve - 1e-6]
    margins = [o.battery_end - o.reserve for o in flown if o.landed]
    replan_times = [r.time_ms for r in sim.replans]
    moved = [r.tasks_moved for r in sim.replans]
    result: dict[str, float] = {
        "total_energy": math.fsum(o.energy_used for o in outcomes),
        "makespan": max((o.work_end for o in outcomes), default=0.0),
        "total_distance": math.fsum(o.distance for o in outcomes),
        "energy_feasibility_rate": 100.0 * len(safe) / len(flown) if flown else 100.0,
        "min_battery_margin": min(margins) if margins else _NAN,
        "unassigned": float(len([t for t in tasks if t.id not in done])),
        "deadline_violations": float(len(sim.deadlines_missed)),
        "stranded_uavs": float(sum(o.flew and not o.landed for o in outcomes)),
        "depleted_uavs": float(sum(o.depleted for o in outcomes)),
        "margin_alerts": float(sum(o.margin_alerts for o in outcomes)),
        "rth_events": float(sum(e.kind == "RTH" for e in sim.log)),
        "energy_estimation_error": _estimation_error(outcomes),
        "compute_time": sim.initial_compute_s,
        "replans": float(len(sim.replans)),
        "replan_time_ms": _mean(replan_times),
        "tasks_moved_per_replan": _mean(moved),
    }
    completed = sum(t.id in done for t in tasks)
    result["energy_per_completed_task"] = result["total_energy"] / completed if completed else _NAN
    result.update(_rates(tasks, done))
    result.update(_workload([o.busy_time for o in outcomes]))
    return result


def _estimation_error(outcomes: Sequence[UAVOutcome]) -> float:
    """Absolute planned-vs-actual energy difference, as a percentage of planned energy."""
    planned = math.fsum(o.planned_energy for o in outcomes)
    if planned <= 0.0:
        return 0.0
    return 100.0 * math.fsum(abs(o.energy_used - o.planned_energy) for o in outcomes) / planned


def _mean(values: Iterable[float]) -> float:
    items = list(values)
    return math.fsum(items) / len(items) if items else 0.0


def optimality_gap(objective: float, optimum: float) -> float:
    """Relative gap in percent; report only against a proven optimum."""
    if abs(optimum) <= 1e-12:
        return 0.0 if abs(objective) <= 1e-12 else float("inf")
    return 100.0 * (objective - optimum) / abs(optimum)


__all__ = ["Event", "all_tasks", "optimality_gap", "plan_metrics", "simulation_metrics"]
