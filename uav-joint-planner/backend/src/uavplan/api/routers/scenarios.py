"""Scenario generation and CRUD."""

from __future__ import annotations

import json
from dataclasses import replace

from fastapi import APIRouter, Request
from fastapi.responses import Response
from sqlmodel import Session, col, select

from uavplan.api.db import ScenarioRow, utcnow
from uavplan.api.deps import ApiError, engine_of, load_scenario, new_id, require_valid, respond
from uavplan.api.schemas import ErrorResponse, GenerateRequest, ScenarioSummary
from uavplan.core.generator import GeneratorParams, generate, preset
from uavplan.core.models import Scenario

router = APIRouter(prefix="/api/scenarios", tags=["scenarios"])
ERRORS = {404: {"model": ErrorResponse}, 422: {"model": ErrorResponse}}


def _store(request: Request, scenario: Scenario, scenario_id: str | None = None) -> Scenario:
    require_valid(scenario)
    stored = replace(scenario, id=scenario_id or new_id())
    with Session(engine_of(request)) as session:
        row = session.get(ScenarioRow, stored.id)
        if row is None:
            row = ScenarioRow(id=stored.id, name=stored.name, data=stored.to_json())
        else:
            row.name, row.data, row.updated_at = stored.name, stored.to_json(), utcnow()
        session.add(row)
        session.commit()
    return stored


@router.post("/generate", response_model=Scenario, responses=ERRORS)
def generate_scenario(body: GenerateRequest, request: Request):
    preset_id = body.preset_id or body.preset
    if preset_id is not None:
        try:
            scenario = preset(preset_id, body.seed, body.size)
        except KeyError as error:
            raise ApiError(404, "unknown_preset", f"No preset '{preset_id}'.") from error
    else:
        values = body.params.model_dump() if body.params else {}
        if values.get("battery_min", 100.0) > values.get("battery_max", 100.0):
            raise ApiError(422, "invalid_params", "battery_min exceeds battery_max.")
        try:
            scenario = generate(GeneratorParams(**values), body.seed)
        except ValueError as error:
            raise ApiError(422, "invalid_params", str(error)) from error
    return respond(_store(request, scenario) if body.save else scenario)


@router.get("", response_model=list[ScenarioSummary])
def list_scenarios(request: Request):
    with Session(engine_of(request)) as session:
        rows = session.exec(select(ScenarioRow).order_by(col(ScenarioRow.updated_at).desc())).all()
    items = []
    for row in rows:
        data = json.loads(row.data)
        items.append(
            {
                "id": row.id,
                "name": row.name,
                "n_uavs": len(data["uavs"]),
                "n_tasks": len(data["tasks"]),
                "updated_at": row.updated_at.isoformat(),
            }
        )
    return respond(items)


@router.post("", response_model=Scenario, status_code=201, responses=ERRORS)
def create_scenario(body: Scenario, request: Request):
    return respond(_store(request, body), 201)


@router.post("/import", response_model=Scenario, status_code=201, responses=ERRORS)
def import_scenario(body: Scenario, request: Request):
    return respond(_store(request, body), 201)


@router.get("/{scenario_id}", response_model=Scenario, responses=ERRORS)
def get_scenario(scenario_id: str, request: Request):
    return respond(load_scenario(engine_of(request), scenario_id))


@router.put("/{scenario_id}", response_model=Scenario, responses=ERRORS)
def update_scenario(scenario_id: str, body: Scenario, request: Request):
    load_scenario(engine_of(request), scenario_id)
    return respond(_store(request, body, scenario_id))


@router.delete("/{scenario_id}", status_code=204, responses=ERRORS)
def delete_scenario(scenario_id: str, request: Request) -> Response:
    with Session(engine_of(request)) as session:
        row = session.get(ScenarioRow, scenario_id)
        if row is None:
            raise ApiError(404, "scenario_not_found", f"No scenario with id '{scenario_id}'.")
        session.delete(row)
        session.commit()
    return Response(status_code=204)


@router.get("/{scenario_id}/export", response_model=Scenario, responses=ERRORS)
def export_scenario(scenario_id: str, request: Request) -> Response:
    scenario = load_scenario(engine_of(request), scenario_id)
    return Response(
        scenario.to_json(),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{scenario.id}.json"'},
    )
