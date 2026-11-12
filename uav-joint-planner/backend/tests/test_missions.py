"""The twelve mission presets: validity, determinism, energy feasibility and events."""

from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path

import pytest

from uavplan.core.allocation.integrated import IntegratedConfig
from uavplan.core.environment import EnvironmentManager, polygon_cells
from uavplan.core.generator import preset, validate_scenario
from uavplan.core.missions import MISSION_IDS, SIZES, mission, mission_meta
from uavplan.core.models import TERRAIN_TYPES, EventType, Scenario
from uavplan.core.registry import get_planner
from uavplan.core.simulator import SimConfig, Simulator

CASES = [(m, s) for m in MISSION_IDS for s in SIZES]
FOLDER = Path(__file__).resolve().parent.parent / "scenarios"
CATEGORIES = {"Disaster", "Infrastructure", "Delivery", "Agriculture", "Safety", "Maritime"}


def _plan(scenario: Scenario):
    recharge = bool(scenario.meta and scenario.meta.allow_recharge)
    config = IntegratedConfig(allow_recharge=recharge)
    return get_planner("integrated").plan(scenario, config), config


def _run(scenario: Scenario):
    plan, config = _plan(scenario)
    return Simulator(scenario, plan, planner_config=config).run()


def test_there_are_twelve_missions() -> None:
    assert len(MISSION_IDS) == 12
    assert len(set(MISSION_IDS)) == 12


@pytest.mark.parametrize(("mission_id", "size"), CASES)
def test_mission_is_valid(mission_id: str, size: str) -> None:
    assert validate_scenario(mission(mission_id, size)) == []  # type: ignore[arg-type]


@pytest.mark.parametrize("mission_id", MISSION_IDS)
@pytest.mark.parametrize("seed", [0, 7, 123])
def test_mission_is_valid_for_other_seeds(mission_id: str, seed: int) -> None:
    assert validate_scenario(mission(mission_id, "standard", seed)) == []


@pytest.mark.parametrize("mission_id", MISSION_IDS)
def test_mission_is_deterministic_and_varies_with_the_seed(mission_id: str) -> None:
    assert (
        mission(mission_id, "standard", 5).to_json() == mission(mission_id, "standard", 5).to_json()
    )
    assert (
        mission(mission_id, "standard", 5).to_json() != mission(mission_id, "standard", 6).to_json()
    )


@pytest.mark.parametrize("mission_id", MISSION_IDS)
def test_seed_keeps_the_map_and_story(mission_id: str) -> None:
    a, b = mission(mission_id, "standard", 1), mission(mission_id, "standard", 2)
    assert a.terrain_layers == b.terrain_layers
    assert a.meta == b.meta
    assert a.grid == b.grid


@pytest.mark.parametrize("mission_id", MISSION_IDS)
def test_small_size_is_smaller_with_the_same_look(mission_id: str) -> None:
    small, standard = mission(mission_id, "small"), mission(mission_id, "standard")
    assert len(small.tasks) < len(standard.tasks)
    assert small.terrain_layers == standard.terrain_layers
    assert small.meta == standard.meta


@pytest.mark.parametrize(("mission_id", "size"), CASES)
def test_json_file_matches_the_generator(mission_id: str, size: str) -> None:
    suffix = "" if size == "standard" else "-small"
    text = (FOLDER / f"{mission_id}{suffix}.json").read_text(encoding="utf-8")
    assert text == mission(mission_id, size).to_json() + "\n"  # type: ignore[arg-type]
    assert Scenario.from_dict(json.loads(text)) == mission(mission_id, size)  # type: ignore[arg-type]


@pytest.mark.parametrize("mission_id", MISSION_IDS)
def test_preset_reaches_missions(mission_id: str) -> None:
    assert preset(mission_id, 3) == mission(mission_id, "standard", 3)
    assert preset(mission_id, 3, "small") == mission(mission_id, "small", 3)


def test_unknown_mission_or_size_is_rejected() -> None:
    with pytest.raises(KeyError):
        mission("atlantis")
    with pytest.raises(KeyError):
        mission("flood", "huge")  # type: ignore[arg-type]


