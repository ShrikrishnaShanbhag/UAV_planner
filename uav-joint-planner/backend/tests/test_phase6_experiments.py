"""Phase 6: experiment runner, statistics and the report."""

from __future__ import annotations

import math
import threading
from pathlib import Path

import pytest

from uavplan.cli import main
from uavplan.experiments.ablation import DYNAMIC_ABLATIONS, STATIC_ABLATIONS, integrated_config
from uavplan.experiments.report import build_report
from uavplan.experiments.runner import (
    ALL_ALGORITHMS,
    RunSpec,
    build_scenario,
    build_specs,
    execute,
    read_rows,
    replan_config,
    run_experiments,
)
from uavplan.experiments.stats import (
    add_optimality_gaps,
    bootstrap_ci,
    describe,
    holm,
    paired_test,
    significance,
    summarize,
)


def test_specs_give_every_algorithm_the_same_instances() -> None:
    specs = build_specs(["A", "B"], ALL_ALGORITHMS, 3)
    by_algorithm: dict[str, set[tuple[str, int]]] = {}
    for spec in specs:
        by_algorithm.setdefault(spec.algorithm, set()).add((spec.scenario, spec.seed))
    assert by_algorithm["integrated"] == by_algorithm["nearest"]
    assert all(s.scenario == "A" for s in specs if s.algorithm == "exact_cpsat")


def test_specs_include_ablations_only_where_defined() -> None:
    specs = build_specs(["A", "D"], ["integrated"], 1, ablations=True)
    a = {s.variant for s in specs if s.scenario == "A"}
    d = {s.variant for s in specs if s.scenario == "D"}
    assert a == {"full", *STATIC_ABLATIONS}
    assert d == {"full", *STATIC_ABLATIONS, *DYNAMIC_ABLATIONS}


def test_scaling_and_noise_specs() -> None:
    f = build_specs(["F"], ["integrated", "genetic"], 30)
    assert {s.scenario for s in f} >= {"F-2x10", "F-12x100"}
    assert {s.seed for s in f} == set(range(5))
    assert {s.algorithm for s in f} == {"integrated"}
    g = build_specs(["G"], ["integrated"], 2)
    assert sorted({s.noise for s in g}) == [0.0, 0.05, 0.1, 0.2]


def test_build_scenario_dispatches_by_name() -> None:
    assert len(build_scenario(RunSpec("F-4x25", 1, "nearest")).tasks) == 25
    assert len(build_scenario(RunSpec("G", 1, "nearest")).uavs) == 4
    assert len(build_scenario(RunSpec("A", 1, "nearest")).uavs) == 3


def test_replan_config_for_each_variant() -> None:
    assert replan_config(RunSpec("D", 0, "integrated")).mode == "local_then_global"
    baseline = replan_config(RunSpec("D", 0, "nearest"))
    assert baseline.mode == "always_global" and baseline.planner == "nearest"
    assert replan_config(RunSpec("D", 0, "integrated", "no_replanning")) is None
    assert replan_config(RunSpec("D", 0, "integrated", "local_only")).mode == "local_only"
    assert replan_config(RunSpec("A", 0, "exact_cpsat")) is None


def test_ablation_config_switches_the_flag() -> None:
    assert not integrated_config("no_insertion", 0).use_insertion
    assert integrated_config("full", 0).use_insertion


def test_execute_row_has_every_metric_family() -> None:
    row = execute(RunSpec("A", 1, "integrated"))
    assert row["plan_compute_time"] > 0
    assert 0 <= row["sim_completion_rate"] <= 100
    assert row["sim_energy_per_completed_task"] > 0
    assert row["n_tasks"] == 10


def test_execute_is_deterministic_apart_from_timing() -> None:
    a, b = execute(RunSpec("D", 2, "integrated")), execute(RunSpec("D", 2, "integrated"))
    timing = {"plan_compute_time", "sim_compute_time", "sim_replan_time_ms"}
    assert {k: v for k, v in a.items() if k not in timing} == {
        k: v for k, v in b.items() if k not in timing
    }


def test_runner_resumes_and_skips_finished_rows(tmp_path: Path) -> None:
    specs = build_specs(["A"], ["nearest"], 2)
    run_experiments(specs[:1], tmp_path, workers=1)
    assert len(read_rows(tmp_path / "raw.csv")) == 1
    seen: list[tuple[int, int]] = []
    rows = run_experiments(specs, tmp_path, workers=1, progress=lambda d, t: seen.append((d, t)))
    assert seen[0] == (1, 2) and seen[-1] == (2, 2)
    assert len(rows) == 2


def test_runner_can_be_cancelled(tmp_path: Path) -> None:
    specs = build_specs(["A"], ["nearest"], 12)
    cancel = threading.Event()

    def stop_after_first(done: int, total: int) -> None:
        if done >= 1:
            cancel.set()

    rows = run_experiments(specs, tmp_path, workers=1, progress=stop_after_first, cancel=cancel)
    assert 1 <= len(rows) < len(specs)
    resumed = run_experiments(specs, tmp_path, workers=2)
    assert len(resumed) == len(specs)


