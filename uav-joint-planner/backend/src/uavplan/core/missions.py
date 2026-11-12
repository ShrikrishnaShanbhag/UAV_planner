"""Twelve real-world mission presets, each seeded and available in two sizes.

Place types are real (river, hospital, power line); no place or organisation is. Every number
is an illustrative simulation setting, not a measurement.

Map coordinates are cells with ``y`` increasing up the map.
"""

from __future__ import annotations

import itertools
import math
import zlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from typing import Literal, get_args

import numpy as np

from uavplan.core.environment import EnvironmentManager, point_in_polygon, polygon_cells
from uavplan.core.models import (
    UAV,
    Cell,
    Difficulty,
    Event,
    EventType,
    GridSpec,
    Obstacle,
    Point,
    Priority,
    Scenario,
    ScenarioMeta,
    Station,
    Task,
    TerrainLayer,
    TerrainType,
    Wind,
)

Size = Literal["small", "standard"]
Rect = tuple[int, int, int, int]
Polygon = tuple[Point, ...]
Area = Rect | Polygon

SIZES: tuple[str, ...] = get_args(Size)
DEFAULT_SEED = 42
ILLUSTRATIVE = "Values are illustrative simulation settings."
# Inspections and hand-overs take minutes, so scripted events land while UAVs are still flying.
TASK_TIME_SCALE = 3.0
# A small mission is over sooner, so its scripted events come sooner too.
SMALL_EVENT_TIME_SCALE = 0.6


@dataclass(frozen=True, slots=True)
class _Job:
    """A kind of task. Energy and duration vary a little with the seed."""

    label: str
    capability: str | None
    energy: float
    duration: float
    priority: Priority = Priority.MEDIUM
    mandatory: bool = False
    deadline: float | None = None
    release: float = 0.0


def _ring(cx: float, cy: float, rx: float, ry: float, points: int = 12) -> Polygon:
    """A closed oval as a polygon; used for fires, slicks and hazards."""
    return tuple(
        (
            round(cx + rx * math.cos(2.0 * math.pi * i / points), 2),
            round(cy + ry * math.sin(2.0 * math.pi * i / points), 2),
        )
        for i in range(points)
    )


def _shift(polygon: Polygon, dx: float, dy: float) -> Polygon:
    return tuple((x + dx, y + dy) for x, y in polygon)


