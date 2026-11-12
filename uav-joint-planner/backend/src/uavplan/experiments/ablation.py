"""Experiment variants: ablations of the proposed method and the weight sweep."""

from __future__ import annotations

from dataclasses import replace

from uavplan.core.allocation.integrated import IntegratedConfig, IntegratedPlanner
from uavplan.core.cost import CostWeights, ObjectiveWeights
from uavplan.core.generator import preset
from uavplan.core.metrics import plan_metrics
from uavplan.core.replanning import ReplanConfig

FULL = "full"
STATIC_ABLATIONS: dict[str, dict[str, bool]] = {
    "no_energy_cost": {"use_energy_in_cost": False},
    "no_workload": {"use_workload_term": False},
    "no_insertion": {"use_insertion": False},
    "no_local_search": {"use_local_search": False},
    "no_inter_route": {"use_inter_route": False},
    "no_prefix_safety": {"strict_prefix_safety": False},
}
# ``None`` means events are applied but the plan is never repaired.
DYNAMIC_ABLATIONS: dict[str, ReplanConfig | None] = {
    "no_replanning": None,
    "local_only": ReplanConfig(mode="local_only"),
    "always_global": ReplanConfig(mode="always_global"),
}
VARIANT_LABELS = {
    FULL: "Full method",
    "no_energy_cost": "No energy in cost",
    "no_workload": "No workload term",
    "no_insertion": "Append only",
    "no_local_search": "No local search",
    "no_inter_route": "No inter-route search",
    "no_prefix_safety": "No prefix safety",
    "no_replanning": "No replanning",
    "local_only": "Local repair only",
    "always_global": "Always global replan",
}


def integrated_config(variant: str, seed: int) -> IntegratedConfig:
    """The planner configuration for a variant of the proposed method."""
    return IntegratedConfig(seed=seed, **STATIC_ABLATIONS.get(variant, {}))


def weight_sweep(scenario_id: str = "B", seeds: int = 3) -> list[dict[str, float]]:
    """Trade energy against makespan by sweeping the cost and objective weights."""
    rows: list[dict[str, float]] = []
    planner = IntegratedPlanner()
    for step in range(9):
        share = 0.1 + 0.1 * step  # the energy share of the energy + time budget
        cost = CostWeights(
            w_d=0.2, w_e=round(0.7 * share, 3), w_t=round(0.7 * (1 - share), 3), w_l=0.1
        )
        objective = ObjectiveWeights(alpha=round(0.7 * share, 3), beta=round(0.7 * (1 - share), 3))
        for seed in range(seeds):
            scenario = preset(scenario_id, seed)
            base = replace(IntegratedConfig(seed=seed), weights=cost, objective=objective)
            metrics = plan_metrics(planner.plan(scenario, base), scenario)
            rows.append(
                {
                    "energy_share": round(share, 3),
                    "seed": float(seed),
                    "total_energy": metrics["total_energy"],
                    "makespan": metrics["makespan"],
                    "completion_rate": metrics["completion_rate"],
                }
            )
    return rows
