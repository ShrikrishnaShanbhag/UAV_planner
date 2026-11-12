"""FastAPI application factory."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

import uavplan
from uavplan.api import ws
from uavplan.api.db import make_engine
from uavplan.api.deps import ApiError, clean
from uavplan.api.jobs import JobRegistry
from uavplan.api.routers import experiments, meta, plans, scenarios, simulations
from uavplan.api.sessions import SessionStore


def _error(status: int, code: str, message: str, details: object = None) -> JSONResponse:
    body = {"error": {"code": code, "message": message, "details": clean(details)}}
    return JSONResponse(body, status_code=status)


def create_app(db_url: str | None = None, results_dir: str | None = None) -> FastAPI:
    db_file = Path(os.environ.get("UAVPLAN_DB", "uavplan.db"))
    if db_url is None:
        db_file.parent.mkdir(parents=True, exist_ok=True)
    database = db_url or f"sqlite:///{db_file}"
    results = results_dir or os.environ.get("UAVPLAN_RESULTS", "data/results")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        app.state.jobs.shutdown()

    app = FastAPI(
        title=(
            "Optimizing Routing, Task Allocation, and Energy Management "
            "in Collaborative UAV Systems"
        ),
        version=uavplan.__version__,
        description=(
            "Energy-aware route planning and battery feasibility. All values are simulated and "
            "illustrative, in normalized energy units unless stated."
        ),
        lifespan=lifespan,
    )
    app.state.engine = make_engine(database)
    app.state.jobs = JobRegistry()
    app.state.sessions = SessionStore()
    app.state.results_dir = results
    Path(results).mkdir(parents=True, exist_ok=True)

    app.add_middleware(
        CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
    )

    @app.exception_handler(ApiError)
    async def on_api_error(_: Request, error: ApiError) -> JSONResponse:
        return _error(error.status, error.code, error.message, error.details)

    @app.exception_handler(RequestValidationError)
    async def on_validation_error(_: Request, error: RequestValidationError) -> JSONResponse:
        details = [
            {"loc": list(e["loc"]), "message": e["msg"], "type": e["type"]} for e in error.errors()
        ]
        return _error(422, "validation_error", "The request failed validation.", details)

    @app.exception_handler(HTTPException)
    async def on_http_error(_: Request, error: HTTPException) -> JSONResponse:
        return _error(error.status_code, "http_error", str(error.detail))

    for router in (
        meta.router,
        scenarios.router,
        plans.router,
        simulations.router,
        experiments.router,
    ):
        app.include_router(router)
    app.include_router(ws.router)
    return app


app = create_app()
