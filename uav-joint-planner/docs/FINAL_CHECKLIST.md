# Optimizing Routing, Task Allocation, and Energy Management in Collaborative UAV Systems: Final checklist

Each proposal item traced to the code, test and evidence that deliver it. Paths are relative to
the repository root. `core/` means `backend/src/uavplan/core/`. Test files are in `backend/tests/`.
All results are simulated and illustrative.

## Objectives

| # | Objective | Code | Test | Evidence |
|---|---|---|---|---|
| 1 | Intelligent task allocation | `core/allocation/integrated.py`, `core/cost.py` | `test_phase3_integrated.py::test_weights_change_the_chosen_uav`, `::test_higher_priority_task_wins_scarce_energy` | `REPORT.md` section 1; Planner "Explain this assignment" |
| 2 | Energy-aware route optimization | `core/routing/paths.py`, `improve.py`, `core/environment.py` | `test_phase1_model.py::test_energy_path_avoids_expensive_zone`, `::test_astar_matches_dijkstra`, `test_phase2_baselines.py::test_two_opt_never_worsens_and_stays_feasible` | `figures/route_maps_demo.png` |
| 3 | Battery-aware mission planning | `core/energy.py`, `core/feasibility.py` | `test_phase1_model.py::test_25eu_vs_30eu_example_is_infeasible`, `::test_prefix_safety_catches_route_safe_in_total_only`, `test_phase3_integrated.py::test_proposed_plans_are_always_feasible` (200 scenarios) | `figures/battery_timeline_demo.png`; Model page |
| 4 | Collaborative coordination | Workload term in `core/cost.py`, inter-route search in `core/routing/improve.py` | `test_phase2_baselines.py::test_inter_route_search_moves_tasks_to_the_better_uav` | Workload imbalance in `REPORT.md`. **Partial:** coordination is centralized; see below |
| 5 | Dynamic task reassignment | `core/replanning.py`, `core/simulator.py` | `test_phase5_dynamic.py` (46 tests) | `REPORT.md` section 5; Simulation page |
| 6 | Performance evaluation | `experiments/` | `test_phase6_experiments.py` | `results/sample/REPORT.md` |

## Contributions

| Claim | Code | Test or evidence |
|---|---|---|
| Energy-feasible route and battery planning | `core/allocation/integrated.py` | Scored by whole-route evaluation; `REPORT.md` section 2 |
| Dynamic reassignment with local repair, then escalation | `core/replanning.py` | `test_phase5_dynamic.py::test_escalation_restores_a_mandatory_task`, `::test_local_only_never_escalates` |
| Multi-objective evaluation | `core/cost.py`, `experiments/plots.py` | `figures/pareto_energy_makespan.png` |
| Ablation studies | `experiments/ablation.py` | `test_phase3_integrated.py::test_each_search_flag_changes_behaviour`; `REPORT.md` section 4 |

## Baselines

| Item | Code | Test |
|---|---|---|
| B1 Nearest-UAV | `core/allocation/nearest.py` | `test_phase2_baselines.py::test_nearest_may_be_infeasible_but_reports_it` |
| B2 Greedy energy-aware | `core/allocation/greedy_energy.py` | `::test_greedy_energy_never_violates_energy` |
| B3 Independent allocation + routing | `core/allocation/independent.py` | `::test_independent_is_always_feasible` |
| B4 OR-Tools VRP | `core/allocation/ortools_vrp.py` | `test_phase4_strong.py::test_ortools_output_passes_our_checker` |
| B5 Genetic algorithm | `core/allocation/genetic.py` | `test_phase4_strong.py::test_genetic_plans_are_feasible` |
| Exact solver and optimality gap | `core/allocation/exact_cpsat.py` | `test_phase4_strong.py::test_exact_is_no_worse_than_any_heuristic` |
| Proposed with dynamic replanning | `core/replanning.py` | `test_phase5_dynamic.py::test_replanning_beats_no_replanning_on_a_uav_failure` |

## Metrics

Defined in `core/metrics.py`, tested in `test_phase2_baselines.py` (`test_plan_metrics_values`,
`test_simulation_metrics_match_plan_when_noise_free`). All are columns in `raw.csv`.

| Proposal metric | Column |
|---|---|
| Total energy | `total_energy` (also `energy_per_completed_task`) |
| Mission completion time | `makespan` |
| Task completion rate | `completion_rate`, `_mandatory`, `_weighted` |
| Total travel distance | `total_distance` |
| Energy feasibility rate | `energy_feasibility_rate` |
| Workload imbalance | `workload_cv`, `workload_range` |
| Replanning time | `sim_replan_time_ms` |
| Computation time | `plan_compute_time` |
| Infeasible-plan frequency | `plan_plan_feasible`, `violations` |
| Unassigned tasks | `unassigned`, with reasons in each plan |
| Performance as the fleet grows | Scenario F; `figures/scaling_compute_time.png` |

## Scenarios

| ID | Code | Test | Evidence |
|---|---|---|---|
| A–E | `core/generator.py`, `backend/scenarios/*.json` | `test_phase1_model.py::test_preset_shapes`, `::test_preset_json_files_are_in_sync` | `REPORT.md` section 1 |
| F scaling | `core/generator.py::scaling_scenario` | `test_phase6_experiments.py::test_scaling_and_noise_specs` | `REPORT.md` section 6 |
| G noise | `core/generator.py::noise_scenario` | same | `REPORT.md` section 7 |
| Earthquake demo | `core/generator.py::demo_scenario` | `frontend/e2e/smoke.spec.ts` | Dashboard |

