"""Batch experiment runner: parallel across processes, seeded, resumable, cancellable."""

from __future__ import annotations

import csv
import math
import os
import re
import threading
from collections.abc import Callable, Iterable
from concurrent.futures import FIRST_COMPLETED, Future, ProcessPoolExecutor, wait
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from uavplan.core.allocation.base import PlannerConfig
from uavplan.core.allocation.exact_cpsat import ExactConfig
from uavplan.core.allocation.genetic import GeneticConfig
from uavplan.core.allocation.ortools_vrp import ORToolsConfig
from uavplan.core.generator import (
    NOISE_LEVELS,
    SCALING_TASKS,
    SCALING_UAVS,
    noise_scenario,
    preset,
    scaling_scenario,
)
from uavplan.core.metrics import plan_metrics, simulation_metrics
from uavplan.core.missions import MISSION_IDS, mission_meta
from uavplan.core.models import Scenario
from uavplan.core.registry import get_planner
from uavplan.core.replanning import ReplanConfig
from uavplan.core.simulator import SimConfig, Simulator
from uavplan.experiments.ablation import (
    DYNAMIC_ABLATIONS,
    FULL,
    STATIC_ABLATIONS,
    integrated_config,
)

Progress = Callable[[int, int], None]

CORE_ALGORITHMS = (
    "nearest",
    "greedy_energy",
    "independent",
    "integrated",
    "ortools_vrp",
    "genetic",
)
EXACT = "exact_cpsat"
ALL_ALGORITHMS = (*CORE_ALGORITHMS, EXACT)
PROPOSED = "integrated"
DYNAMIC_SCENARIOS = ("D", "E")
SCALING_ALGORITHMS = ("nearest", "greedy_energy", "independent", "integrated", "ortools_vrp")
NOISE_ALGORITHMS = ("greedy_energy", "independent", "integrated")
SCALING_MAX_SEEDS = 5

META_COLUMNS = ["key", "scenario", "seed", "algorithm", "variant", "noise", "n_uavs", "n_tasks"]
PLAN_METRICS = [
    "total_energy", "makespan", "total_distance", "energy_feasibility_rate", "min_battery_margin",
    "unassigned", "deadline_violations", "violations", "plan_feasible", "compute_time",
    "objective_z", "completion_rate", "completion_rate_mandatory", "completion_rate_weighted",
    "workload_cv", "workload_range", "energy_per_completed_task", "proven_optimal",
]  # fmt: skip
SIM_METRICS = [
    "total_energy", "makespan", "total_distance", "energy_feasibility_rate", "min_battery_margin",
    "unassigned", "deadline_violations", "stranded_uavs", "depleted_uavs", "replans",
    "replan_time_ms", "tasks_moved_per_replan", "completion_rate", "completion_rate_mandatory",
    "completion_rate_weighted", "workload_cv", "workload_range", "energy_per_completed_task",
    "margin_alerts", "energy_estimation_error", "rth_events",
]  # fmt: skip
COLUMNS = [*META_COLUMNS, *(f"plan_{m}" for m in PLAN_METRICS), *(f"sim_{m}" for m in SIM_METRICS)]
_TEXT_COLUMNS = {"key", "scenario", "algorithm", "variant"}


@dataclass(frozen=True, slots=True, order=True)
class RunSpec:
    """One run: a scenario instance, an algorithm and an experimental variant."""

    scenario: str
    seed: int
    algorithm: str
    variant: str = FULL
    noise: float = 0.0

    @property
    def key(self) -> str:
        return f"{self.scenario}|{self.seed}|{self.algorithm}|{self.variant}|{self.noise}"


def build_scenario(spec: RunSpec) -> Scenario:
    match = re.fullmatch(r"F-(\d+)x(\d+)", spec.scenario)
    if match:
        return scaling_scenario(int(match[1]), int(match[2]), spec.seed)
    if spec.scenario == "G":
        return noise_scenario(spec.seed)
    return preset(spec.scenario, spec.seed)


def planner_config(spec: RunSpec) -> PlannerConfig:
    """Budgets are deterministic counts, so results do not depend on machine load."""
    if spec.algorithm == PROPOSED:
        config = integrated_config(spec.variant, spec.seed)
        if spec.scenario in MISSION_IDS and mission_meta(spec.scenario).allow_recharge:
            return replace(config, allow_recharge=True)
        return config
    if spec.algorithm == "ortools_vrp":
        return ORToolsConfig(seed=spec.seed)
    if spec.algorithm == "genetic":
        return GeneticConfig(seed=spec.seed)
    if spec.algorithm == EXACT:
        return ExactConfig(seed=spec.seed)
    return PlannerConfig(seed=spec.seed)


def replan_config(spec: RunSpec) -> ReplanConfig | None:
    """The proposed method repairs locally; baselines re-run themselves from the state."""
    if spec.variant in DYNAMIC_ABLATIONS:
        return DYNAMIC_ABLATIONS[spec.variant]
    if spec.algorithm == PROPOSED:
        return ReplanConfig()
    if spec.algorithm == EXACT:
        return None
    return ReplanConfig(mode="always_global", planner=spec.algorithm)


