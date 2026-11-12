"""B2 Greedy energy-aware: nearest UAV that can still do the task and get home."""

from __future__ import annotations

import time

from uavplan.core.allocation.base import (
    NO_CAPABLE_UAV,
    PlannerConfig,
    PlanningContext,
    priority_order,
)
from uavplan.core.environment import straight_line
from uavplan.core.models import ENERGY_VIOLATIONS, Plan, Scenario, ViolationType

_BLOCKING = ENERGY_VIOLATIONS | {ViolationType.UNREACHABLE}


class GreedyEnergyPlanner:
    """Like B1, but keeps only UAVs whose extended route passes the energy checks.

    Tasks are appended to the end of a route and never reordered. Deadlines are not
    part of this baseline's decision, so deadline violations are reported afterwards.
    """

    name = "greedy_energy"

    def plan(self, scenario: Scenario, config: PlannerConfig | None = None) -> Plan:
        started = time.perf_counter()
        cfg = config or PlannerConfig()
        ctx = PlanningContext(scenario, cfg)
        assignment = ctx.empty_assignment()
        unassigned: dict[str, str] = {}
        size = scenario.grid.cell_size
        for task in priority_order(scenario.tasks):
            capable = [u for u in ctx.uav_ids if ctx.uav_task_ok(u, task.id)]
            if not capable:
                unassigned[task.id] = NO_CAPABLE_UAV
                continue
            viable = [
                u
                for u in capable
                if not any(
                    v.type in _BLOCKING
                    for v in ctx.evaluate(u, assignment[u] + (task.id,)).violations
                )
            ]
            if not viable:
                unassigned[task.id] = "no capable UAV has enough energy to do it and return"
                continue
            chosen = min(
                viable,
                key=lambda u: (
                    straight_line(ctx.last_position(u, assignment[u]), task.cell, size),
                    u,
                ),
            )
            assignment[chosen] += (task.id,)
        return ctx.build_plan(self.name, assignment, unassigned, started, config=cfg)
