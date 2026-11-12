"""Discrete-event mission simulator with scripted and live events.

Each UAV executes a list of segments (move, wait, work). Time jumps from event to event;
positions at arbitrary times are recovered afterwards by interpolation, so the timeline
can be sampled at any tick without simulating every tick.

When an event triggers a replan, each UAV finishes the grid edge it is on (or its current
task, if so configured). That *commit point* is where the new plan starts.
"""

from __future__ import annotations

import bisect
import heapq
import itertools
from dataclasses import dataclass, field, replace
from typing import Any, Literal

import numpy as np

from uavplan.core.allocation.base import PlannerConfig, PlanningContext
from uavplan.core.energy import EnergyEstimator
from uavplan.core.environment import EnvironmentManager, polygon_cells, straight_line
from uavplan.core.models import (
    UAV,
    Cell,
    Event,
    EventType,
    Leg,
    Obstacle,
    PathInfo,
    Plan,
    Route,
    Scenario,
    Task,
    UAVStatus,
    jsonable,
)
from uavplan.core.replanning import DynamicReplanningEngine, ReplanConfig, ReplanResult
from uavplan.core.state import MissionState, TaskStatus

SegmentKind = Literal["takeoff", "move", "wait", "work", "landing", "charge"]
InProgressPolicy = Literal["finish", "abandon"]


@dataclass(frozen=True, slots=True)
class SimConfig:
    """``replan=None`` means events are applied but the plan is never repaired."""

    noise_sigma: float = 0.0
    seed: int = 0
    tick: float = 1.0
    in_progress: InProgressPolicy = "finish"
    early_rth: bool = False
    replan: ReplanConfig | None = field(default_factory=ReplanConfig)


@dataclass(frozen=True, slots=True)
class Segment:
    """One continuous action. ``elapsed`` is cumulative time at each cell of ``cells``."""

    uav_id: str
    kind: SegmentKind
    t0: float
    t1: float
    b0: float
    b1: float
    cells: tuple[Cell, ...]
    elapsed: tuple[float, ...] = ()
    distance: float = 0.0
    task_id: str | None = None
    returning: bool = False
    depleted: bool = False
    edge_distances: tuple[float, ...] = ()
    edge_efforts: tuple[float, ...] = ()
    planned_energy: float = 0.0

    def position(self, t: float) -> tuple[float, float]:
        if len(self.cells) == 1 or not self.elapsed or self.t1 <= self.t0:
            return (float(self.cells[-1][0]), float(self.cells[-1][1]))
        offset = min(max(t - self.t0, 0.0), self.elapsed[-1])
        i = min(bisect.bisect_right(self.elapsed, offset), len(self.cells) - 1)
        a, b = self.cells[i - 1], self.cells[i]
        span = self.elapsed[i] - self.elapsed[i - 1]
        f = 0.0 if span <= 0 else (offset - self.elapsed[i - 1]) / span
        return (a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f)

    def energy_fraction(self, t: float) -> float:
        """Share of this segment's energy spent by ``t``, following edge effort on a move."""
        total = sum(self.edge_efforts)
        if self.kind != "move" or total <= 0.0 or len(self.elapsed) != len(self.edge_efforts) + 1:
            return min(max((t - self.t0) / (self.t1 - self.t0), 0.0), 1.0)
        offset = min(max(t - self.t0, 0.0), self.elapsed[-1])
        i = min(bisect.bisect_right(self.elapsed, offset), len(self.elapsed) - 1)
        span = self.elapsed[i] - self.elapsed[i - 1]
        local = 0.0 if span <= 0 else (offset - self.elapsed[i - 1]) / span
        return (sum(self.edge_efforts[: i - 1]) + self.edge_efforts[i - 1] * local) / total

    def battery(self, t: float) -> float:
        if self.t1 <= self.t0:
            return self.b1
        return self.b0 + (self.b1 - self.b0) * self.energy_fraction(t)

    def status(self) -> UAVStatus:
        if self.depleted:
            return UAVStatus.UNAVAILABLE
        if self.kind == "move":
            return UAVStatus.RETURNING if self.returning else UAVStatus.EN_ROUTE
        if self.kind in ("work", "wait"):
            return UAVStatus.WORKING
        if self.kind == "charge":
            return UAVStatus.CHARGING
        return UAVStatus.EN_ROUTE


@dataclass(frozen=True, slots=True)
class LogEntry:
    time: float
    kind: str
    uav_id: str | None = None
    task_id: str | None = None
    detail: str = ""


@dataclass(slots=True)
class UAVOutcome:
    """Final accounting for one UAV."""

    uav_id: str
    battery_start: float
    battery_end: float
    distance: float
    energy_used: float
    landed: bool
    depleted: bool
    flew: bool
    work_end: float
    reserve: float
    busy_time: float
    planned_energy: float = 0.0
    margin_alerts: int = 0


