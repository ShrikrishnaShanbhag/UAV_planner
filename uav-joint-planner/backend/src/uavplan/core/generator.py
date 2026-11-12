"""Seeded scenario generator, presets A–G and the hand-made earthquake demo."""

from __future__ import annotations

import zlib
from dataclasses import dataclass, replace
from typing import Literal

import numpy as np

from uavplan.core.energy import EnergyEstimator
from uavplan.core.environment import EnvironmentManager
from uavplan.core.models import (
    TERRAIN_TYPES,
    UAV,
    Cell,
    EnergyConfig,
    Event,
    EventType,
    GridSpec,
    Obstacle,
    Priority,
    Scenario,
    Station,
    Task,
)

Distribution = Literal["uniform", "clustered", "edge"]
PRESET_IDS = ("A", "B", "C", "D", "E", "DEMO")
DEFAULT_SEED = 42


@dataclass(frozen=True, slots=True)
class _Archetype:
    capabilities: frozenset[str]
    speed: float
    energy_per_meter: float


_ARCHETYPES = (
    _Archetype(frozenset({"camera", "thermal"}), 14.0, 0.014),
    _Archetype(frozenset({"camera", "lidar"}), 11.0, 0.017),
    _Archetype(frozenset({"camera", "payload"}), 9.0, 0.021),
    _Archetype(frozenset({"camera", "thermal", "lidar"}), 12.0, 0.019),
)

# (label, capability, nominal task energy in EU)
_TASK_KINDS = (
    ("Inspect bridge", "camera", 8.0),
    ("Survey field", "lidar", 12.0),
    ("Inspect power line", "thermal", 10.0),
    ("Photograph building", "camera", 4.0),
    ("Deliver supplies", "payload", 9.0),
)


@dataclass(frozen=True, slots=True)
class GeneratorParams:
    n_uavs: int = 4
    n_tasks: int = 20
    width: int = 50
    height: int = 50
    cell_size: float = 20.0
    obstacle_density: float = 0.08
    n_no_fly: int = 1
    n_energy_zones: int = 2
    n_stations: int = 2
    distribution: Distribution = "uniform"
    battery_min: float = 100.0
    battery_max: float = 100.0
    priority_weights: tuple[float, float, float, float] = (0.3, 0.4, 0.2, 0.1)
    deadline_fraction: float = 0.3
    n_urgent: int = 0
    fail_uav: bool = False


def _seed_sequence(label: str, seed: int) -> np.random.Generator:
    return np.random.default_rng([zlib.crc32(label.encode()), seed])


def _random_rect(rng: np.random.Generator, w: int, h: int, lo: int, hi: int) -> tuple[int, ...]:
    rw, rh = int(rng.integers(lo, hi + 1)), int(rng.integers(lo, hi + 1))
    x0, y0 = int(rng.integers(0, max(1, w - rw))), int(rng.integers(0, max(1, h - rh)))
    return (x0, y0, x0 + rw - 1, y0 + rh - 1)


def _regions(params: GeneratorParams, rng: np.random.Generator) -> list[Obstacle]:
    w, h = params.width, params.height
    regions: list[Obstacle] = []
    target = params.obstacle_density * w * h
    covered, index = 0, 0
    while covered < target:
        x0, y0, x1, y1 = _random_rect(rng, w, h, 2, 8)
        regions.append(Obstacle(f"obs{index}", "obstacle", rect=(x0, y0, x1, y1)))
        covered += (x1 - x0 + 1) * (y1 - y0 + 1)
        index += 1
    for i in range(params.n_no_fly):
        x0, y0, x1, y1 = _random_rect(rng, w, h, 4, 9)
        regions.append(Obstacle(f"nfz{i}", "no_fly", rect=(x0, y0, x1, y1)))
    for i in range(params.n_energy_zones):
        x0, y0, x1, y1 = _random_rect(rng, w, h, 8, 16)
        multiplier = round(float(rng.uniform(1.5, 2.5)), 3)
        regions.append(
            Obstacle(f"zone{i}", "energy_zone", rect=(x0, y0, x1, y1), energy_multiplier=multiplier)
        )
    return regions


def _sample_free(rng: np.random.Generator, allowed: list[Cell], used: set[Cell]) -> Cell:
    while True:
        cell = allowed[int(rng.integers(0, len(allowed)))]
        if cell not in used:
            used.add(cell)
            return cell


def _nearest_allowed(cell: Cell, allowed: list[Cell], used: set[Cell]) -> Cell:
    best = min(
        (c for c in allowed if c not in used),
        key=lambda c: ((c[0] - cell[0]) ** 2 + (c[1] - cell[1]) ** 2, c),
    )
    used.add(best)
    return best


