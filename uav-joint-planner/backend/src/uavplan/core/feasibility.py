"""FeasibilityChecker: evaluates routes against every hard constraint.

Route feasibility for ``[s, j1, ..., jk, station]``::

    E_route + Σ E_task + E_return + E_reserve ≤ B_i

With strict safety on, the UAV must also be able to reach a station, keeping its
reserve, from every intermediate task.
"""

from __future__ import annotations

from dataclasses import dataclass

from uavplan.core.energy import EnergyEstimator, ReturnOption
from uavplan.core.environment import EnvironmentManager
from uavplan.core.models import (
    EPS,
    UAV,
    BatteryPoint,
    Cell,
    FeasibilityReport,
    Leg,
    PathInfo,
    Route,
    Scenario,
    Task,
    UAVStatus,
    Violation,
    ViolationType,
    charge_station,
    is_charge_stop,
)


@dataclass(frozen=True, slots=True)
class LegCost:
    path: PathInfo
    time: float
    energy: float


@dataclass(frozen=True, slots=True)
class RouteEval:
    """Result of walking one route; everything the planners need to score it."""

    uav_id: str
    seq: tuple[str, ...]
    violations: tuple[Violation, ...]
    distance: float
    travel_energy: float
    task_energy: float
    return_energy: float
    takeoff_energy: float
    landing_energy: float
    wait_energy: float
    completion_time: float
    load: float
    margin: float
    station_id: str | None
    recharged: float = 0.0

    @property
    def feasible(self) -> bool:
        return not self.violations

    @property
    def total_energy(self) -> float:
        return (
            self.travel_energy
            + self.task_energy
            + self.return_energy
            + self.takeoff_energy
            + self.landing_energy
            + self.wait_energy
        )


def is_capable(uav: UAV, task: Task) -> bool:
    return task.capability is None or task.capability in uav.capabilities


