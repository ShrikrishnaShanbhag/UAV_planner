#!/usr/bin/env python3
"""Cross-platform task runner. The Makefile delegates here, so Windows users without
`make` can run the same targets: ``python scripts/dev.py test``.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
NPM = shutil.which("npm") or "npm"
UV = shutil.which("uv") or "uv"


def run(cmd: list[str], cwd: Path) -> None:
    print(f"$ ({cwd.name}) {' '.join(cmd)}", flush=True)
    env = {**os.environ, "PYTHONUTF8": "1"}
    code = subprocess.call(cmd, cwd=cwd, env=env)
    if code != 0:
        sys.exit(code)


def uv_run(*args: str) -> None:
    run([UV, "run", "--python", "3.12", *args], BACKEND)


def setup() -> None:
    run([UV, "sync", "--python", "3.12", "--all-extras"], BACKEND)
    run([NPM, "install"], FRONTEND)


def test_backend() -> None:
    uv_run(
        "pytest", "--cov=uavplan.core", "--cov-report=term:skip-covered", "--cov-fail-under=85"
    )


def test_frontend() -> None:
    run([NPM, "run", "test"], FRONTEND)


def test() -> None:
    test_backend()
    test_frontend()


def lint() -> None:
    uv_run("ruff", "check", ".")
    uv_run("ruff", "format", "--check", ".")
    uv_run("mypy")
    run([NPM, "run", "typecheck"], FRONTEND)


def fmt() -> None:
    uv_run("ruff", "check", "--fix", ".")
    uv_run("ruff", "format", ".")


def run_api() -> None:
    uv_run("uvicorn", "uavplan.api.main:app", "--reload", "--port", "8000")


def run_web() -> None:
    run([NPM, "run", "dev"], FRONTEND)


def experiments_quick() -> None:
    uv_run(
        "uavplan", "experiment", "--scenarios", "A,B,C,D,E", "--algorithms", "all",
        "--ablations", "--seeds", "5", "--out", "../results", "--name", "quick", "--report",
    )


def experiments_full() -> None:
    uv_run(
        "uavplan", "experiment", "--scenarios", "A,B,C,D,E,F,G", "--algorithms", "all",
        "--ablations", "--seeds", "30", "--out", "../results", "--name", "full", "--report",
    )


def experiments_scenarios() -> None:
    uv_run(
        "uavplan", "experiment", "--scenarios", "missions",
        "--algorithms", "nearest,greedy_energy,independent,integrated",
        "--seeds", "5", "--out", "../results", "--name", "scenarios", "--report",
    )


def e2e() -> None:
    run([NPM, "run", "e2e"], FRONTEND)


def openapi() -> None:
    uv_run("python", "-m", "uavplan.api.export_openapi", "../frontend/openapi.json", "../docs/API.md")
    run([NPM, "run", "gen:api"], FRONTEND)


def docker() -> None:
    run(["docker", "compose", "up", "--build"], ROOT)


TARGETS = {
    "setup": setup,
    "test": test,
    "test-backend": test_backend,
    "test-frontend": test_frontend,
    "lint": lint,
    "fmt": fmt,
    "run-api": run_api,
    "run-web": run_web,
    "experiments": experiments_quick,
    "experiments-quick": experiments_quick,
    "experiments-full": experiments_full,
    "experiments-scenarios": experiments_scenarios,
    "report": experiments_quick,
    "e2e": e2e,
    "openapi": openapi,
    "docker": docker,
}


def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] not in TARGETS:
        print(f"usage: python scripts/dev.py [{'|'.join(TARGETS)}]")
        sys.exit(2)
    TARGETS[sys.argv[1]]()


if __name__ == "__main__":
    main()
