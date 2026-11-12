"""Point-to-point path search: Dijkstra, A* and energy-weighted A*.

:class:`~uavplan.core.environment.EnvironmentManager` uses SciPy's C implementation
when it fills the key-node path cache. These explicit versions serve on-demand queries
and the tests that cross-check the three searches against each other.
"""

from __future__ import annotations

import heapq
import itertools
from collections.abc import Callable
from dataclasses import dataclass

from uavplan.core.energy import EnergyEstimator
from uavplan.core.environment import EnvironmentManager, octile
from uavplan.core.models import UAV, Cell, PathMetric

Heuristic = Callable[[Cell], float]


@dataclass(frozen=True, slots=True)
class SearchResult:
    cells: tuple[Cell, ...]
    cost: float
    expanded: int


def _search(
    env: EnvironmentManager, start: Cell, goal: Cell, use_effort: bool, heuristic: Heuristic
) -> SearchResult | None:
    if not (env.is_free(start) and env.is_free(goal)):
        return None
    counter = itertools.count()
    best: dict[Cell, float] = {start: 0.0}
    parent: dict[Cell, Cell] = {}
    frontier: list[tuple[float, int, Cell]] = [(heuristic(start), next(counter), start)]
    closed: set[Cell] = set()
    while frontier:
        _, _, cell = heapq.heappop(frontier)
        if cell in closed:
            continue
        closed.add(cell)
        if cell == goal:
            path = [cell]
            while path[-1] != start:
                path.append(parent[path[-1]])
            return SearchResult(tuple(reversed(path)), best[goal], len(closed))
        for nxt, distance, effort in env.neighbors(cell):
            cost = best[cell] + (effort if use_effort else distance)
            if cost < best.get(nxt, float("inf")):
                best[nxt] = cost
                parent[nxt] = cell
                heapq.heappush(frontier, (cost + heuristic(nxt), next(counter), nxt))
    return None


def dijkstra(
    env: EnvironmentManager, start: Cell, goal: Cell, use_effort: bool = False
) -> SearchResult | None:
    """Uninformed search by distance, or by energy-weighted length."""
    return _search(env, start, goal, use_effort, lambda _: 0.0)


def astar(env: EnvironmentManager, start: Cell, goal: Cell) -> SearchResult | None:
    """A* by distance with the admissible octile heuristic."""
    size = env.grid.cell_size
    return _search(env, start, goal, False, lambda c: octile(c, goal, size))


def energy_astar(
    env: EnvironmentManager, start: Cell, goal: Cell, energy_per_meter: float = 1.0
) -> SearchResult | None:
    """A* where edge weight is energy; heuristic is octile × minimum energy per metre."""
    size = env.grid.cell_size
    floor = env.min_multiplier()
    result = _search(env, start, goal, True, lambda c: octile(c, goal, size) * floor)
    if result is None:
        return None
    return SearchResult(result.cells, result.cost * energy_per_meter, result.expanded)


@dataclass(frozen=True, slots=True)
class PathOption:
    cells: tuple[Cell, ...]
    distance: float
    energy: float


@dataclass(frozen=True, slots=True)
class LegPaths:
    """One leg under both path metrics, for showing that shortest is not always cheapest."""

    uav_id: str
    energy_optimal: PathOption
    distance_optimal: PathOption


def compare_leg_paths(
    env: EnvironmentManager, estimator: EnergyEstimator, uav: UAV, start: Cell, end: Cell
) -> LegPaths | None:
    """Price a leg along its energy-optimal path and its shortest-distance path."""

    def option(metric: PathMetric) -> PathOption | None:
        path = env.path(start, end, metric)
        if path is None:
            return None
        return PathOption(path.cells, path.distance, estimator.travel_energy(uav, path))

    energy_optimal, distance_optimal = option("energy"), option("distance")
    if energy_optimal is None or distance_optimal is None:
        return None
    return LegPaths(uav.id, energy_optimal, distance_optimal)
