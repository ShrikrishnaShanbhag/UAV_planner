"""Matplotlib report figures (PNG and SVG, 300 dpi).

Styling follows one rule set: each algorithm keeps a fixed colour across every figure,
a figure has one y-scale, grids are solid hairlines, and text stays in neutral inks.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.axes import Axes
from matplotlib.figure import Figure
from matplotlib.patches import Rectangle

from uavplan.core.energy import EnergyEstimator
from uavplan.core.environment import EnvironmentManager, region_cells
from uavplan.core.generator import demo_scenario
from uavplan.core.metrics import plan_metrics
from uavplan.core.models import Plan, Scenario
from uavplan.core.registry import ALGORITHMS, get_planner
from uavplan.core.simulator import Simulator
from uavplan.experiments.ablation import (
    DYNAMIC_ABLATIONS,
    FULL,
    STATIC_ABLATIONS,
    VARIANT_LABELS,
)
from uavplan.experiments.runner import (
    CORE_ALGORITHMS,
    PROPOSED,
    SCALING_ALGORITHMS,
    RunSpec,
    planner_config,
)

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_2 = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
AXIS = "#c3c2b7"
SERIES = ("#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6")
ALGO_COLOR = dict(zip([*CORE_ALGORITHMS, "exact_cpsat"], SERIES, strict=True))
SHORT = {
    "nearest": "B1",
    "greedy_energy": "B2",
    "independent": "B3",
    "integrated": "Proposed",
    "ortools_vrp": "B4",
    "genetic": "B5",
    "exact_cpsat": "Exact",
}
NOTE = "Simulated, illustrative values."
METRIC_LABEL = {
    "sim_completion_rate": "Tasks completed (%)",
    "sim_completion_rate_mandatory": "Mandatory tasks completed (%)",
    "sim_energy_per_completed_task": "Energy per completed task (EU)",
    "sim_makespan": "Mission time (s)",
    "sim_total_energy": "Total energy (EU)",
    "sim_workload_cv": "Workload imbalance (CV)",
    "sim_energy_feasibility_rate": "UAVs landing with reserve intact (%)",
    "plan_objective_z": "Objective Z (lower is better)",
}
SCENARIOS = ("A", "B", "C", "D", "E")


def apply_style() -> None:
    plt.rcParams.update(
        {
            "figure.facecolor": SURFACE,
            "axes.facecolor": SURFACE,
            "savefig.facecolor": SURFACE,
            "font.family": ["Segoe UI", "DejaVu Sans", "sans-serif"],
            "font.size": 9,
            "text.color": INK,
            "axes.labelcolor": INK_2,
            "axes.edgecolor": AXIS,
            "axes.linewidth": 0.8,
            "axes.titlesize": 10,
            "axes.titleweight": "semibold",
            "axes.titlelocation": "left",
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.grid": True,
            "axes.axisbelow": True,
            "grid.color": GRID,
            "grid.linewidth": 0.6,
            "grid.linestyle": "-",
            "xtick.color": MUTED,
            "ytick.color": MUTED,
            "xtick.labelcolor": INK_2,
            "ytick.labelcolor": INK_2,
            "xtick.major.size": 0,
            "ytick.major.size": 0,
            "legend.frameon": False,
            "legend.fontsize": 8,
            "lines.linewidth": 2.0,
            "lines.markersize": 5,
            "svg.fonttype": "none",
        }
    )


def _save(fig: Figure, folder: Path, name: str) -> list[str]:
    fig.savefig(folder / f"{name}.png", dpi=300, bbox_inches="tight")
    fig.savefig(folder / f"{name}.svg", bbox_inches="tight")
    plt.close(fig)
    return [f"{name}.png"]


def _select(
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


def _values(items: list[dict[str, Any]], metric: str) -> np.ndarray:
    data = np.asarray([float(r[metric]) for r in items], dtype=float)
    return data[~np.isnan(data)]


def _ci(data: np.ndarray, seed: int = 0) -> tuple[float, float]:
    rng = np.random.default_rng(seed)
    picks = rng.integers(0, data.size, (2000, data.size))
    means = data[picks].mean(axis=1)
    return float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def _legend_below(fig: Figure, algorithms: list[str]) -> None:
    handles = [
        Rectangle((0, 0), 1, 1, color=ALGO_COLOR[a], label=ALGORITHMS[a].label) for a in algorithms
    ]
    fig.legend(handles=handles, loc="lower center", ncol=3, bbox_to_anchor=(0.5, -0.12))


def grouped_bars(rows: list[dict[str, Any]], folder: Path, metric: str) -> list[str]:
    scenarios = [s for s in SCENARIOS if _select(rows, s, PROPOSED)]
    algorithms = [a for a in CORE_ALGORITHMS if any(r["algorithm"] == a for r in rows)]
    if not scenarios or not algorithms:
        return []
    fig, ax = plt.subplots(figsize=(7.2, 3.2))
    width = 0.78 / len(algorithms)
    for i, algorithm in enumerate(algorithms):
        means, lows, highs, xs = [], [], [], []
        for j, scenario in enumerate(scenarios):
            data = _values(_select(rows, scenario, algorithm), metric)
            if data.size == 0:
                continue
            low, high = _ci(data)
            xs.append(j + (i - (len(algorithms) - 1) / 2) * width)
            means.append(float(data.mean()))
            lows.append(max(float(data.mean()) - low, 0.0))
            highs.append(max(high - float(data.mean()), 0.0))
        ax.bar(
            xs, means, width * 0.82, color=ALGO_COLOR[algorithm], edgecolor=SURFACE, linewidth=0.5
        )
        ax.errorbar(xs, means, yerr=[lows, highs], fmt="none", ecolor=INK_2, elinewidth=0.8)
    ax.set_xticks(range(len(scenarios)), scenarios)
    ax.grid(axis="x", visible=False)
    ax.set_ylabel(METRIC_LABEL[metric])
    ax.set_title(f"{METRIC_LABEL[metric]} by scenario")
    ax.text(1.0, 1.02, f"Mean with 95% bootstrap CI. {NOTE}", transform=ax.transAxes, ha="right",
            color=MUTED, fontsize=7)  # fmt: skip
    _legend_below(fig, algorithms)
    return _save(fig, folder, f"bars_{metric}")


def mission_bars(
    rows: list[dict[str, Any]], folder: Path, metric: str, missions: list[str]
) -> list[str]:
    """One group of bars per mission, one bar per planner."""
    algorithms = [a for a in CORE_ALGORITHMS if any(r["algorithm"] == a for r in rows)]
    if not missions or not algorithms:
        return []
    fig, ax = plt.subplots(figsize=(10.0, 3.4))
    width = 0.78 / len(algorithms)
    for i, algorithm in enumerate(algorithms):
        xs, means = [], []
        for j, name in enumerate(missions):
            data = _values(_select(rows, name, algorithm), metric)
            if data.size:
                xs.append(j + (i - (len(algorithms) - 1) / 2) * width)
                means.append(float(data.mean()))
        ax.bar(
            xs, means, width * 0.82, color=ALGO_COLOR[algorithm], edgecolor=SURFACE, linewidth=0.5
        )
    ax.set_xticks(range(len(missions)), [m.replace("_", " ") for m in missions])
    ax.tick_params(axis="x", labelrotation=30)
    ax.grid(axis="x", visible=False)
    ax.set_ylabel(METRIC_LABEL[metric])
    ax.set_title(f"{METRIC_LABEL[metric]} by mission")
    ax.text(1.0, 1.02, f"Mean over seeds. {NOTE}", transform=ax.transAxes, ha="right",
            color=MUTED, fontsize=7)  # fmt: skip
    _legend_below(fig, algorithms)
    return _save(fig, folder, f"missions_{metric}")


def box_plots(rows: list[dict[str, Any]], folder: Path, metric: str) -> list[str]:
    scenarios = [s for s in SCENARIOS if _select(rows, s, PROPOSED)]
    algorithms = [a for a in CORE_ALGORITHMS if any(r["algorithm"] == a for r in rows)]
    if not scenarios:
        return []
    fig, axes = plt.subplots(1, len(scenarios), figsize=(1.8 * len(scenarios) + 1, 3.0), sharey=True,
                             squeeze=False)  # fmt: skip
    for ax, scenario in zip(axes[0], scenarios, strict=True):
        shown = [(a, _values(_select(rows, scenario, a), metric)) for a in algorithms]
        shown = [(a, d) for a, d in shown if d.size]
        parts = ax.boxplot([d for _, d in shown], patch_artist=True, widths=0.6,
                           medianprops={"color": INK, "linewidth": 1.0},
                           whiskerprops={"color": MUTED}, capprops={"color": MUTED},
                           flierprops={"markersize": 2.5, "markeredgecolor": MUTED})  # fmt: skip
        for patch, (algorithm, _) in zip(parts["boxes"], shown, strict=True):
            patch.set_facecolor(ALGO_COLOR[algorithm])
            patch.set_edgecolor(SURFACE)
        ax.set_xticks(
            range(1, len(shown) + 1), [SHORT[a] for a, _ in shown], rotation=60, fontsize=7
        )
        ax.grid(axis="x", visible=False)
        ax.set_title(scenario)
    axes[0][0].set_ylabel(METRIC_LABEL[metric])
    fig.suptitle(f"{METRIC_LABEL[metric]}: spread across seeds", x=0.02, ha="left", fontsize=10,
                 fontweight="semibold")  # fmt: skip
    fig.tight_layout()
    return _save(fig, folder, f"box_{metric}")


def scaling_curves(rows: list[dict[str, Any]], folder: Path) -> list[str]:
    points: dict[tuple[str, int, int], list[float]] = defaultdict(list)
    for row in rows:
        if row["scenario"].startswith("F-"):
            points[(row["algorithm"], row["n_uavs"], row["n_tasks"])].append(
                row["plan_compute_time"]
            )
    if not points:
        return []
    fleets = sorted({k[1] for k in points})
    fig, axes = plt.subplots(1, len(fleets), figsize=(2.2 * len(fleets) + 0.8, 3.0), sharey=True,
                             squeeze=False)  # fmt: skip
    algorithms = [a for a in SCALING_ALGORITHMS if any(k[0] == a for k in points)]
    for ax, fleet in zip(axes[0], fleets, strict=True):
        for algorithm in algorithms:
            series = sorted((k[2], float(np.mean(v))) for k, v in points.items()
                            if k[0] == algorithm and k[1] == fleet)  # fmt: skip
            if series:
                xs, ys = zip(*series, strict=True)
                ax.plot(xs, ys, marker="o", color=ALGO_COLOR[algorithm])
        ax.set_xscale("log")
        ax.set_yscale("log")
        ax.set_title(f"{fleet} UAVs")
        ax.set_xlabel("Tasks")
    axes[0][0].set_ylabel("Planning time (s), log scale")
    fig.tight_layout()
    _legend_below(fig, algorithms)
    return _save(fig, folder, "scaling_compute_time")


def noise_robustness(rows: list[dict[str, Any]], folder: Path) -> list[str]:
    grouped: dict[tuple[str, float], list[float]] = defaultdict(list)
    for row in rows:
        if row["scenario"] == "G":
            grouped[(row["algorithm"], row["noise"])].append(row["sim_energy_feasibility_rate"])
    if not grouped:
        return []
    fig, ax = plt.subplots(figsize=(5.2, 3.0))
    algorithms = [a for a in CORE_ALGORITHMS if any(k[0] == a for k in grouped)]
    for algorithm in algorithms:
        series = sorted((k[1], float(np.mean(v))) for k, v in grouped.items() if k[0] == algorithm)
        xs, ys = zip(*series, strict=True)
        ax.plot(xs, ys, marker="o", color=ALGO_COLOR[algorithm], label=ALGORITHMS[algorithm].label)
    ax.set_xlabel("Energy noise σ")
    ax.set_ylabel(METRIC_LABEL["sim_energy_feasibility_rate"])
    ax.set_title("Does the reserve absorb energy estimation error?")
    ax.legend()
    return _save(fig, folder, "noise_robustness")


def ablation_bars(rows: list[dict[str, Any]], folder: Path, metric: str) -> list[str]:
    scenarios = [s for s in SCENARIOS if _select(rows, s, PROPOSED, "no_insertion")]
    if not scenarios:
        return []
    variants = [*STATIC_ABLATIONS, *DYNAMIC_ABLATIONS]
    fig, axes = plt.subplots(1, len(scenarios), figsize=(1.9 * len(scenarios) + 1.6, 3.2), sharey=True, sharex=True,
                             squeeze=False)  # fmt: skip
    for ax, scenario in zip(axes[0], scenarios, strict=True):
        full = float(np.mean(_values(_select(rows, scenario, PROPOSED), metric)))
        ys, deltas = [], []
        for y, variant in enumerate(variants):
            data = _values(_select(rows, scenario, PROPOSED, variant), metric)
            if data.size:
                ys.append(y)
                deltas.append(float(data.mean()) - full)
        ax.barh(ys, deltas, 0.56, color=SERIES[0])
        ax.axvline(0.0, color=AXIS, linewidth=0.8)
        ax.set_yticks(range(len(variants)), [VARIANT_LABELS[v] for v in variants])
        ax.invert_yaxis()
        ax.grid(axis="y", visible=False)
        ax.set_title(scenario)
    fig.suptitle(f"Ablations: change in {METRIC_LABEL[metric].lower()} vs the full method", x=0.02,
                 ha="left", fontsize=10, fontweight="semibold")  # fmt: skip
    fig.tight_layout()
    return _save(fig, folder, f"ablation_{metric}")


def pareto_scatter(sweep: list[dict[str, float]], folder: Path) -> list[str]:
    if not sweep:
        return []
    shares = sorted({r["energy_share"] for r in sweep})
    points = []
    for share in shares:
        group = [r for r in sweep if r["energy_share"] == share]
        points.append(
            (share, float(np.mean([r["total_energy"] for r in group])),
             float(np.mean([r["makespan"] for r in group])))
        )  # fmt: skip
    front = [p for p in points if not any(
        q[1] <= p[1] and q[2] <= p[2] and (q[1] < p[1] or q[2] < p[2]) for q in points)]  # fmt: skip
    fig, ax = plt.subplots(figsize=(5.2, 3.2))
    others = [p for p in points if p not in front]
    if others:
        ax.scatter(
            [p[1] for p in others], [p[2] for p in others], s=36, color=MUTED, label="Dominated"
        )
    ordered = sorted(front, key=lambda p: p[1])
    ax.plot([p[1] for p in ordered], [p[2] for p in ordered], color=SERIES[0], marker="o", markersize=7,
            label="Non-dominated")  # fmt: skip
    for point in (ordered[0], ordered[-1]):
        ax.annotate(f"energy share {point[0]:.1f}", (point[1], point[2]), xytext=(5, 5),
                    textcoords="offset points", fontsize=7, color=INK_2)  # fmt: skip
    ax.set_xlabel("Total energy (EU)")
    ax.set_ylabel("Mission time (s)")
    ax.set_title("Energy vs mission time across objective weights")
    ax.legend()
    return _save(fig, folder, "pareto_energy_makespan")


def _draw_map(ax: Axes, scenario: Scenario) -> None:
    env = EnvironmentManager.from_scenario(scenario)
    image = np.ones((scenario.grid.height, scenario.grid.width, 3))
    zone = np.array([0.94, 0.93, 0.88])
    for region in scenario.obstacles:
        if region.kind == "energy_zone":
            for x, y in region_cells(region, scenario.grid):
                image[y, x] = zone
    image[~env.free] = np.array([0.70, 0.69, 0.66])
    ax.imshow(image, origin="lower", interpolation="nearest")
    ax.set_xticks([])
    ax.set_yticks([])
    ax.grid(visible=False)
    for spine in ax.spines.values():
        spine.set_visible(False)


def route_maps(scenario: Scenario, plans: dict[str, Plan], folder: Path) -> list[str]:
    columns = 3
    count = len(plans)
    lines = (count + columns - 1) // columns
    fig, axes = plt.subplots(lines, columns, figsize=(3.0 * columns, 3.1 * lines), squeeze=False)
    colors = {u.id: SERIES[i % len(SERIES)] for i, u in enumerate(scenario.uavs)}
    for ax in axes.flat[count:]:
        ax.set_visible(False)
    for ax, (name, plan) in zip(axes.flat, plans.items(), strict=False):
        _draw_map(ax, scenario)
        for route in plan.routes:
            for leg in route.legs:
                xs, ys = zip(*leg.path, strict=True)
                ax.plot(xs, ys, color=colors[route.uav_id], linewidth=1.3,
                        alpha=0.55 if leg.kind == "return" else 1.0)  # fmt: skip
        placed = {t for r in plan.routes for t in r.task_ids}
        for task in scenario.tasks:
            done = task.id in placed
            ax.scatter(*task.cell, s=11, color=INK if done else SURFACE, edgecolor=INK, linewidth=0.6,
                       zorder=3)  # fmt: skip
        for station in scenario.stations:
            ax.scatter(*station.cell, s=30, marker="s", color=INK, zorder=4)
        metrics = plan_metrics(plan, scenario)
        count = len(plan.report.violations)
        state = (
            "feasible" if plan.report.feasible else f"{count} violation{'s' if count != 1 else ''}"
        )
        ax.set_title(
            f"{SHORT.get(name, name)}: {metrics['completion_rate']:.0f}% tasks, {state}", fontsize=8
        )
    handles = [Rectangle((0, 0), 1, 1, color=colors[u.id], label=u.id) for u in scenario.uavs]
    handles.append(Rectangle((0, 0), 1, 1, color=[0.70, 0.69, 0.66], label="Obstacle / no-fly"))
    handles.append(Rectangle((0, 0), 1, 1, color=[0.94, 0.93, 0.88], label="High-energy zone"))
    fig.legend(handles=handles, loc="lower center", ncol=6, bbox_to_anchor=(0.5, -0.03))
    fig.tight_layout()
    return _save(fig, folder, "route_maps_demo")


def battery_timeline(scenario: Scenario, plan: Plan, folder: Path) -> list[str]:
    result = Simulator(scenario, plan).run()
    frames = result.timeline(tick=2.0)
    fig, ax = plt.subplots(figsize=(6.4, 3.0))
    for i, uav in enumerate(scenario.uavs):
        times = [f["t"] for f in frames]
        battery = [f["uavs"][i]["battery"] for f in frames]
        ids = [u["id"] for u in frames[0]["uavs"]]
        battery = [f["uavs"][ids.index(uav.id)]["battery"] for f in frames]
        ax.plot(times, battery, color=SERIES[i], label=uav.id)
        ax.annotate(uav.id, (times[-1], battery[-1]), xytext=(4, 0), textcoords="offset points",
                    va="center", fontsize=7, color=INK_2)  # fmt: skip
    reserve = EnergyEstimator(scenario.energy).reserve(scenario.uavs[0])
    ax.axhline(reserve, color=INK_2, linewidth=0.8)
    ax.text(0.0, reserve + 1.5, f"Reserve ({reserve:.0f} EU)", fontsize=7, color=INK_2)
    ax.set_xlabel("Mission time (s)")
    ax.set_ylabel("Battery (EU)")
    ax.set_ylim(bottom=0)
    ax.set_title(f"Battery over time, earthquake demo, proposed method. {NOTE}")
    ax.legend(ncol=4, loc="upper right")
    return _save(fig, folder, "battery_timeline_demo")


def gantt(scenario: Scenario, plan: Plan, folder: Path) -> list[str]:
    result = Simulator(scenario, plan).run()
    kinds = {"Flying to task": SERIES[0], "Working": SERIES[1], "Returning": SERIES[2]}
    fig, ax = plt.subplots(figsize=(6.4, 2.6))
    uavs = [u.id for u in scenario.uavs]
    for y, uav_id in enumerate(uavs):
        for seg in result.segments[uav_id]:
            if seg.t1 <= seg.t0:
                continue
            name = "Working" if seg.kind in ("work", "wait") else (
                "Returning" if seg.returning else "Flying to task")  # fmt: skip
            ax.broken_barh([(seg.t0, seg.t1 - seg.t0)], (y - 0.3, 0.6), color=kinds[name],
                           edgecolor=SURFACE, linewidth=0.6)  # fmt: skip
    ax.set_yticks(range(len(uavs)), uavs)
    ax.invert_yaxis()
    ax.grid(axis="y", visible=False)
    ax.set_xlabel("Mission time (s)")
    ax.set_title(f"UAV schedules, earthquake demo, proposed method. {NOTE}")
    handles = [Rectangle((0, 0), 1, 1, color=c, label=k) for k, c in kinds.items()]
    ax.legend(handles=handles, ncol=3, loc="upper center", bbox_to_anchor=(0.5, -0.25))
    return _save(fig, folder, "gantt_demo")


def make_mission_figures(
    rows: list[dict[str, Any]], folder: Path, missions: list[str]
) -> list[str]:
    apply_style()
    folder.mkdir(parents=True, exist_ok=True)
    names: list[str] = []
    for metric in ("sim_completion_rate", "sim_completion_rate_mandatory",
                   "sim_energy_per_completed_task"):  # fmt: skip
        names += mission_bars(rows, folder, metric, missions)
    return names


def make_figures(
    rows: list[dict[str, Any]], sweep: list[dict[str, float]], folder: Path
) -> list[str]:
    apply_style()
    folder.mkdir(parents=True, exist_ok=True)
    names: list[str] = []
    for metric in ("sim_completion_rate", "sim_completion_rate_mandatory",
                   "sim_energy_per_completed_task", "sim_makespan", "sim_workload_cv"):  # fmt: skip
        names += grouped_bars(rows, folder, metric)
    for metric in ("sim_completion_rate", "sim_energy_per_completed_task", "sim_makespan"):
        names += box_plots(rows, folder, metric)
    names += scaling_curves(rows, folder)
    names += noise_robustness(rows, folder)
    for metric in ("sim_completion_rate", "plan_objective_z"):
        names += ablation_bars(rows, folder, metric)
    names += pareto_scatter(sweep, folder)

    scenario = demo_scenario()
    plans = {
        name: get_planner(name).plan(scenario, planner_config(RunSpec("DEMO", 0, name)))
        for name in CORE_ALGORITHMS
    }
    names += route_maps(scenario, plans, folder)
    names += battery_timeline(scenario, plans[PROPOSED], folder)
    names += gantt(scenario, plans[PROPOSED], folder)
    return names
