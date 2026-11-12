"""Request and response models. Domain objects reuse the core dataclasses directly."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from uavplan.core.cost import CostWeights, ObjectiveWeights
from uavplan.core.generator import Distribution
from uavplan.core.models import (
    FeasibilityReport,
    GridSpec,
    Obstacle,
    Plan,
    Scenario,
    Station,
    TerrainLayer,
    Wind,
)
from uavplan.core.routing.paths import LegPaths

Metrics = dict[str, float | None]
ReplanModeName = Literal["none", "local_only", "local_then_global", "always_global"]


class ErrorBody(BaseModel):
    code: str
    message: str
    details: Any = None


class ErrorResponse(BaseModel):
    error: ErrorBody


class ConfigField(BaseModel):
    name: str
    type: str
    default: Any


class AlgorithmMeta(BaseModel):
    name: str
    label: str
    description: str
    config_schema: list[ConfigField]


class PresetPreview(BaseModel):
    """Just enough of a scenario to draw a thumbnail."""

    grid: GridSpec
    terrain_layers: list[TerrainLayer]
    obstacles: list[Obstacle]
    stations: list[Station]
    task_cells: list[tuple[int, int]]
    wind: Wind | None = None


class PresetMeta(BaseModel):
    id: str
    name: str
    description: str
    category: str = ""
    story: str = ""
    icon: str = ""
    theme: str = ""
    difficulty: str = ""
    tags: list[str] = []
    energy_focus: str = ""
    allow_recharge: bool = False
    sizes: list[str] = ["standard"]
    n_uavs: int = 0
    n_tasks: int = 0
    n_events: int = 0
    preview: PresetPreview | None = None


class GenerateParams(BaseModel):
    n_uavs: int = Field(4, ge=1, le=20)
    n_tasks: int = Field(20, ge=0, le=200)
    width: int = Field(50, ge=5, le=200)
    height: int = Field(50, ge=5, le=200)
    cell_size: float = Field(20.0, gt=0)
    obstacle_density: float = Field(0.08, ge=0.0, le=0.6)
    n_no_fly: int = Field(1, ge=0, le=10)
    n_energy_zones: int = Field(2, ge=0, le=10)
    n_stations: int = Field(2, ge=1, le=10)
    distribution: Distribution = "uniform"
    battery_min: float = Field(100.0, gt=0)
    battery_max: float = Field(100.0, gt=0)
    deadline_fraction: float = Field(0.3, ge=0.0, le=1.0)
    n_urgent: int = Field(0, ge=0, le=50)
    fail_uav: bool = False


class GenerateRequest(BaseModel):
    """Give a ``preset`` id, or ``params`` for a random scenario."""

    preset: str | None = None
    preset_id: str | None = None
    size: Literal["small", "standard"] = "standard"
    params: GenerateParams | None = None
    seed: int = 42
    save: bool = False


class ScenarioSummary(BaseModel):
    id: str
    name: str
    n_uavs: int
    n_tasks: int
    updated_at: datetime


class ScenarioRef(BaseModel):
    """A problem to work on: a saved scenario's id, or a scenario inline."""

    scenario_id: str | None = None
    scenario: Scenario | None = None


class PlanRequest(ScenarioRef):
    algorithm: str = "integrated"
    config: dict[str, Any] = Field(default_factory=dict)
    weights: CostWeights | None = None
    objective: ObjectiveWeights | None = None
    reserve_ratio: float | None = Field(None, ge=0.0, le=0.9)


class PlanResult(BaseModel):
    """``plan`` is null when a large instance became the background job ``job_id``."""

    plan: Plan | None = None
    metrics: Metrics | None = None
    job_id: str | None = None


class CompareRequest(ScenarioRef):
    algorithms: list[str] = Field(min_length=1)
    weights: CostWeights | None = None
    objective: ObjectiveWeights | None = None
    reserve_ratio: float | None = Field(None, ge=0.0, le=0.9)


class CompareEntry(BaseModel):
    algorithm: str
    plan: Plan | None = None
    metrics: Metrics | None = None
    error: str | None = None


class CompareResult(BaseModel):
    scenario: Scenario
    results: list[CompareEntry]


class FeasibilityRequest(ScenarioRef):
    """``assignment`` maps each UAV id to its ordered task ids."""

    assignment: dict[str, list[str]]


class FeasibilityResult(BaseModel):
    report: FeasibilityReport


class LegRef(BaseModel):
    uav_id: str
    start: tuple[int, int]
    end: tuple[int, int]


class PathCompareRequest(ScenarioRef):
    """Legs to price under both path metrics."""

    legs: list[LegRef]


class PathTotals(BaseModel):
    distance: float
    energy: float


class PathCompareResult(BaseModel):
    legs: list[LegPaths]
    energy_optimal: PathTotals
    distance_optimal: PathTotals


class SimulationRequest(PlanRequest):
    noise_sigma: float = Field(0.0, ge=0.0, le=1.0)
    seed: int = 0
    replan_mode: ReplanModeName = "local_then_global"
    in_progress: Literal["finish", "abandon"] = "finish"
    early_rth: bool = False


class SimulationCreated(BaseModel):
    sim_id: str


class SimulationState(BaseModel):
    sim_id: str
    scenario: Scenario
    algorithm: str
    plan: Plan
    end_time: float
    injected_events: int
    metrics: Metrics
    log: list[dict[str, Any]]
    replans: list[dict[str, Any]]
    energy: list[dict[str, Any]]


class Timeline(BaseModel):
    end_time: float
    frames: list[dict[str, Any]]


class ExperimentRequest(BaseModel):
    scenarios: list[str] = Field(default=["A", "B", "C", "D", "E"], min_length=1)
    algorithms: list[str] = Field(default=["nearest", "greedy_energy", "independent", "integrated"])
    seeds: int = Field(5, ge=1, le=100)
    ablations: bool = False


class JobCreated(BaseModel):
    job_id: str


class JobInfo(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    kind: str
    status: Literal["queued", "running", "finished", "cancelled", "failed"]
    done: int
    total: int
    message: str = ""
    error: str | None = None
    result: Any = None


class ExperimentSummary(BaseModel):
    id: str
    status: str
    params: dict[str, Any]
    created_at: datetime


class ExperimentResults(BaseModel):
    id: str
    status: str
    summary: list[dict[str, Any]]
    stats: list[dict[str, Any]]
    pareto: list[dict[str, Any]]
    figures: list[str]
    report: str


class RunSummary(BaseModel):
    id: str
    kind: str
    scenario_id: str
    scenario_name: str
    algorithm: str
    completion_rate: float | None
    feasible: bool | None
    created_at: datetime


class Health(BaseModel):
    status: str
    version: str
