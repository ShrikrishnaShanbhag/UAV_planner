"""Phase 5: events, replanning modes and the simulator's dynamic behaviour."""

from __future__ import annotations

import itertools
import json
import time as clock
from dataclasses import replace

import pytest

from conftest import make_scenario, make_task, make_uav
from uavplan.core.allocation.base import PlanningContext
from uavplan.core.generator import preset
from uavplan.core.metrics import simulation_metrics
from uavplan.core.models import Event, EventType, Plan, Priority, Scenario, Station, UAVStatus
from uavplan.core.registry import get_planner
from uavplan.core.replanning import ReplanConfig
from uavplan.core.simulator import SimConfig, Simulator
from uavplan.core.state import TaskStatus


def manual_plan(scenario: Scenario, assignment: dict[str, tuple[str, ...]]) -> Plan:
    ctx = PlanningContext(scenario)
    full = {u: assignment.get(u, ()) for u in ctx.uav_ids}
    placed = {t for seq in full.values() for t in seq}
    left = {t: "manual" for t in ctx.tasks if t not in placed}
    return ctx.build_plan("manual", full, left, clock.perf_counter())


def simple_scenario(events: tuple[Event, ...] = ()) -> Scenario:
    uavs = [make_uav("U1"), make_uav("U2", start=(19, 19))]
    tasks = [make_task("T1", (5, 5)), make_task("T2", (10, 3)), make_task("T3", (15, 15))]
    stations = [Station("S1", (0, 0)), Station("S2", (19, 19))]
    return make_scenario(uavs, tasks, stations, events=events)


def run(scenario: Scenario, assignment, config: SimConfig | None = None):
    return Simulator(scenario, manual_plan(scenario, assignment), config).run()


# -- UAV failure ------------------------------------------------------------------------


def test_after_uav_unavailable_none_of_its_tasks_stay_assigned() -> None:
    event = Event(EventType.UAV_UNAVAILABLE, 2.0, uav_id="U1")
    scenario = simple_scenario((event,))
    result = run(scenario, {"U1": ("T1", "T2"), "U2": ("T3",)})
    plan = result.replans[0].plan
    assert "U1" not in plan.assignment()
    assert set(plan.assignment()["U2"]) == {"T1", "T2", "T3"}
    assert result.status_after["U1"] == UAVStatus.UNAVAILABLE
    assert all(s == TaskStatus.COMPLETED for s in result.task_status.values())
    work_by_u1 = [s for s in result.segments["U1"] if s.kind == "work"]
    assert not work_by_u1


def test_failed_uav_stops_where_it_was_and_keeps_only_distance_flown() -> None:
    event = Event(EventType.UAV_UNAVAILABLE, 2.0, uav_id="U1")
    result = run(simple_scenario((event,)), {"U1": ("T1",), "U2": ()})
    outcome = result.outcomes["U1"]
    assert 0 < outcome.distance <= 2.0 * 10.0 + 1e-6
    assert result.sample("U1", 100.0)["status"] == "unavailable"


def test_failure_during_a_task_returns_it_to_the_pool() -> None:
    event = Event(EventType.UAV_UNAVAILABLE, 5.0, uav_id="U1")
    uavs = [make_uav("U1", start=(1, 0)), make_uav("U2")]
    tasks = [make_task("T1", (1, 0), duration=50.0)]
    scenario = make_scenario(uavs, tasks, events=(event,))
    result = run(scenario, {"U1": ("T1",)})
    assert result.task_status["T1"] == TaskStatus.COMPLETED
    assert [s.task_id for s in result.segments["U2"] if s.kind == "work"] == ["T1"]


# -- blocked regions --------------------------------------------------------------------


def wall(top: int = 12) -> Event:
    cells = tuple((8, y) for y in range(top + 1))
    return Event(EventType.BLOCK_REGION, 1.0, cells=cells)


