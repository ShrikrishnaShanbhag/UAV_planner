"""Descriptive statistics and paired significance tests."""

from __future__ import annotations

import csv
import math
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from scipy import stats as scipy_stats

from uavplan.core.metrics import optimality_gap
from uavplan.experiments.ablation import FULL
from uavplan.experiments.runner import EXACT, PROPOSED

HIGHER_IS_BETTER = {
    "sim_completion_rate",
    "sim_completion_rate_mandatory",
    "sim_completion_rate_weighted",
    "sim_energy_feasibility_rate",
    "sim_min_battery_margin",
    "plan_plan_feasible",
}
SUMMARY_METRICS = [
    "sim_completion_rate",
    "sim_completion_rate_mandatory",
    "sim_completion_rate_weighted",
    "sim_total_energy",
    "sim_energy_per_completed_task",
    "sim_makespan",
    "sim_total_distance",
    "sim_energy_feasibility_rate",
    "sim_min_battery_margin",
    "sim_workload_cv",
    "sim_deadline_violations",
    "sim_stranded_uavs",
    "sim_depleted_uavs",
    "sim_margin_alerts",
    "sim_rth_events",
    "sim_energy_estimation_error",
    "sim_replans",
    "sim_replan_time_ms",
    "sim_tasks_moved_per_replan",
    "plan_objective_z",
    "plan_plan_feasible",
    "plan_compute_time",
    "plan_optimality_gap",
]
TEST_METRICS = [
    "sim_completion_rate",
    "sim_completion_rate_mandatory",
    "sim_completion_rate_weighted",
    "sim_energy_per_completed_task",
    "sim_makespan",
    "sim_energy_feasibility_rate",
    "sim_workload_cv",
    "plan_objective_z",
]
TEST_SCENARIOS = ("A", "B", "C", "D", "E")
ALPHA = 0.05


def bootstrap_ci(
    values: Sequence[float], resamples: int = 2000, seed: int = 0
) -> tuple[float, float]:
    """95% percentile bootstrap interval of the mean, reproducible for a seed."""
    data = np.asarray(values, dtype=float)
    if data.size == 0:
        return (math.nan, math.nan)
    rng = np.random.default_rng(seed)
    picks = rng.integers(0, data.size, (resamples, data.size))
    means = data[picks].mean(axis=1)
    return (float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5)))


def describe(values: Sequence[float]) -> dict[str, float]:
    data = np.asarray([v for v in values if not math.isnan(v)], dtype=float)
    if data.size == 0:
        return dict.fromkeys(
            ("n", "mean", "std", "median", "q1", "q3", "ci_low", "ci_high"), math.nan
        )
    low, high = bootstrap_ci(data.tolist())
    return {
        "n": float(data.size),
        "mean": float(data.mean()),
        "std": float(data.std(ddof=1)) if data.size > 1 else 0.0,
        "median": float(np.median(data)),
        "q1": float(np.percentile(data, 25)),
        "q3": float(np.percentile(data, 75)),
        "ci_low": low,
        "ci_high": high,
    }


def add_optimality_gaps(rows: list[dict[str, Any]]) -> None:
    """Add ``plan_optimality_gap``, but only where the exact solver proved optimality."""
    optimum = {
        (r["scenario"], r["seed"]): r["plan_objective_z"]
        for r in rows
        if r["algorithm"] == EXACT and r["plan_proven_optimal"] == 1.0
    }
    for row in rows:
        reference = optimum.get((row["scenario"], row["seed"]))
        row["plan_optimality_gap"] = (
            math.nan
            if reference is None or row["plan_violations"] > 0
            else optimality_gap(row["plan_objective_z"], reference)
        )


