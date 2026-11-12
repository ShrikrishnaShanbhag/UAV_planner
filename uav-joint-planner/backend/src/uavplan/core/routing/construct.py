"""Route construction heuristics over an abstract cell distance."""

from __future__ import annotations

from collections.abc import Callable, Sequence

from uavplan.core.models import Cell

Distance = Callable[[Cell, Cell], float]
Stop = tuple[str, Cell]


def nearest_neighbour(start: Cell, stops: Sequence[Stop], dist: Distance) -> tuple[str, ...]:
    """Visit the closest remaining stop next. Ties break by stop id."""
    remaining = sorted(stops)
    order: list[str] = []
    position = start
    while remaining:
        best = min(remaining, key=lambda s: (dist(position, s[1]), s[0]))
        remaining.remove(best)
        order.append(best[0])
        position = best[1]
    return tuple(order)


def cheapest_insertion(
    start: Cell, end: Cell, stops: Sequence[Stop], dist: Distance
) -> tuple[str, ...]:
    """Repeatedly insert the stop and position that add the least distance."""
    remaining = sorted(stops)
    route: list[Stop] = []
    while remaining:
        best: tuple[float, str, int] | None = None
        chosen: Stop | None = None
        for stop in remaining:
            for position in range(len(route) + 1):
                prev = route[position - 1][1] if position > 0 else start
                nxt = route[position][1] if position < len(route) else end
                added = dist(prev, stop[1]) + dist(stop[1], nxt) - dist(prev, nxt)
                candidate = (added, stop[0], position)
                if best is None or candidate < best:
                    best, chosen = candidate, stop
        assert best is not None and chosen is not None
        route.insert(best[2], chosen)
        remaining.remove(chosen)
    return tuple(s[0] for s in route)
