"""Builds ``REPORT.md``: every table and sentence below comes from the run's data."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np

from uavplan.core.missions import MISSION_IDS, mission_meta
from uavplan.core.registry import ALGORITHMS
from uavplan.experiments.ablation import (
    DYNAMIC_ABLATIONS,
    FULL,
    STATIC_ABLATIONS,
    VARIANT_LABELS,
    weight_sweep,
)
from uavplan.experiments.plots import make_figures, make_mission_figures
from uavplan.experiments.runner import (
    CORE_ALGORITHMS,
    EXACT,
    PROPOSED,
    read_rows,
)
from uavplan.experiments.stats import (
    ALPHA,
    HIGHER_IS_BETTER,
    add_optimality_gaps,
    significance,
    summarize,
    write_csv,
)

SCENARIOS = ("A", "B", "C", "D", "E")
METRIC_NAMES = {
    "sim_completion_rate": "completion rate",
    "sim_completion_rate_mandatory": "mandatory completion rate",
    "sim_completion_rate_weighted": "priority-weighted completion",
    "sim_energy_per_completed_task": "energy per completed task",
    "sim_makespan": "mission time",
    "sim_energy_feasibility_rate": "safe-landing rate",
    "sim_workload_cv": "workload imbalance",
    "plan_objective_z": "objective Z",
}
DISCLAIMER = (
    "> All values are **simulated and illustrative**, in normalized energy units (EU) unless "
    "stated. They are not measured drone performance."
)


def _fmt(value: float, digits: int = 2) -> str:
    return "n/a" if value is None or math.isnan(value) else f"{value:.{digits}f}"


def _table(header: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(r) + " |" for r in rows]
    return "\n".join(lines)


def _group(
    rows: list[dict[str, Any]],
    scenario: str,
    algorithm: str,
    variant: str = FULL,
    noise: float = 0.0,
) -> list[dict[str, Any]]:
    return [
        r
        for r in rows
        if r["scenario"] == scenario
        and r["algorithm"] == algorithm
        and r["variant"] == variant
        and r["noise"] == noise
    ]


def _stat(members: list[dict[str, Any]], metric: str, digits: int = 2) -> str:
    data = np.asarray([float(r[metric]) for r in members], dtype=float)
    data = data[~np.isnan(data)]
    if data.size == 0:
        return "n/a"
    spread = float(data.std(ddof=1)) if data.size > 1 else 0.0
    return f"{data.mean():.{digits}f} ± {spread:.{digits}f}"


def _label(name: str) -> str:
    return ALGORITHMS[name].label if name in ALGORITHMS else name


def _baseline_label(name: str) -> str:
    if ":" in name:
        return VARIANT_LABELS[name.split(":", 1)[1]]
    return _label(name)


def _scenario_tables(rows: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for scenario in SCENARIOS:
        body: list[list[str]] = []
        for algorithm in [*CORE_ALGORITHMS, EXACT]:
            members = _group(rows, scenario, algorithm)
            if not members:
                continue
            body.append(
                [
                    f"**{_label(algorithm)}**" if algorithm == PROPOSED else _label(algorithm),
                    _stat(members, "sim_completion_rate", 1),
                    _stat(members, "sim_completion_rate_mandatory", 1),
                    _stat(members, "sim_energy_per_completed_task", 2),
                    _stat(members, "sim_makespan", 0),
                    _stat(members, "sim_energy_feasibility_rate", 0),
                    _stat(members, "plan_plan_feasible", 0),
                    _stat(members, "plan_compute_time", 3),
                ]
            )
        if body:
            header = [
                "Algorithm", "Tasks done %", "Mandatory %", "EU per task", "Mission time s",
                "Safe landing %", "Plan feasible %", "Plan time s",
            ]  # fmt: skip
            parts.append(f"### Scenario {scenario}\n\n{_table(header, body)}")
    return "\n\n".join(parts)


def _significance_table(tests: list[dict[str, Any]]) -> str:
    keep = ("sim_completion_rate", "sim_completion_rate_mandatory", "sim_energy_per_completed_task",
            "sim_makespan", "plan_objective_z")  # fmt: skip
    body = [
        [
            t["scenario"], METRIC_NAMES[t["metric"]], _baseline_label(t["baseline"]),
            f"{t['wins']}/{t['ties']}/{t['losses']}", _fmt(t["p_holm"], 4), _fmt(t["effect_size"]),
            "yes" if t["significant"] else "no",
        ]
        for t in tests
        if t["metric"] in keep and ":" not in t["baseline"]
    ]  # fmt: skip
    header = [
        "Scenario",
        "Metric",
        "Proposed vs",
        "Win/tie/loss",
        "p (Holm)",
        "Effect",
        "Significant",
    ]
    return _table(header, body)


def _gap_section(rows: list[dict[str, Any]]) -> str:
    exact = _group(rows, "A", EXACT)
    if not exact:
        return "The exact solver was not part of this run."
    proven = sum(r["plan_proven_optimal"] == 1.0 for r in exact)
    body = []
    for algorithm in CORE_ALGORITHMS:
        members = _group(rows, "A", algorithm)
        gaps = np.asarray([r["plan_optimality_gap"] for r in members], dtype=float)
        gaps = gaps[~np.isnan(gaps)]
        if members:
            body.append(
                [
                    _label(algorithm),
                    str(gaps.size),
                    _fmt(float(np.median(gaps)) if gaps.size else math.nan),
                    _fmt(float(gaps.mean()) if gaps.size else math.nan),
                    _fmt(float(gaps.max()) if gaps.size else math.nan),
                ]
            )
    note = (
        f"The exact solver proved optimality on {proven} of {len(exact)} Scenario A instances. "
        "Gaps are measured only on those instances and only for plans with no hard violations."
        " Large gaps mostly come from tasks left unassigned, because each one adds at least 10 to Z."
    )
    return note + "\n\n" + _table(
        ["Algorithm", "Instances", "Median gap %", "Mean gap %", "Max gap %"], body
    )  # fmt: skip


def _ablation_section(rows: list[dict[str, Any]]) -> str:
    variants = [*STATIC_ABLATIONS, *DYNAMIC_ABLATIONS]
    body: list[list[str]] = []
    for scenario in SCENARIOS:
        full = _group(rows, scenario, PROPOSED)
        if not full:
            continue
        body.append(
            [scenario, VARIANT_LABELS[FULL], _stat(full, "sim_completion_rate", 1),
             _stat(full, "sim_completion_rate_mandatory", 1), _stat(full, "plan_objective_z", 2)]
        )  # fmt: skip
        for variant in variants:
            members = _group(rows, scenario, PROPOSED, variant)
            if members:
                body.append(
                    [scenario, VARIANT_LABELS[variant], _stat(members, "sim_completion_rate", 1),
                     _stat(members, "sim_completion_rate_mandatory", 1),
                     _stat(members, "plan_objective_z", 2)]
                )  # fmt: skip
    if len(body) <= len(SCENARIOS):
        return "Ablations were not part of this run."
    return _table(["Scenario", "Variant", "Tasks done %", "Mandatory %", "Objective Z"], body)


def _replanning_section(rows: list[dict[str, Any]]) -> str:
    variants = ["no_replanning", "local_only", FULL, "always_global"]
    body: list[list[str]] = []
    for scenario in ("D", "E"):
        for variant in variants:
            members = _group(rows, scenario, PROPOSED, variant)
            if members:
                label = (
                    "Local then global (default)" if variant == FULL else VARIANT_LABELS[variant]
                )
                body.append(
                    [scenario, label, _stat(members, "sim_completion_rate", 1),
                     _stat(members, "sim_completion_rate_mandatory", 1),
                     _stat(members, "sim_replan_time_ms", 1),
                     _stat(members, "sim_tasks_moved_per_replan", 2)]
                )  # fmt: skip
    if not body:
        return "Dynamic scenarios were not part of this run."
    return _table(
        ["Scenario", "Mode", "Tasks done %", "Mandatory %", "Replan time ms", "Tasks moved"], body
    )


def _scaling_section(rows: list[dict[str, Any]]) -> str:
    sizes = sorted({(r["n_uavs"], r["n_tasks"]) for r in rows if r["scenario"].startswith("F-")})
    if not sizes:
        return "The scaling sweep (Scenario F) was not part of this run."
    algorithms = sorted({r["algorithm"] for r in rows if r["scenario"].startswith("F-")},
                        key=lambda a: list(ALGORITHMS).index(a))  # fmt: skip
    body = []
    for n_uavs, n_tasks in sizes:
        cells = [f"{n_uavs}", f"{n_tasks}"]
        for algorithm in algorithms:
            cells.append(
                _stat(_group(rows, f"F-{n_uavs}x{n_tasks}", algorithm), "plan_compute_time", 3)
            )
        body.append(cells)
    return _table(["UAVs", "Tasks", *[_label(a) + " (s)" for a in algorithms]], body)


def _noise_section(rows: list[dict[str, Any]]) -> str:
    levels = sorted({r["noise"] for r in rows if r["scenario"] == "G"})
    if not levels:
        return "The noise experiment (Scenario G) was not part of this run."
    body = []
    for noise in levels:
        for algorithm in sorted({r["algorithm"] for r in rows if r["scenario"] == "G"}):
            members = _group(rows, "G", algorithm, FULL, noise)
            body.append(
                [_fmt(noise), _label(algorithm), _stat(members, "sim_energy_feasibility_rate", 1),
                 _stat(members, "sim_stranded_uavs", 2), _stat(members, "sim_margin_alerts", 2),
                 _stat(members, "sim_energy_estimation_error", 1), _stat(members, "sim_completion_rate", 1)]
            )  # fmt: skip
    return _table(
        [
            "σ",
            "Algorithm",
            "Safe landing %",
            "Stranded UAVs",
            "Margin alerts",
            "Energy error %",
            "Tasks done %",
        ],
        body,
    )


def _where_no_help(tests: list[dict[str, Any]], seeds: int) -> str:
    """Report, from the data, every comparison the proposed method did not win."""
    baseline = [t for t in tests if ":" not in t["baseline"]]
    worse: list[str] = []
    numerically_worse: list[str] = []
    for test in baseline:
        delta = test["mean_proposed"] - test["mean_baseline"]
        unfavourable = delta < -1e-9 if test["higher_is_better"] else delta > 1e-9
        if not unfavourable:
            continue
        sentence = (
            f"Scenario {test['scenario']}, {METRIC_NAMES[test['metric']]}, against "
            f"{_baseline_label(test['baseline'])}: proposed {test['mean_proposed']:.2f}, baseline "
            f"{test['mean_baseline']:.2f} (p Holm {test['p_holm']:.3f}, "
            f"win/tie/loss {test['wins']}/{test['ties']}/{test['losses']})."
        )
        (worse if test["significant"] else numerically_worse).append(sentence)
    no_effect = sorted(
        {
            f"{VARIANT_LABELS[t['baseline'].split(':', 1)[1]]} ({t['scenario']})"
            for t in tests
            if ":" in t["baseline"]
            and t["metric"] == "plan_objective_z"
            and t["wins"] + t["losses"] == 0
        }
    )
    parts: list[str] = []
    if seeds < 6:
        parts.append(
            f"**Limited statistical power.** This run used {seeds} seeds. A two-sided Wilcoxon test "
            f"on {seeds} pairs cannot reach p < {ALPHA}, however large the effect, so no comparison "
            "here can be called significant. Use the 30-seed protocol for inference."
        )
    parts.append("### Significantly worse than a baseline\n")
    parts.append("\n".join(f"- {s}" for s in worse) or "None in this run.")
    parts.append("\n### Worse on average, but not significantly\n")
    parts.append("\n".join(f"- {s}" for s in numerically_worse) or "None in this run.")
    parts.append("\n### Ablations with no effect on the plan objective\n")
    parts.append(
        "The objective did not change on any seed when these components were removed, so they "
        "showed no benefit there: " + ", ".join(no_effect) + "."
        if no_effect
        else "Every ablated component changed the objective on at least one seed."
    )
    parts.append(
        "\n### Reading the objective against B1\n\nZ does not count hard-constraint violations; "
        "the lexicographic comparison handles them first. B1 often scores a low Z because its "
        "plans assign every task while breaking energy limits, so a lower Z for B1 does not "
        "mean a better plan. See the plan-feasible and safe-landing columns in section 1."
    )
    parts.append(
        "\n### Reading total energy\n\nTotal energy favours methods that complete fewer tasks. "
        "This report therefore compares energy per completed task; see `raw.csv` for totals."
    )
    return "\n".join(parts)


def _mean(members: list[dict[str, Any]], metric: str) -> float:
    data = np.asarray([float(r[metric]) for r in members], dtype=float)
    data = data[~np.isnan(data)]
    return float(data.mean()) if data.size else math.nan


def _mission_table(rows: list[dict[str, Any]], missions: list[str], metric: str) -> str:
    algorithms = [a for a in CORE_ALGORITHMS if any(r["algorithm"] == a for r in rows)]
    body = []
    for name in missions:
        cells = [_stat(_group(rows, name, a), metric, 2) for a in algorithms]
        body.append([mission_meta(name).name, mission_meta(name).category, *cells])
    header = ["Mission", "Type", *(_label(a) for a in algorithms)]
    return _table(header, body)


def _type_table(rows: list[dict[str, Any]], missions: list[str]) -> str:
    algorithms = [a for a in CORE_ALGORITHMS if any(r["algorithm"] == a for r in rows)]
    categories = sorted({mission_meta(m).category for m in missions})
    body = []
    for category in categories:
        members = [m for m in missions if mission_meta(m).category == category]
        cells = []
        for algorithm in algorithms:
            group = [r for m in members for r in _group(rows, m, algorithm)]
            cells.append(
                f"{_mean(group, 'sim_completion_rate'):.1f} / "
                f"{_mean(group, 'sim_energy_per_completed_task'):.2f}"
            )
        body.append([category, str(len(members)), *cells])
    return _table(["Mission type", "Missions", *(_label(a) for a in algorithms)], body)


def _mission_verdict(rows: list[dict[str, Any]], missions: list[str]) -> str:
    """Counts come straight from the data, so the sentence changes with the results."""
    baselines = [
        a for a in CORE_ALGORITHMS if a != PROPOSED and any(r["algorithm"] == a for r in rows)
    ]
    wins = ties = losses = 0
    for name in missions:
        own = _mean(_group(rows, name, PROPOSED), "sim_completion_rate")
        best = max(_mean(_group(rows, name, b), "sim_completion_rate") for b in baselines)
        if abs(own - best) < 0.05:
            ties += 1
        elif own > best:
            wins += 1
        else:
            losses += 1
    return (
        f"Mean completion rate of the proposed method against the best baseline on each "
        f"mission: higher on {wins}, equal on {ties}, lower on {losses} of {len(missions)}."
    )


def _mission_section(rows: list[dict[str, Any]], missions: list[str]) -> str:
    recharging = [mission_meta(m).name for m in missions if mission_meta(m).allow_recharge]
    note = ""
    if recharging:
        note = (
            f"\n\nOn {', '.join(recharging)} the proposed method may use charging stops, as that "
            "mission specifies. The baselines cannot, so read that row with this in mind."
        )
    return f"""{_mission_verdict(rows, missions)}{note}

