"""Domain model: entities, scenarios, routes and plans.

Energy is in normalized energy units (EU) by default and joules in physical mode. All
values are simulated and illustrative; none are measured drone performance.
"""

from __future__ import annotations

import dataclasses
import json
import math
from dataclasses import dataclass, field
from enum import Enum, IntEnum, StrEnum
from typing import Any, Literal

Cell = tuple[int, int]
Point = tuple[float, float]
EnergyMode = Literal["normalized", "physical"]
PathMetric = Literal["energy", "distance"]
RegionKind = Literal["obstacle", "no_fly", "energy_zone"]
TerrainType = Literal[
    "water", "forest", "urban", "field", "road", "rubble", "fire", "smoke", "hill", "coast", "slick"
]
Difficulty = Literal["easy", "medium", "hard"]
TERRAIN_TYPES: tuple[str, ...] = (
    "water",
    "forest",
    "urban",
    "field",
    "road",
    "rubble",
    "fire",
    "smoke",
    "hill",
    "coast",
    "slick",
)

EPS = 1e-9
CHARGE_PREFIX = "@charge:"


class Priority(IntEnum):
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4


class UAVStatus(StrEnum):
    IDLE = "idle"
    EN_ROUTE = "en_route"
    WORKING = "working"
    RETURNING = "returning"
    CHARGING = "charging"
    UNAVAILABLE = "unavailable"


class ViolationType(StrEnum):
    CAPABILITY = "CAPABILITY"
    UNREACHABLE = "UNREACHABLE"
    ENERGY_TOTAL = "ENERGY_TOTAL"
    ENERGY_PREFIX = "ENERGY_PREFIX"
    NO_RETURN = "NO_RETURN"
    DEADLINE = "DEADLINE"
    UAV_UNAVAILABLE = "UAV_UNAVAILABLE"


ENERGY_VIOLATIONS = frozenset(
    {ViolationType.ENERGY_TOTAL, ViolationType.ENERGY_PREFIX, ViolationType.NO_RETURN}
)


class EventType(StrEnum):
    NEW_TASK = "NEW_TASK"
    BLOCK_REGION = "BLOCK_REGION"
    UAV_UNAVAILABLE = "UAV_UNAVAILABLE"
    BATTERY_DROP = "BATTERY_DROP"
    TASK_DURATION_CHANGE = "TASK_DURATION_CHANGE"
    TASK_CANCELLED = "TASK_CANCELLED"
    UAV_JOINS = "UAV_JOINS"
    RTH = "RTH"
    STATION_OUTAGE = "STATION_OUTAGE"
    WIND_CHANGE = "WIND_CHANGE"
    TASK_DEADLINE_CHANGE = "TASK_DEADLINE_CHANGE"
    TERRAIN_CHANGE = "TERRAIN_CHANGE"


def charge_stop(station_id: str) -> str:
    """Route entry meaning "fly to this station and recharge"."""
    return f"{CHARGE_PREFIX}{station_id}"


def is_charge_stop(entry: str) -> bool:
    return entry.startswith(CHARGE_PREFIX)


def charge_station(entry: str) -> str:
    return entry.removeprefix(CHARGE_PREFIX)


def jsonable(obj: Any) -> Any:
    """Convert model objects to plain JSON types with a deterministic layout."""
    if isinstance(obj, Enum):
        return obj.value
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: jsonable(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, frozenset | set):
        return sorted(jsonable(v) for v in obj)
    if isinstance(obj, list | tuple):
        return [jsonable(v) for v in obj]
    if isinstance(obj, dict):
        return {str(k): jsonable(v) for k, v in sorted(obj.items(), key=lambda kv: str(kv[0]))}
    return obj


def _cell(value: Any) -> Cell:
    return (int(value[0]), int(value[1]))


def _cells(values: Any) -> tuple[Cell, ...]:
    return tuple(_cell(v) for v in values)


def _points(values: Any) -> tuple[Point, ...]:
    return tuple((float(v[0]), float(v[1])) for v in values)