## Ablations

Full, no energy in cost, no workload term, append only, no local search, no inter-route, no
replanning, local only, always global. Defined in `experiments/ablation.py`; results in
`REPORT.md` sections 4 and 5. The strict prefix flag is tested separately
(`test_phase3_integrated.py::test_strict_prefix_flag_changes_behaviour`) and is not in the batch,
because it cannot change results with the default energy-optimal paths (`docs/MODEL.md`, section 4).

## Dynamic events

All in `core/simulator.py` and `core/replanning.py`, tested in `test_phase5_dynamic.py`.

| Event | Test |
|---|---|
| `NEW_TASK` | `test_new_task_is_completed_with_replanning_and_not_without` |
| `BLOCK_REGION` | `test_after_block_region_no_route_crosses_it` |
| `UAV_UNAVAILABLE` | `test_after_uav_unavailable_none_of_its_tasks_stay_assigned` |
| `BATTERY_DROP` | `test_after_battery_drop_plan_is_feasible_again`, `test_unrecoverable_battery_drop_is_reported` |
| `TASK_DURATION_CHANGE` | `test_duration_change_extends_the_work` |
| `TASK_CANCELLED` | `test_task_cancelled_is_removed` |
| `UAV_JOINS` | `test_uav_joining_takes_unassigned_work` |
| Live injection | `test_live_injection_between_advances`, `test_phase7_api.py::test_websocket_play_seek_inject` |

## Visual interface

| Page | Code | Test |
|---|---|---|
| Dashboard, Planner | `frontend/src/pages/` | `e2e/smoke.spec.ts` (load demo, plan, routes, battery bars) |
| Scenario Builder | `pages/ScenarioBuilder.tsx` | `e2e/smoke.spec.ts` (place, draw, reject bad placement) |
| Simulation | `pages/Simulation.tsx` | `e2e/smoke.spec.ts` (inject failure, see replan) |
| Compare, Model, Experiments | `pages/` | `e2e/screenshots.spec.ts` drives each page; `frontend/src/test/app.test.tsx` |
| Streamlit option from the proposal | `apps/streamlit_app.py` | Run with Streamlit's AppTest during verification |

## Energy management update

Full item-by-item status is in `docs/ENERGY_AUDIT.md`.

| Item | Code | Test |
|---|---|---|
| Single estimator | `core/energy.py` | `test_energy_update.py::test_fixed_scale_energy_comes_from_the_estimator` |
| Planned vs actual, margin alert | `core/simulator.py` | `::test_planned_equals_actual_without_noise`, `::test_margin_alert_when_actual_energy_eats_the_reserve` |
| No-prefix-safety ablation | `experiments/ablation.py` | `::test_no_prefix_safety_is_in_the_ablation_batch` |
| Charging stops | `core/feasibility.py`, `core/allocation/integrated.py` | `::test_recharge_stop_saves_a_task_that_would_be_dropped`, `::test_simulator_executes_a_charging_stop` |
| Adaptive reserve | `core/energy.py` | `::test_adaptive_reserve_formula`, `::test_adaptive_reserve_rejects_a_route_the_fixed_reserve_accepts` |
| Early return-to-home | `core/simulator.py` | `::test_early_rth_brings_the_uav_home_and_replans_its_tasks` |
| Energy breakdown chart | `frontend/src/components/charts/index.tsx` | `e2e/smoke.spec.ts` |
| Energy vs distance path toggle | `core/routing/paths.py`, `/api/paths/compare`, `pages/Planner.tsx` | `::test_compare_paths_shows_shortest_is_not_lowest_energy`, `e2e/smoke.spec.ts` |

## Not delivered, or only partly

- **Collaborative coordination is partial.** Allocation and workload balancing are coordinated by a
  central planner. Communication, distributed execution and collision avoidance are not modelled.
- **CI has not been observed running.** `.github/workflows/ci.yml` exists, but this repository has
  no GitHub remote yet. The same commands pass locally.
- **`make` was not run.** The development machine has no `make`. The Makefile only delegates to
  `scripts/dev.py`, whose targets were run directly.
- **OR-Tools determinism has a caveat** at the largest Scenario F sizes, where its 20 s safety cap
  is reached.
- **The literature review the proposal calls for is outside this software.** The novelty of the
  method has not been checked against the closest published work.
- **Modal dialogs were not built.** The spec's component list mentions them, but no page needs one.
- **Physical energy mode has no UI control.** It works through the scenario JSON and the API.

## Final verification

| Check | Result |
|---|---|
| Backend tests (`python scripts/dev.py test`) | 608 passed |
| `core/` coverage | 97.6% (gate is 85%) |
| Frontend tests (Vitest) | 38 passed |
| Playwright | 3 smoke tests and the page walk-through passed |
| `ruff`, `ruff format`, `mypy --strict` on `core/`, `tsc --noEmit` | clean |
| `experiments-quick` | about 1 minute; report and figures in `results/sample/` |
| `experiments-full` | finished, 2,620 runs; written to `results/full/` (not committed) |
| Streamlit app | runs without exceptions under Streamlit's AppTest |
| Fresh clone + `docker compose up` | built, both containers healthy, 3 smoke tests passed through nginx |
| Reproducibility | quick and full runs agree on all 310 shared rows, apart from measured compute time. The energy update did not change any existing result; it adds 25 `no_prefix_safety` rows and 3 columns |