### By mission type: tasks done % / EU per completed task

{_type_table(rows, missions)}

### Tasks done %

{_mission_table(rows, missions, "sim_completion_rate")}

### Mandatory tasks done %

{_mission_table(rows, missions, "sim_completion_rate_mandatory")}

### EU per completed task

{_mission_table(rows, missions, "sim_energy_per_completed_task")}

### Safe-landing rate %

{_mission_table(rows, missions, "sim_energy_feasibility_rate")}"""


def _optional_mission_block(rows: list[dict[str, Any]], missions: list[str]) -> str:
    if not missions:
        return ""
    return f"## Results by mission type\n\n{_mission_section(rows, missions)}\n\n"


def _mission_report(out_dir: Path, rows: list[dict[str, Any]], missions: list[str]) -> Path:
    tests = significance(rows)
    write_csv(out_dir / "stats.csv", tests)
    figures = make_mission_figures(rows, out_dir / "figures", missions)
    figure_block = "\n\n".join(f"![{name}](figures/{name})" for name in figures)
    seeds = max(r["seed"] for r in rows) + 1
    report = f"""# Experiment report: missions

{DISCLAIMER}

Mission maps, fleets and events are illustrative simulation settings, not real places or
measurements. Runs: {len(rows)}. Seeds per configuration: {seeds}. Standard size. Every planner
receives the same mission instances and scripted events. After an event each baseline re-runs
itself from the current state, while the proposed method repairs locally and escalates only when
needed. With only {seeds} seeds, treat small differences as noise.

