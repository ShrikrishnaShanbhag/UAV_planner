"""B1 Nearest-UAV: allocates by proximity with no energy check while planning."""

from __future__ import annotations

import time

from uavplan.core.allocation.base import PlannerConfig, PlanningContext
from uavplan.core.models import Plan, Scenario


class NearestUAVPlanner:
    """Each task, in priority order, goes to the closest capable UAV.

    "Closest" is straight-line distance from the UAV's start or last assigned task.
    Visit order is assignment order. Feasibility is only checked afterwards, and the
    violations are reported rather than repaired.
    """

    name = "nearest"

    def plan(self, scenario: Scenario, config: PlannerConfig | None = None) -> Plan:
        started = time.perf_counter()
        cfg = config or PlannerConfig()
        ctx = PlanningContext(scenario, cfg)
        assignment, unassigned = ctx.nearest_assignment()
        return ctx.build_plan(self.name, assignment, unassigned, started, config=cfg)
