"""Metadata and health endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Request
from sqlmodel import Session, col, select

import uavplan
from uavplan.api.db import RunRow
from uavplan.api.deps import config_schema, engine_of, respond
from uavplan.api.schemas import AlgorithmMeta, Health, PresetMeta, RunSummary
from uavplan.core.generator import PRESET_IDS, preset
from uavplan.core.missions import MISSION_IDS, SIZES
from uavplan.core.models import Scenario, ScenarioMeta
from uavplan.core.registry import ALGORITHMS

router = APIRouter(prefix="/api", tags=["meta"])


@router.get("/health", response_model=Health)
def health() -> Health:
    return Health(status="ok", version=uavplan.__version__)


@router.get("/meta/algorithms", response_model=list[AlgorithmMeta])
def algorithms():
    return respond(
        [
            {
                "name": info.name,
                "label": info.label,
                "description": info.description,
                "config_schema": config_schema(info.config_type),
            }
            for info in ALGORITHMS.values()
        ]
    )


def _preset_item(preset_id: str, scenario: Scenario, sizes: tuple[str, ...]) -> dict[str, object]:
    item: dict[str, object] = {
        "id": preset_id,
        "name": scenario.name,
        "description": scenario.description,
        "sizes": list(sizes),
        "n_uavs": len(scenario.uavs),
        "n_tasks": len(scenario.tasks),
        "n_events": len(scenario.events),
        "preview": {
            "grid": scenario.grid,
            "terrain_layers": scenario.terrain_layers,
            "obstacles": scenario.obstacles,
            "stations": scenario.stations,
            "task_cells": [task.cell for task in scenario.tasks],
            "wind": scenario.wind,
        },
    }
    meta = scenario.meta or ScenarioMeta(preset_id, scenario.name)
    item.update(
        category=meta.category,
        story=meta.story,
        icon=meta.icon,
        theme=meta.theme,
        difficulty=meta.difficulty if scenario.meta else "",
        tags=list(meta.tags),
        energy_focus=meta.energy_focus,
        allow_recharge=meta.allow_recharge,
    )
    return item


@router.get("/meta/presets", response_model=list[PresetMeta])
def presets():
    """Missions first, then the benchmark presets (which have no mission metadata)."""
    items = [_preset_item(m, preset(m), SIZES) for m in MISSION_IDS]
    items += [_preset_item(p, preset(p), ("standard",)) for p in PRESET_IDS]
    return respond(items)


@router.get("/runs", response_model=list[RunSummary])
def recent_runs(request: Request, limit: int = 10):
    with Session(engine_of(request)) as session:
        statement = select(RunRow).order_by(col(RunRow.created_at).desc()).limit(limit)
        rows = session.exec(statement).all()
    return respond(
        [
            {
                "id": r.id,
                "kind": r.kind,
                "scenario_id": r.scenario_id,
                "scenario_name": r.scenario_name,
                "algorithm": r.algorithm,
                "completion_rate": r.completion_rate,
                "feasible": r.feasible,
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ]
    )
