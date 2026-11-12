"""Assignment cost ``C_ij``, global objective ``Z`` and the lexicographic plan order."""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Literal, NamedTuple

from uavplan.core.energy import EnergyEstimator
from uavplan.core.feasibility import RouteEval
from uavplan.core.models import ObjectiveBreakdown, Plan, Scenario, Task, Violation

Normalization = Literal["max", "minmax"]
_TOL = 1e-9


@dataclass(frozen=True, slots=True)
class CostWeights:
    """Weights of ``C_ij = w_d·D̂ + w_e·Ê + w_t·T̂ + w_l·L̂``."""

    w_d: float = 0.2
    w_e: float = 0.4
    w_t: float = 0.3
    w_l: float = 0.1
    normalization: Normalization = "max"


@dataclass(frozen=True, slots=True)
class ObjectiveWeights:
    """Weights of ``Z``; ``lam · M · P_j`` is charged per unassigned task."""

    alpha: float = 0.35
    beta: float = 0.35
    gamma: float = 0.1
    delta: float = 0.2
    lam: float = 1.0
    big_m: float = 10.0


@dataclass(frozen=True, slots=True)
class ObjectiveScales:
    """Reference magnitudes so that each term of ``Z`` is dimensionless."""

    energy: float = 1.0
    time: float = 1.0
    distance: float = 1.0
    load_mean: float = 1.0


@dataclass(slots=True)
class InsertionCandidate:
    """Effect of inserting one task into one UAV's route at one position."""

    uav_id: str
    position: int
    seq: tuple[str, ...]
    delta_distance: float
    delta_energy: float
    delta_time: float
    delta_imbalance: float
    violations: tuple[Violation, ...] = ()
    cost: float = field(default=math.inf)

    @property
    def feasible(self) -> bool:
        return not self.violations


def coefficient_of_variation(loads: Sequence[float]) -> float:
    if not loads:
        return 0.0
    mean = math.fsum(loads) / len(loads)
    if mean <= _TOL:
        return 0.0
    variance = math.fsum((x - mean) ** 2 for x in loads) / len(loads)
    return math.sqrt(variance) / mean


def variance(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    mean = math.fsum(values) / len(values)
    return math.fsum((x - mean) ** 2 for x in values) / len(values)


def _normalizer(values: list[float], mode: Normalization) -> list[float]:
    if mode == "minmax":
        low, high = min(values), max(values)
        span = high - low
        return [0.0 if span <= _TOL else (v - low) / span for v in values]
    scale = max(abs(v) for v in values)
    return [0.0 if scale <= _TOL else v / scale for v in values]


def score_candidates(candidates: list[InsertionCandidate], weights: CostWeights) -> None:
    """Set ``cost`` on each candidate, normalizing each term over the current step."""
    if not candidates:
        return
    mode = weights.normalization
    d = _normalizer([c.delta_distance for c in candidates], mode)
    e = _normalizer([c.delta_energy for c in candidates], mode)
    t = _normalizer([c.delta_time for c in candidates], mode)
    l_hat = _normalizer([c.delta_imbalance for c in candidates], mode)
    for i, candidate in enumerate(candidates):
        candidate.cost = (
            weights.w_d * d[i] + weights.w_e * e[i] + weights.w_t * t[i] + weights.w_l * l_hat[i]
        )


def fixed_scales(scenario: Scenario) -> ObjectiveScales:
    """Scenario-derived scales, used when no reference plan is available."""
    span = (scenario.grid.width + scenario.grid.height) * scenario.grid.cell_size
    n_uav = max(1, len(scenario.uavs))
    speed = sum(u.speed for u in scenario.uavs) / n_uav if scenario.uavs else 10.0
    work = sum(t.duration for t in scenario.tasks)
    distance = max(span * n_uav, 1.0)
    time_scale = max(span / speed + work / n_uav, 1.0)
    energy = EnergyEstimator(scenario.energy).nominal_energy(
        scenario.uavs, scenario.tasks, distance
    )
    return ObjectiveScales(max(energy, 1.0), time_scale, distance, time_scale)


def compute_objective(
    evals: Iterable[RouteEval],
    unassigned: Iterable[Task],
    weights: ObjectiveWeights,
    scales: ObjectiveScales,
) -> ObjectiveBreakdown:
    """``Z = α·ΣE_i + β·max T_i + γ·ΣD_i + δ·Var(L_i) + λ·Σ_{unassigned} P_j·M``.

    Each of the first four terms is divided by a reference scale. Variance is divided
    by the squared reference load, so that term behaves like a squared CV.
    """
    routes = list(evals)
    total_energy = math.fsum(e.total_energy for e in routes)
    makespan = max((e.completion_time for e in routes), default=0.0)
    total_distance = math.fsum(e.distance for e in routes)
    load_variance = variance([e.load for e in routes])
    energy_term = weights.alpha * total_energy / scales.energy
    time_term = weights.beta * makespan / scales.time
    distance_term = weights.gamma * total_distance / scales.distance
    balance_term = weights.delta * load_variance / (scales.load_mean**2)
    penalty = weights.lam * weights.big_m * sum(int(t.priority) for t in unassigned)
    return ObjectiveBreakdown(
        z=energy_term + time_term + distance_term + balance_term + penalty,
        total_energy=total_energy,
        makespan=makespan,
        total_distance=total_distance,
        load_variance=load_variance,
        unassigned_penalty=penalty,
        energy_term=energy_term,
        time_term=time_term,
        distance_term=distance_term,
        balance_term=balance_term,
    )


class PlanScore(NamedTuple):
    """Lexicographic key. Lower is better in every field."""

    violations: int
    mandatory_unassigned: int
    z: float


def compare_scores(a: PlanScore, b: PlanScore) -> int:
    """Return negative if ``a`` is better, positive if ``b`` is better, else 0."""
    if a.violations != b.violations:
        return -1 if a.violations < b.violations else 1
    if a.mandatory_unassigned != b.mandatory_unassigned:
        return -1 if a.mandatory_unassigned < b.mandatory_unassigned else 1
    if abs(a.z - b.z) <= _TOL:
        return 0
    return -1 if a.z < b.z else 1


def plan_score(plan: Plan, tasks: dict[str, Task]) -> PlanScore:
    mandatory = sum(1 for u in plan.unassigned if tasks[u.task_id].mandatory)
    return PlanScore(len(plan.report.violations), mandatory, plan.objective.z)


def compare_plans(a: Plan, b: Plan, tasks: dict[str, Task]) -> int:
    """Order plans by: (1) hard constraints, (2) mandatory tasks, (3/4) ``Z``."""
    return compare_scores(plan_score(a, tasks), plan_score(b, tasks))