def _task_cells(
    params: GeneratorParams,
    rng: np.random.Generator,
    allowed: list[Cell],
    used: set[Cell],
    count: int,
) -> list[Cell]:
    w, h = params.width, params.height
    if params.distribution == "uniform":
        return [_sample_free(rng, allowed, used) for _ in range(count)]
    cells: list[Cell] = []
    if params.distribution == "clustered":
        centres = [
            (float(rng.uniform(5, w - 5)), float(rng.uniform(5, h - 5)))
            for _ in range(max(2, count // 6))
        ]
        for i in range(count):
            cx, cy = centres[i % len(centres)]
            x = int(np.clip(round(float(rng.normal(cx, 3.5))), 0, w - 1))
            y = int(np.clip(round(float(rng.normal(cy, 3.5))), 0, h - 1))
            cells.append(_nearest_allowed((x, y), allowed, used))
        return cells
    for _ in range(count):
        depth = int(rng.integers(0, 5))
        along = float(rng.uniform(0, 1))
        side = int(rng.integers(0, 4))
        raw: Cell
        if side == 0:
            raw = (int(along * (w - 1)), depth)
        elif side == 1:
            raw = (int(along * (w - 1)), h - 1 - depth)
        elif side == 2:
            raw = (depth, int(along * (h - 1)))
        else:
            raw = (w - 1 - depth, int(along * (h - 1)))
        cells.append(_nearest_allowed(raw, allowed, used))
    return cells


def _make_task(
    task_id: str,
    cell: Cell,
    rng: np.random.Generator,
    params: GeneratorParams,
    fleet_caps: list[str],
    release: float = 0.0,
    urgent: bool = False,
) -> Task:
    kinds = [k for k in _TASK_KINDS if k[1] in fleet_caps]
    label, capability, nominal = kinds[int(rng.integers(0, len(kinds)))]
    energy = round(nominal * float(rng.uniform(0.8, 1.2)), 3)
    duration = round(energy * float(rng.uniform(3.0, 5.0)), 2)
    if urgent:
        priority = Priority.CRITICAL if rng.random() < 0.5 else Priority.HIGH
    else:
        priority = Priority(int(rng.choice([1, 2, 3, 4], p=np.array(params.priority_weights))))
    mandatory = priority == Priority.CRITICAL or (priority == Priority.HIGH and rng.random() < 0.5)
    deadline: float | None = None
    if urgent:
        deadline = round(release + float(rng.uniform(200.0, 400.0)), 2)
    elif rng.random() < params.deadline_fraction:
        slack = float(rng.uniform(300.0, 900.0))
        deadline = round(slack * (0.7 if priority >= Priority.HIGH else 1.0) + duration, 2)
    return Task(
        id=task_id,
        cell=cell,
        priority=priority,
        capability=capability,
        energy=energy,
        duration=duration,
        deadline=deadline,
        mandatory=mandatory,
        release_time=release,
        label=label,
    )


def generate(
    params: GeneratorParams,
    seed: int,
    scenario_id: str = "generated",
    name: str = "Generated scenario",
    description: str = "",
) -> Scenario:
    """Build a random scenario. The same parameters and seed always give the same result."""
    rng = _seed_sequence(scenario_id, seed)
    grid = GridSpec(params.width, params.height, params.cell_size)
    regions = _regions(params, rng)
    env = EnvironmentManager(grid, regions)
    free = env.free_cells()
    if not free:
        raise ValueError("obstacle density leaves no free cells")
    # Keep everything in the largest connected component so tasks are always reachable.
    unseen = set(free)
    largest: set[Cell] = set()
    while unseen:
        component = env.component_of(min(unseen))
        unseen -= component
        if len(component) > len(largest):
            largest = component
    allowed = sorted(largest)
    needed = params.n_stations + params.n_tasks + params.n_urgent
    if len(allowed) < needed:
        raise ValueError("not enough free cells for the requested entities")

    used: set[Cell] = set()
    stations = tuple(
        Station(f"S{i + 1}", _sample_free(rng, allowed, used)) for i in range(params.n_stations)
    )
    uavs: list[UAV] = []
    for i in range(params.n_uavs):
        arch = _ARCHETYPES[i % len(_ARCHETYPES)]
        home = stations[i % len(stations)]
        battery = round(float(rng.uniform(params.battery_min, params.battery_max)), 3)
        uavs.append(
            UAV(
                id=f"U{i + 1}",
                start=home.cell,
                battery=battery,
                home=home.id,
                capabilities=arch.capabilities,
                speed=arch.speed,
                energy_per_meter=arch.energy_per_meter,
            )
        )
    caps = sorted({c for u in uavs for c in u.capabilities})
    tasks = tuple(
        _make_task(f"T{i + 1}", cell, rng, params, caps)
        for i, cell in enumerate(_task_cells(params, rng, allowed, used, params.n_tasks))
    )

    events: list[Event] = []
    if params.n_urgent:
        times = sorted(float(rng.uniform(30.0, 180.0)) for _ in range(params.n_urgent))
        cells = _task_cells(params, rng, allowed, used, params.n_urgent)
        for i, (time, cell) in enumerate(zip(times, cells, strict=True)):
            rounded = round(time, 2)
            task = _make_task(f"N{i + 1}", cell, rng, params, caps, rounded, urgent=True)
            events.append(Event(EventType.NEW_TASK, rounded, task=task))
    if params.fail_uav:
        victim = uavs[int(rng.integers(0, len(uavs)))]
        events.append(
            Event(
                EventType.UAV_UNAVAILABLE,
                round(float(rng.uniform(40.0, 120.0)), 2),
                uav_id=victim.id,
            )
        )
    return Scenario(
        id=scenario_id,
        name=name,
        grid=grid,
        uavs=tuple(uavs),
        tasks=tasks,
        stations=stations,
        obstacles=tuple(regions),
        events=tuple(events),
        energy=EnergyConfig(),
        seed=seed,
        description=description,
    )


_PRESETS: dict[str, tuple[str, str, GeneratorParams]] = {
    "A": ("Small mission", "3 UAVs, 10 tasks, static.", GeneratorParams(3, 10)),
    "B": ("Larger mission", "5 UAVs, 30 tasks, varied priorities.", GeneratorParams(5, 30)),
    "C": (
        "Limited battery",
        "4 UAVs, 20 tasks, starting batteries 40–100 EU.",
        GeneratorParams(4, 20, battery_min=40.0, battery_max=100.0),
    ),
    "D": (
        "Dynamic task arrival",
        "4 UAVs, 20 tasks, plus 5 urgent tasks arriving during the mission.",
        GeneratorParams(4, 20, n_urgent=5),
    ),
    "E": (
        "UAV unavailability",
        "5 UAVs, 25 tasks; one UAV fails mid-mission.",
        GeneratorParams(5, 25, fail_uav=True),
    ),
}

SCALING_UAVS = (2, 4, 8, 12)
SCALING_TASKS = (10, 25, 50, 100)
NOISE_LEVELS = (0.0, 0.05, 0.1, 0.2)


def preset(preset_id: str, seed: int = DEFAULT_SEED, size: str = "standard") -> Scenario:
    """Build preset A–E (random, seeded), the hand-made ``DEMO``, or a named mission."""
    from uavplan.core.missions import MISSION_IDS, mission

    if preset_id in MISSION_IDS:
        return mission(preset_id, size, seed)  # type: ignore[arg-type]
    key = preset_id.upper()
    if key == "DEMO":
        return demo_scenario()
    if key not in _PRESETS:
        raise KeyError(f"unknown preset '{preset_id}'")
    name, description, params = _PRESETS[key]
    return generate(params, seed, key, f"{key}: {name}", description)


def scaling_scenario(n_uavs: int, n_tasks: int, seed: int) -> Scenario:
    """Scenario F: one point of the fleet-size × task-count sweep."""
    return generate(
        GeneratorParams(n_uavs, n_tasks),
        seed,
        f"F-{n_uavs}x{n_tasks}",
        f"F: {n_uavs} UAVs × {n_tasks} tasks",
        "Scaling sweep.",
    )


def noise_scenario(seed: int) -> Scenario:
    """Scenario G: a static mission, simulated at several energy-noise levels."""
    return generate(
        GeneratorParams(4, 20),
        seed,
        "G",
        "G: Energy-noise robustness",
        "4 UAVs, 20 tasks; the simulator perturbs energy use by N(1, σ).",
    )


def demo_scenario() -> Scenario:
    """Hand-made earthquake response map."""
    regions = (
        Obstacle("b1", "obstacle", rect=(10, 8, 15, 13), label="Collapsed block A"),
        Obstacle("b2", "obstacle", rect=(30, 6, 35, 10), label="Collapsed block B"),
        Obstacle("b3", "obstacle", rect=(12, 32, 18, 37), label="Collapsed block C"),
        Obstacle("b4", "obstacle", rect=(37, 30, 42, 36), label="Collapsed block D"),
        Obstacle("b5", "obstacle", rect=(22, 40, 26, 45), label="Collapsed block E"),
        Obstacle(
            "river",
            "energy_zone",
            polygon=(
                (0.0, 20.0),
                (18.0, 18.0),
                (34.0, 24.0),
                (49.0, 21.0),
                (49.0, 27.0),
                (34.0, 30.0),
                (18.0, 24.0),
                (0.0, 26.0),
            ),
            energy_multiplier=1.8,
            label="River corridor (gusts)",
        ),
        Obstacle(
            "nfz",
            "no_fly",
            polygon=((22.0, 11.0), (28.0, 11.0), (30.0, 16.0), (25.0, 19.0), (20.0, 16.0)),
            label="Hospital helipad no-fly zone",
        ),
    )
    stations = (Station("Base-W", (3, 46)), Station("Base-E", (46, 3)))
    uavs = (
        UAV(
            "Scout-1",
            (3, 46),
            100.0,
            "Base-W",
            frozenset({"camera", "thermal"}),
            speed=14.0,
            energy_per_meter=0.013,
        ),
        UAV(
            "Mapper-2",
            (3, 46),
            100.0,
            "Base-W",
            frozenset({"camera", "lidar"}),
            speed=11.0,
            energy_per_meter=0.016,
        ),
        UAV(
            "Carrier-3",
            (46, 3),
            100.0,
            "Base-E",
            frozenset({"camera", "payload"}),
            speed=9.0,
            energy_per_meter=0.019,
        ),
        UAV(
            "Scout-4",
            (46, 3),
            85.0,
            "Base-E",
            frozenset({"camera", "thermal", "lidar"}),
            speed=12.0,
            energy_per_meter=0.017,
        ),
    )
    h, c, m, low = Priority.HIGH, Priority.CRITICAL, Priority.MEDIUM, Priority.LOW
    tasks = (
        Task("T1", (8, 15), c, "thermal", 8.0, 30.0, 600.0, True, label="Search survivors A"),
        Task("T2", (17, 10), h, "camera", 6.0, 24.0, None, True, label="Assess collapsed block A"),
        Task("T3", (33, 13), c, "thermal", 9.0, 35.0, 500.0, True, label="Search survivors B"),
        Task("T4", (28, 6), m, "camera", 4.0, 16.0, None, label="Photograph block B"),
        Task("T5", (10, 30), h, "lidar", 11.0, 45.0, None, True, label="Map collapse zone C"),
        Task("T6", (20, 34), c, "thermal", 8.5, 32.0, 700.0, True, label="Search survivors C"),
        Task("T7", (42, 26), m, "camera", 5.0, 20.0, None, label="Inspect bridge deck"),
        Task("T8", (24, 26), h, "camera", 7.0, 30.0, 900.0, label="Inspect river crossing"),
        Task("T9", (44, 38), h, "payload", 9.0, 25.0, None, True, label="Deliver medical kit"),
        Task("T10", (35, 40), m, "lidar", 10.0, 40.0, None, label="Survey collapsed block D"),
        Task("T11", (5, 22), low, "camera", 4.0, 16.0, None, label="Photograph embankment"),
        Task("T12", (15, 44), m, "payload", 8.0, 22.0, None, label="Deliver radio relay"),
        Task("T13", (40, 12), low, "camera", 4.5, 18.0, None, label="Photograph power line"),
        Task("T14", (46, 18), h, "thermal", 8.0, 30.0, None, label="Inspect substation"),
        Task("T15", (29, 37), m, "camera", 5.5, 22.0, None, label="Inspect road access"),
        Task("T16", (7, 5), low, "lidar", 9.0, 36.0, None, label="Survey north ridge"),
    )
    return Scenario(
        id="DEMO",
        name="Earthquake response demo",
        grid=GridSpec(50, 50, 20.0),
        uavs=uavs,
        tasks=tasks,
        stations=stations,
        obstacles=regions,
        seed=DEFAULT_SEED,
        description=(
            "Hand-made map with collapsed buildings, a river corridor and a no-fly zone. "
            "Values are simulated and illustrative."
        ),
    )


def validate_scenario(scenario: Scenario) -> list[str]:
    """Return human-readable problems; an empty list means the scenario is usable."""
    errors: list[str] = []
    env = EnvironmentManager.from_scenario(scenario)
    for group, ids in (
        ("UAV", [u.id for u in scenario.uavs]),
        ("task", [t.id for t in scenario.tasks]),
        ("station", [s.id for s in scenario.stations]),
    ):
        if len(ids) != len(set(ids)):
            errors.append(f"duplicate {group} ids")
    if not scenario.stations:
        errors.append("scenario needs at least one station")
    station_ids = {s.id for s in scenario.stations}
    for station in scenario.stations:
        if not env.is_free(station.cell):
            errors.append(f"station {station.id} is inside an obstacle or out of bounds")
    estimator = EnergyEstimator(scenario.energy)
    for uav in scenario.uavs:
        if not env.is_free(uav.start):
            errors.append(f"UAV {uav.id} starts inside an obstacle or out of bounds")
        if uav.home not in station_ids:
            errors.append(f"UAV {uav.id} has unknown home station '{uav.home}'")
        if uav.battery > uav.battery_max + 1e-9:
            errors.append(f"UAV {uav.id} battery exceeds its capacity")
        if uav.battery <= estimator.reserve(uav):
            errors.append(f"UAV {uav.id} battery must be above the reserve")
        if uav.speed <= 0:
            errors.append(f"UAV {uav.id} speed must be positive")
    capabilities = {c for u in scenario.uavs for c in u.capabilities}
    for task in scenario.tasks:
        if not env.is_free(task.cell):
            errors.append(f"task {task.id} is inside an obstacle or out of bounds")
        if task.capability is not None and task.capability not in capabilities:
            errors.append(f"task {task.id} needs '{task.capability}', which no UAV has")
        if task.energy < 0 or task.duration < 0:
            errors.append(f"task {task.id} has negative energy or duration")
    errors += _layer_errors(scenario)
    errors += _reachability_errors(scenario, env)
    errors += _event_errors(scenario, env)
    return errors


def _layer_errors(scenario: Scenario) -> list[str]:
    errors: list[str] = []
    for layer in scenario.terrain_layers:
        if layer.type not in TERRAIN_TYPES:
            errors.append(f"terrain layer {layer.id} has unknown type '{layer.type}'")
        if layer.rect is None and layer.polygon is None and layer.cells is None:
            errors.append(f"terrain layer {layer.id} has no geometry")
        if layer.energy_multiplier is not None and layer.energy_multiplier <= 0:
            errors.append(f"terrain layer {layer.id} has a non-positive energy multiplier")
    if scenario.wind is not None and scenario.wind.speed < 0:
        errors.append("wind speed must not be negative")
    return errors


def _reachability_errors(scenario: Scenario, env: EnvironmentManager) -> list[str]:
    """Every free task must connect to a station that a free UAV could return to."""
    reachable: set[Cell] = set()
    for station in scenario.stations:
        if station.cell not in reachable:
            reachable |= env.component_of(station.cell)
    return [
        f"task {task.id} cannot be reached from any station"
        for task in scenario.tasks
        if env.is_free(task.cell) and task.cell not in reachable
    ]


def _event_errors(scenario: Scenario, env: EnvironmentManager) -> list[str]:
    errors: list[str] = []
    uav_ids = {u.id for u in scenario.uavs}
    task_ids = {t.id for t in scenario.tasks}
    station_ids = {s.id for s in scenario.stations}
    capabilities = {c for u in scenario.uavs for c in u.capabilities}
    lost_stations: set[str] = set()
    for event in sorted(scenario.events, key=lambda e: e.time):
        if event.uav is not None:
            uav_ids.add(event.uav.id)
            capabilities |= event.uav.capabilities
        if event.task is not None:
            task_ids.add(event.task.id)
            if not env.is_free(event.task.cell):
                errors.append(f"new task {event.task.id} is inside an obstacle or out of bounds")
            needed = event.task.capability
            if needed is not None and needed not in capabilities:
                errors.append(f"new task {event.task.id} needs '{needed}', which no UAV has")
        if event.uav_id is not None and event.uav_id not in uav_ids:
            errors.append(f"event at t={event.time:g} names unknown UAV '{event.uav_id}'")
        if event.task_id is not None and event.task_id not in task_ids:
            errors.append(f"event at t={event.time:g} names unknown task '{event.task_id}'")
        if event.type == EventType.WIND_CHANGE and event.wind is None:
            errors.append(f"wind change at t={event.time:g} has no wind")
        if event.type == EventType.STATION_OUTAGE:
            if event.station_id not in station_ids:
                errors.append(f"outage at t={event.time:g} names unknown station")
            else:
                lost_stations.add(str(event.station_id))
    if station_ids and lost_stations == station_ids:
        errors.append("scripted outages leave no station in service")
    return errors


def with_energy(scenario: Scenario, **changes: object) -> Scenario:
    """Copy of a scenario with some energy-model settings changed."""
    return replace(scenario, energy=replace(scenario.energy, **changes))  # type: ignore[arg-type]
