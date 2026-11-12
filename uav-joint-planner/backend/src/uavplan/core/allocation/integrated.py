"""PROPOSED: energy-feasible route planning.

Each candidate assignment is judged by its effect on the UAV's whole route and battery
feasibility, not just by distance. See ``docs/ALGORITHMS.md`` for pseudocode.
"""

from __future__ import annotations

import time
from collections.abc import Collection, Iterable
from dataclasses import dataclass, replace

import numpy as np

from uavplan.core.allocation.base import (
    NO_CAPABLE_UAV,
    Assignment,
    PlannerConfig,
    PlanningContext,
    Seq,
    better_score,
    priority_order,
)
from uavplan.core.cost import (
    InsertionCandidate,
    coefficient_of_variation,
    compare_scores,
    score_candidates,
)
from uavplan.core.models import (
    CandidateTrace,
    Plan,
    Scenario,
    Task,
    TaskExplanation,
    charge_stop,
    is_charge_stop,
)
from uavplan.core.routing.improve import inter_route_search, or_opt, two_opt


@dataclass(frozen=True, slots=True)
class IntegratedConfig(PlannerConfig):
    """Each ``use_*`` flag can be switched off for an ablation study."""

    use_energy_in_cost: bool = True
    use_workload_term: bool = True
    use_insertion: bool = True
    use_local_search: bool = True
    use_inter_route: bool = True
    use_repair_move: bool = True
    strict_prefix_safety: bool = True
    use_lns: bool = False
    lns_iterations: int = 200
    lns_remove: int = 3
    max_inter_route_moves: int = 300
    repair_budget: int = 5000
    rescue_budget: int = 20000
    allow_recharge: bool = False
    max_recharges: int = 1


def as_integrated(config: PlannerConfig | None) -> IntegratedConfig:
    if isinstance(config, IntegratedConfig):
        return config
    base = config or PlannerConfig()
    return IntegratedConfig(
        seed=base.seed, weights=base.weights, objective=base.objective, scales=base.scales
    )