def test_after_block_region_no_route_crosses_it() -> None:
    event = wall()
    uav = make_uav(start=(0, 5), energy_per_meter=0.005)
    scenario = make_scenario(
        [uav], [make_task("T", (15, 5))], [Station("S1", (0, 5))], events=(event,)
    )
    result = run(scenario, {"U1": ("T",)})
    blocked = set(event.cells or ())
    plan = result.replans[0].plan
    assert plan.report.feasible
    for route in plan.routes:
        for leg in route.legs:
            assert not blocked.intersection(leg.path)
    flown = {c for s in result.segments["U1"] for c in s.cells}
    assert not blocked.intersection(flown)
    assert result.task_status["T"] == TaskStatus.COMPLETED


def test_block_with_polygon_uses_rasterized_cells() -> None:
    polygon = ((7.0, 0.0), (9.0, 0.0), (9.0, 12.0), (7.0, 12.0))
    event = Event(EventType.BLOCK_REGION, 1.0, polygon=polygon)
    uav = make_uav(start=(0, 5), energy_per_meter=0.005)
    scenario = make_scenario(
        [uav], [make_task("T", (15, 5))], [Station("S1", (0, 5))], events=(event,)
    )
    result = run(scenario, {"U1": ("T",)})
    flown = {c for s in result.segments["U1"] for c in s.cells}
    assert (8, 5) not in flown
    assert result.task_status["T"] == TaskStatus.COMPLETED


def test_task_cut_off_by_a_block_is_reported_unassigned() -> None:
    cells = tuple((8, y) for y in range(20))
    event = Event(EventType.BLOCK_REGION, 1.0, cells=cells)
    uav = make_uav(start=(0, 5), energy_per_meter=0.005)
    scenario = make_scenario(
        [uav], [make_task("T", (15, 5))], [Station("S1", (0, 5))], events=(event,)
    )
    result = run(scenario, {"U1": ("T",)})
    assert [u.task_id for u in result.replans[0].plan.unassigned] == ["T"]
    assert result.task_status["T"] == TaskStatus.FAILED


def test_uav_caught_inside_a_block_escapes() -> None:
    event = Event(EventType.BLOCK_REGION, 0.5, cells=((1, 0), (2, 0), (3, 0)))
    scenario = simple_scenario((event,))
    result = run(scenario, {"U1": ("T1",)})
    assert (
        any(e.kind == "ESCAPED_BLOCK" for e in result.log)
        or result.task_status["T1"] == TaskStatus.COMPLETED
    )


def test_escape_when_uav_is_working_inside_a_new_block() -> None:
    event = Event(
        EventType.BLOCK_REGION, 5.0, cells=((5, 5), (5, 6), (6, 5), (6, 6), (4, 5), (5, 4))
    )
    uav = make_uav(start=(5, 5), airborne=True)
    scenario = make_scenario(
        [uav], [make_task("T", (5, 5), duration=30.0), make_task("U", (10, 10))], events=(event,)
    )
    result = run(scenario, {"U1": ("T", "U")})
    assert any(e.kind == "ESCAPED_BLOCK" for e in result.log)
    assert result.task_status["T"] == TaskStatus.COMPLETED
    assert result.task_status["U"] == TaskStatus.COMPLETED


def test_without_replanning_a_blocked_route_halts_the_uav() -> None:
    event = wall(19)
    uav = make_uav(start=(0, 5), energy_per_meter=0.005)
    scenario = make_scenario(
        [uav], [make_task("T", (15, 5))], [Station("S1", (0, 5))], events=(event,)
    )
    result = run(scenario, {"U1": ("T",)}, SimConfig(replan=None))
    assert any(e.kind == "ROUTE_BLOCKED" for e in result.log)
    assert result.task_status["T"] == TaskStatus.FAILED
    assert not result.outcomes["U1"].landed


# -- battery drop -----------------------------------------------------------------------


def test_after_battery_drop_plan_is_feasible_again() -> None:
    event = Event(EventType.BATTERY_DROP, 2.0, uav_id="U1", amount=80.0)
    scenario = simple_scenario((event,))
    result = run(scenario, {"U1": ("T1", "T2"), "U2": ("T3",)})
    plan = result.replans[0].plan
    assert plan.report.feasible
    assert result.replans[0].moved or result.replans[0].dropped
    assert result.outcomes["U1"].landed


