"""EnvironmentManager: the grid graph, obstacles, no-fly zones and the path cache."""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from itertools import pairwise

import networkx as nx
import numpy as np
from numpy.typing import NDArray
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import dijkstra as sp_dijkstra

from uavplan.core.energy import edge_effort
from uavplan.core.models import Cell, GridSpec, Obstacle, PathInfo, PathMetric, Point, Scenario

_HALF_DIRECTIONS = ((1, 0), (0, 1), (1, 1), (-1, 1))
_DIRECTIONS = (*_HALF_DIRECTIONS, *((-dx, -dy) for dx, dy in _HALF_DIRECTIONS))


def _on_segment(x: float, y: float, a: Point, b: Point) -> bool:
    cross = (b[0] - a[0]) * (y - a[1]) - (b[1] - a[1]) * (x - a[0])
    if abs(cross) > 1e-9:
        return False
    return min(a[0], b[0]) - 1e-9 <= x <= max(a[0], b[0]) + 1e-9 and (
        min(a[1], b[1]) - 1e-9 <= y <= max(a[1], b[1]) + 1e-9
    )


def point_in_polygon(x: float, y: float, polygon: Sequence[Point]) -> bool:
    """Ray-casting test; points on the boundary count as inside."""
    inside = False
    n = len(polygon)
    for i in range(n):
        a, b = polygon[i], polygon[(i + 1) % n]
        if _on_segment(x, y, a, b):
            return True
        if (a[1] > y) != (b[1] > y) and x < (b[0] - a[0]) * (y - a[1]) / (b[1] - a[1]) + a[0]:
            inside = not inside
    return inside


def polygon_cells(polygon: Sequence[Point], width: int, height: int) -> list[Cell]:
    """All grid cells whose centre lies inside ``polygon``."""
    xs = [p[0] for p in polygon]
    ys = [p[1] for p in polygon]
    x0, x1 = max(0, math.floor(min(xs))), min(width - 1, math.ceil(max(xs)))
    y0, y1 = max(0, math.floor(min(ys))), min(height - 1, math.ceil(max(ys)))
    return [
        (x, y)
        for y in range(y0, y1 + 1)
        for x in range(x0, x1 + 1)
        if point_in_polygon(float(x), float(y), polygon)
    ]


def region_cells(region: Obstacle, grid: GridSpec) -> list[Cell]:
    """Cells covered by a region, clipped to the grid."""
    cells: list[Cell] = []
    if region.rect is not None:
        x0, y0, x1, y1 = region.rect
        cells.extend(
            (x, y)
            for y in range(max(0, y0), min(grid.height - 1, y1) + 1)
            for x in range(max(0, x0), min(grid.width - 1, x1) + 1)
        )
    if region.polygon is not None:
        cells.extend(polygon_cells(region.polygon, grid.width, grid.height))
    if region.cells is not None:
        cells.extend(
            (x, y) for x, y in region.cells if 0 <= x < grid.width and 0 <= y < grid.height
        )
    return cells


def octile(a: Cell, b: Cell, cell_size: float) -> float:
    """Octile distance: the exact obstacle-free path length on an 8-connected grid."""
    dx, dy = abs(a[0] - b[0]), abs(a[1] - b[1])
    return cell_size * (max(dx, dy) + (math.sqrt(2.0) - 1.0) * min(dx, dy))


def straight_line(a: Cell, b: Cell, cell_size: float) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1]) * cell_size


def _turn_angle(a: Cell, b: Cell, c: Cell) -> float:
    h1 = math.atan2(b[1] - a[1], b[0] - a[0])
    h2 = math.atan2(c[1] - b[1], c[0] - b[0])
    delta = abs(h2 - h1)
    return min(delta, 2.0 * math.pi - delta)


