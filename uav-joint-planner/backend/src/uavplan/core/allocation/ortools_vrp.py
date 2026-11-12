"""B4 OR-Tools VRP: a strong multi-depot baseline with energy and time dimensions.

Energy and time are converted to integers by rounding up, and capacities and deadlines
by rounding down, so the solver is conservative compared with the FeasibilityChecker.
"""

from __future__ import annotations

import dataclasses
import math
import time
from dataclasses import dataclass

from uavplan.core.allocation.base import (
    NO_CAPABLE_UAV,
    Assignment,
    PlannerConfig,
    PlanningContext,
    priority_order,
)
from uavplan.core.models import Cell, Plan, Scenario, Task

_ENERGY_SCALE = 1000
_TIME_SCALE = 100
_COST_SCALE = 100_000
_BIG = 10**9
_HORIZON = 10**9


@dataclass(frozen=True, slots=True)
class ORToolsConfig(PlannerConfig):
    """``solution_limit`` is the deterministic budget; the time limit is a safety cap."""

    solution_limit: int = 400
    time_limit_s: int = 20


class _Matrices:
    """Integer arc matrices for one vehicle, over tasks + starts + ends."""

    def __init__(
        self, ctx: PlanningContext, uav_id: str, tasks: list[Task], n_vehicles: int, index: int
    ) -> None:
        n = len(tasks)
        size = n + 2 * n_vehicles
        uav = ctx.checker.uavs[uav_id]
        est = ctx.checker.estimator
        cells: list[Cell | None] = [t.cell for t in tasks]
        cells += [ctx.checker.uavs[u].start for u in ctx.uav_ids]
        cells += [None] * n_vehicles
        weights, scales = ctx.objective_weights, ctx.scales
        self.energy = [[0] * size for _ in range(size)]
        self.time = [[0] * size for _ in range(size)]
        self.cost = [[0] * size for _ in range(size)]
        at_station = uav.start in {s.cell for s in ctx.scenario.stations}
        for a, cell_a in enumerate(cells):
            if cell_a is None:
                continue
            service = tasks[a].duration if a < n else 0.0
            for b in range(n):
                leg = ctx.checker.leg(uav, cell_a, tasks[b].cell)
                if leg is None:
                    self.energy[a][b] = self.time[a][b] = self.cost[a][b] = _BIG
                    continue
                energy = leg.energy + est.task_energy(uav, tasks[b])
                self.energy[a][b] = math.ceil(energy * _ENERGY_SCALE)
                self.time[a][b] = math.ceil((service + leg.time) * _TIME_SCALE)
                self.cost[a][b] = round(
                    _COST_SCALE
                    * (
                        weights.alpha * energy / scales.energy
                        + weights.gamma * leg.path.distance / scales.distance
                    )
                )
            end = n + n_vehicles + index
            ret = ctx.checker.return_option(uav, cell_a)
            idle = a == n + index and at_station
            if idle:
                continue
            if ret is None:
                self.energy[a][end] = self.time[a][end] = self.cost[a][end] = _BIG
                continue
            energy = ret.energy + est.landing_energy(uav)
            self.energy[a][end] = math.ceil(energy * _ENERGY_SCALE)
            self.time[a][end] = math.ceil((service + ret.time) * _TIME_SCALE)
            self.cost[a][end] = round(
                _COST_SCALE
                * (
                    weights.alpha * energy / scales.energy
                    + weights.gamma * ret.path.distance / scales.distance
                )
            )


