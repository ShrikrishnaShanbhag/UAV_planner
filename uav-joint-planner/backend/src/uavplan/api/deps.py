"""Shared helpers for routers: errors, JSON output, scenario lookup and planner config."""

from __future__ import annotations

import dataclasses
import json
import math
import uuid
from collections.abc import Callable
from dataclasses import replace
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse
from sqlalchemy.engine import Engine
from sqlmodel import Session

from uavplan.api.db import RunRow, ScenarioRow
from uavplan.api.jobs import JobRegistry
from uavplan.api.schemas import ScenarioRef
from uavplan.core.allocation.base import PlannerConfig
from uavplan.core.cost import CostWeights, ObjectiveWeights
from uavplan.core.generator import validate_scenario, with_energy
from uavplan.core.models import Plan, Scenario, jsonable
from uavplan.core.registry import ALGORITHMS, get_planner


class ApiError(Exception):
    """Rendered as ``{"error": {"code", "message", "details"}}``."""

    def __init__(self, status: int, code: str, message: str, details: Any = None) -> None:
        super().__init__(message)
        self.status, self.code, self.message, self.details = status, code, message, details


def clean(value: Any) -> Any:
    """Replace NaN and infinity, which JSON cannot represent, with null."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {k: clean(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [clean(v) for v in value]
    return value


def to_json(value: Any) -> Any:
    return clean(jsonable(value))


def respond(value: Any, status: int = 200) -> JSONResponse:
    """Serialize through the core's deterministic layout."""
    return JSONResponse(to_json(value), status_code=status)


def engine_of(request: Request) -> Engine:
    engine: Engine = request.app.state.engine
    return engine


def jobs_of(request: Request) -> JobRegistry:
    jobs: JobRegistry = request.app.state.jobs
    return jobs


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def require_valid(scenario: Scenario) -> None:
    errors = validate_scenario(scenario)
    if errors:
        raise ApiError(422, "invalid_scenario", "The scenario failed validation.", errors)


def load_scenario(engine: Engine, scenario_id: str) -> Scenario:
    with Session(engine) as session:
        row = session.get(ScenarioRow, scenario_id)
    if row is None:
        raise ApiError(404, "scenario_not_found", f"No scenario with id '{scenario_id}'.")
    return Scenario.from_dict(json.loads(row.data))


def resolve_scenario(
    request: Request, body: ScenarioRef, reserve_ratio: float | None = None
) -> Scenario:
    if body.scenario is not None:
        scenario = body.scenario
    elif body.scenario_id is not None:
        scenario = load_scenario(engine_of(request), body.scenario_id)
    else:
        raise ApiError(422, "missing_scenario", "Provide either scenario_id or scenario.")
    if reserve_ratio is not None:
        scenario = with_energy(scenario, reserve_ratio=reserve_ratio)
    require_valid(scenario)
    return scenario


def build_config(
    algorithm: str,
    config: dict[str, Any] | None = None,
    weights: CostWeights | None = None,
    objective: ObjectiveWeights | None = None,
) -> PlannerConfig:
    """Turn request fields into the planner's own typed configuration."""
    if algorithm not in ALGORITHMS:
        raise ApiError(404, "unknown_algorithm", f"No algorithm named '{algorithm}'.")
    config_type = ALGORITHMS[algorithm].config_type
    allowed = {f.name for f in dataclasses.fields(config_type)} - {"weights", "objective", "scales"}
    values = dict(config or {})
    unknown = sorted(set(values) - allowed)
    if unknown:
        raise ApiError(422, "unknown_config", f"Unknown config fields: {', '.join(unknown)}.")
    result = config_type(**values)
    if weights is not None:
        result = replace(result, weights=weights)
    if objective is not None:
        result = replace(result, objective=objective)
    return result


def run_planner(scenario: Scenario, algorithm: str, config: PlannerConfig) -> Plan:
    try:
        return get_planner(algorithm).plan(scenario, config)
    except ValueError as error:
        raise ApiError(422, "algorithm_rejected_instance", str(error)) from error


def record_run(
    engine: Engine, kind: str, scenario: Scenario, algorithm: str, plan: Plan, completion: float
) -> None:
    with Session(engine) as session:
        session.add(
            RunRow(
                id=new_id(),
                kind=kind,
                scenario_id=scenario.id,
                scenario_name=scenario.name,
                algorithm=algorithm,
                completion_rate=completion,
                feasible=plan.report.feasible,
            )
        )
        session.commit()


def config_schema(config_type: type) -> list[dict[str, Any]]:
    """Fields a client may set for an algorithm, with their defaults."""
    fields = []
    for item in dataclasses.fields(config_type):
        if item.name in ("weights", "objective", "scales"):
            continue
        default: Any = item.default
        if isinstance(default, type(dataclasses.MISSING)):
            default = None
        fields.append({"name": item.name, "type": str(item.type), "default": default})
    return fields


Callback = Callable[..., Any]