class EnvironmentManager:
    """Owns graph ``G = (V, E)`` over free cells and caches key-node shortest paths.

    Paths are computed lazily, one shortest-path tree per source, and cached by
    unordered cell pair so that a→b and b→a have exactly equal cost. Any map change
    invalidates the cache.
    """

    def __init__(
        self,
        grid: GridSpec,
        obstacles: Iterable[Obstacle] = (),
        metric: PathMetric = "energy",
    ) -> None:
        self.grid = grid
        self.metric: PathMetric = metric
        self.free: NDArray[np.bool_] = np.ones((grid.height, grid.width), dtype=bool)
        self.multiplier: NDArray[np.float64] = np.ones((grid.height, grid.width))
        self.version = 0
        for region in obstacles:
            self.apply_region(region)
        self._invalidate()

    @classmethod
    def from_scenario(cls, scenario: Scenario) -> EnvironmentManager:
        return cls(scenario.grid, scenario.regions(), scenario.energy.path_metric)

    # -- map changes -------------------------------------------------------------------

    def apply_region(self, region: Obstacle) -> list[Cell]:
        """Apply a region to the map and return the cells it covers."""
        cells = region_cells(region, self.grid)
        for x, y in cells:
            if region.kind == "energy_zone":
                self.multiplier[y, x] = max(self.multiplier[y, x], region.energy_multiplier)
            else:
                self.free[y, x] = False
        self._invalidate()
        return cells

    def block_cells(self, cells: Iterable[Cell]) -> None:
        """Remove cells from the graph while the mission is running."""
        for x, y in cells:
            if self.in_bounds((x, y)):
                self.free[y, x] = False
        self._invalidate()

    def _invalidate(self) -> None:
        self.version += 1
        self._graphs: dict[PathMetric, csr_matrix] = {}
        self._trees: dict[tuple[PathMetric, int], NDArray[np.int32]] = {}
        self._paths: dict[tuple[PathMetric, Cell, Cell], PathInfo | None] = {}
        self._neighbors: dict[Cell, list[tuple[Cell, float, float]]] = {}

    # -- queries -----------------------------------------------------------------------

    def in_bounds(self, cell: Cell) -> bool:
        return 0 <= cell[0] < self.grid.width and 0 <= cell[1] < self.grid.height

    def is_free(self, cell: Cell) -> bool:
        return self.in_bounds(cell) and bool(self.free[cell[1], cell[0]])

    def free_cells(self) -> list[Cell]:
        ys, xs = np.nonzero(self.free)
        return [(int(x), int(y)) for x, y in zip(xs, ys, strict=True)]

    def min_multiplier(self) -> float:
        return float(np.min(self.multiplier[self.free])) if self.free.any() else 1.0

    def edge(self, a: Cell, b: Cell) -> tuple[float, float]:
        """Return ``(distance, effort)`` of the edge between two adjacent cells."""
        straight = a[0] == b[0] or a[1] == b[1]
        d = self.grid.cell_size * (1.0 if straight else math.sqrt(2.0))
        m_a = float(self.multiplier[a[1], a[0]])
        m_b = float(self.multiplier[b[1], b[0]])
        return d, edge_effort(d, m_a, m_b)

    def neighbors(self, cell: Cell) -> list[tuple[Cell, float, float]]:
        """8-connected free neighbours as ``(cell, distance, effort)``.

        A diagonal step is allowed only if both orthogonally adjacent cells are free,
        so a path can never cut an obstacle corner.
        """
        cached = self._neighbors.get(cell)
        if cached is not None:
            return cached
        x, y = cell
        result: list[tuple[Cell, float, float]] = []
        if self.is_free(cell):
            for dx, dy in _DIRECTIONS:
                nxt = (x + dx, y + dy)
                if not self.is_free(nxt):
                    continue
                if dx and dy and not (self.is_free((x + dx, y)) and self.is_free((x, y + dy))):
                    continue
                d, effort = self.edge(cell, nxt)
                result.append((nxt, d, effort))
        self._neighbors[cell] = result
        return result

    def _graph(self, metric: PathMetric) -> csr_matrix:
        if metric in self._graphs:
            return self._graphs[metric]
        w, h, s = self.grid.width, self.grid.height, self.grid.cell_size
        ys, xs = np.mgrid[0:h, 0:w]
        rows: list[NDArray[np.int64]] = []
        cols: list[NDArray[np.int64]] = []
        data: list[NDArray[np.float64]] = []
        for dx, dy in _HALF_DIRECTIONS:
            x2, y2 = xs + dx, ys + dy
            valid = (x2 >= 0) & (x2 < w) & (y2 >= 0) & (y2 < h)
            ax, ay, bx, by = xs[valid], ys[valid], x2[valid], y2[valid]
            mask = self.free[ay, ax] & self.free[by, bx]
            if dx and dy:
                mask &= self.free[ay, bx] & self.free[by, ax]
            ax, ay, bx, by = ax[mask], ay[mask], bx[mask], by[mask]
            dist = np.full(ax.shape, s * (math.sqrt(2.0) if dx and dy else 1.0), dtype=np.float64)
            weight = (
                edge_effort(dist, self.multiplier[ay, ax], self.multiplier[by, bx])
                if metric == "energy"
                else dist
            )
            a_idx, b_idx = ay * w + ax, by * w + bx
            rows += [a_idx, b_idx]
            cols += [b_idx, a_idx]
            data += [weight, weight]
        n = w * h
        graph = csr_matrix(
            (np.concatenate(data), (np.concatenate(rows), np.concatenate(cols))), shape=(n, n)
        )
        self._graphs[metric] = graph
        return graph

    def path(self, a: Cell, b: Cell, metric: PathMetric | None = None) -> PathInfo | None:
        """Cached shortest path under ``metric``; ``None`` if no path exists."""
        metric = metric or self.metric
        forward = a <= b
        key_a, key_b = (a, b) if forward else (b, a)
        key = (metric, key_a, key_b)
        if key not in self._paths:
            self._paths[key] = self._compute_path(key_a, key_b, metric)
        info = self._paths[key]
        if info is None or forward:
            return info
        return PathInfo(
            cells=info.cells[::-1],
            distance=info.distance,
            effort=info.effort,
            turn_radians=info.turn_radians,
            edge_distances=info.edge_distances[::-1],
            edge_efforts=info.edge_efforts[::-1],
        )

    def _compute_path(self, a: Cell, b: Cell, metric: PathMetric) -> PathInfo | None:
        if not (self.is_free(a) and self.is_free(b)):
            return None
        if a == b:
            return PathInfo((a,), 0.0, 0.0, 0.0, (), ())
        w = self.grid.width
        src, dst = a[1] * w + a[0], b[1] * w + b[0]
        tree_key = (metric, src)
        if tree_key not in self._trees:
            _, pred = sp_dijkstra(
                self._graph(metric), directed=True, indices=src, return_predecessors=True
            )
            self._trees[tree_key] = pred
        pred = self._trees[tree_key]
        if pred[dst] < 0:
            return None
        rev: list[Cell] = [b]
        node = dst
        while node != src:
            node = int(pred[node])
            rev.append((node % w, node // w))
        return self.path_from_cells(tuple(rev[::-1]))

    def path_from_cells(self, cells: tuple[Cell, ...]) -> PathInfo:
        """Measure an explicit cell sequence."""
        distances: list[float] = []
        efforts: list[float] = []
        for u, v in pairwise(cells):
            d, effort = self.edge(u, v)
            distances.append(d)
            efforts.append(effort)
        turn = sum(
            _turn_angle(cells[i - 1], cells[i], cells[i + 1]) for i in range(1, len(cells) - 1)
        )
        return PathInfo(cells, sum(distances), sum(efforts), turn, tuple(distances), tuple(efforts))

    def escape_cell(self, cell: Cell) -> Cell | None:
        """Nearest free cell to ``cell`` (for a UAV caught inside a newly blocked region)."""
        if self.is_free(cell):
            return cell
        if not self.free.any():
            return None
        ys, xs = np.nonzero(self.free)
        dist = (xs - cell[0]) ** 2 + (ys - cell[1]) ** 2
        order = np.lexsort((xs, ys, dist))
        i = int(order[0])
        return (int(xs[i]), int(ys[i]))

    def component_of(self, cell: Cell) -> set[Cell]:
        """Free cells connected to ``cell``."""
        if not self.is_free(cell):
            return set()
        seen = {cell}
        stack = [cell]
        while stack:
            for nxt, _, _ in self.neighbors(stack.pop()):
                if nxt not in seen:
                    seen.add(nxt)
                    stack.append(nxt)
        return seen

    def to_networkx(self) -> nx.Graph[Cell]:
        """Export ``G`` with ``distance`` and ``effort`` edge attributes."""
        graph: nx.Graph[Cell] = nx.Graph()
        for cell in self.free_cells():
            graph.add_node(cell)
            for nxt, d, effort in self.neighbors(cell):
                graph.add_edge(cell, nxt, distance=d, effort=effort)
        return graph
