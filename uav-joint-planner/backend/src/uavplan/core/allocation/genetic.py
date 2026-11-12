"""B5 Genetic algorithm: giant-tour encoding with a feasibility-aware split."""

from __future__ import annotations

import time
from dataclasses import dataclass

import numpy as np

from uavplan.core.allocation.base import (
    NO_CAPABLE_UAV,
    Assignment,
    PlannerConfig,
    PlanningContext,
    priority_order,
)
from uavplan.core.models import Plan, Scenario

Tour = tuple[int, ...]


@dataclass(frozen=True, slots=True)
class GeneticConfig(PlannerConfig):
    population: int = 40
    generations: int = 60
    mutation_rate: float = 0.3
    tournament: int = 3
    elite: int = 2


class GeneticPlanner:
    """Evolves one permutation of all tasks; a decoder splits it into UAV routes.

    Decoding walks the tour and appends each task to the current UAV if the route stays
    feasible, otherwise to the next UAV that can take it. Tasks nobody can take are left
    unassigned, so every decoded plan is feasible and fitness only has to rank them.
    """

    name = "genetic"

    def plan(self, scenario: Scenario, config: PlannerConfig | None = None) -> Plan:
        started = time.perf_counter()
        cfg = config if isinstance(config, GeneticConfig) else self._coerce(config)
        ctx = PlanningContext(scenario, cfg)
        rng = np.random.default_rng(cfg.seed)
        ordered = [t.id for t in priority_order(scenario.tasks)]
        if not ordered or not ctx.uav_ids:
            left = {t: "no UAV available" for t in ordered}
            return ctx.build_plan(self.name, ctx.empty_assignment(), left, started, config=cfg)

        cache: dict[Tour, tuple[float, Assignment, dict[str, str]]] = {}

        def evaluate(tour: Tour) -> float:
            if tour not in cache:
                assignment, left = self._decode(ctx, ordered, tour)
                score = ctx.score(assignment, left)
                fitness = score.violations * 1e9 + score.mandatory_unassigned * 1e6 + score.z
                cache[tour] = (fitness, assignment, left)
            return cache[tour][0]

        n = len(ordered)
        population: list[Tour] = [tuple(range(n))]
        while len(population) < cfg.population:
            population.append(tuple(int(i) for i in rng.permutation(n)))

        for _ in range(cfg.generations):
            ranked = sorted(population, key=lambda t: (evaluate(t), t))
            children: list[Tour] = ranked[: cfg.elite]
            while len(children) < cfg.population:
                a = self._select(ranked, cfg.tournament, rng, evaluate)
                b = self._select(ranked, cfg.tournament, rng, evaluate)
                child = self._crossover(a, b, rng)
                if rng.random() < cfg.mutation_rate:
                    child = self._mutate(child, rng)
                children.append(child)
            population = children

        best = min(population, key=lambda t: (evaluate(t), t))
        _, assignment, left = cache[best]
        return ctx.build_plan(self.name, assignment, left, started, config=cfg)

    @staticmethod
    def _coerce(config: PlannerConfig | None) -> GeneticConfig:
        base = config or PlannerConfig()
        return GeneticConfig(
            seed=base.seed, weights=base.weights, objective=base.objective, scales=base.scales
        )

    @staticmethod
    def _decode(
        ctx: PlanningContext, ordered: list[str], tour: Tour
    ) -> tuple[Assignment, dict[str, str]]:
        assignment = ctx.empty_assignment()
        left: dict[str, str] = {}
        current = 0
        m = len(ctx.uav_ids)
        for gene in tour:
            task_id = ordered[gene]
            capable = False
            placed = False
            for offset in range(m):
                index = (current + offset) % m
                uav_id = ctx.uav_ids[index]
                if not ctx.uav_task_ok(uav_id, task_id):
                    continue
                capable = True
                seq = assignment[uav_id] + (task_id,)
                if ctx.evaluate(uav_id, seq).feasible:
                    assignment[uav_id] = seq
                    current = index
                    placed = True
                    break
            if not placed:
                left[task_id] = (
                    "no UAV could take this task in the evolved tour" if capable else NO_CAPABLE_UAV
                )
        return assignment, left

    @staticmethod
    def _select(ranked, size, rng, evaluate) -> Tour:  # type: ignore[no-untyped-def]
        picks = rng.integers(0, len(ranked), size)
        return min((ranked[int(i)] for i in picks), key=lambda t: (evaluate(t), t))

    @staticmethod
    def _crossover(a: Tour, b: Tour, rng: np.random.Generator) -> Tour:
        """Order crossover (OX)."""
        n = len(a)
        i, j = sorted(int(x) for x in rng.integers(0, n, 2))
        slice_a = a[i : j + 1]
        taken = set(slice_a)
        rest = [g for g in b if g not in taken]
        return tuple(rest[:i]) + slice_a + tuple(rest[i:])

    @staticmethod
    def _mutate(tour: Tour, rng: np.random.Generator) -> Tour:
        """Swap two genes, or invert a segment."""
        i, j = sorted(int(x) for x in rng.integers(0, len(tour), 2))
        if rng.random() < 0.5:
            genes = list(tour)
            genes[i], genes[j] = genes[j], genes[i]
            return tuple(genes)
        return tour[:i] + tour[i : j + 1][::-1] + tour[j + 1 :]