def summarize(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Mean, spread and confidence interval per scenario, algorithm, variant and noise."""
    groups: dict[tuple[str, str, str, float], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[(row["scenario"], row["algorithm"], row["variant"], row["noise"])].append(row)
    summary: list[dict[str, Any]] = []
    for (scenario, algorithm, variant, noise), members in sorted(groups.items()):
        for metric in SUMMARY_METRICS:
            values = [float(r[metric]) for r in members if metric in r]
            summary.append(
                {
                    "scenario": scenario,
                    "algorithm": algorithm,
                    "variant": variant,
                    "noise": noise,
                    "metric": metric,
                    **describe(values),
                }
            )
    return summary


@dataclass(frozen=True, slots=True)
class PairedTest:
    n: int
    wins: int
    ties: int
    losses: int
    p_value: float
    effect: float


def paired_test(a: Sequence[float], b: Sequence[float], higher_better: bool) -> PairedTest:
    """Wilcoxon signed-rank of ``a`` vs ``b`` with a rank-biserial effect size.

    ``effect`` is positive when ``a`` tends to be numerically larger, and wins count the
    pairs where ``a`` is better in the metric's own direction.
    """
    pairs = [(x, y) for x, y in zip(a, b, strict=True) if not (math.isnan(x) or math.isnan(y))]
    diffs = np.asarray([x - y for x, y in pairs], dtype=float)
    nonzero = diffs[np.abs(diffs) > 1e-9]
    better = nonzero > 0 if higher_better else nonzero < 0
    wins, losses = int(better.sum()), int((~better).sum())
    ties = len(pairs) - nonzero.size
    if nonzero.size == 0:
        return PairedTest(len(pairs), 0, ties, 0, 1.0, 0.0)
    ranks = scipy_stats.rankdata(np.abs(nonzero))
    positive = float(ranks[nonzero > 0].sum())
    negative = float(ranks[nonzero < 0].sum())
    effect = (positive - negative) / (positive + negative)
    p_value = float(scipy_stats.wilcoxon(nonzero, alternative="two-sided").pvalue)
    return PairedTest(len(pairs), wins, ties, losses, p_value, effect)


def holm(p_values: Sequence[float]) -> list[float]:
    """Holm–Bonferroni adjusted p-values."""
    order = sorted(range(len(p_values)), key=lambda i: p_values[i])
    adjusted = [1.0] * len(p_values)
    running = 0.0
    for rank, index in enumerate(order):
        running = max(running, (len(p_values) - rank) * p_values[index])
        adjusted[index] = min(running, 1.0)
    return adjusted


def _seed_values(
    rows: list[dict[str, Any]], scenario: str, algorithm: str, variant: str, metric: str
) -> dict[int, float]:
    return {
        r["seed"]: float(r[metric])
        for r in rows
        if r["scenario"] == scenario
        and r["algorithm"] == algorithm
        and r["variant"] == variant
        and r["noise"] == 0.0
    }


def significance(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """PROPOSED vs each baseline and ablation, paired by seed.

    Holm correction is applied across the comparisons within one scenario and metric.
    """
    comparisons = sorted(
        {
            (r["algorithm"], r["variant"])
            for r in rows
            if r["scenario"] in TEST_SCENARIOS
            and (r["algorithm"], r["variant"]) != (PROPOSED, FULL)
        }
    )
    results: list[dict[str, Any]] = []
    for scenario in TEST_SCENARIOS:
        for metric in TEST_METRICS:
            family: list[dict[str, Any]] = []
            proposed = _seed_values(rows, scenario, PROPOSED, FULL, metric)
            if not proposed:
                continue
            for algorithm, variant in comparisons:
                other = _seed_values(rows, scenario, algorithm, variant, metric)
                seeds = sorted(set(proposed) & set(other))
                if not seeds:
                    continue
                a, b = [proposed[s] for s in seeds], [other[s] for s in seeds]
                test = paired_test(a, b, metric in HIGHER_IS_BETTER)
                family.append(
                    {
                        "scenario": scenario,
                        "metric": metric,
                        "baseline": algorithm if variant == FULL else f"{algorithm}:{variant}",
                        "n": test.n,
                        "mean_proposed": float(np.nanmean(a)),
                        "mean_baseline": float(np.nanmean(b)),
                        "wins": test.wins,
                        "ties": test.ties,
                        "losses": test.losses,
                        "p_value": test.p_value,
                        "effect_size": test.effect,
                        "higher_is_better": metric in HIGHER_IS_BETTER,
                    }
                )
            for entry, adjusted in zip(family, holm([e["p_value"] for e in family]), strict=True):
                entry["p_holm"] = adjusted
                entry["significant"] = adjusted < ALPHA
            results.extend(family)
    return results


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
