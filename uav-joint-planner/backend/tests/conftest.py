"""Shared test helpers."""

from __future__ import annotations

from dataclasses import replace

import pytest

from uavplan.core.models import UAV, EnergyConfig, GridSpec, Obstacle, Scenario, Station, Task


def make_uav(uid: str = "U1", start: tuple[int, int] = (0, 0), battery: float = 100.0, **kw) -> UAV:
    kw.setdefault("home", "S1")
    kw.setdefault("capabilities", frozenset({"camera"}))
    kw.setdefault("speed", 10.0)
    kw.setdefault("energy_per_meter", 0.01)
    return UAV(id=uid, start=start, battery=battery, **kw)


def make_task(tid: str, cell: tuple[int, int], **kw) -> Task:
    kw.setdefault("energy", 5.0)
    kw.setdefault("duration", 10.0)
    return Task(id=tid, cell=cell, **kw)


def make_scenario(
    uavs: list[UAV],
    tasks: list[Task],
    stations: list[Station] | None = None,
    obstacles: list[Obstacle] | None = None,
    grid: GridSpec | None = None,
    energy: EnergyConfig | None = None,
    **kw,
) -> Scenario:
    return Scenario(
        id="test",
        name="test",
        grid=grid or GridSpec(20, 20, 10.0),
        uavs=tuple(uavs),
        tasks=tuple(tasks),
        stations=tuple(stations or [Station("S1", (0, 0))]),
        obstacles=tuple(obstacles or []),
        energy=energy or EnergyConfig(),
        **kw,
    )


@pytest.fixture
def small_scenario() -> Scenario:
    uavs = [make_uav("U1"), replace(make_uav("U2"), start=(19, 19))]
    tasks = [make_task("T1", (5, 5)), make_task("T2", (10, 3)), make_task("T3", (15, 15))]
    return make_scenario(uavs, tasks)