def test_unrecoverable_battery_drop_is_reported() -> None:
    event = Event(EventType.BATTERY_DROP, 2.0, uav_id="U1", amount=99.5)
    uav = make_uav(start=(0, 0), energy_per_meter=0.1)
    scenario = make_scenario([uav], [make_task("T", (15, 0))], events=(event,))
    result = run(scenario, {"U1": ("T",)})
    assert not result.replans[0].plan.report.feasible
    assert result.replans[0].plan.report.violations


def test_battery_drop_without_replanning_can_deplete() -> None:
    event = Event(EventType.BATTERY_DROP, 2.0, uav_id="U1", amount=98.0)
    scenario = simple_scenario((event,))
    result = run(scenario, {"U1": ("T1", "T2")}, SimConfig(replan=None))
    assert result.outcomes["U1"].depleted


# -- escalation -------------------------------------------------------------------------


def escalation_scenario() -> tuple[Scenario, dict[str, tuple[str, ...]]]:
    """U3 fails. X then needs U2, whose two tasks must both move, beyond one repair move."""
    event = Event(EventType.UAV_UNAVAILABLE, 1.0, uav_id="U3")
    both = frozenset({"camera", "lidar"})
    uavs = [
        make_uav("U1", start=(19, 19), battery=100.0),
        make_uav("U2", battery=60.0, capabilities=both),
        make_uav("U3", start=(1, 0), capabilities=both),
    ]
    tasks = [
        make_task(
            "X", (3, 0), energy=38.0, capability="lidar", priority=Priority.CRITICAL, mandatory=True
        ),
        make_task("A", (0, 2), energy=12.0),
        make_task("B", (0, 3), energy=12.0),
    ]
    stations = [Station("S1", (0, 0)), Station("S2", (19, 19))]
    scenario = make_scenario(uavs, tasks, stations, events=(event,))
    return scenario, {"U2": ("A", "B"), "U3": ("X",)}


def test_escalation_restores_a_mandatory_task() -> None:
    scenario, assignment = escalation_scenario()
    result = run(scenario, assignment)
    replan = result.replans[0]
    assert replan.escalated and replan.scope == "global"
    assert replan.escalation_reason == "mandatory task left unassigned"
    assert "X" in replan.plan.assignment()["U2"]
    assert result.task_status["X"] == TaskStatus.COMPLETED


def test_local_only_never_escalates() -> None:
    scenario, assignment = escalation_scenario()
    result = run(scenario, assignment, SimConfig(replan=ReplanConfig(mode="local_only")))
    replan = result.replans[0]
    assert not replan.escalated and replan.scope == "local"
    assert "X" in {u.task_id for u in replan.plan.unassigned}


def test_always_global_replans_everything() -> None:
    scenario, assignment = escalation_scenario()
    result = run(scenario, assignment, SimConfig(replan=ReplanConfig(mode="always_global")))
    replan = result.replans[0]
    assert replan.scope == "global"
    assert "X" in replan.plan.assignment()["U2"]


def test_local_repair_is_enough_for_a_simple_failure() -> None:
    event = Event(EventType.UAV_UNAVAILABLE, 2.0, uav_id="U1")
    scenario = simple_scenario((event,))
    result = run(
        scenario,
        {"U1": ("T1",), "U2": ("T3",)},
        SimConfig(replan=ReplanConfig(check_suboptimal=False)),
    )
    assert result.replans[0].scope == "local"
    assert not result.replans[0].escalated


def test_suboptimal_threshold_triggers_escalation() -> None:
    event = Event(EventType.UAV_UNAVAILABLE, 2.0, uav_id="U1")
    scenario = simple_scenario((event,))
    tight = SimConfig(replan=ReplanConfig(suboptimal_threshold=0.0))
    result = run(scenario, {"U1": ("T1", "T2"), "U2": ("T3",)}, tight)
    assert result.replans[0].escalation_reason == "plan became materially suboptimal"


# -- other events -----------------------------------------------------------------------