## Results by mission type

{_mission_section(rows, missions)}

## Significance tests

Paired Wilcoxon signed-rank tests of the proposed method against each baseline, by seed, with
Holm–Bonferroni correction. Effect is the rank-biserial correlation.

{_significance_table(tests)}

## Figures

{figure_block}
"""
    path = out_dir / "REPORT.md"
    path.write_text(report, encoding="utf-8", newline="\n")
    return path


def build_report(out_dir: Path) -> Path:
    """Compute statistics and figures for a finished run, and write ``REPORT.md``."""
    rows = read_rows(out_dir / "raw.csv")
    if not rows:
        raise ValueError(f"no results in {out_dir / 'raw.csv'}")
    add_optimality_gaps(rows)
    write_csv(out_dir / "summary.csv", summarize(rows))
    missions = [m for m in MISSION_IDS if any(r["scenario"] == m for r in rows)]
    if len({r["scenario"] for r in rows}) == len(missions):
        return _mission_report(out_dir, rows, missions)
    tests = significance(rows)
    write_csv(out_dir / "stats.csv", tests)

    has_b = any(r["scenario"] == "B" for r in rows)
    sweep = weight_sweep("B" if has_b else "A")
    write_csv(out_dir / "pareto.csv", sweep)
    figures = make_figures(rows, sweep, out_dir / "figures")

    seeds = max(r["seed"] for r in rows) + 1
    figure_block = "\n\n".join(f"![{name}](figures/{name})" for name in figures)
    report = f"""# Experiment report

