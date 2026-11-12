"""EnergyEstimator: the single source of truth for every energy value in the system."""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from itertools import pairwise
from typing import TypeVar

import numpy as np
from numpy.typing import NDArray

from uavplan.core.models import UAV, Cell, EnergyConfig, PathInfo, Scenario, Station, Task, Wind

N = TypeVar("N", float, NDArray[np.float64])
WIND_MIN_FACTOR = 0.6


def edge_effort(distance: N, multiplier_a: N, multiplier_b: N) -> N:
    """Energy-weighted length of an edge, using the mean of its endpoint multipliers."""
    return distance * (multiplier_a + multiplier_b) * 0.5


@dataclass(frozen=True, slots=True)
class ReturnOption:
    """The cheapest way home from a cell."""

    station_id: str
    path: PathInfo
    energy: float
    time: float


class EnergyEstimator:
    """Computes travel, task, return and reserve energy.

    Normalized mode (default, EU): ``E_travel = k_i · effort``, task energy is given.
    Physical mode (J): ``E = P_cruise · t``, task energy is ``P_hover · dur + overhead``.
    """

    def __init__(self, config: EnergyConfig | None = None, wind: Wind | None = None) -> None:
        self.config = config or EnergyConfig()
        self.wind = wind

    @classmethod
    def for_scenario(cls, scenario: Scenario) -> EnergyEstimator:
        return cls(scenario.energy, scenario.wind)

    @property
    def unit(self) -> str:
        return "EU" if self.config.mode == "normalized" else "J"

    def travel_time(self, uav: UAV, path: PathInfo) -> float:
        return path.distance / uav.speed

    def wind_factor(self, uav: UAV, path: PathInfo) -> float:
        """Effort-weighted mean of ``max(0.6, 1 + k_w · headwind / v)`` along a path."""
        wind = self.wind
        if wind is None or wind.speed <= 0.0 or len(path.cells) < 2:
            return 1.0
        wind_x, wind_y = wind.vector()
        total = weighted = 0.0
        for (a, b), effort in zip(pairwise(path.cells), path.edge_efforts, strict=True):
            step_x, step_y = b[0] - a[0], b[1] - a[1]
            headwind = -(wind_x * step_x + wind_y * step_y) / math.hypot(step_x, step_y)
            weighted += effort * max(1.0 + wind.k * headwind / uav.speed, WIND_MIN_FACTOR)
            total += effort
        return weighted / total if total > 0.0 else 1.0

    def travel_energy(self, uav: UAV, path: PathInfo) -> float:
        wind = self.wind_factor(uav, path)
        if self.config.mode == "normalized":
            return uav.energy_per_meter * path.effort * wind
        return (
            uav.power_cruise * (path.effort / uav.speed) * wind
            + self.config.turn_energy_per_rad * path.turn_radians
        )

    def task_energy(self, uav: UAV, task: Task) -> float:
        if self.config.mode == "normalized":
            return task.energy
        return uav.power_hover * task.duration + task.energy

    def wait_energy(self, uav: UAV, seconds: float) -> float:
        """Energy spent hovering while waiting for a task's release time."""
        if self.config.mode == "normalized":
            return uav.hover_rate * seconds
        return uav.power_hover * seconds

    def takeoff_energy(self, uav: UAV) -> float:
        return 0.0 if uav.airborne else uav.takeoff_energy

    def landing_energy(self, uav: UAV) -> float:
        return uav.landing_energy

    def nominal_energy(self, uavs: Sequence[UAV], tasks: Sequence[Task], distance: float) -> float:
        """Fleet-average energy for a distance plus all tasks; a scale, not a plan."""
        if not uavs:
            return 0.0
        path = PathInfo((), distance, distance, 0.0, (), ())
        travel = sum(self.travel_energy(u, path) for u in uavs) / len(uavs)
        work = sum(self.task_energy(uavs[0], t) for t in tasks)
        return travel + work

    def reserve(self, uav: UAV, remaining_fraction: float = 1.0) -> float:
        """``E_reserve = ρ_eff · B^max``.

        ``ρ_eff`` is ``ρ``, or with the adaptive reserve on,
        ``ρ + k · σ · √(remaining_fraction)``: the less of the route is left, the less
        estimation error can still accumulate.
        """
        ratio = self.config.reserve_ratio
        if self.config.adaptive_reserve:
            fraction = min(max(remaining_fraction, 0.0), 1.0)
            ratio += self.config.adaptive_k * self.config.adaptive_sigma * math.sqrt(fraction)
        return ratio * uav.battery_max

    def charge_gain(self, uav: UAV, battery: float) -> float:
        """Energy added by one charging stop: a linear rate, capped at full capacity."""
        room = max(uav.battery_max - battery, 0.0)
        return min(room, self.config.charge_rate * self.config.charge_time)

    def relaunch_energy(self, uav: UAV) -> float:
        """Takeoff from a charging stop; the UAV has landed, whatever its first takeoff was."""
        return uav.takeoff_energy

    def best_return(
        self,
        uav: UAV,
        cell: Cell,
        stations: tuple[Station, ...],
        lookup: Callable[[Cell, Cell], PathInfo | None],
    ) -> ReturnOption | None:
        """``E_return``: minimum energy over reachable stations (or home only, if set)."""
        best: ReturnOption | None = None
        for station in sorted(stations, key=lambda s: s.id):
            if self.config.home_only_return and station.id != uav.home:
                continue
            path = lookup(cell, station.cell)
            if path is None:
                continue
            energy = self.travel_energy(uav, path)
            if best is None or energy < best.energy:
                best = ReturnOption(station.id, path, energy, self.travel_time(uav, path))
        return best
