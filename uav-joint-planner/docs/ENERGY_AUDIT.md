# Optimizing Routing, Task Allocation, and Energy Management in Collaborative UAV Systems: Energy Audit

Written by reading the code before any energy changes were made. "Audit" is the status found at
that point. `core/` means `backend/src/uavplan/core/`. Test files are in `backend/tests/`.

## Energy model

| # | Item | Audit | Where | Test | Note |
|---|---|---|---|---|---|
| 1 | Single EnergyEstimator used everywhere | Partial | `core/energy.py`; used by `feasibility.py`, `simulator.py`, `ortools_vrp.py`, `exact_cpsat.py` | `test_phase1_model.py` | Allocation, routing, feasibility and the simulator all use it. Three stray copies remain: `cost.py::fixed_scales` (`k × distance`), and `reserve_ratio × battery_max` in `generator.py::validate_scenario` and `experiments/plots.py`. |
| 2 | Normalized and physical modes | Done | `core/energy.py` | `test_normalized_travel_energy`, `test_physical_100w_for_60s_is_6kj` | |
| 3 | Hover/task, takeoff and landing energy | Done | `core/energy.py`, `core/feasibility.py`, `core/simulator.py` | `test_physical_task_energy_and_turn_penalty`, `test_takeoff_and_landing_counted_once`, `test_takeoff_landing_and_waiting_are_simulated` | |
| 4 | Per-UAV parameters | Done | `core/models.py::UAV` | `test_normalized_travel_energy` | Capacity, speed, cruise and hover power, EU per metre. |

## Feasibility and safety

| # | Item | Audit | Where | Test | Note |
|---|---|---|---|---|---|
| 5 | Total check | Done | `core/feasibility.py::_walk` | `test_reserve_makes_a_tight_route_infeasible` | |
| 6 | Prefix safety | Done | `core/feasibility.py::_prefix_violations` | `test_prefix_safety_catches_route_safe_in_total_only` | Checked at each task. Mid-leg points are covered by the argument in `MODEL.md`. |
| 7 | Return to nearest reachable station | Done | `core/energy.py::best_return` | `test_return_uses_cheapest_station_or_home_only` | Home-only is an option. |
| 8 | Configurable reserve, default 10% | Done | `core/models.py::EnergyConfig`, `core/energy.py::reserve` | `test_reserve_uses_capacity_not_current_battery`, `test_plan_inline_scenario_with_weights_and_reserve` | |
| 9 | Named 25 vs 30 EU test | Done | `core/feasibility.py` | `test_25eu_vs_30eu_example_is_infeasible` | |

## Routing

| # | Item | Audit | Where | Test | Note |
|---|---|---|---|---|---|
| 10 | Energy-weighted A* alongside distance A* | Done | `core/routing/paths.py` | `test_energy_astar_matches_energy_dijkstra`, `test_astar_matches_dijkstra`, `test_energy_path_avoids_expensive_zone` | |
| 11 | Energy and feasibility re-checked after route changes | Done | `core/routing/improve.py` via `PlanningContext.score` | `test_two_opt_never_worsens_and_stays_feasible` | B3's 2-opt is distance-only by design, then re-checks and drops tasks. |

## Dynamic energy handling

| # | Item | Audit | Where | Test | Note |
|---|---|---|---|---|---|
| 12 | BATTERY_DROP replans or reports | Done | `core/simulator.py`, `core/replanning.py` | `test_after_battery_drop_plan_is_feasible_again`, `test_unrecoverable_battery_drop_is_reported` | |
| 13 | Seeded energy noise | Done | `core/simulator.py::_noise` | `test_noise_changes_energy_and_is_seeded` | |
| 14 | Planned vs actual energy per UAV, with a margin alert | Missing | none | none | The simulator records actual energy only, and raises no alert. |

## Metrics and experiments

| # | Item | Audit | Where | Test | Note |
|---|---|---|---|---|---|
| 15 | Energy metrics | Done | `core/metrics.py` | `test_plan_metrics_values`, `test_infeasible_plan_depletes_battery_and_fails_tasks` | Total, per completed task, minimum margin, feasibility rate, `depleted_uavs`. |
| 16 | Scenario C and noise sweep G | Done | `core/generator.py`, `experiments/runner.py` | `test_preset_shapes`, `test_scaling_and_noise_specs` | |
| 17 | Energy ablations | Partial | `experiments/ablation.py` | `test_specs_include_ablations_only_where_defined` | No energy in cost and no replanning are in the batch. No prefix safety is a planner flag but is not in the batch. |

## UI

| # | Item | Audit | Where | Test | Note |
|---|---|---|---|---|---|
| 18 | Battery bar split by travel, task, return, reserve | Done | `frontend/src/components/panels/UAVPanel.tsx` | `app.test.tsx` "shows the energy budget split" | |
| 19 | Battery-over-time chart | Done | `frontend/src/components/charts/index.tsx`, `pages/Simulation.tsx` | `e2e/screenshots.spec.ts` | |
| 20 | Interactive 25 vs 30 EU example | Done | `frontend/src/pages/Model.tsx` | `app.test.tsx` "opens on the proposal's infeasible example" | |

## Extras requested

| Item | Audit | Note |
|---|---|---|
| a. Recharge stops (`allow_recharge`) | Missing | Routes are single-sortie. |
| b. Adaptive reserve (`adaptive_reserve`) | Missing | Reserve is a fixed ratio. |
| c. Early return-to-home with RTH event | Missing | UAVs fly on until depleted. |
| d. Energy breakdown chart | Missing | The battery bar has no takeoff-landing part or per-UAV stacked chart. |
| e. Distance vs energy path toggle | Missing | Both path metrics exist in `core/`, but nothing shows them side by side. |

## Final status

Every item is Done. Items that the audit marked Partial or Missing:

| Item | Final | What changed | Test |
|---|---|---|---|
| 1 | Done | `fixed_scales`, `validate_scenario` and the plots now call `EnergyEstimator`. The frontend's live builder check still multiplies ratio by capacity as an instant hint; the server repeats that check through the estimator on save. | `test_fixed_scale_energy_comes_from_the_estimator` |
| 14 | Done | Planned and actual energy per UAV, `MARGIN_ALERT`, a table on the Simulation page. | `test_planned_equals_actual_without_noise`, `test_margin_alert_when_actual_energy_eats_the_reserve` |
| 17 | Done | `no_prefix_safety` added to the batch. It has no effect with energy-optimal paths, as `MODEL.md` shows it cannot. | `test_no_prefix_safety_is_in_the_ablation_batch` |
| a | Done | `allow_recharge`, off by default. | `test_recharge_stop_saves_a_task_that_would_be_dropped`, `test_recharge_and_adaptive_plans_are_always_feasible` |
| b | Done | `adaptive_reserve`, off by default. | `test_adaptive_reserve_formula` |
| c | Done | `early_rth`, off by default, logged as an `RTH` event. | `test_early_rth_brings_the_uav_home_and_replans_its_tasks` |
| d | Done | Energy breakdown chart on the Planner. | `e2e/smoke.spec.ts` |
| e | Done | Path toggle on the Planner, `POST /api/paths/compare`. | `test_compare_paths_shows_shortest_is_not_lowest_energy`, `test_compare_paths_endpoint` |

Items 2–13, 15, 16 and 18–20 were already Done at the audit and needed no change.