@pytest.mark.parametrize("mission_id", MISSION_IDS)
def test_meta_is_complete(mission_id: str) -> None:
    meta = mission_meta(mission_id)
    assert meta.id == mission_id
    assert meta.name and meta.description and meta.story and meta.energy_focus
    assert meta.icon and meta.theme and meta.tags
    assert meta.category in CATEGORIES
    assert meta.difficulty in ("easy", "medium", "hard")
    assert len(re.findall(r"[.!?](?:\s|$)", meta.story)) in (2, 3)


def test_every_category_and_difficulty_is_used() -> None:
    metas = [mission_meta(m) for m in MISSION_IDS]
    assert {m.category for m in metas} == CATEGORIES
    assert {m.difficulty for m in metas} == {"easy", "medium", "hard"}


@pytest.mark.parametrize("mission_id", MISSION_IDS)
def test_no_task_or_station_is_ever_blocked_or_cut_off(mission_id: str) -> None:
    """Applies every scripted block, then checks that everything is still connected."""
    scenario = mission(mission_id)
    env = EnvironmentManager.from_scenario(scenario)
    for event in scenario.events:
        if event.polygon is not None:
            env.block_cells(polygon_cells(event.polygon, scenario.grid.width, scenario.grid.height))
    component = env.component_of(scenario.stations[0].cell)
    new_tasks = [e.task for e in scenario.events if e.task is not None]
    for station in scenario.stations:
        assert station.cell in component, station.id
    for task in (*scenario.tasks, *new_tasks):
        assert task.cell in component, task.id


@pytest.mark.parametrize("mission_id", MISSION_IDS)
def test_every_layer_type_is_known(mission_id: str) -> None:
    for layer in mission(mission_id).terrain_layers:
        assert layer.type in TERRAIN_TYPES


@pytest.mark.parametrize(("mission_id", "size"), CASES)
def test_proposed_plans_are_energy_feasible(mission_id: str, size: str) -> None:
    plan, _ = _plan(mission(mission_id, size))  # type: ignore[arg-type]
    assert plan.report.feasible, plan.report.violations
    assert all(route.margin >= -1e-6 for route in plan.routes)


@pytest.mark.parametrize("mission_id", MISSION_IDS)
def test_the_default_planner_assigns_every_mandatory_task(mission_id: str) -> None:
    scenario = mission(mission_id)
    plan, _ = _plan(scenario)
    tasks = {t.id: t for t in scenario.tasks}
    assert not [u.task_id for u in plan.unassigned if tasks[u.task_id].mandatory]


@pytest.mark.parametrize(("mission_id", "size"), CASES)
def test_scripted_events_fire_and_trigger_replanning(mission_id: str, size: str) -> None:
    scenario = mission(mission_id, size)  # type: ignore[arg-type]
    assert scenario.events
    result = _run(scenario)
    for event in scenario.events:
        assert any(e.kind == event.type.value and e.time == event.time for e in result.log)
    assert len(result.replans) == len(scenario.events)


@pytest.mark.parametrize(("mission_id", "size"), CASES)
def test_the_first_event_arrives_while_uavs_are_flying(mission_id: str, size: str) -> None:
    scenario = mission(mission_id, size)  # type: ignore[arg-type]
    assert _run(scenario).end_time > scenario.events[0].time


@pytest.mark.parametrize("mission_id", MISSION_IDS)
def test_most_of_each_mission_is_completed(mission_id: str) -> None:
    result = _run(mission(mission_id))
    active = [s for s in result.task_status.values() if s.value != "cancelled"]
    done = sum(s.value == "completed" for s in active)
    assert done / len(active) >= 0.75


@pytest.mark.parametrize("mission_id", MISSION_IDS)
def test_simulation_without_replanning_also_runs(mission_id: str) -> None:
    scenario = mission(mission_id, "small")
    plan, _ = _plan(scenario)
    result = Simulator(scenario, plan, SimConfig(replan=None)).run()
    assert not result.replans


def test_powerline_needs_charging_stops_to_finish_the_line() -> None:
    scenario = mission("powerline")
    assert scenario.meta is not None and scenario.meta.allow_recharge
    with_stops, _ = _plan(scenario)
    without = get_planner("integrated").plan(scenario, IntegratedConfig(allow_recharge=False))
    assert sum(r.recharged_energy for r in with_stops.routes) > 0
    assert len(with_stops.unassigned) < len(without.unassigned)
    assert 20 <= len(scenario.tasks) <= 40