@dataclass(slots=True)
class SimulationResult:
    scenario_id: str
    algorithm: str
    end_time: float
    outcomes: dict[str, UAVOutcome]
    segments: dict[str, list[Segment]]
    log: list[LogEntry]
    task_status: dict[str, TaskStatus]
    task_finish: dict[str, float]
    deadlines_missed: list[str]
    replans: list[ReplanResult] = field(default_factory=list)
    initial_compute_s: float = 0.0
    status_after: dict[str, UAVStatus] = field(default_factory=dict)
    start_cells: dict[str, Cell] = field(default_factory=dict)
    start_battery: dict[str, float] = field(default_factory=dict)
    tasks: dict[str, Task] = field(default_factory=dict)

    def sample(self, uav_id: str, t: float) -> dict[str, Any]:
        """State of one UAV at time ``t``."""
        return sample_segments(
            uav_id,
            self.segments.get(uav_id, []),
            t,
            self.start_cells[uav_id],
            self.start_battery[uav_id],
            self.status_after.get(uav_id, UAVStatus.IDLE),
        )

    def timeline(self, tick: float = 1.0, max_frames: int | None = None) -> list[dict[str, Any]]:
        """Frames of every UAV's state, one per ``tick`` (downsampled to ``max_frames``)."""
        step = tick
        if max_frames is not None and max_frames > 1:
            step = max(tick, self.end_time / (max_frames - 1))
        count = int(self.end_time / step) + 1
        times = [i * step for i in range(count)]
        if not times or times[-1] < self.end_time:
            times.append(self.end_time)
        uav_ids = sorted(self.start_cells)
        return [{"t": t, "uavs": [self.sample(u, t) for u in uav_ids]} for t in times]

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = jsonable(
            {
                "scenario_id": self.scenario_id,
                "algorithm": self.algorithm,
                "end_time": self.end_time,
                "outcomes": self.outcomes,
                "log": self.log,
                "task_status": self.task_status,
                "task_finish": self.task_finish,
                "deadlines_missed": self.deadlines_missed,
                "replans": [replan_summary(r) for r in self.replans],
            }
        )
        return result


def replan_summary(result: ReplanResult) -> dict[str, Any]:
    """A replan without its plan body, for logs and result files."""
    return {
        "event_type": result.event_type,
        "time": result.time,
        "time_ms": result.time_ms,
        "scope": result.scope,
        "escalated": result.escalated,
        "escalation_reason": result.escalation_reason,
        "affected_uavs": list(result.affected_uavs),
        "affected_tasks": list(result.affected_tasks),
        "moved": [jsonable(m) for m in result.moved],
        "added": list(result.added),
        "dropped": list(result.dropped),
        "z_before": result.z_before,
        "z_after": result.z_after,
    }


def sample_segments(
    uav_id: str,
    segments: list[Segment],
    t: float,
    start_cell: Cell,
    start_battery: float,
    end_status: UAVStatus,
) -> dict[str, Any]:
    """Interpolated state of a UAV at time ``t`` from its executed segments."""
    if not segments or t < segments[0].t0:
        return {
            "id": uav_id,
            "x": float(start_cell[0]),
            "y": float(start_cell[1]),
            "battery": start_battery,
            "status": UAVStatus.IDLE.value,
        }
    index = max(bisect.bisect_right([s.t0 for s in segments], t) - 1, 0)
    seg = segments[index]
    if t > seg.t1 and index == len(segments) - 1:
        x, y = seg.position(seg.t1)
        return {"id": uav_id, "x": x, "y": y, "battery": seg.b1, "status": end_status.value}
    x, y = seg.position(t)
    return {"id": uav_id, "x": x, "y": y, "battery": seg.battery(t), "status": seg.status().value}