@dataclass(frozen=True, slots=True)
class GridSpec:
    """A ``width`` × ``height`` cell map; each cell is ``cell_size`` metres wide."""

    width: int = 50
    height: int = 50
    cell_size: float = 20.0

    @staticmethod
    def from_dict(d: dict[str, Any]) -> GridSpec:
        return GridSpec(int(d["width"]), int(d["height"]), float(d["cell_size"]))


@dataclass(frozen=True, slots=True)
class EnergyConfig:
    """Scenario-wide switches for the energy model."""

    mode: EnergyMode = "normalized"
    reserve_ratio: float = 0.10
    home_only_return: bool = False
    strict_prefix_safety: bool = True
    turn_energy_per_rad: float = 0.0
    path_metric: PathMetric = "energy"
    adaptive_reserve: bool = False
    adaptive_k: float = 1.0
    adaptive_sigma: float = 0.1
    charge_rate: float = 0.5
    charge_time: float = 120.0

    @staticmethod
    def from_dict(d: dict[str, Any]) -> EnergyConfig:
        names = {f.name for f in dataclasses.fields(EnergyConfig)}
        return EnergyConfig(**{k: v for k, v in d.items() if k in names})


@dataclass(frozen=True, slots=True)
class Obstacle:
    """A rectangle (inclusive cell bounds) or polygon (cell coordinates).

    ``obstacle`` and ``no_fly`` regions remove cells from the graph; ``blocked`` cells
    added mid-mission use ``cells``. ``energy_zone`` regions stay flyable but multiply
    the energy of edges inside them.
    """

    id: str
    kind: RegionKind = "obstacle"
    rect: tuple[int, int, int, int] | None = None
    polygon: tuple[Point, ...] | None = None
    cells: tuple[Cell, ...] | None = None
    energy_multiplier: float = 1.0
    label: str = ""

    @staticmethod
    def from_dict(d: dict[str, Any]) -> Obstacle:
        rect = d.get("rect")
        return Obstacle(
            id=str(d["id"]),
            kind=d.get("kind", "obstacle"),
            rect=(int(rect[0]), int(rect[1]), int(rect[2]), int(rect[3])) if rect else None,
            polygon=_points(d["polygon"]) if d.get("polygon") else None,
            cells=_cells(d["cells"]) if d.get("cells") else None,
            energy_multiplier=float(d.get("energy_multiplier", 1.0)),
            label=str(d.get("label", "")),
        )


@dataclass(frozen=True, slots=True)
class Wind:
    """A uniform wind. ``direction_deg`` is the compass bearing it blows towards.

    0° points up the map (increasing ``y``) and 90° points right (increasing ``x``).
    ``k`` is the sensitivity ``k_w`` in the leg energy factor.
    """

    speed: float = 0.0
    direction_deg: float = 0.0
    k: float = 1.0

    def vector(self) -> Point:
        """Wind velocity in cell axes (metres per second)."""
        angle = math.radians(self.direction_deg)
        return (self.speed * math.sin(angle), self.speed * math.cos(angle))

    @staticmethod
    def from_dict(d: dict[str, Any]) -> Wind:
        return Wind(
            float(d.get("speed", 0.0)),
            float(d.get("direction_deg", 0.0)),
            float(d.get("k", 1.0)),
        )


@dataclass(frozen=True, slots=True)
class TerrainLayer:
    """A typed map region, drawn on the map and optionally affecting flight.

    ``blocked`` removes its cells from the graph. ``energy_multiplier`` scales the energy of
    edges inside it, through the same cell multipliers as an ``energy_zone`` region.
    """

    id: str
    type: TerrainType
    rect: tuple[int, int, int, int] | None = None
    polygon: tuple[Point, ...] | None = None
    cells: tuple[Cell, ...] | None = None
    blocked: bool = False
    energy_multiplier: float | None = None
    label: str = ""

    def as_region(self) -> Obstacle | None:
        """The planning effect of this layer, or ``None`` if it is only drawn."""
        if self.blocked:
            kind: RegionKind = "obstacle"
            multiplier = 1.0
        elif self.energy_multiplier is not None and self.energy_multiplier != 1.0:
            kind, multiplier = "energy_zone", self.energy_multiplier
        else:
            return None
        return Obstacle(
            f"layer:{self.id}",
            kind,
            rect=self.rect,
            polygon=self.polygon,
            cells=self.cells,
            energy_multiplier=multiplier,
            label=self.label,
        )

    @staticmethod
    def from_dict(d: dict[str, Any]) -> TerrainLayer:
        rect = d.get("rect")
        multiplier = d.get("energy_multiplier")
        return TerrainLayer(
            id=str(d["id"]),
            type=d["type"],
            rect=(int(rect[0]), int(rect[1]), int(rect[2]), int(rect[3])) if rect else None,
            polygon=_points(d["polygon"]) if d.get("polygon") else None,
            cells=_cells(d["cells"]) if d.get("cells") else None,
            blocked=bool(d.get("blocked", False)),
            energy_multiplier=None if multiplier is None else float(multiplier),
            label=str(d.get("label", "")),
        )