def test_earthquake_keeps_the_demo_map() -> None:
    demo, quake = preset("DEMO"), mission("earthquake")
    assert [t.id for t in quake.tasks] == [t.id for t in demo.tasks]
    assert [u.id for u in quake.uavs] == [u.id for u in demo.uavs]
    rubble = {x.id: x for x in quake.terrain_layers if x.type == "rubble"}
    for region in demo.obstacles:
        if region.kind == "obstacle":
            assert rubble[f"rubble-{region.id}"].rect == region.rect
    types = {e.type for e in quake.events}
    assert types == {EventType.BLOCK_REGION, EventType.NEW_TASK}
    assert min(e.time for e in quake.events) == 300.0


def test_events_named_in_the_mission_table_are_present() -> None:
    expected = {
        "flood": {EventType.BLOCK_REGION, EventType.NEW_TASK},
        "wildfire": {EventType.BLOCK_REGION, EventType.WIND_CHANGE},
        "cyclone": {EventType.STATION_OUTAGE, EventType.BATTERY_DROP},
        "powerline": {EventType.NEW_TASK},
        "mountain_sar": {EventType.NEW_TASK, EventType.UAV_UNAVAILABLE},
        "medical": {EventType.NEW_TASK, EventType.TASK_DEADLINE_CHANGE},
        "agriculture": {EventType.TASK_CANCELLED},
        "gas_leak": {EventType.BLOCK_REGION, EventType.NEW_TASK},
        "oil_spill": {
            EventType.TERRAIN_CHANGE,
            EventType.TASK_CANCELLED,
            EventType.NEW_TASK,
            EventType.WIND_CHANGE,
        },
        "crowd": {EventType.NEW_TASK},
        "landslide": {EventType.BLOCK_REGION},
    }
    for mission_id, types in expected.items():
        assert {e.type for e in mission(mission_id).events} == types, mission_id


def test_wildfire_grows_every_200_seconds() -> None:
    blocks = [e for e in mission("wildfire").events if e.type == EventType.BLOCK_REGION]
    assert [e.time for e in blocks] == [200.0, 400.0, 600.0]
    sizes = [len(polygon_cells(e.polygon, 50, 50)) for e in blocks]
    assert sizes == sorted(sizes) and len(set(sizes)) == 3


def test_landslide_second_slide_forces_a_longer_way_round() -> None:
    scenario = mission("landslide")
    env = EnvironmentManager.from_scenario(scenario)
    west, east = scenario.stations[0].cell, scenario.stations[1].cell
    before = env.path(west, east)
    block = next(e for e in scenario.events if e.type == EventType.BLOCK_REGION)
    env.block_cells(polygon_cells(block.polygon, 60, 40))
    after = env.path(west, east)
    assert before is not None and after is not None
    assert after.distance > before.distance * 1.1


def test_repeat_visits_share_a_place_at_different_times() -> None:
    scenario = mission("crowd")
    by_cell: dict[tuple[int, int], list[float]] = {}
    for task in scenario.tasks:
        by_cell.setdefault(task.cell, []).append(task.release_time)
    assert all(times == [0.0, 200.0, 400.0] for times in by_cell.values())


def test_no_real_place_or_organisation_names() -> None:
    banned = re.compile(
        r"\b(red cross|unicef|fema|nasa|amazon|google|california|himalaya|ganges|kerala|"
        r"india|japan|tokyo|london|new york|exxon|shell|bp)\b",
        re.IGNORECASE,
    )
    for mission_id in MISSION_IDS:
        assert not banned.search(mission(mission_id).to_json()), mission_id


@pytest.mark.parametrize("mission_id", MISSION_IDS)
def test_descriptions_say_the_values_are_illustrative(mission_id: str) -> None:
    assert "illustrative" in mission(mission_id).description


def test_cancelling_a_finished_task_leaves_it_completed() -> None:
    scenario = mission("agriculture", "small")
    late = replace(scenario.events[0], time=100000.0)
    result = _run(replace(scenario, events=(late,)))
    assert result.task_status[late.task_id].value == "completed"
