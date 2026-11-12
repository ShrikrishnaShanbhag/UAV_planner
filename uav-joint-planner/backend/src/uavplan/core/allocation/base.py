"""Planner protocol, planning context and the shared plan assembly step."""

from __future__ import annotations

import dataclasses
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from uavplan.core.cost import (
    CostWeights,
    ObjectiveScales,
    ObjectiveWeights,
    PlanScore,
    compare_scores,
    compute_objective,
    fixed_scales,
)
from uavplan.core.environment import EnvironmentManager, straight_line
from uavplan.core.feasibility import FeasibilityChecker, RouteEval, is_capable
from uavplan.core.models import (
    Cell,
    ObjectiveBreakdown,
    Plan,
    Scenario,
    Task,
    TaskExplanation,
    UAVStatus,
    UnassignedTask,
    jsonable,
)

Seq = tuple[str, ...]
Assignment = dict[str, Seq]
NO_CAPABLE_UAV = "no available UAV has the required capability"


@dataclass(frozen=True, slots=True)
class PlannerConfig:
    """Settings shared by all planners."""

    seed: int = 0
    weights: CostWeights = field(default_factory=CostWeights)
    objective: ObjectiveWeights = field(default_factory=ObjectiveWeights)
    scales: ObjectiveScales | None = None


class Planner(Protocol):
    """Every algorithm is a stateless ``plan(scenario, config) -> Plan``."""

    name: str

    def plan(self, scenario: Scenario, config: PlannerConfig | None = None) -> Plan: ...


def priority_order(tasks: Iterable[Task]) -> list[Task]:
    """Sort by priority (descending), deadline, release time, then id."""
    return sorted(
        tasks,
        key=lambda t: (
            -int(t.priority),
            t.deadline if t.deadline is not None else float("inf"),
            t.release_time,
            t.id,
        ),
    )


def better_score(a: PlanScore, b: PlanScore) -> bool:
    return compare_scores(a, b) < 0


class PlanningContext:
    """Per-plan view of a scenario: checkers, weights and objective scales.

    ``checker`` is what the planner plans against and may be relaxed for an ablation.
    ``report_checker`` always uses the scenario's own rules, so every plan is judged by
    the same yardstick.
    """

    def __init__(
        self,
        scenario: Scenario,
        config: PlannerConfig | None = None,
        strict_prefix: bool | None = None,
    ) -> None:
        self.scenario = scenario
        self.config = config or PlannerConfig()
        self.env = EnvironmentManager.from_scenario(scenario)
        self.report_checker = FeasibilityChecker(scenario, env=self.env)
        self.checker = (
            self.report_checker
            if strict_prefix is None or strict_prefix == self.report_checker.strict_prefix
            else FeasibilityChecker(scenario, env=self.env, strict_prefix=strict_prefix)
        )
        self.tasks = {t.id: t for t in scenario.tasks}
        self.uav_ids = sorted(u.id for u in scenario.uavs if u.status != UAVStatus.UNAVAILABLE)
        self.objective_weights = self.config.objective
        self.search_weights = self.config.objective
        self.scales = self.config.scales or self._reference_scales()

    # -- helpers shared by planners ----------------------------------------------------

    def uav_task_ok(self, uav_id: str, task_id: str) -> bool:
        task = self.tasks.get(task_id)  # charging stops are not tasks and stay on their route
        return task is not None and is_capable(self.checker.uavs[uav_id], task)

    def evaluate(self, uav_id: str, seq: Seq) -> RouteEval:
        return self.checker.evaluate(uav_id, seq)

    def last_position(self, uav_id: str, seq: Seq) -> Cell:
        return self.tasks[seq[-1]].cell if seq else self.checker.uavs[uav_id].start

    def path_distance(self, a: Cell, b: Cell) -> float:
        path = self.env.path(a, b)
        return float("inf") if path is None else path.distance

    def empty_assignment(self) -> Assignment:
        return {u: () for u in self.uav_ids}

    def nearest_assignment(self) -> tuple[Assignment, dict[str, str]]:
        """Nearest-UAV allocation by straight-line distance, with no energy awareness."""
        assignment = self.empty_assignment()
        unassigned: dict[str, str] = {}
        size = self.scenario.grid.cell_size
        for task in priority_order(self.scenario.tasks):
            capable = [u for u in self.uav_ids if self.uav_task_ok(u, task.id)]
            if not capable:
                unassigned[task.id] = NO_CAPABLE_UAV
                continue
            chosen = min(
                capable,
                key=lambda u: (
                    straight_line(self.last_position(u, assignment[u]), task.cell, size),
                    u,
                ),
            )
            assignment[chosen] += (task.id,)
        return assignment, unassigned

    def _reference_scales(self) -> ObjectiveScales:
        """Normalize ``Z`` by the nearest-UAV plan, falling back to fixed scales."""
        fallback = fixed_scales(self.scenario)
        if not self.uav_ids:
            return fallback
        assignment, _ = self.nearest_assignment()
        evals = [self.report_checker.evaluate(u, assignment[u]) for u in self.uav_ids]
        energy = sum(e.total_energy for e in evals)
        makespan = max(e.completion_time for e in evals)
        distance = sum(e.distance for e in evals)
        load = sum(e.load for e in evals) / len(evals)
        return ObjectiveScales(
            energy if energy > 1e-9 else fallback.energy,
            makespan if makespan > 1e-9 else fallback.time,
            distance if distance > 1e-9 else fallback.distance,
            load if load > 1e-9 else fallback.load_mean,
        )

    def objective(
        self, evals: Iterable[RouteEval], unassigned: Iterable[Task]
    ) -> ObjectiveBreakdown:
        return compute_objective(evals, unassigned, self.objective_weights, self.scales)

    def score(self, assignment: Mapping[str, Seq], unassigned: Iterable[str]) -> PlanScore:
        """Lexicographic score of a candidate assignment under the planning checker."""
        evals = [self.checker.evaluate(u, assignment[u]) for u in self.uav_ids]
        left = [self.tasks[t] for t in unassigned]
        z = compute_objective(evals, left, self.search_weights, self.scales).z
        return PlanScore(
            sum(len(e.violations) for e in evals), sum(1 for t in left if t.mandatory), z
        )

    def build_plan(
        self,
        algorithm: str,
        assignment: Mapping[str, Seq],
        unassigned: Mapping[str, str],
        started: float,
        explanations: tuple[TaskExplanation, ...] = (),
        config: Any = None,
    ) -> Plan:
        """Assemble the final plan, judged by the scenario's own feasibility rules."""
        checker = self.report_checker
        routes = tuple(checker.build_route(u, assignment[u]) for u in self.uav_ids)
        report = checker.check_assignment({u: assignment[u] for u in self.uav_ids})
        evals = [checker.evaluate(u, assignment[u]) for u in self.uav_ids]
        objective = self.objective(evals, [self.tasks[t] for t in unassigned])
        return Plan(
            algorithm=algorithm,
            scenario_id=self.scenario.id,
            routes=routes,
            unassigned=tuple(UnassignedTask(t, unassigned[t]) for t in sorted(unassigned)),
            report=report,
            objective=objective,
            compute_time_s=time.perf_counter() - started,
            explanations=explanations,
            config=config_dict(config) if config is not None else {},
        )


def config_dict(config: Any) -> dict[str, Any]:
    """JSON-friendly dict of a config dataclass, leaving out run-specific scales."""
    if not dataclasses.is_dataclass(config) or isinstance(config, type):
        raise TypeError("config must be a dataclass instance")
    data: dict[str, Any] = jsonable(config)
    data.pop("scales", None)
    return data