def test_new_task_is_completed_with_replanning_and_not_without() -> None:
    task = make_task("N", (2, 2), priority=Priority.CRITICAL, release_time=3.0)
    event = Event(EventType.NEW_TASK, 3.0, task=task)
    scenario = simple_scenario((event,))
    with_replan = run(scenario, {"U1": ("T1",), "U2": ("T3",)})
    assert with_replan.task_status["N"] == TaskStatus.COMPLETED
    # T2 was left out of the manual plan, so the replan picks it up as well.
    assert with_replan.replans[0].added == ("N", "T2")
    without = run(scenario, {"U1": ("T1",), "U2": ("T3",)}, SimConfig(replan=None))
    assert without.task_status["N"] == TaskStatus.FAILED
    assert simulation_metrics(without, scenario)["completion_rate"] < 100.0


def test_task_cancelled_is_removed() -> None:
    event = Event(EventType.TASK_CANCELLED, 2.0, task_id="T2")
    scenario = simple_scenario((event,))
    result = run(scenario, {"U1": ("T1", "T2")})
    assert result.task_status["T2"] == TaskStatus.CANCELLED
    assert "T2" not in result.replans[0].plan.assignment()["U1"]
    # The cancelled task leaves the denominator, and the replan picks up T3.
    assert simulation_metrics(result, scenario)["completion_rate"] == 100.0


def test_cancelled_task_is_not_credited_without_replanning() -> None:
    event = Event(EventType.TASK_CANCELLED, 1.0, task_id="T1")
    scenario = simple_scenario((event,))
    result = run(scenario, {"U1": ("T1",)}, SimConfig(replan=None))
    assert result.task_status["T1"] == TaskStatus.CANCELLED


def test_duration_change_extends_the_work() -> None:
    event = Event(EventType.TASK_DURATION_CHANGE, 1.0, task_id="T1", new_duration=99.0)
    scenario = simple_scenario((event,))
    result = run(scenario, {"U1": ("T1",)})
    work = next(s for s in result.segments["U1"] if s.kind == "work")
    assert work.t1 - work.t0 == pytest.approx(99.0)


def test_duration_change_that_breaks_a_deadline_moves_or_drops_the_task() -> None:
    event = Event(EventType.TASK_DURATION_CHANGE, 1.0, task_id="T", new_duration=500.0)
    scenario = make_scenario(
        [make_uav()], [make_task("T", (2, 0), deadline=100.0)], events=(event,)
    )
    result = run(scenario, {"U1": ("T",)})
    assert result.replans[0].dropped == ("T",)


def test_uav_joining_takes_unassigned_work() -> None:
    newcomer = make_uav("U9", start=(10, 10), home="S1")
    event = Event(EventType.UAV_JOINS, 2.0, uav=newcomer)
    scenario = make_scenario(
        [make_uav(battery=11.0)], [make_task("T", (10, 10), energy=20.0)], events=(event,)
    )
    result = run(scenario, {})
    assert result.task_status["T"] == TaskStatus.COMPLETED
    assert result.outcomes["U9"].flew


def test_landed_uav_can_be_relaunched_for_a_new_task() -> None:
    task = make_task("N", (2, 0), release_time=500.0)
    event = Event(EventType.NEW_TASK, 500.0, task=task)
    uav = make_uav(takeoff_energy=1.0)
    scenario = make_scenario([uav], [make_task("T", (1, 0))], events=(event,))
    result = run(scenario, {"U1": ("T",)})
    assert result.task_status["N"] == TaskStatus.COMPLETED
    assert [s.kind for s in result.segments["U1"]].count("takeoff") == 2


def test_abandon_policy_returns_task_in_progress_to_the_pool() -> None:
    event = Event(EventType.NEW_TASK, 5.0, task=make_task("N", (3, 0), priority=Priority.LOW))
    uav = make_uav(start=(1, 0), airborne=True)
    scenario = make_scenario([uav], [make_task("T", (1, 0), duration=50.0)], events=(event,))
    result = run(scenario, {"U1": ("T",)}, SimConfig(in_progress="abandon"))
    works = [s for s in result.segments["U1"] if s.kind == "work" and s.task_id == "T"]
    assert len(works) == 2  # abandoned once, then redone