{DISCLAIMER}

Runs: {len(rows)}. Seeds per configuration: {seeds}. Every algorithm receives the same scenario
instances and event sequences. In D and E each baseline re-runs itself from the current state
after an event, while the proposed method repairs locally and escalates only when needed.

Metrics come from the simulated execution. Means are shown with ± one standard deviation.
The B-series labels are the baselines defined in `docs/ALGORITHMS.md`.

## 1. Results by scenario

{_scenario_tables(rows)}

{_optional_mission_block(rows, missions)}## 2. Significance tests

Paired Wilcoxon signed-rank tests of the proposed method against each baseline, by seed, with
Holm–Bonferroni correction within each scenario and metric. Win/tie/loss is from the proposed
method's side. Effect is the rank-biserial correlation, where a positive value means the
proposed method's raw value is numerically larger. Full results are in `stats.csv`.

{_significance_table(tests)}

## 3. Optimality gap (Scenario A)

{_gap_section(rows)}

## 4. Ablation study

{_ablation_section(rows)}

## 5. Replanning trade-off

{_replanning_section(rows)}

## 6. Scalability (Scenario F)

Planning times were measured while runs shared the machine, so read them as rough magnitudes.

{_scaling_section(rows)}

## 7. Energy-noise robustness (Scenario G)

{_noise_section(rows)}

## 8. Where the integrated method does not help

{_where_no_help(tests, seeds)}

## 9. Figures

{figure_block}

---
Metric definitions: {", ".join(sorted(HIGHER_IS_BETTER))} are higher-is-better; the rest are
lower-is-better. Protocol: `docs/EXPERIMENTS.md`.
"""
    path = out_dir / "REPORT.md"
    path.write_text(report, encoding="utf-8", newline="\n")
    return path


__all__ = ["build_report"]
