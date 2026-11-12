"""Shared mission state: UAV states, task lifecycle and derived planning scenarios."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum

from uavplan.core.models import (
    UAV,
    Cell,
    Obstacle,
    Scenario,
    Station,
    Task,
    TerrainLayer,
    UAVStatus,
    Wind,
)


class TaskStatus(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


@dataclass(slots=True)
class UAVState:
    """Where a UAV will next be free to act: its commit point."""

    uav: UAV
    cell: Cell
    battery: float
    status: UAVStatus
    ready_time: float
    airborne: bool

    def planning_view(self) -> UAV:
        """The UAV as a planner should see it from this state onward."""
        return replace(
            self.uav,
            start=self.cell,
            battery=self.battery,
            status=self.status,
            ready_time=self.ready_time,
            airborne=self.airborne,
        )


class UAVStateManager:
    """Tracks each UAV's position, battery and status during a mission."""

    def __init__(self, uavs: tuple[UAV, ...] = ()) -> None:
        self.states: dict[str, UAVState] = {}
        for uav in uavs:
            self.add(uav)

    def add(self, uav: UAV) -> None:
        self.states[uav.id] = UAVState(
            uav, uav.start, uav.battery, uav.status, uav.ready_time, uav.airborne
        )

    def get(self, uav_id: str) -> UAVState:
        return self.states[uav_id]

    def ids(self) -> list[str]:
        return sorted(self.states)

    def available(self) -> list[str]:
        return [i for i in self.ids() if self.states[i].status != UAVStatus.UNAVAILABLE]


class TaskManager:
    """Owns the task lifecycle: pending → in progress → completed/failed/cancelled."""

    def __init__(self, tasks: tuple[Task, ...] = ()) -> None:
        self.tasks: dict[str, Task] = {}
        self.status: dict[str, TaskStatus] = {}
        for task in tasks:
            self.add(task)

    def add(self, task: Task) -> None:
        self.tasks[task.id] = task
        self.status[task.id] = TaskStatus.PENDING

    def set(self, task_id: str, status: TaskStatus) -> None:
        self.status[task_id] = status

    def with_status(self, *statuses: TaskStatus) -> list[str]:
        return sorted(t for t, s in self.status.items() if s in statuses)

    def pending(self) -> list[str]:
        return self.with_status(TaskStatus.PENDING)

    def update_duration(self, task_id: str, duration: float) -> None:
        self.tasks[task_id] = replace(self.tasks[task_id], duration=duration)

    def update_deadline(self, task_id: str, deadline: float) -> None:
        self.tasks[task_id] = replace(self.tasks[task_id], deadline=deadline)


@dataclass(slots=True)
class MissionState:
    """The single state object shared by the simulator and the replanning engine."""

    base: Scenario
    uavs: UAVStateManager
    tasks: TaskManager
    time: float = 0.0
    blocked: list[Obstacle] = field(default_factory=list)
    wind: Wind | None = None
    stations_out: set[str] = field(default_factory=set)
    layers: dict[str, TerrainLayer] = field(default_factory=dict)

    @classmethod
    def from_scenario(cls, scenario: Scenario) -> MissionState:
        return cls(
            scenario,
            UAVStateManager(scenario.uavs),
            TaskManager(scenario.tasks),
            wind=scenario.wind,
            layers={layer.id: layer for layer in scenario.terrain_layers},
        )

    def stations(self) -> tuple[Station, ...]:
        """Stations that are still in service."""
        return tuple(s for s in self.base.stations if s.id not in self.stations_out)

    def derived_scenario(self) -> Scenario:
        """A scenario describing the rest of the mission from the current state.

        Pending tasks released later stay in the task list, so the planner can plan
        for them; it waits for ``release_time`` on arrival.
        """
        uavs = tuple(self.uavs.states[i].planning_view() for i in self.uavs.ids())
        tasks = tuple(self.tasks.tasks[t] for t in self.tasks.pending())
        return replace(
            self.base,
            uavs=uavs,
            tasks=tasks,
            obstacles=(*self.base.obstacles, *self.blocked),
            events=(),
            stations=self.stations(),
            wind=self.wind,
            terrain_layers=tuple(self.layers.values()),
        )