def test_finish_policy_completes_task_in_progress() -> None:
    event = Event(EventType.NEW_TASK, 5.0, task=make_task("N", (3, 0)))
    uav = make_uav(start=(1, 0), airborne=True)
    scenario = make_scenario([uav], [make_task("T", (1, 0), duration=50.0)], events=(event,))
    result = run(scenario, {"U1": ("T",)})
    assert len([s for s in result.segments["U1"] if s.task_id == "T" and s.kind == "work"]) == 1


# -- simulator integrity ----------------------------------------------------------------


@pytest.mark.parametrize("preset_id", ["D", "E"])
@pytest.mark.parametrize("seed", range(5))
def test_battery_is_continuous_across_replans(preset_id: str, seed: int) -> None:
    scenario = preset(preset_id, seed)
    plan = get_planner("integrated").plan(scenario)
    result = Simulator(scenario, plan).run()
    assert result.replans
    for segments in result.segments.values():
        for a, b in itertools.pairwise(segments):
            assert b.t0 >= a.t1 - 1e-9
            assert b.b0 == pytest.approx(a.b1, abs=1e-9)
    assert all(r.plan.report.feasible for r in result.replans)


@pytest.mark.parametrize("preset_id", ["D", "E"])
def test_dynamic_simulation_is_deterministic(preset_id: str) -> None:
    scenario = preset(preset_id, 3)
    plan = get_planner("integrated").plan(scenario)

    def body() -> str:
        data = Simulator(scenario, plan).run().to_dict()
        for replan in data["replans"]:
            replan.pop("time_ms")
        for entry in data["log"]:
            if entry["kind"] == "REPLAN":
                entry.pop("detail")
        return json.dumps(data, sort_keys=True)

    assert body() == body()


def test_live_injection_between_advances() -> None:
    scenario = simple_scenario()
    sim = Simulator(scenario, manual_plan(scenario, {"U1": ("T1",), "U2": ("T3",)}))
    sim.advance(3.0)
    assert sim.now == 3.0
    task = make_task("N", (2, 2), priority=Priority.CRITICAL, release_time=3.0)
    # An event dated in the past takes effect immediately.
    sim.inject(Event(EventType.NEW_TASK, 0.0, task=task))
    sim.advance(3.0)
    assert len(sim.replans) == 1
    result = sim.run()
    assert result.task_status["N"] == TaskStatus.COMPLETED
    metrics = simulation_metrics(result, scenario)
    assert metrics["replans"] == 1.0
    assert metrics["completion_rate"] == 100.0  # includes the live-injected task


def test_replan_records_diff_and_timing() -> None:
    event = Event(EventType.UAV_UNAVAILABLE, 2.0, uav_id="U1")
    scenario = simple_scenario((event,))
    result = run(scenario, {"U1": ("T1",), "U2": ("T3",)})
    replan = result.replans[0]
    assert replan.time_ms > 0
    assert [(m.task_id, m.from_uav, m.to_uav) for m in replan.moved] == [("T1", "U1", "U2")]
    assert replan.tasks_moved == 1
    assert "U1" in replan.affected_uavs and "T1" in replan.affected_tasks
    assert result.to_dict()["replans"][0]["scope"] == replan.scope


@pytest.mark.parametrize(
    "name", ["nearest", "greedy_energy", "independent", "ortools_vrp", "genetic"]
)
def test_baselines_replan_through_the_same_interface(name: str) -> None:
    scenario = preset("E", 2)
    plan = get_planner(name).plan(scenario)
    config = SimConfig(replan=ReplanConfig(mode="always_global", planner=name))
    result = Simulator(scenario, plan, config).run()
    assert result.replans and result.replans[0].scope == "global"


def test_replanning_beats_no_replanning_on_a_uav_failure() -> None:
    event = Event(EventType.UAV_UNAVAILABLE, 2.0, uav_id="U1")
    scenario = replace(simple_scenario((event,)))
    assignment = {"U1": ("T1", "T2"), "U2": ("T3",)}
    with_replan = simulation_metrics(run(scenario, assignment), scenario)
    without = simulation_metrics(run(scenario, assignment, SimConfig(replan=None)), scenario)
    assert with_replan["completion_rate"] > without["completion_rate"]