class FeasibilityChecker:
    """Evaluates routes for one scenario, memoizing results by ``(uav, sequence)``."""

    def __init__(
        self,
        scenario: Scenario,
        env: EnvironmentManager | None = None,
        estimator: EnergyEstimator | None = None,
        strict_prefix: bool | None = None,
    ) -> None:
        self.scenario = scenario
        self.env = env or EnvironmentManager.from_scenario(scenario)
        self.estimator = estimator or EnergyEstimator.for_scenario(scenario)
        self.strict_prefix = (
            scenario.energy.strict_prefix_safety if strict_prefix is None else strict_prefix
        )
        self.uavs = {u.id: u for u in scenario.uavs}
        self.tasks = {t.id: t for t in scenario.tasks}
        self._station_cells = {s.cell for s in scenario.stations}
        self._stations = {s.id: s for s in scenario.stations}
        self._legs: dict[tuple[str, Cell, Cell], LegCost | None] = {}
        self._returns: dict[tuple[str, Cell], ReturnOption | None] = {}
        self._evals: dict[tuple[str, tuple[str, ...]], RouteEval] = {}
        self._version = self.env.version

    def _sync(self) -> None:
        if self._version != self.env.version:
            self._legs.clear()
            self._returns.clear()
            self._evals.clear()
            self._version = self.env.version

    def leg(self, uav: UAV, a: Cell, b: Cell) -> LegCost | None:
        """Time and energy of flying from ``a`` to ``b``; ``None`` if unreachable."""
        key = (uav.id, a, b)
        if key in self._legs:
            return self._legs[key]
        path = self.env.path(a, b)
        cost = (
            None
            if path is None
            else LegCost(
                path, self.estimator.travel_time(uav, path), self.estimator.travel_energy(uav, path)
            )
        )
        self._legs[key] = cost
        return cost

    def return_option(self, uav: UAV, cell: Cell) -> ReturnOption | None:
        key = (uav.id, cell)
        if key not in self._returns:
            self._returns[key] = self.estimator.best_return(
                uav, cell, self.scenario.stations, self.env.path
            )
        return self._returns[key]

    def evaluate(self, uav_id: str, seq: tuple[str, ...]) -> RouteEval:
        self._sync()
        key = (uav_id, seq)
        cached = self._evals.get(key)
        if cached is None:
            cached = self._walk(self.uavs[uav_id], seq, None, None)
            self._evals[key] = cached
        return cached

    def _walk(
        self,
        uav: UAV,
        seq: tuple[str, ...],
        legs: list[Leg] | None,
        profile: list[BatteryPoint] | None,
    ) -> RouteEval:
        est = self.estimator
        violations: list[Violation] = []
        if seq and uav.status == UAVStatus.UNAVAILABLE:
            violations.append(
                Violation(ViolationType.UAV_UNAVAILABLE, uav.id, f"{uav.id} is unavailable")
            )
        reserve = est.reserve(uav)
        at_station = uav.start in self._station_cells
        idle = not seq and at_station
        takeoff = 0.0 if idle else est.takeoff_energy(uav)
        landing = 0.0 if idle else est.landing_energy(uav)
        spent = takeoff
        time = uav.ready_time
        pos = uav.start
        distance = travel_e = task_e = wait_e = charged = 0.0
        stop_takeoff = stop_landing = 0.0
        route_length = self._route_length(uav, seq) if est.config.adaptive_reserve else 0.0
        if profile is not None:
            profile.append(BatteryPoint(time, uav.battery - spent, "start"))

        for index, task_id in enumerate(seq):
            if is_charge_stop(task_id):
                station = self._stations[charge_station(task_id)]
                stop = self.leg(uav, pos, station.cell)
                if stop is None:
                    violations.append(
                        Violation(
                            ViolationType.UNREACHABLE,
                            uav.id,
                            f"no path from {pos} to charging station {station.id}",
                        )
                    )
                    continue
                arrival = time + stop.time
                distance += stop.path.distance
                travel_e += stop.energy
                touch_down = est.landing_energy(uav)
                stop_landing += touch_down
                spent += stop.energy + touch_down
                level = uav.battery - spent + charged
                need = self._reserve_at(uav, distance, route_length)
                if level < need - EPS:
                    violations.append(
                        Violation(
                            ViolationType.ENERGY_PREFIX,
                            uav.id,
                            f"reaches charging station {station.id} below the reserve",
                            margin=level - need,
                        )
                    )
                gain = est.charge_gain(uav, level)
                charged += gain
                time = arrival + est.config.charge_time
                relaunch = est.relaunch_energy(uav)
                stop_takeoff += relaunch
                spent += relaunch
                if legs is not None:
                    legs.append(
                        Leg(
                            "to_charge",
                            pos,
                            station.cell,
                            None,
                            stop.path.cells,
                            stop.path.distance,
                            stop.time,
                            stop.energy,
                            stop.path.edge_distances,
                            stop.path.edge_efforts,
                            gain,
                            est.config.charge_time,
                        )
                    )
                if profile is not None:
                    profile.append(BatteryPoint(arrival, level, f"charge at {station.id}"))
                    profile.append(BatteryPoint(time, level + gain - relaunch, station.id))
                pos = station.cell
                continue
            task = self.tasks[task_id]
            if not is_capable(uav, task):
                violations.append(
                    Violation(
                        ViolationType.CAPABILITY,
                        uav.id,
                        f"{uav.id} lacks capability '{task.capability}' for {task_id}",
                        task_id=task_id,
                    )
                )
            leg = self.leg(uav, pos, task.cell)
            if leg is None:
                violations.append(
                    Violation(
                        ViolationType.UNREACHABLE,
                        uav.id,
                        f"no path from {pos} to {task_id} at {task.cell}",
                        task_id=task_id,
                    )
                )
                continue
            arrival = time + leg.time
            start = max(arrival, task.release_time)
            waiting = est.wait_energy(uav, start - arrival)
            this_task = est.task_energy(uav, task)
            time = start + task.duration
            distance += leg.path.distance
            travel_e += leg.energy
            wait_e += waiting
            task_e += this_task
            spent += leg.energy + waiting + this_task
            if legs is not None:
                legs.append(
                    Leg(
                        "to_task",
                        pos,
                        task.cell,
                        task_id,
                        leg.path.cells,
                        leg.path.distance,
                        leg.time,
                        leg.energy,
                        leg.path.edge_distances,
                        leg.path.edge_efforts,
                    )
                )
            if profile is not None:
                level = uav.battery - spent + charged
                profile.append(BatteryPoint(arrival, level + this_task, task_id))
                profile.append(BatteryPoint(time, level, task_id))
            pos = task.cell
            if task.deadline is not None and time > task.deadline + EPS:
                violations.append(
                    Violation(
                        ViolationType.DEADLINE,
                        uav.id,
                        f"{task_id} finishes at {time:.1f}s, deadline {task.deadline:.1f}s",
                        margin=task.deadline - time,
                        task_id=task_id,
                    )
                )
            if index < len(seq) - 1:
                need = self._reserve_at(uav, distance, route_length)
                violations.extend(
                    self._prefix_violations(uav, task, spent - charged, need, landing)
                )

        ret = self.return_option(uav, pos)
        if ret is None:
            violations.append(
                Violation(ViolationType.NO_RETURN, uav.id, f"no station reachable from {pos}")
            )
            ret_energy = ret_time = 0.0
            station_id = None
        else:
            ret_energy, ret_time, station_id = ret.energy, ret.time, ret.station_id
            distance += ret.path.distance
            if legs is not None and ret.path.distance > 0.0:
                legs.append(
                    Leg(
                        "return",
                        pos,
                        ret.path.cells[-1],
                        None,
                        ret.path.cells,
                        ret.path.distance,
                        ret.time,
                        ret.energy,
                        ret.path.edge_distances,
                        ret.path.edge_efforts,
                    )
                )
        time_done = time + ret_time
        total = spent + ret_energy + landing
        margin = uav.battery + charged - total - reserve
        if profile is not None and ret is not None:
            profile.append(BatteryPoint(time_done, uav.battery + charged - total, "return"))
        if not idle and margin < -EPS:
            violations.append(
                Violation(
                    ViolationType.ENERGY_TOTAL,
                    uav.id,
                    f"needs {total + reserve:.3f} {est.unit} including reserve, "
                    f"has {uav.battery:.3f} {est.unit}",
                    margin=margin,
                )
            )
        return RouteEval(
            uav_id=uav.id,
            seq=seq,
            violations=tuple(violations),
            distance=distance,
            travel_energy=travel_e,
            task_energy=task_e,
            return_energy=ret_energy,
            takeoff_energy=takeoff + stop_takeoff,
            landing_energy=landing + stop_landing,
            wait_energy=wait_e,
            completion_time=time_done,
            load=time_done - uav.ready_time,
            margin=margin,
            station_id=station_id,
            recharged=charged,
        )

    def _reserve_at(self, uav: UAV, travelled: float, route_length: float) -> float:
        """Reserve required at a point ``travelled`` metres along the route."""
        if route_length <= 0.0:
            return self.estimator.reserve(uav)
        return self.estimator.reserve(uav, 1.0 - travelled / route_length)

    def _route_length(self, uav: UAV, seq: tuple[str, ...]) -> float:
        """Total route distance including the return, for the adaptive reserve."""
        total = 0.0
        pos = uav.start
        for entry in seq:
            cell = (
                self._stations[charge_station(entry)].cell
                if is_charge_stop(entry)
                else self.tasks[entry].cell
            )
            leg = self.leg(uav, pos, cell)
            if leg is not None:
                total += leg.path.distance
                pos = cell
        ret = self.return_option(uav, pos)
        return total + (ret.path.distance if ret is not None else 0.0)

    def _prefix_violations(
        self, uav: UAV, task: Task, spent: float, reserve: float, landing: float
    ) -> list[Violation]:
        if not self.strict_prefix:
            margin = uav.battery - spent - reserve
            if margin < -EPS:
                return [
                    Violation(
                        ViolationType.ENERGY_PREFIX,
                        uav.id,
                        f"battery falls below reserve after {task.id}",
                        margin=margin,
                        task_id=task.id,
                    )
                ]
            return []
        ret = self.return_option(uav, task.cell)
        if ret is None:
            return [
                Violation(
                    ViolationType.NO_RETURN,
                    uav.id,
                    f"no station reachable from {task.id}",
                    task_id=task.id,
                )
            ]
        margin = uav.battery - spent - ret.energy - landing - reserve
        if margin < -EPS:
            return [
                Violation(
                    ViolationType.ENERGY_PREFIX,
                    uav.id,
                    f"cannot reach a station with reserve after {task.id}",
                    margin=margin,
                    task_id=task.id,
                )
            ]
        return []

    def build_route(self, uav_id: str, seq: tuple[str, ...]) -> Route:
        """Build a full route with legs and battery profile."""
        self._sync()
        uav = self.uavs[uav_id]
        legs: list[Leg] = []
        profile: list[BatteryPoint] = []
        ev = self._walk(uav, seq, legs, profile)
        return Route(
            uav_id=uav_id,
            task_ids=tuple(t for t in seq if not is_charge_stop(t)),
            legs=tuple(legs),
            station_id=ev.station_id,
            start_time=uav.ready_time,
            completion_time=ev.completion_time,
            distance=ev.distance,
            travel_energy=ev.travel_energy,
            task_energy=ev.task_energy,
            return_energy=ev.return_energy,
            takeoff_energy=ev.takeoff_energy,
            landing_energy=ev.landing_energy,
            wait_energy=ev.wait_energy,
            reserve=self.estimator.reserve(uav),
            battery_start=uav.battery,
            battery_end=uav.battery - ev.total_energy + ev.recharged,
            margin=ev.margin,
            feasible=ev.feasible,
            profile=tuple(profile),
            recharged_energy=ev.recharged,
        )

    def check_assignment(self, assignment: dict[str, tuple[str, ...]]) -> FeasibilityReport:
        """Check every UAV's route and collect all violations."""
        violations: list[Violation] = []
        for uav_id in sorted(assignment):
            if uav_id not in self.uavs:
                raise KeyError(f"unknown UAV {uav_id}")
            violations.extend(self.evaluate(uav_id, assignment[uav_id]).violations)
        return FeasibilityReport(not violations, tuple(violations))