@dataclass(frozen=True, slots=True)
class ScenarioMeta:
    """How a mission is presented. All values are illustrative simulation settings."""

    id: str
    name: str
    category: str = ""
    description: str = ""
    story: str = ""
    icon: str = ""
    theme: str = ""
    difficulty: Difficulty = "medium"
    tags: tuple[str, ...] = ()
    energy_focus: str = ""
    allow_recharge: bool = False

    @staticmethod
    def from_dict(d: dict[str, Any]) -> ScenarioMeta:
        return ScenarioMeta(
            id=str(d["id"]),
            name=str(d.get("name", d["id"])),
            category=str(d.get("category", "")),
            description=str(d.get("description", "")),
            story=str(d.get("story", "")),
            icon=str(d.get("icon", "")),
            theme=str(d.get("theme", "")),
            difficulty=d.get("difficulty", "medium"),
            tags=tuple(str(t) for t in d.get("tags", ())),
            energy_focus=str(d.get("energy_focus", "")),
            allow_recharge=bool(d.get("allow_recharge", False)),
        )


@dataclass(frozen=True, slots=True)
class Station:
    """A charging/landing station."""

    id: str
    cell: Cell

    @staticmethod
    def from_dict(d: dict[str, Any]) -> Station:
        return Station(str(d["id"]), _cell(d["cell"]))


@dataclass(frozen=True, slots=True)
class UAV:
    """UAV state ``U_i = (p_i, B_i, B_i^max, C_i, T_i, v_i, P_cruise, P_hover, status, home)``."""

    id: str
    start: Cell
    battery: float
    home: str
    capabilities: frozenset[str] = frozenset({"camera"})
    battery_max: float = 100.0
    speed: float = 10.0
    energy_per_meter: float = 0.03
    power_cruise: float = 100.0
    power_hover: float = 120.0
    hover_rate: float = 0.0
    takeoff_energy: float = 0.0
    landing_energy: float = 0.0
    status: UAVStatus = UAVStatus.IDLE
    ready_time: float = 0.0
    airborne: bool = False

    @staticmethod
    def from_dict(d: dict[str, Any]) -> UAV:
        battery_max = float(d.get("battery_max", 100.0))
        return UAV(
            id=str(d["id"]),
            start=_cell(d["start"]),
            battery=float(d.get("battery", battery_max)),
            home=str(d["home"]),
            capabilities=frozenset(str(c) for c in d.get("capabilities", ["camera"])),
            battery_max=battery_max,
            speed=float(d.get("speed", 10.0)),
            energy_per_meter=float(d.get("energy_per_meter", 0.03)),
            power_cruise=float(d.get("power_cruise", 100.0)),
            power_hover=float(d.get("power_hover", 120.0)),
            hover_rate=float(d.get("hover_rate", 0.0)),
            takeoff_energy=float(d.get("takeoff_energy", 0.0)),
            landing_energy=float(d.get("landing_energy", 0.0)),
            status=UAVStatus(d.get("status", "idle")),
            ready_time=float(d.get("ready_time", 0.0)),
            airborne=bool(d.get("airborne", False)),
        )


