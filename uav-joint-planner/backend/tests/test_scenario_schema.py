"""The scenario schema extension: metadata, terrain layers, wind and the new events."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from conftest import make_scenario, make_task, make_uav
from uavplan.core.energy import EnergyEstimator
from uavplan.core.environment import EnvironmentManager
from uavplan.core.generator import PRESET_IDS, preset, validate_scenario
from uavplan.core.models import (
    EnergyConfig,
    Event,
    EventType,
    GridSpec,
    Scenario,
    ScenarioMeta,
    Station,
    TerrainLayer,
    Wind,
)
from uavplan.core.registry import get_planner
from uavplan.core.simulator import SimConfig, Simulator
from uavplan.core.state import MissionState

GRID = GridSpec(50, 50, 10.0)
DATA = Path(__file__).parent / "data"
GOLDEN = json.loads((DATA / "preset_plan_golden.json").read_text(encoding="utf-8"))


def _task(tid: str = "T1", cell: tuple[int, int] = (5, 5), **kw):
    return make_task(tid, cell, **kw)


def _scenario(uavs, tasks, **kw) -> Scenario:
    return make_scenario(uavs, tasks, grid=GRID, **kw)


def _path(a: tuple[int, int], b: tuple[int, int]):
    env = EnvironmentManager(GridSpec(20, 20, 10.0))
    path = env.path(a, b)
    assert path is not None
    return path


# -- backwards compatibility -------------------------------------------------------------


@pytest.mark.parametrize("key", sorted(GOLDEN))
def test_existing_presets_plan_and_simulate_as_before(key: str) -> None:
    preset_id, algorithm = key.split("/")
    expected = GOLDEN[key]
    scenario = preset(preset_id)
    plan = get_planner(algorithm).plan(scenario)
    result = Simulator(scenario, plan).run()
    assert {u: list(t) for u, t in plan.assignment().items()} == expected["assignment"]
    assert round(plan.objective.total_energy, 6) == expected["energy"]
    assert round(plan.objective.makespan, 6) == expected["makespan"]
    assert round(result.end_time, 6) == expected["sim_end_time"]
    assert round(sum(o.energy_used for o in result.outcomes.values()), 6) == expected["sim_energy"]


def test_a_file_saved_before_the_schema_change_still_loads() -> None:
    data = json.loads((DATA / "legacy_scenario_D.json").read_text(encoding="utf-8"))
    assert "meta" not in data and "terrain_layers" not in data and "wind" not in data
    assert Scenario.from_dict(data) == preset("D")


def test_unknown_fields_are_ignored() -> None:
    data = preset("D").to_dict()
    data["from_the_future"] = {"x": 1}
    data["tasks"][0]["colour"] = "red"
    data["uavs"][0]["paint"] = "blue"
    data["events"][0]["why"] = "because"
    data["energy"]["quantum"] = True
    assert Scenario.from_dict(data) == preset("D")


def test_old_presets_have_no_new_content() -> None:
    for preset_id in PRESET_IDS:
        scenario = preset(preset_id)
        assert scenario.meta is None and scenario.wind is None and not scenario.terrain_layers


def test_new_fields_survive_a_round_trip() -> None:
    scenario = replace(
        _scenario([make_uav()], [_task()]),
        meta=ScenarioMeta("m", "Mission", "Disaster", tags=("a", "b"), difficulty="hard"),
        terrain_layers=(
            TerrainLayer("w", "water", polygon=((1.0, 1.0), (4.0, 1.0), (4.0, 4.0))),
            TerrainLayer("h", "hill", rect=(5, 5, 6, 6), energy_multiplier=1.15),
            TerrainLayer("f", "fire", cells=((7, 7),), blocked=True, label="Front"),
        ),
        wind=Wind(4.0, 200.0, 0.8),
        events=(
            Event(EventType.STATION_OUTAGE, 5.0, station_id="S1", label="Mains lost"),
            Event(EventType.WIND_CHANGE, 6.0, wind=Wind(2.0, 10.0)),
            Event(EventType.TASK_DEADLINE_CHANGE, 7.0, task_id="T1", new_deadline=50.0),
            Event(
                EventType.TERRAIN_CHANGE,
                8.0,
                layer=TerrainLayer("w", "water", rect=(0, 0, 1, 1)),
            ),
        ),
    )
    assert Scenario.from_dict(json.loads(scenario.to_json())) == scenario


# -- wind --------------------------------------------------------------------------------


def test_headwind_leg_costs_more_than_the_same_leg_with_a_tailwind() -> None:
    uav = make_uav(speed=10.0, energy_per_meter=0.02)
    north = _path((5, 2), (5, 12))
    south = _path((5, 12), (5, 2))
    wind = EnergyEstimator(EnergyConfig(), Wind(3.0, 0.0))  # blows towards +y
    calm = EnergyEstimator()
    assert wind.travel_energy(uav, south) > calm.travel_energy(uav, south)
    assert wind.travel_energy(uav, north) < calm.travel_energy(uav, north)
    assert wind.travel_energy(uav, south) == pytest.approx(calm.travel_energy(uav, south) * 1.3)
    assert wind.travel_energy(uav, north) == pytest.approx(calm.travel_energy(uav, north) * 0.7)


def test_tailwind_factor_is_clamped() -> None:
    uav = make_uav(speed=10.0)
    tailwind = EnergyEstimator(EnergyConfig(), Wind(9.0, 0.0))
    assert tailwind.wind_factor(uav, _path((5, 2), (5, 12))) == pytest.approx(0.6)


def test_compass_bearing_90_blows_towards_increasing_x() -> None:
    uav = make_uav(speed=10.0)
    wind = EnergyEstimator(EnergyConfig(), Wind(2.0, 90.0))
    assert wind.wind_factor(uav, _path((2, 5), (12, 5))) == pytest.approx(0.8)
    assert wind.wind_factor(uav, _path((12, 5), (2, 5))) == pytest.approx(1.2)
    assert wind.wind_factor(uav, _path((5, 2), (5, 12))) == pytest.approx(1.0)


def test_crosswind_and_no_wind_change_nothing() -> None:
    uav = make_uav()
    path = _path((2, 5), (12, 5))
    calm = EnergyEstimator().travel_energy(uav, path)
    assert EnergyEstimator(EnergyConfig(), Wind(0.0, 45.0)).travel_energy(uav, path) == calm
    assert EnergyEstimator(EnergyConfig(), None).travel_energy(uav, path) == calm


def test_wind_also_scales_physical_mode() -> None:
    uav = make_uav(speed=10.0)
    path = _path((5, 12), (5, 2))
    physical = EnergyConfig(mode="physical")
    calm = EnergyEstimator(physical).travel_energy(uav, path)
    assert EnergyEstimator(physical, Wind(2.0, 0.0)).travel_energy(uav, path) == pytest.approx(
        calm * 1.2
    )


def test_planned_route_energy_includes_the_wind_in_each_direction() -> None:
    uav = make_uav(speed=10.0)
    task = _task(cell=(0, 25), energy=1.0)
    base = _scenario([uav], [task])
    calm = get_planner("integrated").plan(base).routes[0]
    windy = get_planner("integrated").plan(replace(base, wind=Wind(2.0, 0.0))).routes[0]
    assert windy.travel_energy == pytest.approx(calm.travel_energy * 0.8)
    assert windy.return_energy == pytest.approx(calm.return_energy * 1.2)


def test_planner_drops_a_task_when_the_wind_makes_the_trip_home_too_dear() -> None:
    uav = make_uav(battery=39.0, battery_max=39.0, speed=10.0, energy_per_meter=0.05)
    far = _task(cell=(5, 30), energy=0.5)
    scenario = _scenario([uav], [far])
    assert get_planner("integrated").plan(scenario).routes[0].task_ids == ("T1",)
    windy = get_planner("integrated").plan(replace(scenario, wind=Wind(8.0, 0.0)))
    assert windy.routes[0].task_ids == ()
    assert windy.report.feasible


# -- terrain layers ----------------------------------------------------------------------


def test_blocked_layer_removes_cells_and_a_drawn_layer_does_not() -> None:
    uav, task = make_uav(), _task()
    base = _scenario([uav], [task])
    scenario = replace(
        base,
        terrain_layers=(
            TerrainLayer("rubble", "rubble", rect=(20, 20, 22, 22), blocked=True),
            TerrainLayer("park", "forest", rect=(30, 30, 32, 32)),
        ),
    )
    env = EnvironmentManager.from_scenario(scenario)
    assert not env.is_free((21, 21))
    assert env.is_free((31, 31)) and env.multiplier[31, 31] == 1.0


def test_layer_multiplier_reaches_the_energy_estimator() -> None:
    uav = make_uav()
    task = _task(cell=(25, 5), energy=1.0)
    base = _scenario([uav], [task])
    # Covers the whole map, so the path cannot avoid it.
    hills = TerrainLayer("hill", "hill", rect=(0, 0, 49, 49), energy_multiplier=1.15)
    calm = get_planner("integrated").plan(base).routes[0]
    hilly = get_planner("integrated").plan(replace(base, terrain_layers=(hills,))).routes[0]
    assert hilly.travel_energy == pytest.approx(calm.travel_energy * 1.15)
    assert hilly.distance == pytest.approx(calm.distance)


def test_validation_sees_blocked_layers_and_unreachable_tasks() -> None:
    task = _task(cell=(21, 21))
    scenario = replace(
        _scenario([make_uav()], [task]),
        terrain_layers=(TerrainLayer("r", "rubble", rect=(20, 20, 22, 22), blocked=True),),
    )
    assert any("inside an obstacle" in e for e in validate_scenario(scenario))
    walls = tuple(
        TerrainLayer(f"w{i}", "rubble", rect=rect, blocked=True)
        for i, rect in enumerate(
            ((19, 19, 23, 19), (19, 23, 23, 23), (19, 19, 19, 23), (23, 19, 23, 23))
        )
    )
    enclosed = replace(_scenario([make_uav()], [task]), terrain_layers=walls)
    assert any("cannot be reached" in e for e in validate_scenario(enclosed))


def test_validation_rejects_bad_layers_and_events() -> None:
    base = _scenario([make_uav()], [_task()])
    no_geometry = replace(base, terrain_layers=(TerrainLayer("x", "water"),))
    assert any("no geometry" in e for e in validate_scenario(no_geometry))
    bad_type = replace(
        base,
        terrain_layers=(TerrainLayer("x", "lava", rect=(1, 1, 2, 2)),),  # type: ignore[arg-type]
    )
    assert any("unknown type" in e for e in validate_scenario(bad_type))
    bad_multiplier = replace(
        base, terrain_layers=(TerrainLayer("x", "hill", rect=(1, 1, 2, 2), energy_multiplier=0.0),)
    )
    assert any("multiplier" in e for e in validate_scenario(bad_multiplier))
    assert validate_scenario(replace(base, wind=Wind(-1.0, 0.0)))
    for event, text in (
        (Event(EventType.STATION_OUTAGE, 1.0, station_id="nope"), "unknown station"),
        (Event(EventType.STATION_OUTAGE, 1.0, station_id="S1"), "no station in service"),
        (Event(EventType.UAV_UNAVAILABLE, 1.0, uav_id="nope"), "unknown UAV"),
        (Event(EventType.TASK_CANCELLED, 1.0, task_id="nope"), "unknown task"),
        (Event(EventType.WIND_CHANGE, 1.0), "no wind"),
        (Event(EventType.NEW_TASK, 1.0, task=_task("N", cell=(99, 99))), "out of bounds"),
        (Event(EventType.NEW_TASK, 1.0, task=_task("N", capability="sonar")), "no UAV has"),
    ):
        assert any(text in e for e in validate_scenario(replace(base, events=(event,)))), text


# -- new events --------------------------------------------------------------------------


def _two_station_scenario(*events: Event) -> Scenario:
    uav = make_uav(start=(5, 5), home="S1")
    task = _task(cell=(40, 40), energy=1.0)
    stations = (Station("S1", (5, 5)), Station("S2", (45, 45)))
    return replace(_scenario([uav], [task]), stations=stations, events=events)


def test_station_outage_sends_the_uav_to_another_station() -> None:
    outage = Event(EventType.STATION_OUTAGE, 10.0, station_id="S2", label="Mains lost")
    scenario = _two_station_scenario(outage)
    plan = get_planner("integrated").plan(scenario)
    assert plan.routes[0].station_id == "S2"
    result = Simulator(scenario, plan).run()
    assert result.outcomes["U1"].landed
    assert result.segments["U1"][-1].cells[-1] == (5, 5)
    entry = next(e for e in result.log if e.kind == "STATION_OUTAGE")
    assert entry.detail == "Mains lost"
    assert len(result.replans) == 1


def test_station_outage_strands_a_uav_that_cannot_replan() -> None:
    outage = Event(EventType.STATION_OUTAGE, 10.0, station_id="S2")
    scenario = _two_station_scenario(outage)
    plan = get_planner("integrated").plan(scenario)
    result = Simulator(scenario, plan, SimConfig(replan=None)).run()
    assert not result.outcomes["U1"].landed
    assert any(e.kind == "STRANDED" for e in result.log)


def test_outage_removes_the_station_from_later_plans() -> None:
    scenario = _two_station_scenario()
    state = MissionState.from_scenario(scenario)
    state.stations_out.add("S1")
    assert [s.id for s in state.derived_scenario().stations] == ["S2"]


def test_wind_change_triggers_a_replan_with_the_new_wind() -> None:
    change = Event(EventType.WIND_CHANGE, 5.0, wind=Wind(3.0, 180.0), label="Shift")
    scenario = _two_station_scenario(change)
    plan = get_planner("integrated").plan(scenario)
    sim = Simulator(scenario, plan)
    result = sim.run()
    assert sim.state.wind == Wind(3.0, 180.0)
    assert sim.state.derived_scenario().wind == Wind(3.0, 180.0)
    assert len(result.replans) == 1


def test_wind_change_without_replanning_still_costs_energy() -> None:
    uav = make_uav(start=(5, 5), home="S1", speed=10.0)
    task = _task(cell=(5, 40), energy=1.0)
    base = _scenario([uav], [task])
    headwind = Event(EventType.WIND_CHANGE, 0.0, wind=Wind(5.0, 180.0))
    calm = Simulator(base, get_planner("integrated").plan(base), SimConfig(replan=None)).run()
    windy_scenario = replace(base, events=(headwind,))
    windy = Simulator(
        windy_scenario, get_planner("integrated").plan(windy_scenario), SimConfig(replan=None)
    ).run()
    assert windy.outcomes["U1"].energy_used > calm.outcomes["U1"].energy_used


def test_wind_change_without_replanning_can_deplete_a_uav() -> None:
    uav = make_uav(
        start=(5, 5), home="S1", speed=10.0, battery=40.0, battery_max=40.0, energy_per_meter=0.04
    )
    task = _task(cell=(5, 40), energy=1.0)
    base = _scenario([uav], [task])
    plan = get_planner("integrated").plan(base)
    assert plan.routes[0].task_ids == ("T1",)
    storm = replace(base, events=(Event(EventType.WIND_CHANGE, 0.0, wind=Wind(25.0, 180.0)),))
    result = Simulator(storm, plan, SimConfig(replan=None)).run()
    assert result.outcomes["U1"].depleted


def test_deadline_change_updates_the_task() -> None:
    change = Event(EventType.TASK_DEADLINE_CHANGE, 5.0, task_id="T1", new_deadline=20.0)
    scenario = _two_station_scenario(change)
    sim = Simulator(scenario, get_planner("integrated").plan(scenario))
    result = sim.run()
    assert sim.state.tasks.tasks["T1"].deadline == 20.0
    assert "T1" in result.deadlines_missed or result.task_status["T1"].value != "completed"


def test_terrain_change_replaces_a_layer_without_blocking() -> None:
    old = TerrainLayer("slick", "slick", rect=(20, 20, 25, 25))
    new = TerrainLayer("slick", "slick", rect=(26, 20, 31, 25), energy_multiplier=1.5)
    event = Event(EventType.TERRAIN_CHANGE, 5.0, layer=new)
    scenario = replace(_two_station_scenario(event), terrain_layers=(old,))
    sim = Simulator(scenario, get_planner("integrated").plan(scenario))
    sim.run()
    assert sim.state.derived_scenario().terrain_layers == (new,)
    assert sim.env.multiplier[22, 28] == 1.5
    assert sim.env.is_free((28, 22))


def test_block_event_can_carry_the_layer_to_draw() -> None:
    fire = TerrainLayer("fire", "fire", rect=(30, 30, 33, 33), blocked=True)
    event = Event(EventType.BLOCK_REGION, 5.0, cells=((31, 31),), layer=fire)
    scenario = _two_station_scenario(event)
    sim = Simulator(scenario, get_planner("integrated").plan(scenario))
    sim.run()
    assert not sim.env.is_free((31, 31))
    assert sim.state.layers["fire"] == fire


def test_new_events_are_deterministic() -> None:
    events = (
        Event(EventType.WIND_CHANGE, 5.0, wind=Wind(3.0, 180.0)),
        Event(EventType.STATION_OUTAGE, 9.0, station_id="S2"),
    )
    scenario = _two_station_scenario(*events)
    runs = [
        Simulator(
            scenario, get_planner("integrated").plan(scenario), SimConfig(noise_sigma=0.1, seed=3)
        ).run()
        for _ in range(2)
    ]
    assert runs[0].outcomes == runs[1].outcomes
