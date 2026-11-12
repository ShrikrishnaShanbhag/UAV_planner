"""B3 Independent allocation and routing: the "separate decisions" baseline."""

from __future__ import annotations

import time

import numpy as np
from scipy.optimize import linear_sum_assignment

from uavplan.core.allocation.base import (
    Assignment,
    PlannerConfig,
    PlanningContext,
    Seq,
    priority_order,
)
from uavplan.core.models import Plan, Scenario
from uavplan.core.routing.construct import nearest_neighbour
from uavplan.core.routing.improve import two_opt

_FORBIDDEN = 1e12


class IndependentPlanner:
    """Allocate first, route second, then drop tasks until each route is feasible.

    1. Balanced Hungarian rounds over path distance, respecting capabilities but
       ignoring route energy. Each round gives every UAV at most one task.
    2. For each UAV, order its tasks by nearest neighbour + 2-opt on distance alone.
    3. While a route is infeasible, drop its lowest-priority task (optional before
       mandatory) and report it as unassigned.
    """

    name = "independent"

    def _allocate(self, ctx: PlanningContext) -> tuple[Assignment, dict[str, str]]:
        assignment = ctx.empty_assignment()
        remaining = priority_order(ctx.scenario.tasks)
        positions = {u: ctx.checker.uavs[u].start for u in ctx.uav_ids}
        while remaining and ctx.uav_ids:
            cost = np.full((len(ctx.uav_ids), len(remaining)), _FORBIDDEN)
            for row, uav_id in enumerate(ctx.uav_ids):
                for col, task in enumerate(remaining):
                    if ctx.uav_task_ok(uav_id, task.id):
                        distance = ctx.path_distance(positions[uav_id], task.cell)
                        if distance != float("inf"):
                            cost[row, col] = distance
            rows, cols = linear_sum_assignment(cost)
            taken: list[int] = []
            for row, col in zip(rows.tolist(), cols.tolist(), strict=True):
                if cost[row, col] < _FORBIDDEN:
                    uav_id = ctx.uav_ids[row]
                    assignment[uav_id] += (remaining[col].id,)
                    positions[uav_id] = remaining[col].cell
                    taken.append(col)
            if not taken:
                break
            remaining = [t for i, t in enumerate(remaining) if i not in set(taken)]
        reason = "no capable UAV can reach this task"
        return assignment, {t.id: reason for t in remaining}

    def _route(self, ctx: PlanningContext, uav_id: str, tasks: Seq) -> Seq:
        start = ctx.checker.uavs[uav_id].start
        stops = [(t, ctx.tasks[t].cell) for t in tasks]
        order = nearest_neighbour(start, stops, ctx.path_distance)
        return two_opt(
            order,
            lambda seq: ctx.evaluate(uav_id, seq).distance,
            lambda a, b: a < b - 1e-9,
        )

    def _shed(self, ctx: PlanningContext, uav_id: str, seq: Seq) -> tuple[Seq, list[str]]:
        dropped: list[str] = []
        while seq and not ctx.evaluate(uav_id, seq).feasible:
            victim = min(seq, key=lambda t: (ctx.tasks[t].mandatory, int(ctx.tasks[t].priority), t))
            seq = tuple(t for t in seq if t != victim)
            dropped.append(victim)
        return seq, dropped

    def plan(self, scenario: Scenario, config: PlannerConfig | None = None) -> Plan:
        started = time.perf_counter()
        cfg = config or PlannerConfig()
        ctx = PlanningContext(scenario, cfg)
        allocation, unassigned = self._allocate(ctx)
        assignment = ctx.empty_assignment()
        for uav_id in ctx.uav_ids:
            ordered = self._route(ctx, uav_id, allocation[uav_id])
            assignment[uav_id], dropped = self._shed(ctx, uav_id, ordered)
            for task_id in dropped:
                unassigned[task_id] = f"dropped from {uav_id}: its route was infeasible"
        return ctx.build_plan(self.name, assignment, unassigned, started, config=cfg)