@dataclass(frozen=True, slots=True)
class Task:
    """Task ``J_j = (p_j, P_j, S_j, E_task, dur_j, deadline_j, mandatory, release_j)``."""

    id: str
    cell: Cell
    priority: Priority = Priority.MEDIUM
    capability: str | None = "camera"
    energy: float = 8.0
    duration: float = 30.0
    deadline: float | None = None
    mandatory: bool = False
    release_time: float = 0.0
    label: str = ""

    @staticmethod
    def from_dict(d: dict[str, Any]) -> Task:
        deadline = d.get("deadline")
        return Task(
            id=str(d["id"]),
            cell=_cell(d["cell"]),
            priority=Priority(int(d.get("priority", 2))),
            capability=d.get("capability", "camera"),
            energy=float(d.get("energy", 8.0)),
            duration=float(d.get("duration", 30.0)),
            deadline=None if deadline is None else float(deadline),
            mandatory=bool(d.get("mandatory", False)),
            release_time=float(d.get("release_time", 0.0)),
            label=str(d.get("label", "")),
        )


@dataclass(frozen=True, slots=True)
class Event:
    """A mission change. Which optional fields apply depends on ``type``."""

    type: EventType
    time: float
    task: Task | None = None
    cells: tuple[Cell, ...] | None = None
    polygon: tuple[Point, ...] | None = None
    uav_id: str | None = None
    amount: float | None = None
    task_id: str | None = None
    new_duration: float | None = None
    uav: UAV | None = None
    station_id: str | None = None
    wind: Wind | None = None
    new_deadline: float | None = None
    layer: TerrainLayer | None = None
    label: str = ""

    @staticmethod
    def from_dict(d: dict[str, Any]) -> Event:
        return Event(
            type=EventType(d["type"]),
            time=float(d["time"]),
            task=Task.from_dict(d["task"]) if d.get("task") else None,
            cells=_cells(d["cells"]) if d.get("cells") else None,
            polygon=_points(d["polygon"]) if d.get("polygon") else None,
            uav_id=d.get("uav_id"),
            amount=None if d.get("amount") is None else float(d["amount"]),
            task_id=d.get("task_id"),
            new_duration=None if d.get("new_duration") is None else float(d["new_duration"]),
            uav=UAV.from_dict(d["uav"]) if d.get("uav") else None,
            station_id=d.get("station_id"),
            wind=Wind.from_dict(d["wind"]) if d.get("wind") else None,
            new_deadline=None if d.get("new_deadline") is None else float(d["new_deadline"]),
            layer=TerrainLayer.from_dict(d["layer"]) if d.get("layer") else None,
            label=str(d.get("label", "")),
        )


@dataclass(frozen=True, slots=True)
class Scenario:
    """Everything a planner needs. Mid-mission replans use a derived scenario."""

    id: str
    name: str
    grid: GridSpec
    uavs: tuple[UAV, ...]
    tasks: tuple[Task, ...]
    stations: tuple[Station, ...]
    obstacles: tuple[Obstacle, ...] = ()
    events: tuple[Event, ...] = ()
    energy: EnergyConfig = field(default_factory=EnergyConfig)
    seed: int = 0
    description: str = ""
    meta: ScenarioMeta | None = None
    terrain_layers: tuple[TerrainLayer, ...] = ()
    wind: Wind | None = None

    def regions(self) -> tuple[Obstacle, ...]:
        """Every region that affects flight: obstacles plus the effect of terrain layers."""
        layers = (layer.as_region() for layer in self.terrain_layers)
        return (*self.obstacles, *(region for region in layers if region is not None))

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = jsonable(self)
        return result

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=1, sort_keys=True)

    @staticmethod
    def from_dict(d: dict[str, Any]) -> Scenario:
        return Scenario(
            id=str(d["id"]),
            name=str(d.get("name", d["id"])),
            grid=GridSpec.from_dict(d["grid"]),
            uavs=tuple(UAV.from_dict(u) for u in d["uavs"]),
            tasks=tuple(Task.from_dict(t) for t in d["tasks"]),
            stations=tuple(Station.from_dict(s) for s in d["stations"]),
            obstacles=tuple(Obstacle.from_dict(o) for o in d.get("obstacles", [])),
            events=tuple(Event.from_dict(e) for e in d.get("events", [])),
            energy=EnergyConfig.from_dict(d.get("energy", {})),
            seed=int(d.get("seed", 0)),
            description=str(d.get("description", "")),
            meta=ScenarioMeta.from_dict(d["meta"]) if d.get("meta") else None,
            terrain_layers=tuple(TerrainLayer.from_dict(t) for t in d.get("terrain_layers", [])),
            wind=Wind.from_dict(d["wind"]) if d.get("wind") else None,
        )