# -- statistics -------------------------------------------------------------------------


def test_bootstrap_is_reproducible_and_brackets_the_mean() -> None:
    data = [1.0, 2.0, 3.0, 4.0, 9.0]
    assert bootstrap_ci(data) == bootstrap_ci(data)
    low, high = bootstrap_ci(data)
    assert low <= sum(data) / len(data) <= high
    assert math.isnan(bootstrap_ci([])[0])


def test_describe_ignores_nan() -> None:
    result = describe([1.0, math.nan, 3.0])
    assert result["n"] == 2 and result["mean"] == 2.0
    assert math.isnan(describe([math.nan])["mean"])
    assert describe([5.0])["std"] == 0.0


def test_holm_adjustment() -> None:
    assert holm([0.01, 0.04, 0.03]) == pytest.approx([0.03, 0.06, 0.06])
    assert holm([0.9, 0.9]) == [1.0, 1.0]


def test_paired_test_counts_direction_of_the_metric() -> None:
    a = [10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 16.0, 17.0]
    b = [v - 1 - i * 0.1 for i, v in enumerate(a)]
    higher = paired_test(a, b, higher_better=True)
    assert (higher.wins, higher.losses) == (8, 0)
    assert higher.effect == pytest.approx(1.0)
    assert higher.p_value == pytest.approx(2 / 2**8)
    lower = paired_test(a, b, higher_better=False)
    assert (lower.wins, lower.losses) == (0, 8)


def test_paired_test_handles_ties_and_nan() -> None:
    same = paired_test([1.0, 2.0], [1.0, 2.0], True)
    assert same.p_value == 1.0 and same.ties == 2 and same.effect == 0.0
    assert paired_test([1.0, math.nan], [0.0, 1.0], True).n == 1


def test_gap_reported_only_with_proven_optimum() -> None:
    def row(algorithm: str, seed: int, z: float, proven: float = 0.0, viol: float = 0.0):
        return {"scenario": "A", "seed": seed, "algorithm": algorithm, "plan_objective_z": z,
                "plan_proven_optimal": proven, "plan_violations": viol}  # fmt: skip

    rows = [
        row("exact_cpsat", 0, 10.0, 1.0),
        row("exact_cpsat", 1, 10.0, 0.0),
        row("integrated", 0, 11.0),
        row("integrated", 1, 11.0),
        row("nearest", 0, 1.0, viol=3.0),
    ]
    add_optimality_gaps(rows)
    assert rows[2]["plan_optimality_gap"] == pytest.approx(10.0)
    assert math.isnan(rows[3]["plan_optimality_gap"])
    assert math.isnan(rows[4]["plan_optimality_gap"])


def test_full_pipeline_builds_a_report(tmp_path: Path) -> None:
    specs = [
        *build_specs(["A", "D"], ["nearest", "integrated"], 2, ablations=True),
        *build_specs(["G"], ["integrated"], 1),
        *[RunSpec("F-2x10", 0, "integrated"), RunSpec("F-2x25", 0, "integrated")],
    ]
    run_experiments(specs, tmp_path, workers=4)
    path = build_report(tmp_path)
    text = path.read_text(encoding="utf-8")
    assert "Where the integrated method does not help" in text
    assert "simulated and illustrative" in text
    assert "Limited statistical power" in text
    for name in ("raw.csv", "summary.csv", "stats.csv", "pareto.csv"):
        assert (tmp_path / name).exists()
    figures = {p.name for p in (tmp_path / "figures").glob("*.png")}
    assert {"route_maps_demo.png", "gantt_demo.png", "battery_timeline_demo.png",
            "pareto_energy_makespan.png", "scaling_compute_time.png",
            "noise_robustness.png"} <= figures  # fmt: skip
    assert (tmp_path / "figures" / "gantt_demo.svg").exists()


def test_report_requires_results(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="no results"):
        build_report(tmp_path)


def test_significance_and_summary_on_real_rows() -> None:
    rows = [execute(RunSpec("A", s, a)) for s in range(3) for a in ("nearest", "integrated")]
    add_optimality_gaps(rows)
    assert summarize(rows)
    tests = significance(rows)
    assert {t["baseline"] for t in tests} == {"nearest"}
    assert all(0.0 <= t["p_holm"] <= 1.0 for t in tests)


def test_cli_runs_an_experiment_and_a_report(tmp_path: Path) -> None:
    code = main(["experiment", "--scenarios", "A", "--algorithms", "nearest,integrated", "--seeds", "1",
                 "--out", str(tmp_path), "--name", "run", "--workers", "2"])  # fmt: skip
    assert code == 0
    assert main(["report", "--dir", str(tmp_path / "run")]) == 0
    assert (tmp_path / "run" / "REPORT.md").exists()