class IntegratedSearch:
    """Construction, repair and improvement steps, also reused by the replanning engine."""

    def __init__(self, ctx: PlanningContext, config: IntegratedConfig) -> None:
        self.ctx = ctx
        self.cfg = config
        weights = config.weights
        search = ctx.objective_weights
        if not config.use_energy_in_cost:
            weights = replace(weights, w_e=0.0)
            search = replace(search, alpha=0.0)
        if not config.use_workload_term:
            weights = replace(weights, w_l=0.0)
            search = replace(search, delta=0.0)
        self.weights = weights
        ctx.search_weights = search

    # -- insertion ---------------------------------------------------------------------

    def candidates(
        self, task: Task, assignment: Assignment
    ) -> tuple[list[InsertionCandidate], dict[str, str]]:
        """All feasible insertions of ``task``, scored, plus why other UAVs were rejected."""
        ctx = self.ctx
        loads = {u: ctx.evaluate(u, assignment[u]).load for u in ctx.uav_ids}
        base_cv = coefficient_of_variation(list(loads.values()))
        feasible: list[InsertionCandidate] = []
        rejected: dict[str, str] = {}
        for uav_id in ctx.uav_ids:
            if not ctx.uav_task_ok(uav_id, task.id):
                rejected[uav_id] = f"lacks capability '{task.capability}'"
                continue
            seq = assignment[uav_id]
            current = ctx.evaluate(uav_id, seq)
            positions: Iterable[int] = (
                range(len(seq) + 1) if self.cfg.use_insertion else (len(seq),)
            )
            reason: str | None = None
            found = False
            for position in positions:
                new_seq = seq[:position] + (task.id,) + seq[position:]
                ev = ctx.evaluate(uav_id, new_seq)
                if not ev.feasible:
                    if reason is None:
                        first = ev.violations[0]
                        reason = f"{first.type.value}: {first.detail}"
                    continue
                found = True
                new_loads = [ev.load if u == uav_id else loads[u] for u in ctx.uav_ids]
                feasible.append(
                    InsertionCandidate(
                        uav_id=uav_id,
                        position=position,
                        seq=new_seq,
                        delta_distance=ev.distance - current.distance,
                        delta_energy=ev.total_energy - current.total_energy,
                        delta_time=ev.completion_time - current.completion_time,
                        delta_imbalance=coefficient_of_variation(new_loads) - base_cv,
                    )
                )
            if not found and reason is not None:
                rejected[uav_id] = reason
        score_candidates(feasible, self.weights)
        return feasible, rejected

    @staticmethod
    def _best(candidates: list[InsertionCandidate]) -> InsertionCandidate:
        return min(candidates, key=lambda c: (c.cost, c.uav_id, c.position))

    def _trace(
        self,
        task: Task,
        feasible: list[InsertionCandidate],
        rejected: dict[str, str],
        chosen: str | None,
        note: str,
    ) -> TaskExplanation:
        traces: list[CandidateTrace] = []
        per_uav: dict[str, InsertionCandidate] = {}
        for candidate in feasible:
            held = per_uav.get(candidate.uav_id)
            if held is None or (candidate.cost, candidate.position) < (held.cost, held.position):
                per_uav[candidate.uav_id] = candidate
        for uav_id in self.ctx.uav_ids:
            if uav_id in per_uav:
                c = per_uav[uav_id]
                traces.append(
                    CandidateTrace(
                        uav_id,
                        c.position,
                        True,
                        c.delta_distance,
                        c.delta_energy,
                        c.delta_time,
                        c.delta_imbalance,
                        c.cost,
                    )
                )
            else:
                traces.append(
                    CandidateTrace(
                        uav_id, None, False, rejected_reason=rejected.get(uav_id, "not considered")
                    )
                )
        return TaskExplanation(task.id, chosen, note, tuple(traces))

    def insert_tasks(
        self,
        tasks: Iterable[Task],
        assignment: Assignment,
        unassigned: dict[str, str],
        explanations: dict[str, TaskExplanation] | None = None,
    ) -> None:
        """Insert tasks in priority order at their cheapest feasible position."""
        for task in priority_order(tasks):
            feasible, rejected = self.candidates(task, assignment)
            note = "lowest-cost feasible insertion"
            chosen: str | None = None
            if feasible:
                best = self._best(feasible)
                assignment[best.uav_id] = best.seq
                chosen = best.uav_id
            elif self.cfg.use_repair_move and self._repair(task, assignment):
                chosen = next(u for u in sorted(assignment) if task.id in assignment[u])
                note = "assigned after relocating another task to make room"
            elif self.cfg.allow_recharge and self._insert_with_recharge(task, assignment):
                chosen = next(u for u in sorted(assignment) if task.id in assignment[u])
                note = "assigned by adding a charging stop"
            if chosen is None:
                unassigned[task.id] = self._reason(rejected)
                note = "no feasible UAV"
            else:
                unassigned.pop(task.id, None)
            if explanations is not None:
                explanations[task.id] = self._trace(task, feasible, rejected, chosen, note)

    @staticmethod
    def _reason(rejected: dict[str, str]) -> str:
        if not rejected:
            return NO_CAPABLE_UAV
        if all(r.startswith("lacks capability") for r in rejected.values()):
            return NO_CAPABLE_UAV
        return "no feasible UAV: " + "; ".join(f"{u}: {rejected[u]}" for u in sorted(rejected))

    def _repair(self, task: Task, assignment: Assignment) -> bool:
        """Relocate one assigned task to another UAV so that ``task`` fits in its place."""
        ctx = self.ctx
        budget = self.cfg.repair_budget
        best: tuple[float, str, str, str, Seq, Seq] | None = None
        for donor in ctx.uav_ids:
            if not ctx.uav_task_ok(donor, task.id):
                continue
            route = assignment[donor]
            for i, moved in enumerate(route):
                shortened = route[:i] + route[i + 1 :]
                fitted: list[Seq] = []
                for p in range(len(shortened) + 1):
                    budget -= 1
                    trial = shortened[:p] + (task.id,) + shortened[p:]
                    if ctx.evaluate(donor, trial).feasible:
                        fitted.append(trial)
                if not fitted:
                    continue
                donor_seq = min(fitted, key=lambda s: (ctx.evaluate(donor, s).total_energy, s))
                donor_energy = ctx.evaluate(donor, donor_seq).total_energy
                for receiver in ctx.uav_ids:
                    if receiver == donor or not ctx.uav_task_ok(receiver, moved):
                        continue
                    target = assignment[receiver]
                    for p in range(len(target) + 1):
                        budget -= 1
                        new_target = target[:p] + (moved,) + target[p:]
                        ev = ctx.evaluate(receiver, new_target)
                        if not ev.feasible:
                            continue
                        energy = ev.total_energy + donor_energy
                        option = (energy, donor, receiver, moved, donor_seq, new_target)
                        if best is None or option[:4] < best[:4]:
                            best = option
                    if budget <= 0:
                        break
                if budget <= 0:
                    break
            if budget <= 0:
                break
        if best is None:
            return False
        _, donor, receiver, _, donor_seq, new_target = best
        assignment[donor] = donor_seq
        assignment[receiver] = new_target
        return True

    def _insert_with_recharge(self, task: Task, assignment: Assignment) -> bool:
        """Before dropping a task, try adding a charging stop to a route that could take it."""
        ctx = self.ctx
        stops = [charge_stop(s.id) for s in sorted(ctx.scenario.stations, key=lambda s: s.id)]
        best: tuple[float, str, Seq] | None = None
        for uav_id in ctx.uav_ids:
            seq = assignment[uav_id]
            if not ctx.uav_task_ok(uav_id, task.id):
                continue
            if sum(is_charge_stop(entry) for entry in seq) >= self.cfg.max_recharges:
                continue
            for position in range(len(seq) + 1):
                with_task = seq[:position] + (task.id,) + seq[position:]
                for place in range(len(with_task) + 1):
                    for stop in stops:
                        trial = with_task[:place] + (stop,) + with_task[place:]
                        ev = ctx.evaluate(uav_id, trial)
                        option = (ev.total_energy, uav_id, trial)
                        if ev.feasible and (best is None or option < best):
                            best = option
        if best is None:
            return False
        assignment[best[1]] = best[2]
        return True

    def _prune_stops(self, assignment: Assignment) -> None:
        """Remove charging stops that later improvements have made unnecessary."""
        ctx = self.ctx
        for uav_id, seq in assignment.items():
            for entry in [e for e in seq if is_charge_stop(e)]:
                shorter = tuple(e for e in assignment[uav_id] if e != entry)
                if ctx.evaluate(uav_id, shorter).feasible:
                    assignment[uav_id] = shorter

    # -- improvement -------------------------------------------------------------------

    def improve_routes(
        self, assignment: Assignment, unassigned: dict[str, str], uavs: Collection[str]
    ) -> None:
        """2-opt and or-opt on the given routes; only strictly better moves are accepted."""
        left = list(unassigned)
        for uav_id in sorted(uavs):
            self._improve_route(assignment, uav_id, left)

    def _improve_route(self, assignment: Assignment, uav_id: str, left: list[str]) -> None:
        ctx = self.ctx

        def route_score(seq: Seq) -> tuple[int, int, float]:
            trial = dict(assignment)
            trial[uav_id] = seq
            score = ctx.score(trial, left)
            return (score.violations, score.mandatory_unassigned, score.z)

        def better(a: tuple[int, int, float], b: tuple[int, int, float]) -> bool:
            return a[:2] < b[:2] or (a[:2] == b[:2] and a[2] < b[2] - 1e-9)

        assignment[uav_id] = or_opt(
            two_opt(assignment[uav_id], route_score, better), route_score, better
        )

    def _inter_route(self, assignment: Assignment, left: list[str]) -> Assignment:
        ctx = self.ctx
        return inter_route_search(
            assignment,
            lambda a: ctx.score(a, left),
            better_score,
            ctx.uav_task_ok,
            self.cfg.max_inter_route_moves,
        )

    def improve(self, assignment: Assignment, unassigned: dict[str, str]) -> Assignment:
        """Full improvement pass: within routes, between routes, then refill."""
        ctx = self.ctx
        result = dict(assignment)
        for _ in range(2):
            if self.cfg.use_local_search:
                self.improve_routes(result, unassigned, ctx.uav_ids)
            if self.cfg.use_inter_route:
                result = self._inter_route(result, list(unassigned))
                if self.cfg.use_local_search:
                    self.improve_routes(result, unassigned, ctx.uav_ids)
            if not (unassigned and (self.cfg.use_local_search or self.cfg.use_inter_route)):
                break
            before = len(unassigned)
            self.insert_tasks([ctx.tasks[t] for t in sorted(unassigned)], result, unassigned)
            if len(unassigned) == before:
                break
        return result

    def lns(self, assignment: Assignment, unassigned: dict[str, str]) -> Assignment:
        """Large-neighbourhood search with a deterministic iteration budget."""
        ctx = self.ctx
        rng = np.random.default_rng(self.cfg.seed)
        best, best_left = dict(assignment), dict(unassigned)
        best_score = ctx.score(best, best_left)
        for _ in range(self.cfg.lns_iterations):
            placed = sorted(t for seq in best.values() for t in seq if t in ctx.tasks)
            if not placed:
                break
            count = min(self.cfg.lns_remove, len(placed))
            removed = {placed[int(i)] for i in rng.choice(len(placed), count, replace=False)}
            trial = {u: tuple(t for t in seq if t not in removed) for u, seq in best.items()}
            trial_left = dict(best_left)
            pool = sorted(removed | set(trial_left))
            self.insert_tasks([ctx.tasks[t] for t in pool], trial, trial_left)
            score = ctx.score(trial, trial_left)
            if better_score(score, best_score):
                best, best_left, best_score = trial, trial_left, score
        unassigned.clear()
        unassigned.update(best_left)
        return best

    # -- mandatory rescue --------------------------------------------------------------

    def _search_mandatory(
        self, tasks: list[Task], assignment: Assignment, index: int, budget: list[int]
    ) -> Assignment | None:
        if index == len(tasks):
            return dict(assignment)
        ctx = self.ctx
        task = tasks[index]
        options: list[tuple[float, str, int, Seq]] = []
        for uav_id in ctx.uav_ids:
            if not ctx.uav_task_ok(uav_id, task.id):
                continue
            seq = assignment[uav_id]
            for position in range(len(seq) + 1):
                budget[0] -= 1
                new_seq = seq[:position] + (task.id,) + seq[position:]
                ev = ctx.evaluate(uav_id, new_seq)
                if ev.feasible:
                    options.append((ev.total_energy, uav_id, position, new_seq))
        for _, uav_id, _, new_seq in sorted(options):
            if budget[0] <= 0:
                return None
            old = assignment[uav_id]
            assignment[uav_id] = new_seq
            found = self._search_mandatory(tasks, assignment, index + 1, budget)
            assignment[uav_id] = old
            if found is not None:
                return found
        return None

    def rescue_mandatory(
        self, assignment: Assignment, unassigned: dict[str, str]
    ) -> tuple[Assignment, dict[str, str]]:
        """Backtrack over mandatory tasks if the greedy passes left any unassigned.

        Inserting tasks one at a time at every position enumerates all assignments and
        orderings, so this search is complete whenever its budget is not exhausted.
        """
        ctx = self.ctx
        if not any(ctx.tasks[t].mandatory for t in unassigned):
            return assignment, unassigned
        mandatory = sorted(
            (t for t in ctx.tasks.values() if t.mandatory),
            key=lambda t: (
                sum(ctx.uav_task_ok(u, t.id) for u in ctx.uav_ids),
                t.deadline if t.deadline is not None else float("inf"),
                t.id,
            ),
        )
        seed = ctx.empty_assignment()
        solved = self._search_mandatory(mandatory, seed, 0, [self.cfg.rescue_budget])
        if solved is None:
            return assignment, unassigned
        left: dict[str, str] = {}
        others = [t for t in ctx.tasks.values() if not t.mandatory]
        self.insert_tasks(others, solved, left)
        solved = self.improve(solved, left)
        if compare_scores(ctx.score(solved, left), ctx.score(assignment, unassigned)) < 0:
            return solved, left
        return assignment, unassigned


class IntegratedPlanner:
    """Energy-feasible route planning with feasibility-aware insertion and local search."""

    name = "integrated"

    def plan(self, scenario: Scenario, config: PlannerConfig | None = None) -> Plan:
        started = time.perf_counter()
        cfg = as_integrated(config)
        ctx = PlanningContext(scenario, cfg, strict_prefix=cfg.strict_prefix_safety)
        search = IntegratedSearch(ctx, cfg)
        assignment = ctx.empty_assignment()
        unassigned: dict[str, str] = {}
        explanations: dict[str, TaskExplanation] = {}
        search.insert_tasks(scenario.tasks, assignment, unassigned, explanations)
        assignment = search.improve(assignment, unassigned)
        if cfg.allow_recharge:
            search._prune_stops(assignment)
        if cfg.use_lns:
            assignment = search.lns(assignment, unassigned)
        assignment, unassigned = search.rescue_mandatory(assignment, unassigned)
        return ctx.build_plan(
            self.name,
            assignment,
            unassigned,
            started,
            tuple(explanations[t] for t in sorted(explanations)),
            cfg,
        )
