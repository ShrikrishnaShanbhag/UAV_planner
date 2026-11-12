"""Algorithm registry: maps a name to its planner and default configuration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from uavplan.core.allocation.base import Planner, PlannerConfig
from uavplan.core.allocation.exact_cpsat import ExactConfig, ExactCPSATPlanner
from uavplan.core.allocation.genetic import GeneticConfig, GeneticPlanner
from uavplan.core.allocation.greedy_energy import GreedyEnergyPlanner
from uavplan.core.allocation.independent import IndependentPlanner
from uavplan.core.allocation.integrated import IntegratedConfig, IntegratedPlanner
from uavplan.core.allocation.nearest import NearestUAVPlanner
from uavplan.core.allocation.ortools_vrp import ORToolsConfig, ORToolsVRPPlanner


@dataclass(frozen=True, slots=True)
class AlgorithmInfo:
    name: str
    label: str
    description: str
    factory: Callable[[], Planner]
    config_type: type[PlannerConfig]


ALGORITHMS: dict[str, AlgorithmInfo] = {
    "nearest": AlgorithmInfo(
        "nearest",
        "B1 Nearest-UAV",
        "Closest capable UAV by straight-line distance. No energy check while planning.",
        NearestUAVPlanner,
        PlannerConfig,
    ),
    "greedy_energy": AlgorithmInfo(
        "greedy_energy",
        "B2 Greedy energy-aware",
        "Nearest UAV that can still do the task and return. Appends only.",
        GreedyEnergyPlanner,
        PlannerConfig,
    ),
    "independent": AlgorithmInfo(
        "independent",
        "B3 Independent",
        "Balanced allocation first, routing second, then drops tasks until feasible.",
        IndependentPlanner,
        PlannerConfig,
    ),
    "integrated": AlgorithmInfo(
        "integrated",
        "PROPOSED Integrated",
        "Energy-feasible route planning: scores each insertion by its effect on the whole "
        "route and battery feasibility, then improves within and between routes.",
        IntegratedPlanner,
        IntegratedConfig,
    ),
    "ortools_vrp": AlgorithmInfo(
        "ortools_vrp",
        "B4 OR-Tools VRP",
        "Multi-depot VRP with energy and time dimensions, solved by guided local search.",
        ORToolsVRPPlanner,
        ORToolsConfig,
    ),
    "genetic": AlgorithmInfo(
        "genetic",
        "B5 Genetic algorithm",
        "Giant-tour encoding, order crossover, swap and inversion mutation.",
        GeneticPlanner,
        GeneticConfig,
    ),
    "exact_cpsat": AlgorithmInfo(
        "exact_cpsat",
        "Exact CP-SAT",
        "Exact model for small static instances; gives the reference for optimality gaps.",
        ExactCPSATPlanner,
        ExactConfig,
    ),
}


def get_planner(name: str) -> Planner:
    if name not in ALGORITHMS:
        raise KeyError(f"unknown algorithm '{name}'")
    return ALGORITHMS[name].factory()