class _Mission:
    """Collects a mission's map, then places its tasks on cells that stay reachable."""

    def __init__(
        self,
        meta: ScenarioMeta,
        size: Size,
        seed: int,
        width: int = 50,
        height: int = 50,
        cell_size: float = 20.0,
        pack: float = 100.0,
    ) -> None:
        self.meta = meta
        self.pack = pack
        self.size = size
        self.seed = seed
        self.grid = GridSpec(width, height, cell_size)
        self.rng = np.random.default_rng([zlib.crc32(meta.id.encode()), seed])
        self.layers: list[TerrainLayer] = []
        self.obstacles: list[Obstacle] = []
        self.events: list[Event] = []
        self.stations: list[Station] = []
        self.uavs: list[UAV] = []
        self.tasks: list[Task] = []
        self.wind: Wind | None = None
        self.used: set[Cell] = set()
        self.allowed: list[Cell] = []
        self._allowed_set: set[Cell] = set()
        self._news = 0

    def count(self, standard: int) -> int:
        return standard if self.size == "standard" else max(2, math.ceil(standard / 2))

    # -- the map -----------------------------------------------------------------------

    def layer(
        self,
        layer_id: str,
        layer_type: TerrainType,
        area: Area,
        *,
        blocked: bool = False,
        multiplier: float | None = None,
        label: str = "",
    ) -> TerrainLayer:
        rect, polygon = self._geometry(area)
        layer = TerrainLayer(layer_id, layer_type, rect, polygon, None, blocked, multiplier, label)
        self.layers.append(layer)
        return layer

    def no_fly(self, region_id: str, area: Area, label: str) -> None:
        rect, polygon = self._geometry(area)
        self.obstacles.append(Obstacle(region_id, "no_fly", rect, polygon, label=label))

    @staticmethod
    def _geometry(area: Area) -> tuple[Rect | None, Polygon | None]:
        if len(area) == 4 and isinstance(area[0], int):
            x0, y0, x1, y1 = area
            return (x0, y0, x1, y1), None
        return None, area

    def spread(self, time: float, layer_id: str, polygon: Polygon, label: str) -> None:
        """A blocked layer grows to ``polygon`` at ``time``."""
        old = next(layer for layer in self.layers if layer.id == layer_id)
        grown = TerrainLayer(layer_id, old.type, polygon=polygon, blocked=True, label=old.label)
        self.events.append(
            Event(EventType.BLOCK_REGION, time, polygon=polygon, layer=grown, label=label)
        )

    def freeze(self) -> None:
        """Fix the map. Tasks may only use cells that stay connected after every block."""
        env = EnvironmentManager(self.grid, self._regions())
        for event in self.events:
            if event.polygon is not None:
                env.block_cells(polygon_cells(event.polygon, self.grid.width, self.grid.height))
            if event.cells is not None:
                env.block_cells(event.cells)
        unseen = set(env.free_cells())
        largest: set[Cell] = set()
        while unseen:
            component = env.component_of(min(unseen))
            unseen -= component
            if len(component) > len(largest):
                largest = component
        self._allowed_set = largest
        self.allowed = sorted(largest)

    def _regions(self) -> list[Obstacle]:
        layers = (layer.as_region() for layer in self.layers)
        return [*self.obstacles, *(region for region in layers if region is not None)]

    # -- fleet -------------------------------------------------------------------------

    def station(self, station_id: str, cell: Cell) -> None:
        cell = self._nearest(cell)
        self.used.add(cell)
        self.stations.append(Station(station_id, cell))

    def uav(
        self,
        uav_id: str,
        home: str,
        capabilities: Sequence[str],
        speed: float,
        energy_per_meter: float,
        hover_rate: float = 0.0,
    ) -> None:
        station = next(s for s in self.stations if s.id == home)
        self.uavs.append(
            UAV(
                id=uav_id,
                start=station.cell,
                battery=self.pack,
                battery_max=self.pack,
                home=home,
                capabilities=frozenset(capabilities),
                speed=speed,
                energy_per_meter=energy_per_meter,
                hover_rate=hover_rate,
            )
        )

    # -- tasks -------------------------------------------------------------------------

    def _nearest(self, cell: Cell) -> Cell:
        return min(
            (c for c in self.allowed if c not in self.used),
            key=lambda c: ((c[0] - cell[0]) ** 2 + (c[1] - cell[1]) ** 2, c),
        )

    def _area_cells(self, area: Area) -> list[Cell]:
        rect, polygon = self._geometry(area)
        if rect is not None:
            x0, y0, x1, y1 = rect
            return [c for c in self.allowed if x0 <= c[0] <= x1 and y0 <= c[1] <= y1]
        assert polygon is not None
        return [c for c in self.allowed if point_in_polygon(float(c[0]), float(c[1]), polygon)]

    def _make(self, task_id: str, cell: Cell, job: _Job, number: int | None) -> Task:
        energy = round(job.energy * float(self.rng.uniform(0.9, 1.1)), 3)
        duration = round(job.duration * TASK_TIME_SCALE * float(self.rng.uniform(0.9, 1.1)), 2)
        deadline = None
        if job.deadline is not None:
            deadline = round(job.deadline * float(self.rng.uniform(0.95, 1.05)), 2)
        return Task(
            id=task_id,
            cell=cell,
            priority=job.priority,
            capability=job.capability,
            energy=energy,
            duration=duration,
            deadline=deadline,
            mandatory=job.mandatory,
            release_time=job.release,
            label=job.label if number is None else f"{job.label} {number}",
        )

    def _add(self, cell: Cell, job: _Job, number: int | None) -> Task:
        task = self._make(f"T{len(self.tasks) + 1}", cell, job, number)
        self.tasks.append(task)
        return task

    def _spread_cells(self, area: Area, count: int) -> list[Cell]:
        """Evenly spread picks: the area is cut into ``count`` strips along x, one per strip."""
        pool = sorted(c for c in self._area_cells(area) if c not in self.used)
        if len(pool) < count:
            raise ValueError(f"{self.meta.id}: area too small for {count} tasks")
        cells: list[Cell] = []
        for i in range(count):
            strip = pool[i * len(pool) // count : (i + 1) * len(pool) // count]
            cell = strip[int(self.rng.integers(0, len(strip)))]
            cells.append(cell)
            self.used.add(cell)
        return cells

    def scatter(self, area: Area, count: int, job: _Job) -> list[Task]:
        return [
            self._add(cell, job, i + 1) for i, cell in enumerate(self._spread_cells(area, count))
        ]

    def _line_cell(self, points: Sequence[Point], fraction: float) -> Cell:
        lengths = [math.dist(a, b) for a, b in itertools.pairwise(points)]
        distance = fraction * sum(lengths)
        for (a, b), length in zip(itertools.pairwise(points), lengths, strict=True):
            if distance <= length or (a, b) == (points[-2], points[-1]):
                share = min(distance / length, 1.0) if length else 0.0
                x = a[0] + (b[0] - a[0]) * share + float(self.rng.uniform(-0.8, 0.8))
                y = a[1] + (b[1] - a[1]) * share + float(self.rng.uniform(-0.8, 0.8))
                return self._nearest((round(x), round(y)))
            distance -= length
        raise AssertionError("unreachable")  # pragma: no cover

    def along(self, points: Sequence[Point], count: int, job: _Job) -> list[Task]:
        """Tasks spaced evenly along a line."""
        tasks: list[Task] = []
        for i in range(count):
            cell = self._line_cell(points, (i + 0.5) / count)
            self.used.add(cell)
            tasks.append(self._add(cell, job, i + 1))
        return tasks

    def around(self, polygon: Polygon, count: int, job: _Job) -> list[Task]:
        """Tasks spaced evenly around a closed outline."""
        tasks: list[Task] = []
        closed = (*polygon, polygon[0])
        for i in range(count):
            cell = self._line_cell(closed, i / count)
            self.used.add(cell)
            tasks.append(self._add(cell, job, i + 1))
        return tasks

    def repeat(self, cell: Cell, job: _Job, releases: Sequence[float]) -> None:
        """The same place checked again at each release time."""
        target = self._nearest(cell)
        self.used.add(target)
        for round_number, release in enumerate(releases, start=1):
            self._add(target, replace(job, release=release), round_number)

    # -- scripted events ---------------------------------------------------------------

    def new_task(self, time: float, cell: Cell, job: _Job, label: str) -> Task:
        self._news += 1
        target = self._nearest(cell)
        self.used.add(target)
        task = self._make(f"N{self._news}", target, replace(job, release=time), None)
        self.events.append(Event(EventType.NEW_TASK, time, task=task, label=label))
        return task

    def cancel(self, time: float, task_id: str, label: str) -> None:
        self.events.append(Event(EventType.TASK_CANCELLED, time, task_id=task_id, label=label))

    def wind_change(self, time: float, wind: Wind, label: str) -> None:
        self.events.append(Event(EventType.WIND_CHANGE, time, wind=wind, label=label))

    def move_layer(self, time: float, layer_id: str, polygon: Polygon, label: str) -> None:
        old = next(layer for layer in self.layers if layer.id == layer_id)
        moved = replace(old, rect=None, polygon=polygon)
        self.events.append(Event(EventType.TERRAIN_CHANGE, time, layer=moved, label=label))

    def _timed_events(self) -> tuple[Event, ...]:
        if self.size == "standard":
            return tuple(sorted(self.events, key=lambda e: e.time))
        scaled: list[Event] = []
        for event in self.events:
            time = round(event.time * SMALL_EVENT_TIME_SCALE, 2)
            task = None if event.task is None else replace(event.task, release_time=time)
            scaled.append(replace(event, time=time, task=task))
        return tuple(sorted(scaled, key=lambda e: e.time))

    def build(self) -> Scenario:
        suffix = "" if self.size == "standard" else "-small"
        return Scenario(
            id=f"{self.meta.id}{suffix}",
            name=self.meta.name,
            grid=self.grid,
            uavs=tuple(self.uavs),
            tasks=tuple(self.tasks),
            stations=tuple(self.stations),
            obstacles=tuple(self.obstacles),
            events=self._timed_events(),
            seed=self.seed,
            description=f"{self.meta.description} {ILLUSTRATIVE}",
            meta=self.meta,
            terrain_layers=tuple(self.layers),
            wind=self.wind,
        )


Builder = Callable[[Size, int], Scenario]
_BUILDERS: dict[str, Builder] = {}
_META: dict[str, ScenarioMeta] = {}


def _mission(
    mission_id: str,
    name: str,
    category: str,
    description: str,
    story: str,
    icon: str,
    theme: str,
    difficulty: Difficulty,
    tags: tuple[str, ...],
    energy_focus: str,
    allow_recharge: bool = False,
) -> Callable[[Builder], Builder]:
    meta = ScenarioMeta(
        mission_id,
        name,
        category,
        description,
        story,
        icon,
        theme,
        difficulty,
        tags,
        energy_focus,
        allow_recharge,
    )

    def register(builder: Builder) -> Builder:
        _BUILDERS[mission_id] = builder
        _META[mission_id] = meta
        return builder

    return register


H, C, M, L = Priority.HIGH, Priority.CRITICAL, Priority.MEDIUM, Priority.LOW


@_mission(
    "earthquake",
    "Earthquake response",
    "Disaster",
    "City district after a quake, with collapsed blocks, a lost bridge and a damaged tower.",
    "A strong earthquake has hit a dense city district. Teams need survivors located and "
    "buildings assessed before the next aftershock, and collapsed blocks leave few clear "
    "flight lines.",
    "activity",
    "urban-disaster",
    "medium",
    ("urban", "survivors", "aftershock"),
    "Prefix safety in dense obstacles: every point of a route must still reach a station.",
)
def _earthquake(size: Size, seed: int) -> Scenario:
    from uavplan.core.generator import demo_scenario

    demo = demo_scenario()
    m = _Mission(_META["earthquake"], size, seed)
    m.layer("district", "urban", (6, 2, 46, 47), label="City district")
    m.layer("road", "road", (0, 41, 49, 43), label="Ring road")
    by_id = {o.id: o for o in demo.obstacles}
    for region in ("b1", "b2", "b3", "b4", "b5"):
        rect = by_id[region].rect
        assert rect is not None
        m.layer(f"rubble-{region}", "rubble", rect, blocked=True, label=by_id[region].label)
    river = by_id["river"]
    assert river.polygon is not None
    m.layer("river", "water", river.polygon, multiplier=1.8, label="River corridor (gusts)")
    m.layer("bridge", "rubble", (27, 24, 30, 29), blocked=True, label="Collapsed bridge")
    tower = by_id["nfz"]
    assert tower.polygon is not None
    m.no_fly("tower", tower.polygon, "No-fly zone over a damaged tower")
    m.layer("aftershock", "rubble", (8, 41, 8, 41), blocked=True, label="Road blocked")
    m.spread(
        300.0,
        "aftershock",
        ((8.0, 41.0), (20.0, 41.0), (20.0, 43.0), (8.0, 43.0)),
        "Aftershock blocks the ring road",
    )
    m.freeze()
    m.station("Base-W", (3, 46))
    m.station("Base-E", (46, 3))
    for uav in demo.uavs:
        m.uavs.append(uav)
    keep = ("T1", "T2", "T3", "T5", "T6", "T7", "T9", "T4")
    for task in demo.tasks:
        if size == "small" and task.id not in keep:
            continue
        cell = task.cell
        if seed != DEFAULT_SEED:
            offset = m.rng.integers(-2, 3, size=2)
            cell = (cell[0] + int(offset[0]), cell[1] + int(offset[1]))
        target = m._nearest(cell)
        m.used.add(target)
        deadline = None if task.deadline is None else task.deadline * 1.5
        duration = task.duration * TASK_TIME_SCALE
        m.tasks.append(replace(task, cell=target, duration=duration, deadline=deadline))
    m.new_task(320.0, (33, 45), _Job("Survivor signal", "thermal", 8.0, 30.0, C, True), "Signal")
    return m.build()


@_mission(
    "flood",
    "Flood relief",
    "Disaster",
    "An overflowing river splits a town from its relief camp.",
    "The river has overflowed and cut the town off. UAVs must find people stranded on "
    "rooftops and carry medical kits across open water from the relief camp.",
    "waves",
    "flood",
    "medium",
    ("water", "delivery", "rooftops"),
    "Long legs over water and heavier payload tasks, with a surge that closes airspace.",
)
def _flood(size: Size, seed: int) -> Scenario:
    m = _Mission(_META["flood"], size, seed)
    water: Polygon = (
        (0.0, 20.0),
        (15.0, 17.0),
        (32.0, 19.0),
        (49.0, 16.0),
        (49.0, 30.0),
        (32.0, 33.0),
        (15.0, 30.0),
        (0.0, 32.0),
    )
    m.layer("field", "field", (0, 0, 49, 15), label="Farmland")
    m.layer("town", "urban", (26, 34, 46, 46), label="Town")
    m.layer("river", "water", water, multiplier=1.25, label="Flooded river")
    m.layer("flooded", "water", (26, 34, 36, 39), label="Flooded streets")
    m.layer("road", "road", (8, 0, 10, 49), label="Raised road")
    start: Polygon = ((16.0, 31.0), (24.0, 31.0), (24.0, 35.0), (16.0, 35.0))
    grown: Polygon = ((13.0, 31.0), (25.0, 31.0), (25.0, 41.0), (13.0, 41.0))
    m.layer("surge", "water", start, blocked=True, label="Surge zone (closed to UAVs)")
    m.spread(400.0, "surge", grown, "River rises and the surge zone expands")
    m.freeze()
    m.station("Relief-camp", (6, 8))
    m.station("Road-pad", (44, 10))
    m.uav("Scout-1", "Relief-camp", ("camera",), 13.0, 0.014)
    m.uav("Scout-2", "Road-pad", ("camera",), 13.0, 0.014)
    m.uav("Carrier-1", "Relief-camp", ("camera", "payload"), 9.0, 0.020)
    m.uav("Carrier-2", "Road-pad", ("camera", "payload"), 9.0, 0.020)
    m.uav("Carrier-3", "Relief-camp", ("camera", "payload"), 9.0, 0.020)
    m.scatter((27, 35, 46, 46), m.count(12), _Job("Rooftop scan", "camera", 5.0, 20.0, H))
    m.scatter((3, 36, 12, 47), m.count(3), _Job("Medical-kit drop", "payload", 13.0, 25.0, C, True))
    m.scatter((26, 42, 46, 48), m.count(3), _Job("Medical-kit drop", "payload", 13.0, 25.0, H))
    m.new_task(
        430.0, (40, 40), _Job("Rescue boat request", "camera", 6.0, 25.0, C, True), "Boat request"
    )
    return m.build()


@_mission(
    "wildfire",
    "Wildfire monitoring",
    "Disaster",
    "A forest fire with a moving front, heavy smoke and a shifting wind.",
    "A wildfire is spreading through forested hills. Crews need the fire front mapped "
    "and nearby villages checked, while smoke and a changing wind eat into every sortie.",
    "flame",
    "wildfire",
    "hard",
    ("fire", "wind", "smoke"),
    "Wind and smoke raise energy per leg while a growing no-fly area lengthens returns.",
)
def _wildfire(size: Size, seed: int) -> Scenario:
    m = _Mission(_META["wildfire"], size, seed, pack=55.0)
    m.wind = Wind(4.0, 45.0)
    fire = _ring(25.0, 25.0, 5.0, 4.0)
    m.layer("forest", "forest", (0, 0, 49, 49), label="Forest")
    ridge: Polygon = ((34.0, 34.0), (49.0, 30.0), (49.0, 49.0), (30.0, 49.0))
    m.layer("ridge", "hill", ridge, multiplier=1.15, label="Ridge")
    smoke = _ring(32.0, 32.0, 11.0, 9.0)
    m.layer("smoke", "smoke", smoke, multiplier=1.2, label="Smoke")
    m.layer("village-a", "urban", (4, 36, 11, 44), label="Village")
    m.layer("village-b", "urban", (38, 4, 46, 11), label="Village")
    m.layer("fire", "fire", fire, blocked=True, label="Fire")
    radii = ((7.0, 6.0), (9.0, 7.5), (11.0, 9.0))
    times = (200.0, 400.0, 600.0)
    for time, (rx, ry) in zip(times, radii, strict=True):
        m.spread(time, "fire", _ring(25.0, 25.0, rx, ry), "Fire front advances")
    m.wind_change(300.0, Wind(5.0, 135.0), "Wind shifts south-east")
    m.freeze()
    m.station("Base-S", (4, 4))
    m.station("Base-E", (45, 18))
    m.uav("Heat-1", "Base-S", ("thermal", "camera"), 13.0, 0.014)
    m.uav("Heat-2", "Base-E", ("thermal", "camera"), 13.0, 0.014)
    m.uav("Heat-3", "Base-S", ("thermal", "camera"), 12.0, 0.015)
    m.uav("View-4", "Base-E", ("camera",), 14.0, 0.013)
    front = _ring(25.0, 25.0, 13.0, 11.0)
    m.around(front, m.count(10), _Job("Fire-front point", "thermal", 6.0, 24.0, H, True))
    m.scatter((3, 35, 12, 45), m.count(3), _Job("Village check", "camera", 4.0, 18.0, M))
    m.scatter((37, 3, 47, 12), m.count(3), _Job("Village check", "camera", 4.0, 18.0, M))
    return m.build()


@_mission(
    "cyclone",
    "Coastal cyclone aftermath",
    "Disaster",
    "A battered coastline, strong onshore wind and a damaged power grid.",
    "A cyclone has just passed. Teams need shelters, embankments and the power grid "
    "assessed, while a strong onshore wind keeps blowing and ground stations start to fail.",
    "wind",
    "coastal",
    "hard",
    ("coast", "wind", "outage"),
    "Strong headwind and tailwind asymmetry, and losing a station mid-mission.",
)
def _cyclone(size: Size, seed: int) -> Scenario:
    m = _Mission(_META["cyclone"], size, seed, pack=60.0)
    m.wind = Wind(6.0, 270.0)
    m.layer("land", "field", (0, 0, 33, 49), label="Coastal plain")
    m.layer("sea", "water", (37, 0, 49, 49), label="Sea")
    m.layer("coast", "coast", (33, 0, 36, 49), label="Coast")
    m.layer("village-n", "urban", (10, 36, 24, 46), label="Damaged village")
    m.layer("village-s", "urban", (12, 4, 26, 14), label="Damaged village")
    m.freeze()
    m.station("Inland-N", (3, 42))
    m.station("Inland-S", (3, 8))
    m.station("Coast-pad", (29, 25))
    m.uav("Unit-1", "Inland-N", ("camera", "thermal"), 13.0, 0.014)
    m.uav("Unit-2", "Inland-S", ("camera", "thermal"), 13.0, 0.014)
    m.uav("Unit-3", "Coast-pad", ("camera", "lidar"), 11.0, 0.016)
    m.uav("Unit-4", "Inland-N", ("camera", "lidar"), 11.0, 0.016)
    m.uav("Unit-5", "Inland-S", ("camera",), 14.0, 0.013)
    m.scatter((10, 36, 24, 46), m.count(4), _Job("Shelter check", "thermal", 6.0, 24.0, C, True))
    m.scatter((12, 4, 26, 14), m.count(4), _Job("Shelter check", "thermal", 6.0, 24.0, H))
    m.along(
        ((34.0, 3.0), (34.0, 47.0)),
        m.count(8),
        _Job("Embankment inspection", "lidar", 7.0, 28.0, M),
    )
    m.along(
        ((6.0, 25.0), (28.0, 28.0)),
        m.count(6),
        _Job("Power-pole check", "camera", 3.5, 15.0, M),
    )
    m.events.append(
        Event(
            EventType.STATION_OUTAGE, 250.0, station_id="Coast-pad", label="Coast pad loses power"
        )
    )
    m.events.append(
        Event(EventType.BATTERY_DROP, 350.0, uav_id="Unit-4", amount=10.0, label="Water damage")
    )
    return m.build()


@_mission(
    "powerline",
    "Power-line inspection after a storm",
    "Infrastructure",
    "A long transmission corridor across fields and hills with few stations.",
    "A storm has passed along a long transmission line. Every tower needs checking "
    "for faults, the corridor is far longer than one battery allows, and stations are few.",
    "zap",
    "corridor",
    "hard",
    ("corridor", "recharge", "inspection"),
    "Long distances mean recharge stops are needed to finish the line.",
    allow_recharge=True,
)
def _powerline(size: Size, seed: int) -> Scenario:
    m = _Mission(_META["powerline"], size, seed, 90, 24, 30.0)
    m.layer("field", "field", (0, 0, 89, 23), label="Fields")
    hills: Polygon = ((30.0, 0.0), (56.0, 0.0), (60.0, 23.0), (26.0, 23.0))
    m.layer("hills", "hill", hills, multiplier=1.15, label="Hills")
    m.layer("road", "road", (0, 3, 89, 4), label="Service road")
    m.freeze()
    m.station("West", (3, 5))
    m.station("Mid", (45, 3))
    m.station("East", (87, 5))
    m.uav("Line-1", "West", ("camera", "lidar"), 12.0, 0.016)
    m.uav("Line-2", "Mid", ("camera", "lidar"), 12.0, 0.016)
    m.uav("Line-3", "East", ("camera",), 13.0, 0.015)
    line: tuple[Point, ...] = ((4.0, 8.0), (30.0, 13.0), (58.0, 11.0), (86.0, 16.0))
    m.along(line, m.count(30), _Job("Tower inspection", "camera", 6.5, 24.0, M))
    for offset, cell in enumerate(((22, 12), (47, 13), (70, 14))):
        m.new_task(
            240.0 + offset * 10.0,
            cell,
            _Job("Fault report tower", "lidar", 8.0, 28.0, H, True),
            "Fault reported",
        )
    return m.build()


@_mission(
    "mountain_sar",
    "Mountain search and rescue",
    "Safety",
    "Missing trekkers in steep forested hills, working from a single base camp.",
    "A group of trekkers has been reported missing in steep, forested hills. Search "
    "sectors fan out from one base camp, and a phone signal may change everything.",
    "mountain",
    "mountain",
    "hard",
    ("terrain", "search", "single-station"),
    "Terrain energy from one station, keeping a reserve under uncertainty.",
)
def _mountain(size: Size, seed: int) -> Scenario:
    m = _Mission(_META["mountain_sar"], size, seed, 50, 50, 25.0, pack=75.0)
    m.layer("forest", "forest", (0, 0, 49, 49), label="Forest")
    west: Polygon = ((0.0, 14.0), (22.0, 18.0), (26.0, 49.0), (0.0, 49.0))
    east: Polygon = ((30.0, 49.0), (32.0, 18.0), (49.0, 12.0), (49.0, 49.0))
    m.layer("hills-w", "hill", west, multiplier=1.15, label="Hills")
    m.layer("hills-e", "hill", east, multiplier=1.15, label="Hills")
    valley: Polygon = (
        (26.0, 49.0),
        (30.0, 49.0),
        (32.0, 18.0),
        (28.0, 0.0),
        (24.0, 0.0),
        (22.0, 18.0),
    )
    m.layer("river", "water", valley, label="River valley")
    m.layer("peak-a", "hill", _ring(13.0, 34.0, 4.0, 3.0), blocked=True, label="Crag")
    m.layer("peak-b", "hill", _ring(40.0, 32.0, 3.0, 4.0), blocked=True, label="Crag")
    m.layer("peak-c", "hill", _ring(38.0, 16.0, 3.0, 2.0), blocked=True, label="Crag")
    m.freeze()
    m.station("Base-camp", (26, 5))
    m.uav("Seeker-1", "Base-camp", ("thermal", "camera"), 13.0, 0.014)
    m.uav("Seeker-2", "Base-camp", ("thermal", "camera"), 13.0, 0.014)
    m.uav("Seeker-3", "Base-camp", ("thermal", "camera"), 12.0, 0.015)
    m.uav("Spotter-4", "Base-camp", ("camera",), 14.0, 0.013)
    m.scatter((3, 18, 46, 46), m.count(12), _Job("Sector sweep", "camera", 5.0, 22.0, M))
    m.scatter(
        (14, 22, 36, 44), m.count(3), _Job("Last-known location", "thermal", 8.0, 30.0, C, True)
    )
    m.new_task(260.0, (10, 42), _Job("Phone signal ping", "thermal", 8.0, 30.0, C, True), "Ping")
    m.events.append(
        Event(EventType.UAV_UNAVAILABLE, 350.0, uav_id="Seeker-3", label="Lost to a downdraft")
    )
    return m.build()


@_mission(
    "medical",
    "Medical supply delivery",
    "Delivery",
    "A hospital hub sends urgent blood and vaccines out to rural clinics.",
    "A hospital hub is supplying rural clinics with blood and vaccines. Every delivery "
    "has a deadline, and urgent requests keep arriving while the fleet is in the air.",
    "heart-pulse",
    "town",
    "medium",
    ("delivery", "deadlines", "multi-station"),
    "Deadlines against battery limits, with returns to whichever station is closest.",
)
def _medical(size: Size, seed: int) -> Scenario:
    m = _Mission(_META["medical"], size, seed, 50, 50, 30.0, pack=65.0)
    m.layer("fields", "field", (0, 0, 49, 49), label="Farmland")
    m.layer("town", "urban", (19, 19, 31, 31), label="Town")
    m.layer("road-h", "road", (0, 24, 49, 25), label="Main road")
    m.layer("road-v", "road", (24, 0, 25, 49), label="Main road")
    m.freeze()
    m.station("Hospital-hub", (25, 27))
    m.station("Clinic-N", (6, 44))
    m.station("Clinic-S", (44, 6))
    m.uav("Courier-1", "Hospital-hub", ("payload",), 12.0, 0.018)
    m.uav("Courier-2", "Hospital-hub", ("payload",), 12.0, 0.018)
    m.uav("Courier-3", "Clinic-N", ("payload",), 10.0, 0.020)
    m.uav("Courier-4", "Clinic-S", ("payload",), 10.0, 0.020)
    blood = m.scatter(
        (2, 2, 47, 47), m.count(5), _Job("Deliver blood", "payload", 7.0, 18.0, C, True, 650.0)
    )
    m.scatter(
        (2, 2, 47, 47), m.count(5), _Job("Deliver vaccines", "payload", 6.0, 18.0, H, False, 850.0)
    )
    m.new_task(
        150.0,
        (40, 42),
        _Job("Emergency delivery", "payload", 8.0, 18.0, C, True, 700.0),
        "Emergency",
    )
    m.events.append(
        Event(
            EventType.TASK_DEADLINE_CHANGE,
            200.0,
            task_id=blood[0].id,
            new_deadline=500.0,
            label="Deadline brought forward",
        )
    )
    return m.build()


@_mission(
    "agriculture",
    "Precision agriculture survey",
    "Agriculture",
    "Crop-health surveys and spray patches across farmland and canals.",
    "A farm cooperative wants crop health surveyed and problem patches sprayed. Most "
    "of the work is many small survey cells, and a rain front may cancel part of it.",
    "sprout",
    "farmland",
    "easy",
    ("survey", "many-tasks", "balance"),
    "Many small tasks, and balancing energy across the fleet.",
)
def _agriculture(size: Size, seed: int) -> Scenario:
    m = _Mission(_META["agriculture"], size, seed)
    for row, (y0, y1) in enumerate(((2, 15), (18, 31), (34, 47))):
        for col, (x0, x1) in enumerate(((2, 15), (18, 31), (34, 47))):
            m.layer(f"field-{row}{col}", "field", (x0, y0, x1, y1), label="Field")
    m.layer("canal-h", "water", (0, 16, 49, 17), label="Irrigation canal")
    m.layer("canal-v", "water", (32, 0, 33, 49), label="Irrigation canal")
    m.layer("farm", "urban", (22, 0, 27, 4), label="Farmhouse")
    m.freeze()
    m.station("Farmhouse", (25, 2))
    m.uav("Survey-1", "Farmhouse", ("camera",), 13.0, 0.014)
    m.uav("Survey-2", "Farmhouse", ("camera",), 13.0, 0.014)
    m.uav("Sprayer-3", "Farmhouse", ("camera", "payload"), 9.0, 0.019)
    survey = m.scatter((2, 2, 47, 47), m.count(24), _Job("Survey cell", "camera", 2.2, 10.0, L))
    m.scatter((2, 18, 47, 47), m.count(5), _Job("Spray patch", "payload", 9.0, 30.0, M))
    north = sorted(survey, key=lambda t: (-t.cell[1], t.id))[: m.count(4)]
    for task in north:
        m.cancel(300.0, task.id, "Rain front cancels the survey")
    return m.build()


@_mission(
    "gas_leak",
    "Industrial gas-leak inspection",
    "Infrastructure",
    "A gas alarm at an industrial plant, with a hazard zone that keeps growing.",
    "A gas alarm has gone off at an industrial plant. UAVs must sweep sensors around "
    "the hazard and check the stacks, threading between buildings as the hazard expands.",
    "factory",
    "industrial",
    "medium",
    ("hazard", "no-fly", "sensors"),
    "Tight no-fly geometry and return paths around a growing hazard.",
)
def _gas_leak(size: Size, seed: int) -> Scenario:
    m = _Mission(_META["gas_leak"], size, seed, 50, 50, 15.0, pack=60.0)
    m.layer("yard", "road", (3, 3, 46, 46), label="Plant yard")
    buildings: tuple[Rect, ...] = (
        (8, 8, 15, 13),
        (20, 7, 27, 12),
        (34, 8, 41, 14),
        (7, 34, 14, 40),
        (34, 36, 42, 42),
        (21, 39, 27, 44),
    )
    for i, rect in enumerate(buildings):
        m.layer(f"bld-{i}", "urban", rect, blocked=True, label="Plant building")
    chimneys: tuple[Rect, ...] = ((12, 20, 13, 21), (36, 22, 37, 23), (17, 31, 18, 32))
    for i, rect in enumerate(chimneys):
        m.layer(f"stack-{i}", "rubble", rect, blocked=True, label="Chimney")
    m.layer(
        "hazard", "smoke", _ring(25.0, 25.0, 5.0, 4.0), blocked=True, label="Gas hazard (no-fly)"
    )
    m.spread(300.0, "hazard", _ring(25.0, 25.0, 8.0, 7.0), "Hazard zone expands")
    m.freeze()
    m.station("Gate-W", (2, 25))
    m.station("Gate-E", (47, 25))
    m.uav("Sniffer-1", "Gate-W", ("gas_sensor", "camera"), 11.0, 0.017)
    m.uav("Sniffer-2", "Gate-E", ("gas_sensor", "camera"), 11.0, 0.017)
    m.uav("Viewer-3", "Gate-W", ("camera",), 13.0, 0.015)
    m.around(
        _ring(25.0, 25.0, 11.0, 10.0),
        m.count(10),
        _Job("Sensor sweep", "gas_sensor", 5.0, 22.0, H, True),
    )
    for job_cell in ((14, 22), (38, 24), (19, 33)):
        m.along(
            [(float(job_cell[0]), float(job_cell[1]))] * 2,
            1,
            _Job("Stack check", "camera", 4.0, 16.0, M),
        )
    m.scatter((3, 3, 46, 18), m.count(4), _Job("Perimeter check", "camera", 3.0, 12.0, L))
    m.new_task(380.0, (40, 30), _Job("Sensor alarm", "gas_sensor", 6.0, 24.0, C, True), "New alarm")
    return m.build()


@_mission(
    "oil_spill",
    "Oil spill monitoring",
    "Maritime",
    "A drifting oil slick far offshore, with only a shore base and a platform to land on.",
    "An oil slick is spreading at sea. UAVs must keep its boundary mapped from a shore "
    "base and one offshore platform, while the slick drifts and the wind turns.",
    "droplets",
    "maritime",
    "hard",
    ("sea", "drift", "long-range"),
    "Very long legs and few stations, so return energy dominates.",
)
def _oil_spill(size: Size, seed: int) -> Scenario:
    m = _Mission(_META["oil_spill"], size, seed, 60, 50, 40.0, pack=65.0)
    m.wind = Wind(4.0, 90.0)
    m.layer("sea", "water", (6, 0, 59, 49), multiplier=1.1, label="Open sea")
    m.layer("land", "field", (0, 0, 3, 49), label="Shore")
    m.layer("coast", "coast", (3, 0, 5, 49), label="Coastline")
    centre = (34.0, 24.0)
    slick = _ring(*centre, 9.0, 7.0)
    m.layer("slick", "slick", slick, label="Oil slick")
    m.freeze()
    m.station("Shore", (3, 25))
    m.station("Platform", (50, 42))
    m.uav("Patrol-1", "Shore", ("camera", "thermal"), 14.0, 0.013)
    m.uav("Patrol-2", "Shore", ("camera", "thermal"), 14.0, 0.013)
    m.uav("Patrol-3", "Platform", ("camera",), 15.0, 0.012)
    outline = _ring(*centre, 11.0, 9.0)
    points = m.around(outline, m.count(10), _Job("Slick boundary point", "camera", 4.0, 16.0, H))
    drift = (6, -4)
    moved = _shift(slick, *drift)
    m.move_layer(300.0, "slick", moved, "The slick drifts south-east")
    moved_count = m.count(4)
    for task in points[:moved_count]:
        m.cancel(300.0, task.id, "Point has drifted")
    for task in points[:moved_count]:
        cell = (task.cell[0] + drift[0], task.cell[1] + drift[1])
        m.new_task(300.0, cell, _Job("Drifted boundary point", "camera", 4.0, 16.0, H), "Drifted")
    m.wind_change(450.0, Wind(6.0, 200.0), "Wind backs and strengthens")
    return m.build()


@_mission(
    "crowd",
    "Large-event crowd safety",
    "Safety",
    "A stadium event with repeated gate checks and incidents reported during the mission.",
    "A packed stadium event needs its gates and exits watched. The same places are "
    "checked again and again, and incident reports arrive while the fleet is working.",
    "users",
    "event",
    "easy",
    ("repeat-visits", "hover", "short-range"),
    "Repeated tasks on short legs, where hovering costs more than flying.",
)
def _crowd(size: Size, seed: int) -> Scenario:
    m = _Mission(_META["crowd"], size, seed, 50, 50, 15.0, pack=50.0)
    m.layer("parking-w", "road", (2, 8, 11, 41), label="Parking")
    m.layer("parking-e", "road", (38, 8, 47, 41), label="Parking")
    m.layer("road-s", "road", (0, 3, 49, 5), label="Access road")
    m.layer("road-n", "road", (0, 44, 49, 46), label="Access road")
    m.layer("stadium", "urban", _ring(25.0, 25.0, 13.0, 15.0, 20), label="Stadium")
    m.no_fly("stage", (21, 22, 29, 28), "No-fly zone over the stage")
    m.freeze()
    m.station("Control-S", (3, 3))
    m.station("Control-N", (46, 46))
    for i, home in enumerate(("Control-S", "Control-S", "Control-N", "Control-N"), start=1):
        m.uav(f"Watch-{i}", home, ("camera",), 12.0, 0.015, hover_rate=0.012)
    gates = ((25, 41), (38, 34), (38, 16), (25, 9), (12, 16), (12, 34))
    chosen = gates if size == "standard" else gates[::2]
    releases = (0.0, 200.0, 400.0)
    for gate in chosen:
        m.repeat(gate, _Job("Gate check", "camera", 2.5, 25.0, M), releases)
    m.new_task(150.0, (17, 38), _Job("Incident check", "camera", 3.5, 30.0, C, True), "Incident")
    m.new_task(330.0, (33, 12), _Job("Incident check", "camera", 3.5, 30.0, H, True), "Incident")
    return m.build()


@_mission(
    "landslide",
    "Landslide on a hill road",
    "Disaster",
    "A landslide cuts a hill road, and a second slide closes the corridor completely.",
    "A landslide has cut the only highway through a gorge. UAVs must assess the damage "
    "and look for trapped vehicles, and a second slide forces them out over the river.",
    "mountain-snow",
    "hillroad",
    "medium",
    ("gorge", "re-routing", "blocked-road"),
    "Re-routing and longer returns when the only road corridor is blocked.",
)
def _landslide(size: Size, seed: int) -> Scenario:
    m = _Mission(_META["landslide"], size, seed, 60, 40, 20.0, pack=70.0)
    m.layer("river", "water", (0, 0, 59, 11), multiplier=1.1, label="River below")
    m.layer("cliff-n", "hill", (0, 28, 59, 39), blocked=True, label="Cliff")
    for i, (x0, x1) in enumerate(((0, 5), (11, 29), (35, 51), (57, 59))):
        m.layer(f"cliff-s{i}", "hill", (x0, 12, x1, 17), blocked=True, label="Cliff")
    m.layer("road", "road", (0, 21, 59, 23), label="Hill road")
    m.layer("slide-1", "rubble", (18, 18, 22, 25), blocked=True, label="Landslide debris")
    m.layer("slide-2", "rubble", (40, 18, 44, 19), blocked=True, label="Unstable slope")
    m.spread(
        300.0,
        "slide-2",
        ((40.0, 18.0), (44.0, 18.0), (44.0, 27.0), (40.0, 27.0)),
        "A second slide closes the road",
    )
    m.freeze()
    m.station("West-end", (3, 22))
    m.station("East-end", (56, 22))
    m.uav("Road-1", "West-end", ("camera", "thermal"), 12.0, 0.015)
    m.uav("Road-2", "East-end", ("camera", "thermal"), 12.0, 0.015)
    m.uav("Road-3", "West-end", ("camera", "lidar"), 11.0, 0.017)
    road: tuple[Point, ...] = ((6.0, 22.0), (54.0, 22.0))
    m.along(road, m.count(10), _Job("Road-segment check", "camera", 4.0, 16.0, H))
    m.scatter((8, 18, 52, 27), m.count(4), _Job("Vehicle scan", "thermal", 6.0, 24.0, C, True))
    m.scatter((6, 3, 54, 10), m.count(2), _Job("Riverbank survey", "lidar", 7.0, 26.0, L))
    return m.build()


MISSION_IDS: tuple[str, ...] = tuple(_BUILDERS)


def mission_meta(mission_id: str) -> ScenarioMeta:
    return _META[mission_id]


def mission(mission_id: str, size: Size = "standard", seed: int = DEFAULT_SEED) -> Scenario:
    """Build one mission. The same id, size and seed always give the same scenario."""
    if mission_id not in _BUILDERS:
        raise KeyError(f"unknown mission '{mission_id}'")
    if size not in SIZES:
        raise KeyError(f"unknown size '{size}'")
    return _BUILDERS[mission_id](size, seed)
