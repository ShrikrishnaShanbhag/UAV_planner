"""Exact CP-SAT model of the planning problem, for small static instances.

The optimality gap is meaningful only when the solver proves optimality, so the plan
records ``proven_optimal``. Energy and time are rounded up to integers, which makes the
model slightly conservative. The returned plan is re-evaluated with the real checker.
"""

from __future__ import annotations

import dataclasses
import math
import time
from dataclasses import dataclass
from itertools import combinations, pairwise
from typing import Any

from uavplan.core.allocation.base import Assignment, PlannerConfig, PlanningContext
from uavplan.core.allocation.integrated import IntegratedPlanner
from uavplan.core.models import Plan, Scenario

_E = 1000
_T = 10
_D = 10
_OBJECTIVE = 10**10
# Exceeds the largest possible sum of optional penalties for the supported sizes.
_MANDATORY_WEIGHT = 1000.0


@dataclass(frozen=True, slots=True)
class ExactConfig(PlannerConfig):
    """``max_deterministic_time`` keeps runs reproducible; the wall clock is a safety cap."""

    time_limit_s: float = 60.0
    max_deterministic_time: float = 120.0
    num_workers: int = 8
    max_uavs: int = 4
    max_tasks: int = 12
    use_hint: bool = True


class ExactCPSATPlanner:
    """Minimizes the lexicographic objective exactly, within the solver's limits."""

    name = "exact_cpsat"

    def plan(self, scenario: Scenario, config: PlannerConfig | None = None) -> Plan:
        from ortools.sat.python import cp_model

        started = time.perf_counter()
        cfg = config if isinstance(config, ExactConfig) else self._coerce(config)
        if len(scenario.uavs) > cfg.max_uavs or len(scenario.tasks) > cfg.max_tasks:
            raise ValueError(
                f"exact solver is limited to {cfg.max_uavs} UAVs and {cfg.max_tasks} tasks"
            )
        if any(u.airborne for u in scenario.uavs):
            raise ValueError("exact solver supports static missions only")
        ctx = PlanningContext(scenario, cfg)
        model = cp_model.CpModel()
        arcs, tasks = self._build(model, ctx, cfg)

        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = cfg.time_limit_s
        solver.parameters.max_deterministic_time = cfg.max_deterministic_time
        solver.parameters.num_workers = cfg.num_workers
        solver.parameters.interleave_search = True
        solver.parameters.random_seed = cfg.seed
        status = solver.solve(model)

        proven = status == cp_model.OPTIMAL
        assignment = ctx.empty_assignment()
        unassigned = {t: "no solution found within the solver's limits" for t in ctx.tasks}
        if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            assignment = self._extract(solver, ctx, arcs, tasks)
            placed = {t for seq in assignment.values() for t in seq}
            unassigned = {
                t: "the optimal plan leaves this task out" for t in ctx.tasks if t not in placed
            }
        plan = ctx.build_plan(self.name, assignment, unassigned, started, config=cfg)
        extra = {"proven_optimal": proven, "solver_status": solver.status_name(status)}
        return dataclasses.replace(plan, config={**plan.config, **extra})

    @staticmethod
    def _coerce(config: PlannerConfig | None) -> ExactConfig:
        base = config or PlannerConfig()
        return ExactConfig(
            seed=base.seed, weights=base.weights, objective=base.objective, scales=base.scales
        )

    @staticmethod
    def _horizon(ctx: PlanningContext) -> int:
        """Upper bound on any finish time, in solver time units."""
        checker = ctx.checker
        longest = 0.0
        cells = [t.cell for t in ctx.tasks.values()]
        for uav_id in ctx.uav_ids:
            uav = checker.uavs[uav_id]
            for a in [uav.start, *cells]:
                for b in cells:
                    leg = checker.leg(uav, a, b)
                    if leg is not None:
                        longest = max(longest, leg.time)
                ret = checker.return_option(uav, a)
                if ret is not None:
                    longest = max(longest, ret.time)
        work = sum(t.duration for t in ctx.tasks.values())
        release = max((t.release_time for t in ctx.tasks.values()), default=0.0)
        ready = max((checker.uavs[u].ready_time for u in ctx.uav_ids), default=0.0)
        # One extra unit per arc absorbs the rounding up of each arc's time.
        arcs = len(ctx.tasks) + 1
        return math.ceil((ready + release + work + arcs * longest) * _T) + 2 * arcs

    def _build(
        self, model: Any, ctx: PlanningContext, cfg: ExactConfig
    ) -> tuple[dict[str, dict[tuple[int, int], Any]], list[str]]:
        checker, est = ctx.checker, ctx.checker.estimator
        task_ids = sorted(ctx.tasks)
        node_of = {t: i + 1 for i, t in enumerate(task_ids)}
        horizon = self._horizon(ctx)

        assigned: dict[str, Any] = {t: model.new_bool_var(f"a_{t}") for t in task_ids}
        # A task is served by at most one UAV, so its finish time needs no per-UAV copy.
        finish = {t: model.new_int_var(0, horizon, f"f_{t}") for t in task_ids}
        arrival = {t: model.new_int_var(0, horizon, f"t_{t}") for t in task_ids}
        for t in task_ids:
            # No idling: a UAV starts on arrival, or at release if it arrives early. Loose
            # bounds here would let the solver pad a load to fake a lower variance.
            start = model.new_int_var(0, horizon, f"b_{t}")
            release = math.ceil(ctx.tasks[t].release_time * _T)
            model.add_max_equality(start, [arrival[t], release])
            model.add(finish[t] == start + math.ceil(ctx.tasks[t].duration * _T))
        arcs_by_uav: dict[str, dict[tuple[int, int], Any]] = {}
        visits_by_uav: dict[str, dict[str, Any]] = {}
        loads: list[Any] = []
        completions: list[Any] = []
        energy_terms: list[Any] = []
        distance_terms: list[Any] = []

        for uav_id in ctx.uav_ids:
            uav = checker.uavs[uav_id]
            arcs: list[tuple[int, int, Any]] = []
            lits: dict[tuple[int, int], Any] = {}
            ready = math.ceil(uav.ready_time * _T)
            limit = max(0, math.floor((uav.battery - est.reserve(uav)) * _E))
            spent = {t: model.new_int_var(0, limit, f"e_{uav_id}_{t}") for t in task_ids}
            visit = {t: model.new_bool_var(f"v_{uav_id}_{t}") for t in task_ids}
            completion = model.new_int_var(ready, horizon, f"c_{uav_id}")
            empty = model.new_bool_var(f"empty_{uav_id}")
            arcs.append((0, 0, empty))
            model.add(completion == ready).only_enforce_if(empty)
            takeoff = math.ceil(est.takeoff_energy(uav) * _E)
            landing = math.ceil(est.landing_energy(uav) * _E)

            for t in task_ids:
                task = ctx.tasks[t]
                node = node_of[t]
                arcs.append((node, node, visit[t].Not()))
                model.add_implication(visit[t], assigned[t])
                leg = checker.leg(uav, uav.start, task.cell)
                ret = checker.return_option(uav, task.cell)
                if not ctx.uav_task_ok(uav_id, t) or leg is None or ret is None:
                    model.add(visit[t] == 0)
                    continue
                if task.deadline is not None:
                    model.add(finish[t] <= math.floor(task.deadline * _T)).only_enforce_if(visit[t])

                # Departure from the start.
                lit = model.new_bool_var(f"s_{uav_id}_{t}")
                arcs.append((0, node, lit))
                lits[(0, node)] = lit
                energy = math.ceil(leg.energy * _E) + math.ceil(est.task_energy(uav, task) * _E)
                model.add(spent[t] == takeoff + energy).only_enforce_if(lit)
                model.add(arrival[t] == ready + math.ceil(leg.time * _T)).only_enforce_if(lit)
                energy_terms.append((energy + takeoff, lit))
                distance_terms.append((math.ceil(leg.path.distance * _D), lit))

                # Return to a station.
                end = model.new_bool_var(f"r_{uav_id}_{t}")
                arcs.append((node, 0, end))
                lits[(node, 0)] = end
                ret_energy = math.ceil(ret.energy * _E)
                model.add(spent[t] + ret_energy + landing <= limit).only_enforce_if(end)
                model.add(completion == finish[t] + math.ceil(ret.time * _T)).only_enforce_if(end)
                energy_terms.append((ret_energy + landing, end))
                distance_terms.append((math.ceil(ret.path.distance * _D), end))
                if checker.strict_prefix:
                    model.add(spent[t] + ret_energy + landing <= limit).only_enforce_if(visit[t])

            for a in task_ids:
                if not ctx.uav_task_ok(uav_id, a):
                    continue
                for b in task_ids:
                    if a == b or not ctx.uav_task_ok(uav_id, b):
                        continue
                    leg = checker.leg(uav, ctx.tasks[a].cell, ctx.tasks[b].cell)
                    if leg is None:
                        continue
                    lit = model.new_bool_var(f"x_{uav_id}_{a}_{b}")
                    arcs.append((node_of[a], node_of[b], lit))
                    lits[(node_of[a], node_of[b])] = lit
                    energy = math.ceil(leg.energy * _E) + math.ceil(
                        est.task_energy(uav, ctx.tasks[b]) * _E
                    )
                    model.add(spent[b] == spent[a] + energy).only_enforce_if(lit)
                    model.add(arrival[b] == finish[a] + math.ceil(leg.time * _T)).only_enforce_if(
                        lit
                    )
                    energy_terms.append((energy, lit))
                    distance_terms.append((math.ceil(leg.path.distance * _D), lit))

            model.add_circuit(arcs)
            arcs_by_uav[uav_id] = lits
            visits_by_uav[uav_id] = visit
            load = model.new_int_var(0, horizon, f"l_{uav_id}")
            model.add(load == completion - ready)
            loads.append(load)
            completions.append(completion)

        for t in task_ids:
            model.add(sum(visits_by_uav[u][t] for u in ctx.uav_ids) == assigned[t])

        model.minimize(
            self._objective(model, ctx, assigned, loads, completions, energy_terms, distance_terms)
        )
        if cfg.use_hint and task_ids:
            self._hint(model, ctx, arcs_by_uav, node_of, assigned)
        return arcs_by_uav, task_ids

    def _objective(
        self,
        model: Any,
        ctx: PlanningContext,
        assigned: dict[str, Any],
        loads: list[Any],
        completions: list[Any],
        energy_terms: list[Any],
        distance_terms: list[Any],
    ) -> Any:
        horizon = self._horizon(ctx)
        weights, scales = ctx.objective_weights, ctx.scales
        makespan = model.new_int_var(0, horizon, "makespan")
        model.add_max_equality(makespan, completions)

        # Var(L) = Σ_{i<j} (L_i − L_j)² / m². Unlike Σ L²/m − (ΣL)²/m², every term is
        # non-negative, which gives the solver a much stronger lower bound.
        m = len(loads)
        squares: list[Any] = []
        for (i, a), (j, b) in combinations(enumerate(loads), 2):
            gap = model.new_int_var(-horizon, horizon, f"gap_{i}_{j}")
            model.add(gap == a - b)
            square = model.new_int_var(0, horizon**2, f"sq_{i}_{j}")
            model.add_multiplication_equality(square, [gap, gap])
            squares.append(square)

        # An explicitly scaled integer objective: left to itself, CP-SAT's float scaling
        # can lose the smallest terms, whose coefficients are ~1e12 below the largest.
        energy_coef = round(_OBJECTIVE * weights.alpha / (scales.energy * _E))
        time_coef = round(_OBJECTIVE * weights.beta / (scales.time * _T))
        distance_coef = round(_OBJECTIVE * weights.gamma / (scales.distance * _D))
        balance_coef = round(_OBJECTIVE * weights.delta / (scales.load_mean**2 * _T**2 * m**2))
        terms: list[Any] = [time_coef * makespan]
        terms += [energy_coef * amount * lit for amount, lit in energy_terms]
        terms += [distance_coef * amount * lit for amount, lit in distance_terms]
        terms += [balance_coef * square for square in squares]
        for t, var in assigned.items():
            task = ctx.tasks[t]
            weight = weights.lam * weights.big_m * int(task.priority)
            if task.mandatory:
                weight += _MANDATORY_WEIGHT
            scaled = round(_OBJECTIVE * weight)
            terms.append(scaled - scaled * var)
        return sum(terms)

    @staticmethod
    def _hint(
        model: Any,
        ctx: PlanningContext,
        arcs_by_uav: dict[str, dict[tuple[int, int], Any]],
        node_of: dict[str, int],
        assigned: dict[str, Any],
    ) -> None:
        plan = IntegratedPlanner().plan(ctx.scenario)
        placed = {t for r in plan.routes for t in r.task_ids}
        for t, var in assigned.items():
            model.add_hint(var, int(t in placed))
        for route in plan.routes:
            nodes = [0, *(node_of[t] for t in route.task_ids), 0]
            used = set(pairwise(nodes))
            for key, lit in arcs_by_uav[route.uav_id].items():
                model.add_hint(lit, int(key in used))

    @staticmethod
    def _extract(
        solver: Any,
        ctx: PlanningContext,
        arcs: dict[str, dict[tuple[int, int], Any]],
        tasks: list[str],
    ) -> Assignment:
        assignment = ctx.empty_assignment()
        for uav_id in ctx.uav_ids:
            following = {a: b for (a, b), lit in arcs[uav_id].items() if solver.value(lit)}
            seq: list[str] = []
            node = following.get(0, 0)
            while node != 0:
                seq.append(tasks[node - 1])
                node = following[node]
            assignment[uav_id] = tuple(seq)
        return assignment