@dataclass(frozen=True, slots=True)
class PathInfo:
    """Geometry of one flown path. ``effort`` is length weighted by energy multipliers."""

    cells: tuple[Cell, ...]
    distance: float
    effort: float
    turn_radians: float
    edge_distances: tuple[float, ...]
    edge_efforts: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class Violation:
    type: ViolationType
    uav_id: str
    detail: str
    margin: float = 0.0
    task_id: str | None = None


@dataclass(frozen=True, slots=True)
class FeasibilityReport:
    feasible: bool
    violations: tuple[Violation, ...] = ()


@dataclass(frozen=True, slots=True)
class Leg:
    """One flown leg: to a task, to a charging stop, or the final return."""

    kind: Literal["to_task", "to_charge", "return"]
    start: Cell
    end: Cell
    task_id: str | None
    path: tuple[Cell, ...]
    distance: float
    time: float
    energy: float
    edge_distances: tuple[float, ...] = ()
    edge_efforts: tuple[float, ...] = ()
    charge_energy: float = 0.0
    charge_time: float = 0.0


@dataclass(frozen=True, slots=True)
class BatteryPoint:
    """Planned battery level after an action."""

    time: float
    battery: float
    label: str


@dataclass(frozen=True, slots=True)
class Route:
    """A UAV's complete plan: ``[start, j1, ..., jk, station]``."""

    uav_id: str
    task_ids: tuple[str, ...]
    legs: tuple[Leg, ...]
    station_id: str | None
    start_time: float
    completion_time: float
    distance: float
    travel_energy: float
    task_energy: float
    return_energy: float
    takeoff_energy: float
    landing_energy: float
    wait_energy: float
    reserve: float
    battery_start: float
    battery_end: float
    margin: float
    feasible: bool
    profile: tuple[BatteryPoint, ...] = ()
    recharged_energy: float = 0.0

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


@dataclass(frozen=True, slots=True)
class UnassignedTask:
    task_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class ObjectiveBreakdown:
    """Raw totals plus their normalized, weighted contributions to ``Z``."""

    z: float
    total_energy: float
    makespan: float
    total_distance: float
    load_variance: float
    unassigned_penalty: float
    energy_term: float
    time_term: float
    distance_term: float
    balance_term: float


@dataclass(frozen=True, slots=True)
class CandidateTrace:
    """One UAV considered for a task, for the "Explain this assignment" panel."""

    uav_id: str
    position: int | None
    feasible: bool
    delta_distance: float = 0.0
    delta_energy: float = 0.0
    delta_time: float = 0.0
    delta_imbalance: float = 0.0
    cost: float | None = None
    rejected_reason: str | None = None


@dataclass(frozen=True, slots=True)
class TaskExplanation:
    task_id: str
    chosen_uav: str | None
    note: str
    candidates: tuple[CandidateTrace, ...]


@dataclass(frozen=True, slots=True)
class Plan:
    algorithm: str
    scenario_id: str
    routes: tuple[Route, ...]
    unassigned: tuple[UnassignedTask, ...]
    report: FeasibilityReport
    objective: ObjectiveBreakdown
    compute_time_s: float = 0.0
    explanations: tuple[TaskExplanation, ...] = ()
    config: dict[str, Any] = field(default_factory=dict)

    def assignment(self) -> dict[str, tuple[str, ...]]:
        return {r.uav_id: r.task_ids for r in self.routes}

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = jsonable(self)
        return result

    def canonical_json(self) -> str:
        """Deterministic serialization; excludes the wall-clock ``compute_time_s``."""
        data = self.to_dict()
        data.pop("compute_time_s")
        return json.dumps(data, sort_keys=True)
