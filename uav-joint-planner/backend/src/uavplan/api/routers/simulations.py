"""Creating and inspecting simulations."""

from __future__ import annotations

from fastapi import APIRouter, Request

from uavplan.api.deps import (
    ApiError,
    build_config,
    engine_of,
    new_id,
    record_run,
    resolve_scenario,
    respond,
    run_planner,
    to_json,
)
from uavplan.api.schemas import (
    ErrorResponse,
    SimulationCreated,
    SimulationRequest,
    SimulationState,
    Timeline,
)
from uavplan.api.sessions import SimulationSession
from uavplan.core.metrics import simulation_metrics
from uavplan.core.replanning import ReplanConfig
from uavplan.core.simulator import SimConfig

router = APIRouter(prefix="/api/simulations", tags=["simulations"])
ERRORS = {404: {"model": ErrorResponse}, 422: {"model": ErrorResponse}}


def _session(request: Request, sim_id: str) -> SimulationSession:
    session: SimulationSession | None = request.app.state.sessions.get(sim_id)
    if session is None:
        raise ApiError(404, "simulation_not_found", f"No simulation '{sim_id}'.")
    return session


def _replan_config(body: SimulationRequest) -> ReplanConfig | None:
    if body.replan_mode == "none" or body.algorithm == "exact_cpsat":
        return None
    if body.algorithm == "integrated":
        return ReplanConfig(mode=body.replan_mode)
    return ReplanConfig(mode="always_global", planner=body.algorithm)


@router.post("", response_model=SimulationCreated, status_code=201, responses=ERRORS)
def create_simulation(body: SimulationRequest, request: Request):
    scenario = resolve_scenario(request, body, body.reserve_ratio)
    config = build_config(body.algorithm, body.config, body.weights, body.objective)
    plan = run_planner(scenario, body.algorithm, config)
    sim_config = SimConfig(
        noise_sigma=body.noise_sigma,
        seed=body.seed,
        in_progress=body.in_progress,
        early_rth=body.early_rth,
        replan=_replan_config(body),
    )
    session = SimulationSession(new_id(), scenario, body.algorithm, plan, config, sim_config)
    request.app.state.sessions.add(session)
    metrics = simulation_metrics(session.result, scenario)
    record_run(
        engine_of(request), "simulation", scenario, body.algorithm, plan, metrics["completion_rate"]
    )
    return respond({"sim_id": session.id}, 201)


@router.get("/{sim_id}", response_model=SimulationState, responses=ERRORS)
def get_simulation(sim_id: str, request: Request):
    return respond(_session(request, sim_id).state())


@router.get("/{sim_id}/timeline", response_model=Timeline, responses=ERRORS)
def get_timeline(sim_id: str, request: Request, tick: float = 1.0, max_frames: int = 2000):
    result = _session(request, sim_id).result
    frames = result.timeline(max(tick, 0.01), max(max_frames, 2))
    return respond({"end_time": result.end_time, "frames": to_json(frames)})
