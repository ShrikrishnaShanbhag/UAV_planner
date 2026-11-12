"""Planning, comparison and feasibility checks."""

from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor
from typing import Any

from fastapi import APIRouter, Request

from uavplan.api.deps import (
    ApiError,
    build_config,
    engine_of,
    jobs_of,
    record_run,
    resolve_scenario,
    respond,
    run_planner,
    to_json,
)
from uavplan.api.jobs import Job
from uavplan.api.schemas import (
    CompareEntry,
    CompareRequest,
    CompareResult,
    ErrorResponse,
    FeasibilityRequest,
    FeasibilityResult,
    PathCompareRequest,
    PathCompareResult,
    PlanRequest,
    PlanResult,
)
from uavplan.core.allocation.base import PlannerConfig
from uavplan.core.energy import EnergyEstimator
from uavplan.core.environment import EnvironmentManager
from uavplan.core.feasibility import FeasibilityChecker
from uavplan.core.metrics import plan_metrics
from uavplan.core.models import Scenario
from uavplan.core.routing.paths import compare_leg_paths

router = APIRouter(prefix="/api", tags=["plans"])
ERRORS = {404: {"model": ErrorResponse}, 422: {"model": ErrorResponse}}
# Instances above this many task-UAV pairs run as a background job.
SYNC_LIMIT = 400


def _plan_payload(scenario: Scenario, algorithm: str, config: PlannerConfig) -> dict[str, Any]:
    plan = run_planner(scenario, algorithm, config)
    return {"plan": to_json(plan), "metrics": to_json(plan_metrics(plan, scenario))}


@router.post("/plans", response_model=PlanResult, responses={**ERRORS, 202: {"model": PlanResult}})
def create_plan(body: PlanRequest, request: Request):
    scenario = resolve_scenario(request, body, body.reserve_ratio)
    config = build_config(body.algorithm, body.config, body.weights, body.objective)
    if len(scenario.tasks) * len(scenario.uavs) > SYNC_LIMIT:

        def work(job: Job) -> dict[str, Any]:
            job.progress(0, 1)
            with ProcessPoolExecutor(max_workers=1) as pool:
                payload = pool.submit(_plan_payload, scenario, body.algorithm, config).result()
            job.progress(1, 1)
            return payload

        job = jobs_of(request).submit("plan", work)
        return respond({"plan": None, "metrics": None, "job_id": job.id}, 202)

    plan = run_planner(scenario, body.algorithm, config)
    metrics = plan_metrics(plan, scenario)
    record_run(
        engine_of(request), "plan", scenario, body.algorithm, plan, metrics["completion_rate"]
    )
    return respond({"plan": plan, "metrics": metrics, "job_id": None})


@router.post("/plans/compare", response_model=CompareResult, responses=ERRORS)
def compare_plans(body: CompareRequest, request: Request):
    scenario = resolve_scenario(request, body, body.reserve_ratio)
    results: list[CompareEntry | dict[str, Any]] = []
    for algorithm in body.algorithms:
        config = build_config(algorithm, None, body.weights, body.objective)
        try:
            results.append({"algorithm": algorithm, **_plan_payload(scenario, algorithm, config)})
        except ApiError as error:
            results.append(
                {"algorithm": algorithm, "plan": None, "metrics": None, "error": error.message}
            )
    return respond({"scenario": scenario, "results": results})


@router.post("/paths/compare", response_model=PathCompareResult, responses=ERRORS)
def compare_paths(body: PathCompareRequest, request: Request):
    """Price the same legs along energy-optimal and shortest-distance paths."""
    scenario = resolve_scenario(request, body)
    env = EnvironmentManager.from_scenario(scenario)
    estimator = EnergyEstimator.for_scenario(scenario)
    uavs = {u.id: u for u in scenario.uavs}
    unknown = sorted({leg.uav_id for leg in body.legs} - set(uavs))
    if unknown:
        raise ApiError(422, "invalid_legs", f"Unknown UAVs: {', '.join(unknown)}.")
    legs = []
    for leg in body.legs:
        paths = compare_leg_paths(env, estimator, uavs[leg.uav_id], leg.start, leg.end)
        if paths is None:
            raise ApiError(422, "invalid_legs", f"No path from {leg.start} to {leg.end}.")
        legs.append(paths)
    totals = {
        name: {
            "distance": sum(getattr(leg, name).distance for leg in legs),
            "energy": sum(getattr(leg, name).energy for leg in legs),
        }
        for name in ("energy_optimal", "distance_optimal")
    }
    return respond({"legs": legs, **totals})


@router.post("/feasibility/check", response_model=FeasibilityResult, responses=ERRORS)
def check_feasibility(body: FeasibilityRequest, request: Request):
    scenario = resolve_scenario(request, body)
    checker = FeasibilityChecker(scenario)
    tasks = {t.id for t in scenario.tasks}
    unknown = sorted({t for seq in body.assignment.values() for t in seq} - tasks)
    if unknown or set(body.assignment) - set(checker.uavs):
        raise ApiError(
            422,
            "invalid_assignment",
            "The assignment refers to unknown UAVs or tasks.",
            {
                "unknown_tasks": unknown,
                "unknown_uavs": sorted(set(body.assignment) - set(checker.uavs)),
            },
        )
    assignment = {uav: tuple(seq) for uav, seq in body.assignment.items()}
    return respond({"report": checker.check_assignment(assignment)})