class Simulator:
    """Executes a plan, draining batteries through the EnergyEstimator."""

    def __init__(
        self,
        scenario: Scenario,
        plan: Plan,
        config: SimConfig | None = None,
        planner_config: PlannerConfig | None = None,
    ) -> None:
        self.scenario = scenario
        self.plan = plan
        self.config = config or SimConfig()
        self.planner_config = planner_config
        self.estimator = EnergyEstimator.for_scenario(scenario)
        self.env = EnvironmentManager.from_scenario(scenario)
        self.state = MissionState.from_scenario(scenario)
        self.rng = np.random.default_rng(self.config.seed)
        self.pending: dict[str, list[Segment]] = {u.id: [] for u in scenario.uavs}
        self.done: dict[str, list[Segment]] = {u.id: [] for u in scenario.uavs}
        self.log: list[LogEntry] = []
        self.task_finish: dict[str, float] = {}
        self.deadlines_missed: list[str] = []
        self.final_status: dict[str, UAVStatus] = {}
        self.start_cells = {u.id: u.start for u in scenario.uavs}
        self.start_battery = {u.id: u.battery for u in scenario.uavs}
        self.reserves = {u.id: self.estimator.reserve(u) for u in scenario.uavs}
        self.replans: list[ReplanResult] = []
        self.now = 0.0
        self._counter = itertools.count()
        self._blocks = 0
        self._alerted: set[str] = set()
        self._route_distance: dict[str, float] = {}
        self._events: list[tuple[float, int, Event]] = []
        for event in scenario.events:
            self.inject(event)
        self._engine: DynamicReplanningEngine | None = None
        for route in plan.routes:
            self.install_route(route)

    # -- noise -------------------------------------------------------------------------

    def _noise(self) -> float:
        if self.config.noise_sigma <= 0.0:
            return 1.0
        return max(0.0, float(self.rng.normal(1.0, self.config.noise_sigma)))

    # -- turning routes into segments --------------------------------------------------

    def install_route(self, route: Route, prefix: list[Segment] | None = None) -> None:
        """Replace a UAV's future with ``prefix`` followed by the segments of ``route``."""
        state = self.state.uavs.get(route.uav_id)
        self._alerted.discard(route.uav_id)
        segments: list[Segment] = list(prefix or [])
        time, battery, alive = state.ready_time, state.battery, True
        uav = state.uav
        stops = sum(leg.kind == "to_charge" for leg in route.legs)
        first_takeoff = route.takeoff_energy - stops * self.estimator.relaunch_energy(uav)
        last_landing = route.landing_energy - stops * self.estimator.landing_energy(uav)
        if first_takeoff > 1e-12:
            time, battery, alive = self._append(
                segments,
                route.uav_id,
                "takeoff",
                time,
                0.0,
                battery,
                first_takeoff,
                (state.cell,),
            )
        for leg in route.legs:
            if not alive:
                break
            time, battery, alive = self._append_leg(segments, route.uav_id, leg, time, battery)
            if alive and leg.kind == "to_task":
                time, battery, alive = self._append_task(segments, route.uav_id, leg, time, battery)
            elif alive and leg.kind == "to_charge":
                time, battery, alive = self._append_charge(segments, uav, leg, time, battery)
        if alive and last_landing > 1e-12:
            cell = segments[-1].cells[-1] if segments else state.cell
            self._append(
                segments, route.uav_id, "landing", time, 0.0, battery, last_landing, (cell,)
            )
        self.pending[route.uav_id] = segments
        self._route_distance[route.uav_id] = sum(s.distance for s in segments)

    def _append(
        self,
        segments: list[Segment],
        uav_id: str,
        kind: SegmentKind,
        t0: float,
        duration: float,
        b0: float,
        energy: float,
        cells: tuple[Cell, ...],
        task_id: str | None = None,
    ) -> tuple[float, float, bool]:
        planned = energy
        energy *= self._noise()
        if energy > b0:
            fraction = 0.0 if energy <= 0 else b0 / energy
            segments.append(
                Segment(
                    uav_id,
                    kind,
                    t0,
                    t0 + duration * fraction,
                    b0,
                    0.0,
                    cells,
                    task_id=task_id,
                    depleted=True,
                    planned_energy=planned * fraction,
                )
            )
            return t0 + duration * fraction, 0.0, False
        t1, b1 = t0 + duration, b0 - energy
        segments.append(
            Segment(uav_id, kind, t0, t1, b0, b1, cells, task_id=task_id, planned_energy=planned)
        )
        return t1, b1, True

    def _append_leg(
        self, segments: list[Segment], uav_id: str, leg: Leg, t0: float, b0: float
    ) -> tuple[float, float, bool]:
        energy = leg.energy * self._noise()
        speed = leg.distance / leg.time if leg.time > 0 else 1.0
        elapsed = [0.0]
        for d in leg.edge_distances:
            elapsed.append(elapsed[-1] + d / speed)
        returning = leg.kind == "return"
        if energy <= b0:
            b1 = b0 - energy
            segments.append(
                Segment(
                    uav_id,
                    "move",
                    t0,
                    t0 + leg.time,
                    b0,
                    b1,
                    leg.path,
                    tuple(elapsed),
                    leg.distance,
                    leg.task_id,
                    returning,
                    edge_distances=leg.edge_distances,
                    edge_efforts=leg.edge_efforts,
                    planned_energy=leg.energy,
                )
            )
            return t0 + leg.time, b1, True
        # The battery runs out partway along the leg: stop at the edge where it happens.
        total_effort = sum(leg.edge_efforts) or 1.0
        spent = 0.0
        kept = 1
        for effort in leg.edge_efforts:
            cost = energy * effort / total_effort
            if spent + cost > b0:
                break
            spent += cost
            kept += 1
        stop = elapsed[kept - 1]
        segments.append(
            Segment(
                uav_id,
                "move",
                t0,
                t0 + stop,
                b0,
                0.0,
                leg.path[:kept],
                tuple(elapsed[:kept]),
                sum(leg.edge_distances[: kept - 1]),
                leg.task_id,
                returning,
                True,
                leg.edge_distances[: kept - 1],
                leg.edge_efforts[: kept - 1],
                leg.energy * sum(leg.edge_efforts[: kept - 1]) / total_effort,
            )
        )
        return t0 + stop, 0.0, False

    def _append_charge(
        self, segments: list[Segment], uav: UAV, leg: Leg, t0: float, b0: float
    ) -> tuple[float, float, bool]:
        """Land, charge at the linear rate, and take off again."""
        time, battery, alive = t0, b0, True
        landing = self.estimator.landing_energy(uav)
        if landing > 0.0:
            time, battery, alive = self._append(
                segments, uav.id, "landing", time, 0.0, battery, landing, (leg.end,)
            )
        if not alive:
            return time, battery, False
        gain = self.estimator.charge_gain(uav, battery)
        end = time + leg.charge_time
        segments.append(Segment(uav.id, "charge", time, end, battery, battery + gain, (leg.end,)))
        relaunch = self.estimator.relaunch_energy(uav)
        if relaunch > 0.0:
            return self._append(
                segments, uav.id, "takeoff", end, 0.0, battery + gain, relaunch, (leg.end,)
            )
        return end, battery + gain, True

    def _append_task(
        self, segments: list[Segment], uav_id: str, leg: Leg, t0: float, b0: float
    ) -> tuple[float, float, bool]:
        assert leg.task_id is not None
        uav = self.state.uavs.get(uav_id).uav
        task = self.state.tasks.tasks[leg.task_id]
        time, battery, alive = t0, b0, True
        if task.release_time > time:
            wait = task.release_time - time
            time, battery, alive = self._append(
                segments,
                uav_id,
                "wait",
                time,
                wait,
                battery,
                self.estimator.wait_energy(uav, wait),
                (leg.end,),
            )
        if alive:
            time, battery, alive = self._append(
                segments,
                uav_id,
                "work",
                time,
                task.duration,
                battery,
                self.estimator.task_energy(uav, task),
                (leg.end,),
                leg.task_id,
            )
        return time, battery, alive

    # -- running -----------------------------------------------------------------------

    def inject(self, event: Event) -> None:
        """Schedule an event; one in the past takes effect now."""
        time = max(event.time, self.now)
        heapq.heappush(self._events, (time, next(self._counter), replace(event, time=time)))

    def advance(self, until: float) -> None:
        """Process events and complete segments up to time ``until``."""
        while True:
            self._complete_segments(until)
            if not self._events or self._events[0][0] > until:
                break
            _, _, event = heapq.heappop(self._events)
            self._apply_event(event)

    def _complete_segments(self, until: float) -> None:
        """Complete segments in time order, stopping at the next event."""
        while True:
            limit = min(until, self._events[0][0]) if self._events else until
            due = [(q[0].t1, u) for u, q in self.pending.items() if q and q[0].t1 <= limit]
            if not due:
                break
            uav_id = min(due)[1]
            seg = self.pending[uav_id].pop(0)
            self._complete(seg, not self.pending[uav_id])
        limit = min(until, self._events[0][0]) if self._events else until
        if limit != float("inf"):
            self.now = max(self.now, limit)

    def _complete(self, seg: Segment, is_last: bool) -> None:
        self.done[seg.uav_id].append(seg)
        state = self.state.uavs.get(seg.uav_id)
        state.cell = seg.cells[-1]
        state.battery = seg.b1
        state.ready_time = seg.t1
        state.airborne = True
        if seg.depleted:
            state.status = UAVStatus.UNAVAILABLE
            self.final_status[seg.uav_id] = UAVStatus.UNAVAILABLE
            self.pending[seg.uav_id] = []
            self.log.append(LogEntry(seg.t1, "BATTERY_DEPLETED", seg.uav_id, seg.task_id))
            return
        if seg.kind == "work" and seg.task_id is not None:
            self._finish_task(seg)
        self._check_margin(seg)
        if not is_last and self._should_return_early(seg):
            self._return_early(seg)
            return
        if is_last:
            self._land(seg.uav_id, seg)

    def _return_margin(self, uav_id: str, battery: float, cell: Cell) -> float | None:
        """Battery left after flying home and keeping the reserve; None if no way home."""
        uav = self.state.uavs.get(uav_id).uav
        option = self.estimator.best_return(uav, cell, self.state.stations(), self.env.path)
        if option is None:
            return None
        landing = self.estimator.landing_energy(uav)
        total = self._route_distance.get(uav_id, 0.0)
        left = sum(s.distance for s in self.pending[uav_id])
        fraction = left / total if total > 0.0 else 1.0
        return battery - option.energy - landing - self.estimator.reserve(uav, fraction)

    def _check_margin(self, seg: Segment) -> None:
        """Raise one alert per plan when actual energy eats into the safety margin."""
        if seg.kind not in ("move", "work") or seg.returning or seg.uav_id in self._alerted:
            return
        margin = self._return_margin(seg.uav_id, seg.b1, seg.cells[-1])
        if margin is None or margin < -1e-6:
            self._alerted.add(seg.uav_id)
            text = "no station reachable" if margin is None else f"margin {margin:.2f}"
            self.log.append(LogEntry(seg.t1, "MARGIN_ALERT", seg.uav_id, seg.task_id, text))

    def _should_return_early(self, seg: Segment) -> bool:
        """True if actual energy no longer covers the trip home plus the reserve."""
        if not self.config.early_rth or seg.kind not in ("move", "work") or seg.returning:
            return False
        if self.pending[seg.uav_id][0].returning:
            return False
        margin = self._return_margin(seg.uav_id, seg.b1, seg.cells[-1])
        return margin is not None and margin < -1e-6

    def _return_early(self, seg: Segment) -> None:
        """Abort the route and fly home; the unfinished tasks are replanned for the others."""
        state = self.state.uavs.get(seg.uav_id)
        uav = state.uav
        cell = seg.cells[-1]
        option = self.estimator.best_return(uav, cell, self.state.stations(), self.env.path)
        assert option is not None
        path = option.path
        leg = Leg(
            "return",
            cell,
            path.cells[-1],
            None,
            path.cells,
            path.distance,
            option.time,
            option.energy,
            path.edge_distances,
            path.edge_efforts,
        )
        segments: list[Segment] = []
        time, battery, alive = self._append_leg(segments, uav.id, leg, seg.t1, seg.b1)
        landing = self.estimator.landing_energy(uav)
        if alive and landing > 0.0:
            self._append(
                segments, uav.id, "landing", time, 0.0, battery, landing, (path.cells[-1],)
            )
        self.pending[uav.id] = segments
        self._route_distance[uav.id] = sum(s.distance for s in segments)
        state.status = UAVStatus.UNAVAILABLE  # out of the replan until it has landed
        self.inject(Event(EventType.RTH, seg.t1, uav_id=uav.id))

    def _finish_task(self, seg: Segment) -> None:
        assert seg.task_id is not None
        if self.state.tasks.status[seg.task_id] == TaskStatus.CANCELLED:
            return
        task = self.state.tasks.tasks[seg.task_id]
        self.state.tasks.set(seg.task_id, TaskStatus.COMPLETED)
        self.task_finish[seg.task_id] = seg.t1
        self.log.append(LogEntry(seg.t1, "TASK_COMPLETED", seg.uav_id, seg.task_id))
        if task.deadline is not None and seg.t1 > task.deadline + 1e-9:
            self.deadlines_missed.append(seg.task_id)

    def _land(self, uav_id: str, seg: Segment) -> None:
        state = self.state.uavs.get(uav_id)
        if any(s.cell == seg.cells[-1] for s in self.state.stations()):
            state.status = UAVStatus.CHARGING
            state.airborne = False
            self.final_status[uav_id] = UAVStatus.CHARGING
            self.log.append(LogEntry(seg.t1, "LANDED", uav_id))
        else:
            state.status = UAVStatus.UNAVAILABLE
            self.final_status[uav_id] = UAVStatus.UNAVAILABLE
            self.log.append(LogEntry(seg.t1, "STRANDED", uav_id, detail="no station reached"))

    # -- commit points -----------------------------------------------------------------

    def _cut(self, seg: Segment, t: float) -> Segment:
        """The part of ``seg`` that will still be executed once the UAV commits at ``t``."""
        if seg.depleted:
            return seg
        if seg.kind == "move" and len(seg.cells) > 1:
            offset = t - seg.t0
            i = min(bisect.bisect_right(seg.elapsed, offset), len(seg.cells) - 1)
            total = sum(seg.edge_efforts) or 1.0
            fraction = sum(seg.edge_efforts[:i]) / total
            return replace(
                seg,
                t1=seg.t0 + seg.elapsed[i],
                b1=seg.b0 - (seg.b0 - seg.b1) * fraction,
                cells=seg.cells[: i + 1],
                elapsed=seg.elapsed[: i + 1],
                distance=sum(seg.edge_distances[:i]),
                edge_distances=seg.edge_distances[:i],
                edge_efforts=seg.edge_efforts[:i],
                planned_energy=seg.planned_energy * fraction,
            )
        if seg.kind == "work" and self.config.in_progress == "finish":
            return seg
        return replace(
            seg, t1=t, b1=seg.battery(t), planned_energy=seg.planned_energy * self._share(seg, t)
        )

    def _snapshot_assignment(self, t: float) -> dict[str, tuple[str, ...]]:
        """Tasks each UAV still has to start, before any commit."""
        snapshot: dict[str, tuple[str, ...]] = {}
        for uav_id, queue in self.pending.items():
            work = [s for s in queue if s.kind == "work" and s.task_id is not None]
            started = work and work[0] is queue[0] and t >= queue[0].t0
            if started and self.config.in_progress == "finish":
                work = work[1:]
            snapshot[uav_id] = tuple(s.task_id for s in work if s.task_id is not None)
        return snapshot

    def _commit(self, uav_id: str, t: float) -> list[Segment]:
        """Stop a UAV's plan at its next commit point and return what it still flies."""
        state = self.state.uavs.get(uav_id)
        queue = self.pending[uav_id]
        kept: list[Segment] = []
        if queue and t >= queue[0].t0:
            seg = self._cut(queue[0], t)
            kept = [seg]
            state.cell = seg.cells[-1]
            state.battery = seg.b1
            state.ready_time = seg.t1
            state.airborne = True
            if seg.depleted:
                state.status = UAVStatus.UNAVAILABLE
            if seg.kind == "work" and seg.task_id is not None and seg is queue[0]:
                self.state.tasks.set(seg.task_id, TaskStatus.IN_PROGRESS)
        state.ready_time = max(state.ready_time, t)
        self.pending[uav_id] = kept
        return kept

    def _commit_all(self, t: float) -> tuple[dict[str, list[Segment]], dict[str, tuple[str, ...]]]:
        snapshot = self._snapshot_assignment(t)
        kept: dict[str, list[Segment]] = {}
        for uav_id in sorted(self.pending):
            status = self.state.uavs.get(uav_id).status
            if status != UAVStatus.UNAVAILABLE:
                kept[uav_id] = self._commit(uav_id, t)
        return kept, snapshot

    def _escape(self, uav_id: str, kept: list[Segment]) -> None:
        """Move a UAV out of a region that has just been blocked around it."""
        state = self.state.uavs.get(uav_id)
        if self.env.is_free(state.cell):
            return
        target = self.env.escape_cell(state.cell)
        if target is None:
            return
        uav = state.uav
        d = straight_line(state.cell, target, self.scenario.grid.cell_size)
        path = PathInfo((state.cell, target), d, d, 0.0, (d,), (d,))
        energy = self.estimator.travel_energy(uav, path)
        duration = self.estimator.travel_time(uav, path)
        b1 = max(state.battery - energy, 0.0)
        kept.append(
            Segment(
                uav_id,
                "move",
                state.ready_time,
                state.ready_time + duration,
                state.battery,
                b1,
                (state.cell, target),
                (0.0, duration),
                d,
                edge_distances=(d,),
                edge_efforts=(d,),
            )
        )
        state.cell, state.battery, state.ready_time = target, b1, state.ready_time + duration
        self.log.append(LogEntry(state.ready_time, "ESCAPED_BLOCK", uav_id))

    # -- events ------------------------------------------------------------------------

    def _apply_event(self, event: Event) -> None:
        self.log.append(
            LogEntry(
                event.time, event.type.value, event.uav_id, self._event_task(event), event.label
            )
        )
        replan_on = self.config.replan is not None
        kept: dict[str, list[Segment]] = {}
        snapshot: dict[str, tuple[str, ...]] = {}
        if replan_on:
            kept, snapshot = self._commit_all(event.time)

        if event.type == EventType.NEW_TASK and event.task is not None:
            self.state.tasks.add(event.task)
        elif event.type == EventType.BLOCK_REGION:
            self._apply_block(event, replan_on)
        elif event.type == EventType.UAV_UNAVAILABLE and event.uav_id is not None:
            self._apply_unavailable(event.uav_id, event.time, kept)
        elif event.type == EventType.BATTERY_DROP and event.uav_id is not None:
            self._apply_battery_drop(event.uav_id, event.amount or 0.0, replan_on)
        elif event.type == EventType.TASK_DURATION_CHANGE and event.task_id is not None:
            if event.new_duration is not None:
                self.state.tasks.update_duration(event.task_id, event.new_duration)
        elif event.type == EventType.TASK_CANCELLED and event.task_id is not None:
            if self.state.tasks.status[event.task_id] != TaskStatus.COMPLETED:
                self.state.tasks.set(event.task_id, TaskStatus.CANCELLED)
        elif event.type == EventType.UAV_JOINS and event.uav is not None:
            self._apply_join(event, kept)
        elif event.type == EventType.STATION_OUTAGE and event.station_id is not None:
            self.state.stations_out.add(event.station_id)
        elif event.type == EventType.WIND_CHANGE:
            self._apply_wind(event, replan_on)
        elif (
            event.type == EventType.TASK_DEADLINE_CHANGE
            and event.task_id is not None
            and event.new_deadline is not None
        ):
            self.state.tasks.update_deadline(event.task_id, event.new_deadline)
        if event.layer is not None:
            self.state.layers[event.layer.id] = event.layer
            if event.type == EventType.TERRAIN_CHANGE:
                self.env = EnvironmentManager.from_scenario(self.state.derived_scenario())

        if replan_on:
            self._replan(event, kept, snapshot)

    @staticmethod
    def _event_task(event: Event) -> str | None:
        return event.task.id if event.task is not None else event.task_id

    def _apply_block(self, event: Event, replan_on: bool) -> None:
        grid = self.scenario.grid
        cells = set(event.cells or ())
        if event.polygon is not None:
            cells.update(polygon_cells(event.polygon, grid.width, grid.height))
        self._blocks += 1
        self.state.blocked.append(
            Obstacle(f"blocked{self._blocks}", "no_fly", cells=tuple(sorted(cells)))
        )
        self.env.block_cells(cells)
        if not replan_on:
            self._halt_at_block(cells)

    def _halt_at_block(self, cells: set[Cell]) -> None:
        """Without replanning, a UAV stops at the edge of a region its route enters."""
        for uav_id, queue in self.pending.items():
            for index, seg in enumerate(queue):
                if seg.kind != "move" or not cells.intersection(seg.cells[1:]):
                    continue
                stop = next(i for i, c in enumerate(seg.cells) if i > 0 and c in cells) - 1
                cut = self._cut(seg, seg.t0 + seg.elapsed[stop]) if stop > 0 else None
                self.pending[uav_id] = [*queue[:index], *([cut] if cut else [])]
                if not self.pending[uav_id]:
                    self.state.uavs.get(uav_id).status = UAVStatus.UNAVAILABLE
                    self.final_status[uav_id] = UAVStatus.UNAVAILABLE
                self.log.append(LogEntry(seg.t0, "ROUTE_BLOCKED", uav_id))
                break

    def _apply_unavailable(self, uav_id: str, t: float, kept: dict[str, list[Segment]]) -> None:
        state = self.state.uavs.get(uav_id)
        queue = kept.get(uav_id) or self.pending[uav_id]
        if queue and t >= queue[0].t0:
            current = queue[0]
            if current.kind == "work" and current.task_id is not None:
                self.state.tasks.set(current.task_id, TaskStatus.PENDING)
            self.done[uav_id].append(stopped := self._stop_at(current, t))
            state.cell, state.battery = stopped.cells[-1], stopped.b1
        self.pending[uav_id] = []
        kept[uav_id] = []
        state.status = UAVStatus.UNAVAILABLE
        self.final_status[uav_id] = UAVStatus.UNAVAILABLE

    @staticmethod
    def _share(seg: Segment, t: float) -> float:
        """Fraction of a segment's energy that has been spent by time ``t``."""
        return seg.energy_fraction(t) if seg.t1 > seg.t0 else 1.0

    @staticmethod
    def _stop_at(seg: Segment, t: float) -> Segment:
        """The segment truncated at ``t``, keeping only the distance actually flown."""
        if seg.kind != "move" or len(seg.cells) < 2:
            return replace(
                seg,
                t1=t,
                b1=seg.battery(t),
                cells=(seg.cells[0],),
                elapsed=(),
                planned_energy=seg.planned_energy * Simulator._share(seg, t),
            )
        i = max(bisect.bisect_right(seg.elapsed, t - seg.t0) - 1, 0)
        return replace(
            seg,
            t1=t,
            b1=seg.battery(t),
            cells=(seg.cells[i],),
            elapsed=(),
            distance=sum(seg.edge_distances[:i]),
            planned_energy=seg.planned_energy * Simulator._share(seg, t),
        )

    def _apply_battery_drop(self, uav_id: str, amount: float, replan_on: bool) -> None:
        state = self.state.uavs.get(uav_id)
        if replan_on:
            state.battery = max(state.battery - amount, 0.0)
            return
        # Without replanning, the same flight simply starts from a lower level.
        adjusted: list[Segment] = []
        for seg in self.pending[uav_id]:
            b0, b1 = seg.b0 - amount, seg.b1 - amount
            if b1 < 0.0:
                adjusted.append(replace(seg, b0=max(b0, 0.0), b1=0.0, depleted=True))
                break
            adjusted.append(replace(seg, b0=b0, b1=b1))
        self.pending[uav_id] = adjusted

    def _apply_wind(self, event: Event, replan_on: bool) -> None:
        """Change the wind. Without replanning, the flights already planned are re-priced."""
        before = self.estimator
        self.state.wind = event.wind
        self.estimator = EnergyEstimator(self.scenario.energy, event.wind)
        if not replan_on:
            for uav_id in self.pending:
                self._reprice(uav_id, before)

    def _reprice(self, uav_id: str, before: EnergyEstimator) -> None:
        """Rebuild a UAV's remaining battery curve under the current wind."""
        uav = self.state.uavs.get(uav_id).uav
        adjusted: list[Segment] = []
        level: float | None = None
        for seg in self.pending[uav_id]:
            b0 = seg.b0 if level is None else level
            energy = seg.b0 - seg.b1
            if seg.kind == "move" and len(seg.cells) > 1:
                path = PathInfo(
                    seg.cells, seg.distance, seg.distance, 0.0, seg.edge_distances, seg.edge_efforts
                )
                energy *= self.estimator.wind_factor(uav, path) / before.wind_factor(uav, path)
            if b0 - energy < 0.0:
                adjusted.append(replace(seg, b0=b0, b1=0.0, depleted=True))
                self.pending[uav_id] = adjusted
                return
            level = b0 - energy
            adjusted.append(replace(seg, b0=b0, b1=level))
        self.pending[uav_id] = adjusted

    def _apply_join(self, event: Event, kept: dict[str, list[Segment]]) -> None:
        assert event.uav is not None
        uav = replace(event.uav, ready_time=event.time)
        self.state.uavs.add(uav)
        self.pending[uav.id] = []
        self.done[uav.id] = []
        kept[uav.id] = []
        self.start_cells[uav.id] = uav.start
        self.start_battery[uav.id] = uav.battery
        self.reserves[uav.id] = self.estimator.reserve(uav)

    def _replan(
        self,
        event: Event,
        kept: dict[str, list[Segment]],
        snapshot: dict[str, tuple[str, ...]],
    ) -> None:
        for uav_id in sorted(kept):
            if self.state.uavs.get(uav_id).status != UAVStatus.UNAVAILABLE:
                self._escape(uav_id, kept[uav_id])
        if self._engine is None:
            assert self.config.replan is not None
            self._engine = DynamicReplanningEngine(
                PlanningContext(self.scenario, self.planner_config),
                self.config.replan,
                self.planner_config,
            )
        result = self._engine.replan(self.state, event, snapshot)
        self.plan = result.plan
        self.replans.append(result)
        self.log.append(
            LogEntry(
                event.time,
                "REPLAN",
                detail=f"{result.scope}; moved {result.tasks_moved}; {result.time_ms:.1f} ms",
            )
        )
        for route in result.plan.routes:
            self.install_route(route, kept.get(route.uav_id, []))
        # A UAV with no new route and no kept work has nothing left to do.
        for uav_id, segments in kept.items():
            if uav_id not in result.plan.assignment():
                self.pending[uav_id] = segments

    def run(self) -> SimulationResult:
        """Run to the end of the mission."""
        self.advance(float("inf"))
        return self.result()

    def result(self) -> SimulationResult:
        for task_id, status in self.state.tasks.status.items():
            if status in (TaskStatus.PENDING, TaskStatus.IN_PROGRESS):
                self.state.tasks.set(task_id, TaskStatus.FAILED)
        outcomes: dict[str, UAVOutcome] = {}
        end_time = 0.0
        for uav_id, segments in self.done.items():
            flew = any(s.kind in ("move", "work") for s in segments)
            start = self.start_battery[uav_id]
            final = segments[-1].b1 if segments else start
            consumed = sum(max(s.b0 - s.b1, 0.0) for s in segments)
            last = segments[-1].t1 if segments else 0.0
            first = segments[0].t0 if segments else 0.0
            end_time = max(end_time, last)
            outcomes[uav_id] = UAVOutcome(
                uav_id=uav_id,
                battery_start=start,
                battery_end=final,
                distance=sum(s.distance for s in segments),
                energy_used=consumed,
                landed=self.final_status.get(uav_id) == UAVStatus.CHARGING,
                depleted=any(s.depleted for s in segments),
                flew=flew,
                work_end=last,
                reserve=self.reserves[uav_id],
                busy_time=last - first,
                planned_energy=sum(s.planned_energy for s in segments),
                margin_alerts=sum(
                    e.kind == "MARGIN_ALERT" and e.uav_id == uav_id for e in self.log
                ),
            )
        return SimulationResult(
            scenario_id=self.scenario.id,
            algorithm=self.plan.algorithm,
            end_time=end_time,
            outcomes=outcomes,
            segments=self.done,
            log=sorted(self.log, key=lambda e: e.time),
            task_status=dict(self.state.tasks.status),
            task_finish=self.task_finish,
            deadlines_missed=sorted(self.deadlines_missed),
            replans=self.replans,
            initial_compute_s=self.plan.compute_time_s,
            status_after=self.final_status,
            start_cells=self.start_cells,
            start_battery=self.start_battery,
            tasks=dict(self.state.tasks.tasks),
        )
