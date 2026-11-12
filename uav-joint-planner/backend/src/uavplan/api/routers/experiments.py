"""Experiment batches and background jobs."""

from __future__ import annotations

import csv
import io
import json
import zipfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, Response
from sqlmodel import Session, col, select

from uavplan.api.db import ExperimentRow
from uavplan.api.deps import ApiError, engine_of, jobs_of, new_id, respond
from uavplan.api.jobs import Job
from uavplan.api.schemas import (
    ErrorResponse,
    ExperimentRequest,
    ExperimentResults,
    ExperimentSummary,
    JobCreated,
    JobInfo,
)
from uavplan.core.registry import ALGORITHMS
from uavplan.experiments.report import build_report
from uavplan.experiments.runner import build_specs, run_experiments

router = APIRouter(prefix="/api", tags=["experiments"])
ERRORS = {404: {"model": ErrorResponse}, 422: {"model": ErrorResponse}}
KNOWN_SCENARIOS = {"A", "B", "C", "D", "E", "F", "G"}


def _job_payload(job: Job) -> dict[str, Any]:
    return {
        "id": job.id,
        "kind": job.kind,
        "status": job.status,
        "done": job.done,
        "total": job.total,
        "message": job.message,
        "error": job.error,
        "result": job.result if job.kind == "plan" else None,
    }


def _experiment(request: Request, experiment_id: str) -> ExperimentRow:
    with Session(engine_of(request)) as session:
        row = session.get(ExperimentRow, experiment_id)
    if row is None:
        raise ApiError(404, "experiment_not_found", f"No experiment '{experiment_id}'.")
    return row


@router.post("/experiments", response_model=JobCreated, status_code=202, responses=ERRORS)
def start_experiment(body: ExperimentRequest, request: Request):
    unknown = sorted(set(body.scenarios) - KNOWN_SCENARIOS) + sorted(
        set(body.algorithms) - set(ALGORITHMS)
    )
    if unknown:
        raise ApiError(422, "invalid_experiment", f"Unknown names: {', '.join(unknown)}.")
    specs = build_specs(body.scenarios, body.algorithms, body.seeds, body.ablations)
    experiment_id = new_id()
    out_dir = Path(request.app.state.results_dir) / experiment_id
    engine = engine_of(request)
    with Session(engine) as session:
        session.add(
            ExperimentRow(
                id=experiment_id,
                status="queued",
                params=body.model_dump_json(),
                out_dir=str(out_dir),
            )
        )
        session.commit()

    def work(job: Job) -> None:
        run_experiments(specs, out_dir, progress=job.progress, cancel=job.cancel)
        if not job.cancel.is_set():
            job.message = "building report"
            build_report(out_dir)
            job.message = "finished"

    def finished(job: Job) -> None:
        with Session(engine) as session:
            row = session.get(ExperimentRow, experiment_id)
            if row is not None:
                row.status = job.status
                session.add(row)
                session.commit()

    jobs_of(request).submit("experiment", work, finished, job_id=experiment_id)
    return respond({"job_id": experiment_id}, 202)


@router.get("/experiments", response_model=list[ExperimentSummary])
def list_experiments(request: Request):
    with Session(engine_of(request)) as session:
        rows = session.exec(
            select(ExperimentRow).order_by(col(ExperimentRow.created_at).desc())
        ).all()
    return respond(
        [
            {
                "id": r.id,
                "status": r.status,
                "params": json.loads(r.params),
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ]
    )


@router.get("/jobs/{job_id}", response_model=JobInfo, responses=ERRORS)
def get_job(job_id: str, request: Request):
    job = jobs_of(request).get(job_id)
    if job is None:
        raise ApiError(404, "job_not_found", f"No job '{job_id}'.")
    return respond(_job_payload(job))


@router.delete("/jobs/{job_id}", response_model=JobInfo, responses=ERRORS)
def cancel_job(job_id: str, request: Request):
    job = jobs_of(request).cancel(job_id)
    if job is None:
        raise ApiError(404, "job_not_found", f"No job '{job_id}'.")
    return respond(_job_payload(job))


def _read_csv(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


@router.get(
    "/experiments/{experiment_id}/results", response_model=ExperimentResults, responses=ERRORS
)
def experiment_results(experiment_id: str, request: Request):
    row = _experiment(request, experiment_id)
    folder = Path(row.out_dir)
    report = folder / "REPORT.md"
    figures = sorted(p.name for p in (folder / "figures").glob("*.png")) if folder.exists() else []
    return respond(
        {
            "id": experiment_id,
            "status": row.status,
            "summary": _read_csv(folder / "summary.csv"),
            "stats": _read_csv(folder / "stats.csv"),
            "pareto": _read_csv(folder / "pareto.csv"),
            "figures": figures,
            "report": report.read_text(encoding="utf-8") if report.exists() else "",
        }
    )


@router.get("/experiments/{experiment_id}/figures/{name}", responses=ERRORS)
def experiment_figure(experiment_id: str, name: str, request: Request) -> FileResponse:
    folder = (Path(_experiment(request, experiment_id).out_dir) / "figures").resolve()
    path = (folder / name).resolve()
    if path.parent != folder or not path.is_file():
        raise ApiError(404, "figure_not_found", f"No figure '{name}'.")
    return FileResponse(path)


@router.get("/experiments/{experiment_id}/download", responses=ERRORS)
def download_experiment(experiment_id: str, request: Request) -> Response:
    folder = Path(_experiment(request, experiment_id).out_dir)
    if not folder.exists():
        raise ApiError(404, "results_not_found", "This experiment has no results yet.")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(folder.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(folder).as_posix())
    return Response(
        buffer.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="experiment-{experiment_id}.zip"'},
    )
