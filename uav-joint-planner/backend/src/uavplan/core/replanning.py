"""DynamicReplanningEngine: repairs the plan when the mission changes.

Follows the proposal's six steps: update the state, find what is affected, re-check
feasibility, repair locally, escalate if needed, and record the outcome.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, replace
from typing import Literal

from uavplan.core.allocation.base import Assignment, PlannerConfig, PlanningContext, Seq
from uavplan.core.allocation.integrated import IntegratedConfig, IntegratedSearch, as_integrated
from uavplan.core.cost import compare_plans, plan_score
from uavplan.core.models import Event, EventType, Plan
from uavplan.core.registry import get_planner
from uavplan.core.state import MissionState

ReplanMode = Literal["local_only", "local_then_global", "always_global"]
PREVIOUSLY_UNASSIGNED = "still unassigned after replanning"


@dataclass(frozen=True, slots=True)
class ReplanConfig:
    mode: ReplanMode = "local_then_global"
    planner: str = "integrated"
    check_suboptimal: bool = True
    suboptimal_threshold: float = 0.15


@dataclass(frozen=True, slots=True)
class MovedTask:
    task_id: str
    from_uav: str
    to_uav: str


@dataclass(frozen=True, slots=True)
class ReplanResult:
    event_type: str
    time: float
    time_ms: float
    plan: Plan
    scope: Literal["local", "global"]
    escalated: bool
    escalation_reason: str | None
    affected_uavs: tuple[str, ...]
    affected_tasks: tuple[str, ...]
    moved: tuple[MovedTask, ...]
    added: tuple[str, ...]
    dropped: tuple[str, ...]
    z_before: float
    z_after: float

    @property
    def tasks_moved(self) -> int:
        return len(self.moved)


class DynamicReplanningEngine:
    """Local repair first, widening the scope only when that is not enough."""

    def __init__(
        self,
        scenario_scales_from: PlanningContext,
        config: ReplanConfig | None = None,
        planner_config: PlannerConfig | None = None,
    ) -> None:
        self.config = config or ReplanConfig()
        self.scales = scenario_scales_from.scales
        self.planner_config: IntegratedConfig = replace(
            as_integrated(planner_config), scales=self.scales
        )

    def _shed_until_feasible(
        self, ctx: PlanningContext, uav_id: str, seq: Seq
    ) -> tuple[Seq, list[str]]:
        shed: list[str] = []
        while seq and not ctx.evaluate(uav_id, seq).feasible:
            victim = min(seq, key=lambda t: (ctx.tasks[t].mandatory, int(ctx.tasks[t].priority), t))
            seq = tuple(t for t in seq if t != victim)
            shed.append(victim)
        return seq, shed

    def _run_global(self, ctx: PlanningContext) -> Plan:
        return get_planner(self.config.planner).plan(ctx.scenario, self.planner_config)

    def replan(self, state: MissionState, event: Event, current: dict[str, Seq]) -> ReplanResult:
        """Produce a new plan for the rest of the mission from the current state."""
        started = time.perf_counter()
        cfg = self.config

        # Step 1: the simulator has frozen completed work; plan from the commit points.
        scenario = state.derived_scenario()
        ctx = PlanningContext(
            scenario, self.planner_config, strict_prefix=self.planner_config.strict_prefix_safety
        )
        search = IntegratedSearch(ctx, self.planner_config)

        # Step 2: find what the event touched.
        before: Assignment = {
            u: tuple(t for t in current.get(u, ()) if t in ctx.tasks) for u in ctx.uav_ids
        }
        orphans = {
            t
            for uav_id, seq in current.items()
            if uav_id not in ctx.uav_ids
            for t in seq
            if t in ctx.tasks
        }
        new_tasks = {event.task.id} if event.type == EventType.NEW_TASK and event.task else set()
        placed = {t for seq in before.values() for t in seq}
        prior_left = sorted(set(ctx.tasks) - placed - orphans - new_tasks)
        z_before = ctx.objective(
            [ctx.report_checker.evaluate(u, before[u]) for u in ctx.uav_ids],
            [ctx.tasks[t] for t in prior_left],
        ).z

        # Step 3: re-check every remaining route, shedding tasks from infeasible ones.
        assignment = dict(before)
        shed: list[str] = []
        for uav_id in ctx.uav_ids:
            assignment[uav_id], dropped_here = self._shed_until_feasible(
                ctx, uav_id, assignment[uav_id]
            )
            shed.extend(dropped_here)
        affected_tasks = sorted(orphans | new_tasks | set(shed))
        affected_uavs = sorted(
            {
                *(u for u in ctx.uav_ids if assignment[u] != before[u]),
                *(u for u in current if u not in ctx.uav_ids and current[u]),
                *([event.uav_id] if event.uav_id else []),
            }
        )

        if cfg.mode == "always_global":
            plan = self._run_global(ctx)
            scope: Literal["local", "global"] = "global"
            escalated = False
            reason: str | None = None
        else:
            # Step 4: local repair, reinserting tasks and re-optimizing touched routes only.
            unassigned = dict.fromkeys(prior_left, PREVIOUSLY_UNASSIGNED)
            pool = sorted(set(affected_tasks) | set(prior_left))
            search.insert_tasks([ctx.tasks[t] for t in pool], assignment, unassigned)
            touched = [u for u in ctx.uav_ids if assignment[u] != before[u]]
            search.improve_routes(assignment, unassigned, touched)
            plan = ctx.build_plan(cfg.planner, assignment, unassigned, started)
            scope, escalated, reason = "local", False, None

            # Step 5: widen the scope if the repair did not restore the plan.
            if cfg.mode == "local_then_global":
                score = plan_score(plan, ctx.tasks)
                if score.violations:
                    reason = "feasibility not restored"
                elif score.mandatory_unassigned:
                    reason = "mandatory task left unassigned"
                elif (
                    cfg.check_suboptimal
                    and z_before > 0
                    and plan.objective.z > (1.0 + cfg.suboptimal_threshold) * z_before
                ):
                    reason = "plan became materially suboptimal"
                if reason is not None:
                    escalated = True
                    candidate = self._run_global(ctx)
                    if compare_plans(candidate, plan, ctx.tasks) < 0:
                        plan, scope = candidate, "global"

        # Step 6: record the outcome.
        old_owner = {t: u for u, seq in current.items() for t in seq if t in ctx.tasks}
        new_owner = {t: r.uav_id for r in plan.routes for t in r.task_ids}
        moved = tuple(
            MovedTask(t, old_owner[t], new_owner[t])
            for t in sorted(old_owner)
            if t in new_owner and new_owner[t] != old_owner[t]
        )
        added = tuple(sorted(t for t in new_owner if t not in old_owner))
        dropped = tuple(sorted(t for t in old_owner if t not in new_owner))
        return ReplanResult(
            event_type=event.type.value,
            time=event.time,
            time_ms=(time.perf_counter() - started) * 1000.0,
            plan=plan,
            scope=scope,
            escalated=escalated,
            escalation_reason=reason,
            affected_uavs=tuple(affected_uavs),
            affected_tasks=tuple(affected_tasks),
            moved=moved,
            added=added,
            dropped=dropped,
            z_before=z_before,
            z_after=plan.objective.z,
        )