class ORToolsVRPPlanner:
    """Guided local search over a VRP with capability, energy and deadline constraints."""

    name = "ortools_vrp"

    def plan(self, scenario: Scenario, config: PlannerConfig | None = None) -> Plan:
        from ortools.constraint_solver import pywrapcp, routing_enums_pb2

        started = time.perf_counter()
        cfg = config if isinstance(config, ORToolsConfig) else self._coerce(config)
        ctx = PlanningContext(scenario, cfg)
        assignment = ctx.empty_assignment()
        unassigned: dict[str, str] = {}
        tasks: list[Task] = []
        for task in priority_order(scenario.tasks):
            if not any(ctx.uav_task_ok(u, task.id) for u in ctx.uav_ids):
                unassigned[task.id] = NO_CAPABLE_UAV
            elif task.deadline is not None and task.release_time + task.duration > task.deadline:
                unassigned[task.id] = "deadline is impossible given release time and duration"
            else:
                tasks.append(task)
        n, m = len(tasks), len(ctx.uav_ids)
        if n == 0 or m == 0:
            for task in tasks:
                unassigned[task.id] = "no UAV available"
            return ctx.build_plan(self.name, assignment, unassigned, started, config=cfg)

        starts = [n + v for v in range(m)]
        ends = [n + m + v for v in range(m)]
        manager = pywrapcp.RoutingIndexManager(n + 2 * m, m, starts, ends)
        routing = pywrapcp.RoutingModel(manager)
        matrices = [_Matrices(ctx, u, tasks, m, v) for v, u in enumerate(ctx.uav_ids)]

        def callback(table: list[list[int]]) -> int:
            def evaluate(from_index: int, to_index: int) -> int:
                return int(table[manager.IndexToNode(from_index)][manager.IndexToNode(to_index)])

            return int(routing.RegisterTransitCallback(evaluate))

        energy_callbacks = [callback(mat.energy) for mat in matrices]
        time_callbacks = [callback(mat.time) for mat in matrices]
        for v, mat in enumerate(matrices):
            routing.SetArcCostEvaluatorOfVehicle(callback(mat.cost), v)

        est = ctx.checker.estimator
        capacities = []
        for uav_id in ctx.uav_ids:
            uav = ctx.checker.uavs[uav_id]
            room = uav.battery - est.reserve(uav) - est.takeoff_energy(uav)
            capacities.append(max(0, math.floor(room * _ENERGY_SCALE)))
        routing.AddDimensionWithVehicleTransitAndCapacity(
            energy_callbacks, 0, capacities, True, "energy"
        )
        routing.AddDimensionWithVehicleTransits(time_callbacks, _HORIZON, _HORIZON, False, "time")
        time_dimension = routing.GetDimensionOrDie("time")
        time_dimension.SetGlobalSpanCostCoefficient(
            max(
                1,
                round(_COST_SCALE * ctx.objective_weights.beta / (ctx.scales.time * _TIME_SCALE)),
            )
        )
        for v, uav_id in enumerate(ctx.uav_ids):
            ready = math.ceil(ctx.checker.uavs[uav_id].ready_time * _TIME_SCALE)
            time_dimension.CumulVar(routing.Start(v)).SetValue(ready)

        weights = ctx.objective_weights
        for node, task in enumerate(tasks):
            index = manager.NodeToIndex(node)
            cumul = time_dimension.CumulVar(index)
            cumul.SetMin(math.ceil(task.release_time * _TIME_SCALE))
            if task.deadline is not None:
                cumul.SetMax(math.floor((task.deadline - task.duration) * _TIME_SCALE))
            penalty = _COST_SCALE * weights.lam * weights.big_m * int(task.priority)
            if task.mandatory:
                penalty *= 100
            routing.AddDisjunction([index], round(penalty))
            allowed = [v for v, u in enumerate(ctx.uav_ids) if ctx.uav_task_ok(u, task.id)]
            routing.VehicleVar(index).SetValues([-1, *allowed])

        params = pywrapcp.DefaultRoutingSearchParameters()
        params.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
        params.local_search_metaheuristic = (
            routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
        )
        params.solution_limit = cfg.solution_limit
        params.time_limit.FromSeconds(cfg.time_limit_s)
        solution = routing.SolveWithParameters(params)

        placed: set[str] = set()
        if solution is not None:
            for v, uav_id in enumerate(ctx.uav_ids):
                index = routing.Start(v)
                seq: list[str] = []
                while not routing.IsEnd(index):
                    node = manager.IndexToNode(index)
                    if node < n:
                        seq.append(tasks[node].id)
                    index = solution.Value(routing.NextVar(index))
                assignment[uav_id] = tuple(seq)
                placed.update(seq)
        for task in tasks:
            if task.id not in placed:
                unassigned[task.id] = "dropped by the solver at a priority-scaled penalty"

        shed = self._shed(ctx, assignment, unassigned)
        plan = ctx.build_plan(self.name, assignment, unassigned, started, config=cfg)
        return dataclasses.replace(plan, config={**plan.config, "shed_tasks": shed})

    @staticmethod
    def _coerce(config: PlannerConfig | None) -> ORToolsConfig:
        base = config or PlannerConfig()
        return ORToolsConfig(
            seed=base.seed, weights=base.weights, objective=base.objective, scales=base.scales
        )

    @staticmethod
    def _shed(ctx: PlanningContext, assignment: Assignment, unassigned: dict[str, str]) -> int:
        """Safety net for cases the VRP model does not cover, such as hover waiting energy."""
        shed = 0
        for uav_id in ctx.uav_ids:
            seq = assignment[uav_id]
            while seq and not ctx.report_checker.evaluate(uav_id, seq).feasible:
                victim = min(
                    seq, key=lambda t: (ctx.tasks[t].mandatory, int(ctx.tasks[t].priority), t)
                )
                seq = tuple(t for t in seq if t != victim)
                unassigned[victim] = "dropped: solver route failed the feasibility check"
                shed += 1
            assignment[uav_id] = seq
        return shed
