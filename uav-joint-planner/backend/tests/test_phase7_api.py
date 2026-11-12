"""Phase 7: REST and WebSocket integration tests."""

from __future__ import annotations

import io
import json
import time
import zipfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from uavplan.api.main import create_app
from uavplan.core.generator import preset
from uavplan.core.models import Event, EventType


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    app = create_app(f"sqlite:///{tmp_path}/test.db", str(tmp_path / "results"))
    with TestClient(app) as test_client:
        yield test_client
    app.state.engine.dispose()


def saved_demo(client: TestClient) -> dict:
    response = client.post("/api/scenarios/generate", json={"preset": "DEMO", "save": True})
    assert response.status_code == 200
    return response.json()


def wait_for_job(client: TestClient, job_id: str, *states: str, timeout: float = 120.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in states:
            return job
        time.sleep(0.2)
    raise AssertionError(f"job {job_id} did not reach {states}: {job}")


def error_code(response) -> str:
    return response.json()["error"]["code"]


# -- meta -------------------------------------------------------------------------------


def test_health(client: TestClient) -> None:
    assert client.get("/api/health").json()["status"] == "ok"


def test_meta_algorithms_and_presets(client: TestClient) -> None:
    algorithms = client.get("/api/meta/algorithms").json()
    names = {a["name"] for a in algorithms}
    assert {"nearest", "integrated", "ortools_vrp", "exact_cpsat"} <= names
    integrated = next(a for a in algorithms if a["name"] == "integrated")
    assert "use_insertion" in {f["name"] for f in integrated["config_schema"]}
    assert {p["id"] for p in client.get("/api/meta/presets").json()} >= {"A", "E", "DEMO"}


def test_openapi_lists_every_route(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]
    for path in ("/api/plans", "/api/plans/compare", "/api/feasibility/check", "/api/simulations",
                 "/api/experiments", "/api/jobs/{job_id}", "/api/scenarios/import"):  # fmt: skip
        assert path in paths


# -- scenarios --------------------------------------------------------------------------


def test_generate_is_deterministic_and_not_saved_by_default(client: TestClient) -> None:
    a = client.post("/api/scenarios/generate", json={"preset": "A", "seed": 5}).json()
    b = client.post("/api/scenarios/generate", json={"preset": "A", "seed": 5}).json()
    assert a == b
    assert client.get("/api/scenarios").json() == []


def test_generate_from_params(client: TestClient) -> None:
    body = {"params": {"n_uavs": 2, "n_tasks": 6, "distribution": "clustered"}, "seed": 1}
    scenario = client.post("/api/scenarios/generate", json=body).json()
    assert len(scenario["uavs"]) == 2 and len(scenario["tasks"]) == 6


def test_generate_rejects_bad_input(client: TestClient) -> None:
    assert (
        error_code(client.post("/api/scenarios/generate", json={"preset": "Z"})) == "unknown_preset"
    )
    bad = {"params": {"battery_min": 90, "battery_max": 50}}
    assert error_code(client.post("/api/scenarios/generate", json=bad)) == "invalid_params"
    response = client.post("/api/scenarios/generate", json={"params": {"n_uavs": 0}})
    assert response.status_code == 422 and error_code(response) == "validation_error"


def test_scenario_crud_cycle(client: TestClient) -> None:
    body = preset("A", 1).to_dict()
    created = client.post("/api/scenarios", json=body)
    assert created.status_code == 201
    scenario_id = created.json()["id"]
    assert client.get(f"/api/scenarios/{scenario_id}").json()["name"] == body["name"]
    listed = client.get("/api/scenarios").json()
    assert listed[0]["id"] == scenario_id and listed[0]["n_tasks"] == 10
    updated = {**created.json(), "name": "Renamed"}
    assert client.put(f"/api/scenarios/{scenario_id}", json=updated).json()["name"] == "Renamed"
    assert client.delete(f"/api/scenarios/{scenario_id}").status_code == 204
    missing = client.get(f"/api/scenarios/{scenario_id}")
    assert missing.status_code == 404 and error_code(missing) == "scenario_not_found"
    assert client.delete(f"/api/scenarios/{scenario_id}").status_code == 404
    assert client.put(f"/api/scenarios/{scenario_id}", json=updated).status_code == 404


def test_import_and_export_round_trip(client: TestClient) -> None:
    imported = client.post("/api/scenarios/import", json=preset("D", 2).to_dict()).json()
    response = client.get(f"/api/scenarios/{imported['id']}/export")
    assert "attachment" in response.headers["content-disposition"]
    exported = json.loads(response.content)
    assert exported["id"] == imported["id"]
    assert len(exported["events"]) == 5


def test_validation_rejects_a_task_inside_an_obstacle(client: TestClient) -> None:
    body = preset("A", 1).to_dict()
    body["obstacles"].append({"id": "x", "kind": "obstacle", "cells": [body["tasks"][0]["cell"]]})
    response = client.post("/api/scenarios", json=body)
    assert response.status_code == 422 and error_code(response) == "invalid_scenario"
    assert any("inside an obstacle" in d for d in response.json()["error"]["details"])


def test_validation_rejects_battery_below_reserve_and_unknown_capability(
    client: TestClient,
) -> None:
    body = preset("A", 1).to_dict()
    body["uavs"][0]["battery"] = 5.0
    body["tasks"][0]["capability"] = "sonar"
    details = " ".join(client.post("/api/scenarios", json=body).json()["error"]["details"])
    assert "above the reserve" in details and "no UAV has" in details


# -- plans ------------------------------------------------------------------------------


def test_plan_saved_scenario(client: TestClient) -> None:
    demo = saved_demo(client)
    result = client.post("/api/plans", json={"scenario_id": demo["id"]}).json()
    assert result["plan"]["algorithm"] == "integrated"
    assert result["plan"]["report"]["feasible"] is True
    assert result["metrics"]["completion_rate"] > 0
    assert result["plan"]["explanations"]
    runs = client.get("/api/runs").json()
    assert runs[0]["kind"] == "plan" and runs[0]["algorithm"] == "integrated"


def test_plan_inline_scenario_with_weights_and_reserve(client: TestClient) -> None:
    body = {
        "scenario": preset("A", 2).to_dict(),
        "algorithm": "integrated",
        "weights": {"w_d": 0.0, "w_e": 1.0, "w_t": 0.0, "w_l": 0.0},
        "reserve_ratio": 0.3,
        "config": {"use_lns": False},
    }
    result = client.post("/api/plans", json=body).json()
    assert result["plan"]["config"]["weights"]["w_e"] == 1.0
    assert result["plan"]["routes"][0]["reserve"] == pytest.approx(30.0)


def test_plan_is_reproducible_through_the_api(client: TestClient) -> None:
    body = {"scenario": preset("B", 2).to_dict(), "algorithm": "integrated"}
    a, b = client.post("/api/plans", json=body).json(), client.post("/api/plans", json=body).json()
    a["plan"].pop("compute_time_s"), b["plan"].pop("compute_time_s")
    a["metrics"].pop("compute_time"), b["metrics"].pop("compute_time")
    assert a == b


def test_plan_errors(client: TestClient) -> None:
    assert error_code(client.post("/api/plans", json={})) == "missing_scenario"
    body = {"scenario": preset("A", 1).to_dict(), "algorithm": "nope"}
    assert error_code(client.post("/api/plans", json=body)) == "unknown_algorithm"
    body = {"scenario": preset("A", 1).to_dict(), "config": {"nope": 1}}
    assert error_code(client.post("/api/plans", json=body)) == "unknown_config"
    body = {"scenario": preset("B", 1).to_dict(), "algorithm": "exact_cpsat"}
    assert error_code(client.post("/api/plans", json=body)) == "algorithm_rejected_instance"
    assert client.post("/api/plans", json={"scenario_id": "nope"}).status_code == 404


def test_large_plan_becomes_a_job(client: TestClient) -> None:
    params = {"n_uavs": 9, "n_tasks": 50}
    scenario = client.post("/api/scenarios/generate", json={"params": params, "seed": 1}).json()
    response = client.post("/api/plans", json={"scenario": scenario, "algorithm": "nearest"})
    assert response.status_code == 202
    job_id = response.json()["job_id"]
    job = wait_for_job(client, job_id, "finished", "failed")
    assert job["status"] == "finished", job
    assert job["result"]["plan"]["algorithm"] == "nearest"


def test_compare_runs_algorithms_side_by_side(client: TestClient) -> None:
    body = {
        "scenario": preset("B", 1).to_dict(),
        "algorithms": ["nearest", "integrated", "exact_cpsat"],
    }
    result = client.post("/api/plans/compare", json=body).json()
    entries = {r["algorithm"]: r for r in result["results"]}
    assert entries["integrated"]["metrics"]["completion_rate"] > 0
    assert entries["exact_cpsat"]["error"] and entries["exact_cpsat"]["plan"] is None


def test_feasibility_check_reports_violations(client: TestClient) -> None:
    demo = saved_demo(client)
    good = client.post(
        "/api/feasibility/check",
        json={"scenario_id": demo["id"], "assignment": {"Scout-1": ["T1"]}},
    )
    assert good.json()["report"]["feasible"] is True
    bad_body = {"scenario_id": demo["id"], "assignment": {"Scout-1": ["T9"]}}  # T9 needs payload
    report = client.post("/api/feasibility/check", json=bad_body).json()["report"]
    assert report["feasible"] is False
    assert report["violations"][0]["type"] == "CAPABILITY"


def test_feasibility_rejects_unknown_ids(client: TestClient) -> None:
    demo = saved_demo(client)
    body = {"scenario_id": demo["id"], "assignment": {"Ghost": ["T1"], "Scout-1": ["T99"]}}
    response = client.post("/api/feasibility/check", json=body)
    assert error_code(response) == "invalid_assignment"
    details = response.json()["error"]["details"]
    assert details == {"unknown_tasks": ["T99"], "unknown_uavs": ["Ghost"]}


# -- simulations ------------------------------------------------------------------------


def make_simulation(client: TestClient, **extra) -> str:
    demo = saved_demo(client)
    response = client.post("/api/simulations", json={"scenario_id": demo["id"], **extra})
    assert response.status_code == 201
    return response.json()["sim_id"]


def test_simulation_state_and_timeline(client: TestClient) -> None:
    sim_id = make_simulation(client)
    state = client.get(f"/api/simulations/{sim_id}").json()
    assert state["end_time"] > 0 and state["plan"]["algorithm"] == "integrated"
    assert state["metrics"]["completion_rate"] == 100.0
    timeline = client.get(f"/api/simulations/{sim_id}/timeline", params={"max_frames": 50}).json()
    assert 2 <= len(timeline["frames"]) <= 51
    assert client.get("/api/simulations/nope").status_code == 404
    assert client.get("/api/simulations/nope/timeline").status_code == 404


def test_simulation_without_replanning_or_with_baseline(client: TestClient) -> None:
    assert make_simulation(client, replan_mode="none")
    assert make_simulation(client, algorithm="greedy_energy")


def test_websocket_play_seek_inject(client: TestClient) -> None:
    sim_id = make_simulation(client)
    with client.websocket_connect(f"/ws/simulations/{sim_id}") as socket:
        first = socket.receive_json()
        assert first["t"] == 0.0 and first["playing"] is False
        assert {u["id"] for u in first["uavs"]} >= {"Scout-1", "Carrier-3"}

        socket.send_json({"cmd": "speed", "payload": {"value": 100}})
        socket.send_json({"cmd": "play"})
        frame = socket.receive_json()
        assert frame["t"] > 0 and frame["playing"] is True
        socket.send_json({"cmd": "pause"})

        socket.send_json({"cmd": "seek", "payload": {"t": 50.0}})
        while True:
            frame = socket.receive_json()
            if frame.get("seeked"):
                break
        assert frame["t"] == 50.0

        socket.send_json(
            {"cmd": "inject_event", "payload": {"type": "UAV_UNAVAILABLE", "uav_id": "Scout-1"}}
        )
        while True:
            frame = socket.receive_json()
            if "replan" in frame:
                break
        assert frame["replan"]["diff"]["event_type"] == "UAV_UNAVAILABLE"
        assert frame["replan"]["time_ms"] > 0
        assert "Scout-1" not in {r["uav_id"] for r in frame["replan"]["plan"]["routes"]}
        assert any(e["kind"] == "REPLAN" for e in frame["events"])
    state = client.get(f"/api/simulations/{sim_id}").json()
    assert state["injected_events"] == 1
    assert state["replans"][0]["event_type"] == "UAV_UNAVAILABLE"


def test_websocket_injects_a_new_task_and_blocked_region(client: TestClient) -> None:
    sim_id = make_simulation(client)
    with client.websocket_connect(f"/ws/simulations/{sim_id}") as socket:
        socket.receive_json()
        task = {
            "id": "N1",
            "cell": [25, 30],
            "priority": 4,
            "capability": "camera",
            "mandatory": True,
        }
        socket.send_json(
            {"cmd": "inject_event", "payload": {"type": "NEW_TASK", "task": task, "time": 20.0}}
        )
        frame = socket.receive_json()
        assert frame["replan"]["diff"]["event_type"] == "NEW_TASK"
        cells = [[x, 22] for x in range(10, 40)]
        socket.send_json(
            {
                "cmd": "inject_event",
                "payload": {"type": "BLOCK_REGION", "cells": cells, "time": 40.0},
            }
        )
        frame = socket.receive_json()
        assert frame["replan"]["diff"]["event_type"] == "BLOCK_REGION"


def test_websocket_replay_keeps_the_past_identical(client: TestClient) -> None:
    sim_id = make_simulation(client)
    session = client.app.state.sessions.get(sim_id)
    before = session.result.sample("Scout-1", 10.0)
    session.inject(Event(EventType.UAV_UNAVAILABLE, 30.0, uav_id="Carrier-3"))
    assert session.result.sample("Scout-1", 10.0) == before
    assert session.plan_at(5.0) is session.plan
    assert session.plan_at(31.0) is session.result.replans[0].plan


def test_websocket_errors(client: TestClient) -> None:
    with client.websocket_connect("/ws/simulations/nope") as socket:
        assert socket.receive_json()["type"] == "error"
    sim_id = make_simulation(client)
    with client.websocket_connect(f"/ws/simulations/{sim_id}") as socket:
        socket.receive_json()
        socket.send_json({"cmd": "dance"})
        assert "unknown command" in socket.receive_json()["message"]
        socket.send_json({"cmd": "inject_event", "payload": {"type": "NOPE"}})
        assert socket.receive_json()["type"] == "error"


# -- experiments and jobs ---------------------------------------------------------------


def test_experiment_results_and_download(client: TestClient) -> None:
    body = {"scenarios": ["A"], "algorithms": ["nearest", "integrated"], "seeds": 2}
    response = client.post("/api/experiments", json=body)
    assert response.status_code == 202
    job_id = response.json()["job_id"]
    job = wait_for_job(client, job_id, "finished", "failed", "cancelled")
    assert job["status"] == "finished", job
    assert job["done"] == job["total"] == 4

    results = client.get(f"/api/experiments/{job_id}/results").json()
    assert results["status"] == "finished"
    assert results["summary"] and results["stats"]
    assert "Where the integrated method does not help" in results["report"]
    assert "gantt_demo.png" in results["figures"]
    figure = client.get(f"/api/experiments/{job_id}/figures/gantt_demo.png")
    assert figure.status_code == 200 and figure.headers["content-type"] == "image/png"
    assert client.get(f"/api/experiments/{job_id}/figures/nope.png").status_code == 404

    archive = zipfile.ZipFile(io.BytesIO(client.get(f"/api/experiments/{job_id}/download").content))
    assert {"raw.csv", "stats.csv", "REPORT.md"} <= set(archive.namelist())
    assert client.get("/api/experiments").json()[0]["id"] == job_id


def test_job_cancellation(client: TestClient) -> None:
    body = {"scenarios": ["A", "B"], "algorithms": ["nearest", "integrated"], "seeds": 40}
    job_id = client.post("/api/experiments", json=body).json()["job_id"]
    wait_for_job(client, job_id, "running")
    cancelled = client.delete(f"/api/jobs/{job_id}")
    assert cancelled.status_code == 200
    job = wait_for_job(client, job_id, "cancelled", "finished", "failed")
    assert job["status"] == "cancelled"
    assert job["done"] < job["total"]
    assert client.get(f"/api/experiments/{job_id}/results").json()["status"] == "cancelled"


def test_experiment_and_job_errors(client: TestClient) -> None:
    response = client.post("/api/experiments", json={"scenarios": ["Q"], "algorithms": ["nope"]})
    assert error_code(response) == "invalid_experiment"
    assert client.get("/api/jobs/nope").status_code == 404
    assert client.delete("/api/jobs/nope").status_code == 404
    assert client.get("/api/experiments/nope/results").status_code == 404
    assert client.get("/api/experiments/nope/download").status_code == 404


def test_unknown_route_uses_the_error_shape(client: TestClient) -> None:
    response = client.get("/api/nope")
    assert response.status_code == 404 and error_code(response) == "http_error"


# -- energy management ------------------------------------------------------------------


def test_simulation_reports_planned_and_actual_energy(client: TestClient) -> None:
    sim_id = make_simulation(client)
    energy = client.get(f"/api/simulations/{sim_id}").json()["energy"]
    assert {e["uav_id"] for e in energy} >= {"Scout-1", "Carrier-3"}
    for entry in energy:
        assert entry["planned"] == pytest.approx(entry["actual"], abs=1e-6)
        assert entry["margin_alerts"] == 0


def test_simulation_accepts_early_rth_and_plan_accepts_recharge(client: TestClient) -> None:
    assert make_simulation(client, early_rth=True, noise_sigma=0.3)
    body = {"scenario": preset("C", 1).to_dict(), "config": {"allow_recharge": True}}
    plan = client.post("/api/plans", json=body).json()["plan"]
    assert plan["config"]["allow_recharge"] is True
    assert plan["report"]["feasible"] is True


def test_compare_paths_endpoint(client: TestClient) -> None:
    demo = saved_demo(client)
    legs = [{"uav_id": "Scout-1", "start": [8, 15], "end": [10, 30]}]
    result = client.post(
        "/api/paths/compare", json={"scenario_id": demo["id"], "legs": legs}
    ).json()
    assert result["energy_optimal"]["energy"] <= result["distance_optimal"]["energy"] + 1e-9
    assert result["distance_optimal"]["distance"] <= result["energy_optimal"]["distance"] + 1e-9
    assert result["legs"][0]["energy_optimal"]["cells"][0] == [8, 15]


def test_compare_paths_rejects_bad_input(client: TestClient) -> None:
    demo = saved_demo(client)
    bad = [{"uav_id": "Ghost", "start": [8, 15], "end": [10, 30]}]
    assert (
        error_code(client.post("/api/paths/compare", json={"scenario_id": demo["id"], "legs": bad}))
        == "invalid_legs"
    )
    blocked = [{"uav_id": "Scout-1", "start": [12, 10], "end": [10, 30]}]
    assert (
        error_code(
            client.post("/api/paths/compare", json={"scenario_id": demo["id"], "legs": blocked})
        )
        == "invalid_legs"
    )
