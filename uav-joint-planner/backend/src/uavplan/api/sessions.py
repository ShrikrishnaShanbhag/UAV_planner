"""Live simulation sessions.

The simulator is deterministic, so injecting an event re-runs the mission from the start
with that event added. The past stays identical, seeking is free, and the WebSocket only
ever plays back a completed result.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

from uavplan.api.deps import to_json
from uavplan.core.allocation.base import PlannerConfig
from uavplan.core.metrics import simulation_metrics
from uavplan.core.models import Event, Plan, Scenario
from uavplan.core.replanning import ReplanResult
from uavplan.core.simulator import SimConfig, SimulationResult, Simulator, replan_summary

MAX_SESSIONS = 32


@dataclass(slots=True)
class SimulationSession:
    id: str
    scenario: Scenario
    algorithm: str
    plan: Plan
    planner_config: PlannerConfig
    sim_config: SimConfig
    injected: list[Event] = field(default_factory=list)
    result: SimulationResult = field(init=False)

    def __post_init__(self) -> None:
        self.run()

    def run(self) -> None:
        simulator = Simulator(self.scenario, self.plan, self.sim_config, self.planner_config)
        for event in self.injected:
            simulator.inject(event)
        self.result = simulator.run()

    def inject(self, event: Event) -> None:
        self.injected.append(event)
        self.run()

    @property
    def end_time(self) -> float:
        return self.result.end_time

    def replans_between(self, start: float, end: float) -> list[ReplanResult]:
        return [r for r in self.result.replans if start < r.time <= end]

    def plan_at(self, t: float) -> Plan:
        """The plan in force at time ``t``."""
        current = self.plan
        for replan in self.result.replans:
            if replan.time <= t:
                current = replan.plan
        return current

    def frame(self, previous: float, t: float, **extra: Any) -> dict[str, Any]:
        """One WebSocket frame: UAV states, log entries since ``previous``, and any replan."""
        result = self.result
        uavs = [result.sample(u, t) for u in sorted(result.start_cells)]
        events = [to_json(e) for e in result.log if previous < e.time <= t]
        data: dict[str, Any] = {
            "type": "frame",
            "t": t,
            "uavs": to_json(uavs),
            "events": events,
            "end_time": result.end_time,
            **extra,
        }
        replans = self.replans_between(previous, t)
        if replans:
            latest = replans[-1]
            data["replan"] = {
                "diff": to_json(replan_summary(latest)),
                "plan": to_json(latest.plan),
                "time_ms": latest.time_ms,
            }
        return data

    def state(self) -> dict[str, Any]:
        result = self.result
        return {
            "sim_id": self.id,
            "scenario": self.scenario,
            "algorithm": self.algorithm,
            "plan": self.plan,
            "end_time": result.end_time,
            "injected_events": len(self.injected),
            "metrics": simulation_metrics(result, self.scenario),
            "log": to_json(result.log),
            "replans": to_json([replan_summary(r) for r in result.replans]),
            "energy": [
                {
                    "uav_id": o.uav_id,
                    "planned": o.planned_energy,
                    "actual": o.energy_used,
                    "battery_end": o.battery_end,
                    "margin_at_end": o.battery_end - o.reserve,
                    "margin_alerts": o.margin_alerts,
                }
                for o in sorted(result.outcomes.values(), key=lambda item: item.uav_id)
            ],
        }


class SessionStore:
    """Keeps the most recent sessions in memory."""

    def __init__(self) -> None:
        self._items: OrderedDict[str, SimulationSession] = OrderedDict()

    def add(self, session: SimulationSession) -> None:
        self._items[session.id] = session
        while len(self._items) > MAX_SESSIONS:
            self._items.popitem(last=False)

    def get(self, sim_id: str) -> SimulationSession | None:
        return self._items.get(sim_id)
