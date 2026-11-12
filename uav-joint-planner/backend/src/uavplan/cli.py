"""Command line interface: ``uavplan plan|simulate|generate|experiment|report``."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from uavplan.core.generator import PRESET_IDS, preset
from uavplan.core.metrics import plan_metrics, simulation_metrics
from uavplan.core.models import Scenario
from uavplan.core.registry import ALGORITHMS, get_planner
from uavplan.core.simulator import SimConfig, Simulator

NOTE = "Values are simulated and illustrative, in normalized energy units unless stated."


def load_scenario(source: str, seed: int) -> Scenario:
    """Load a preset by id, or a scenario from a JSON file."""
    if source.upper() in PRESET_IDS:
        return preset(source, seed)
    return Scenario.from_dict(json.loads(Path(source).read_text(encoding="utf-8")))


def _print_metrics(title: str, metrics: dict[str, float]) -> None:
    print(title)
    for name in sorted(metrics):
        print(f"  {name:28s} {metrics[name]:.4f}")
    print(NOTE)


def cmd_plan(args: argparse.Namespace) -> int:
    scenario = load_scenario(args.scenario, args.seed)
    plan = get_planner(args.algorithm).plan(scenario)
    if args.out:
        Path(args.out).write_text(json.dumps(plan.to_dict(), indent=1), encoding="utf-8")
    for route in plan.routes:
        print(f"{route.uav_id}: {' -> '.join(route.task_ids) or '(idle)'}")
    for violation in plan.report.violations:
        print(f"VIOLATION {violation.type}: {violation.detail}")
    _print_metrics(f"Plan metrics ({plan.algorithm})", plan_metrics(plan, scenario))
    return 0


def cmd_simulate(args: argparse.Namespace) -> int:
    scenario = load_scenario(args.scenario, args.seed)
    plan = get_planner(args.algorithm).plan(scenario)
    result = Simulator(scenario, plan, SimConfig(noise_sigma=args.noise, seed=args.seed)).run()
    if args.out:
        Path(args.out).write_text(json.dumps(result.to_dict(), indent=1), encoding="utf-8")
    _print_metrics(f"Simulation metrics ({plan.algorithm})", simulation_metrics(result, scenario))
    return 0


def cmd_generate(args: argparse.Namespace) -> int:
    Path(args.out).write_text(preset(args.preset, args.seed).to_json() + "\n", encoding="utf-8")
    print(f"wrote {args.out}")
    return 0


def cmd_experiment(args: argparse.Namespace) -> int:
    from uavplan.core.missions import MISSION_IDS
    from uavplan.experiments.report import build_report
    from uavplan.experiments.runner import ALL_ALGORITHMS, build_specs, run_experiments

    names = list(ALL_ALGORITHMS) if args.algorithms == "all" else args.algorithms.split(",")
    scenarios = [
        name
        for token in args.scenarios.split(",")
        for name in (MISSION_IDS if token == "missions" else (token,))
    ]
    specs = build_specs(scenarios, names, args.seeds, args.ablations)
    name = args.name or datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = Path(args.out) / name

    def progress(done: int, total: int) -> None:
        print(f"\r{done}/{total} runs", end="", flush=True)

    run_experiments(specs, out_dir, args.workers, progress)
    print(f"\nresults in {out_dir}")
    if args.report:
        print(f"report: {build_report(out_dir)}")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    from uavplan.experiments.report import build_report

    print(f"report: {build_report(Path(args.dir))}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="uavplan", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--scenario", default="A", help="preset id or path to a JSON file")
        p.add_argument("--algorithm", default="greedy_energy", choices=sorted(ALGORITHMS))
        p.add_argument("--seed", type=int, default=42)
        p.add_argument("--out", default=None)

    plan = sub.add_parser("plan", help="plan a scenario and print its metrics")
    common(plan)
    plan.set_defaults(func=cmd_plan)

    simulate = sub.add_parser("simulate", help="plan and simulate a scenario")
    common(simulate)
    simulate.add_argument("--noise", type=float, default=0.0, help="energy noise sigma")
    simulate.set_defaults(func=cmd_simulate)

    generate = sub.add_parser("generate", help="write a preset scenario to JSON")
    generate.add_argument("--preset", default="A")
    generate.add_argument("--seed", type=int, default=42)
    generate.add_argument("--out", required=True)
    generate.set_defaults(func=cmd_generate)

    experiment = sub.add_parser("experiment", help="run a batch of experiments")
    experiment.add_argument("--scenarios", default="A,B,C,D,E")
    experiment.add_argument("--algorithms", default="all")
    experiment.add_argument("--seeds", type=int, default=30)
    experiment.add_argument("--out", default="results")
    experiment.add_argument("--name", default=None, help="run folder; reuse a name to resume")
    experiment.add_argument("--workers", type=int, default=None)
    experiment.add_argument("--ablations", action="store_true")
    experiment.add_argument("--report", action="store_true", help="also build REPORT.md")
    experiment.set_defaults(func=cmd_experiment)

    report = sub.add_parser("report", help="build REPORT.md for a finished run")
    report.add_argument("--dir", required=True)
    report.set_defaults(func=cmd_report)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result: int = args.func(args)
    return result


if __name__ == "__main__":
    sys.exit(main())