def execute(spec: RunSpec) -> dict[str, Any]:
    """Plan and simulate one run. A module-level function so that it can be pickled."""
    scenario = build_scenario(spec)
    config = planner_config(spec)
    plan = get_planner(spec.algorithm).plan(scenario, config)
    sim_config = SimConfig(noise_sigma=spec.noise, seed=spec.seed, replan=replan_config(spec))
    result = Simulator(scenario, plan, sim_config, config).run()
    row: dict[str, Any] = {
        "key": spec.key,
        "scenario": spec.scenario,
        "seed": spec.seed,
        "algorithm": spec.algorithm,
        "variant": spec.variant,
        "noise": spec.noise,
        "n_uavs": len(scenario.uavs),
        "n_tasks": len(scenario.tasks),
    }
    planned = plan_metrics(plan, scenario)
    planned["proven_optimal"] = float(bool(plan.config.get("proven_optimal", False)))
    row.update({f"plan_{k}": v for k, v in planned.items()})
    row.update({f"sim_{k}": v for k, v in simulation_metrics(result, scenario).items()})
    return row


def build_specs(
    scenarios: Iterable[str],
    algorithms: Iterable[str],
    seeds: int,
    ablations: bool = False,
) -> list[RunSpec]:
    """Every algorithm receives the same scenario instances and event sequences."""
    chosen = list(algorithms)
    specs: list[RunSpec] = []
    for scenario in scenarios:
        if scenario == "F":
            for n_uavs in SCALING_UAVS:
                for n_tasks in SCALING_TASKS:
                    for algorithm in (a for a in SCALING_ALGORITHMS if a in chosen):
                        specs.extend(
                            RunSpec(f"F-{n_uavs}x{n_tasks}", seed, algorithm)
                            for seed in range(min(seeds, SCALING_MAX_SEEDS))
                        )
            continue
        if scenario == "G":
            for noise in NOISE_LEVELS:
                for algorithm in (a for a in NOISE_ALGORITHMS if a in chosen):
                    specs.extend(RunSpec("G", s, algorithm, noise=noise) for s in range(seeds))
            continue
        for algorithm in chosen:
            if algorithm == EXACT and scenario != "A":
                continue  # the exact model is only defined for small static instances
            specs.extend(RunSpec(scenario, s, algorithm) for s in range(seeds))
        if ablations and PROPOSED in chosen:
            variants = list(STATIC_ABLATIONS)
            if scenario in DYNAMIC_SCENARIOS:
                variants += list(DYNAMIC_ABLATIONS)
            for variant in variants:
                specs.extend(RunSpec(scenario, s, PROPOSED, variant) for s in range(seeds))
    return sorted(specs)


def read_rows(path: Path) -> list[dict[str, Any]]:
    """Load a ``raw.csv``, restoring numeric columns."""
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open(newline="", encoding="utf-8") as handle:
        for raw in csv.DictReader(handle):
            row: dict[str, Any] = {}
            for name, value in raw.items():
                if name in _TEXT_COLUMNS:
                    row[name] = value
                elif name in ("seed", "n_uavs", "n_tasks"):
                    row[name] = int(value)
                else:
                    row[name] = float(value) if value != "" else math.nan
            rows.append(row)
    return rows


def _write_sorted(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, COLUMNS, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        writer.writerows(sorted(rows, key=lambda r: str(r["key"])))


def run_experiments(
    specs: list[RunSpec],
    out_dir: Path,
    workers: int | None = None,
    progress: Progress | None = None,
    cancel: threading.Event | None = None,
) -> list[dict[str, Any]]:
    """Run the specs, appending to ``raw.csv`` so that an interrupted batch can resume."""
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "raw.csv"
    rows = read_rows(path)
    finished = {str(r["key"]) for r in rows}
    wanted = [s for s in specs if s.key not in finished]
    total = len(specs)
    done = total - len(wanted)
    if progress is not None:
        progress(done, total)
    if not path.exists():
        _write_sorted(path, [])
    max_workers = workers or max(1, (os.cpu_count() or 2) - 2)
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, COLUMNS, extrasaction="ignore", lineterminator="\n")
        with ProcessPoolExecutor(max_workers=max_workers) as pool:
            queued = iter(wanted)
            running: set[Future[dict[str, Any]]] = set()
            while True:
                stopping = cancel is not None and cancel.is_set()
                while not stopping and len(running) < max_workers * 2:
                    spec = next(queued, None)
                    if spec is None:
                        break
                    running.add(pool.submit(execute, spec))
                if not running:
                    break
                finished_now, running = wait(running, return_when=FIRST_COMPLETED)
                for future in finished_now:
                    row = future.result()
                    rows.append(row)
                    writer.writerow(row)
                    done += 1
                handle.flush()
                if progress is not None:
                    progress(done, total)
    if len(rows) == total or (cancel is None or not cancel.is_set()):
        _write_sorted(path, rows)
    return sorted(rows, key=lambda r: str(r["key"]))
