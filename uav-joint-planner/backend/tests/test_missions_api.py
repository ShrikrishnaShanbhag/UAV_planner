"""Missions through the API, the CLI and the experiment runner."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from uavplan.api.main import create_app
from uavplan.cli import main
from uavplan.core.generator import PRESET_IDS
from uavplan.core.missions import MISSION_IDS, mission
from uavplan.core.models import Scenario
from uavplan.experiments.ablation import FULL
from uavplan.experiments.report import build_report
from uavplan.experiments.runner import (
    RunSpec,
    build_scenario,
    build_specs,
    execute,
    planner_config,
    read_rows,
    run_experiments,
)


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    app = create_app(f"sqlite:///{tmp_path}/test.db", str(tmp_path / "results"))
    with TestClient(app) as test_client:
        yield test_client
    app.state.engine.dispose()


def test_presets_endpoint_lists_every_mission_with_its_metadata(client: TestClient) -> None:
    items = client.get("/api/meta/presets").json()
    assert [i["id"] for i in items] == [*MISSION_IDS, *PRESET_IDS]
    missions = items[: len(MISSION_IDS)]
    for item in missions:
        for field in ("name", "category", "description", "story", "icon", "theme", "energy_focus"):
            assert item[field], (item["id"], field)
        assert item["difficulty"] in ("easy", "medium", "hard")
        assert item["tags"]
        assert item["sizes"] == ["small", "standard"]
        assert item["n_uavs"] and item["n_tasks"] and item["n_events"]
        assert item["preview"]["terrain_layers"]
        assert item["preview"]["task_cells"]
        assert item["preview"]["stations"]


def test_benchmark_presets_stay_in_the_list_without_a_category(client: TestClient) -> None:
    items = {i["id"]: i for i in client.get("/api/meta/presets").json()}
    for preset_id in PRESET_IDS:
        assert items[preset_id]["category"] == ""
        assert items[preset_id]["sizes"] == ["standard"]


def test_generate_accepts_preset_id_size_and_seed(client: TestClient) -> None:
    body = {"preset_id": "flood", "size": "small", "seed": 7}
    response = client.post("/api/scenarios/generate", json=body)
    assert response.status_code == 200
    data = response.json()
    assert Scenario.from_dict(data) == mission("flood", "small", 7)
    assert data["meta"]["category"] == "Disaster"
    assert data["terrain_layers"]


def test_generate_keeps_the_old_preset_field(client: TestClient) -> None:
    assert client.post("/api/scenarios/generate", json={"preset": "A"}).json()["id"] == "A"
    assert client.post("/api/scenarios/generate", json={"preset": "wildfire"}).json()["wind"]


def test_generate_rejects_unknown_mission_and_size(client: TestClient) -> None:
    missing = client.post("/api/scenarios/generate", json={"preset_id": "atlantis"})
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "unknown_preset"
    bad = client.post("/api/scenarios/generate", json={"preset_id": "flood", "size": "huge"})
    assert bad.status_code == 422


def test_a_saved_mission_can_be_planned_and_keeps_its_fields(client: TestClient) -> None:
    body = {"preset_id": "cyclone", "size": "small", "save": True}
    saved = client.post("/api/scenarios/generate", json=body).json()
    loaded = client.get(f"/api/scenarios/{saved['id']}").json()
    assert loaded["wind"] == saved["wind"] and loaded["meta"] == saved["meta"]
    result = client.post("/api/plans", json={"scenario_id": saved["id"]}).json()
    assert result["plan"]["report"]["feasible"]


def test_a_mission_can_be_simulated_through_the_api(client: TestClient) -> None:
    body = {"preset_id": "landslide", "size": "small", "save": True}
    saved = client.post("/api/scenarios/generate", json=body).json()
    response = client.post("/api/simulations", json={"scenario_id": saved["id"]})
    assert response.status_code in (200, 201)


def test_runner_builds_missions_and_specs() -> None:
    assert build_scenario(RunSpec("gas_leak", 3, "integrated")) == mission(
        "gas_leak", "standard", 3
    )
    specs = build_specs(["flood", "crowd"], ["nearest", "integrated"], 2)
    assert len(specs) == 8


def test_only_missions_that_ask_for_it_get_charging_stops() -> None:
    assert planner_config(RunSpec("powerline", 0, "integrated")).allow_recharge  # type: ignore[attr-defined]
    assert not planner_config(RunSpec("flood", 0, "integrated")).allow_recharge  # type: ignore[attr-defined]


def test_execute_records_a_mission_row() -> None:
    row = execute(RunSpec("flood", 0, "integrated", FULL))
    assert row["scenario"] == "flood"
    assert row["sim_replans"] == 2


def test_mission_report_has_the_results_by_mission_type(tmp_path: Path) -> None:
    specs = build_specs(["crowd", "landslide"], ["nearest", "integrated"], 2)
    run_experiments(specs, tmp_path, workers=1)
    text = build_report(tmp_path).read_text(encoding="utf-8")
    assert "Results by mission type" in text
    assert "Large-event crowd safety" in text and "Landslide on a hill road" in text
    assert "illustrative" in text
    assert "of 2." in text
    assert (tmp_path / "figures" / "missions_sim_completion_rate.png").exists()
    assert len(read_rows(tmp_path / "raw.csv")) == 8


def test_powerline_report_flags_that_only_the_proposed_method_recharges(tmp_path: Path) -> None:
    run_experiments(build_specs(["powerline"], ["nearest", "integrated"], 1), tmp_path, workers=1)
    assert "cannot" in build_report(tmp_path).read_text(encoding="utf-8")


def test_cli_expands_the_missions_keyword(tmp_path: Path) -> None:
    code = main(
        [
            "experiment",
            "--scenarios",
            "missions",
            "--algorithms",
            "nearest",
            "--seeds",
            "1",
            "--out",
            str(tmp_path),
            "--name",
            "m",
            "--workers",
            "1",
        ]  # fmt: skip
    )
    assert code == 0
    assert {r["scenario"] for r in read_rows(tmp_path / "m" / "raw.csv")} == set(MISSION_IDS)
