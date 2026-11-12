# Optimizing Routing, Task Allocation, and Energy Management in Collaborative UAV Systems: Experiment protocol

All results are simulated and illustrative. Nothing here is measured drone performance.

## Running

| Command | What it runs |
|---|---|
| `python scripts/dev.py experiments-quick` | 5 seeds, Scenarios A–E, all algorithms and ablations |
| `python scripts/dev.py experiments-full` | 30 seeds, Scenarios A–G |
| `uavplan experiment --scenarios A,B --algorithms all --seeds 30 --out results/` | A custom batch |

`make experiments-quick` and `make experiments-full` do the same. Reusing a `--name` resumes
that run: finished rows in `raw.csv` are skipped.

## Scenarios

| ID | Setup | Tests |
|---|---|---|
| A | 3 UAVs, 10 tasks, static | correctness, optimality gap |
| B | 5 UAVs, 30 tasks, varied priorities | allocation quality |
| C | 4 UAVs, 20 tasks, batteries 40–100 EU | energy feasibility |
| D | 4 UAVs, 20 tasks + 5 urgent arrivals | adaptation |
| E | 5 UAVs, 25 tasks, one UAV fails | recovery |
| F | UAVs in {2,4,8,12} × tasks in {10,25,50,100} | scaling |
| G | 4 UAVs, 20 tasks, energy noise σ in {0, 0.05, 0.1, 0.2} | robustness of the reserve |

F runs at most 5 seeds per size. Its algorithm set leaves out the genetic algorithm and the
exact solver, whose cost at these sizes would dominate the batch. G uses B2, B3 and the
proposed method.

## Energy metrics

| Column | Meaning |
|---|---|
| `total_energy` | Energy used by all UAVs |
| `energy_per_completed_task` | Total energy ÷ tasks completed |
| `min_battery_margin` | Lowest battery minus reserve at return |
| `energy_feasibility_rate` | Planned: routes with no energy violation. Simulated: UAVs landing with the reserve intact |
| `depleted_uavs` | UAVs that ran out of energy in flight |
| `margin_alerts` | Times actual energy ate into the safety margin |
| `energy_estimation_error` | Absolute planned-vs-actual difference, as a % of planned |
| `rth_events` | Early returns to home; zero unless `early_rth` is on |

Scenario C tests limited batteries, and Scenario G tests robustness to energy-estimation noise.
Energy ablations are: no energy in cost, no prefix safety, and no replanning. With energy-optimal paths,
removing prefix safety cannot change a plan (`MODEL.md`, section 4), so a zero effect is the expected
result and confirms that argument.

Charging stops, the adaptive reserve and early return-to-home are off in the standard protocol.

## Fairness

- Every algorithm gets the same scenario instances and event sequences for each seed.
- In D and E, each baseline re-runs itself from the current mission state after an event.
  Without this, the comparison would mostly measure the absence of adaptation. The proposed
  method repairs locally and escalates when needed. Its no-replanning and always-global
  variants appear as ablations.
- Every plan is judged by the same `FeasibilityChecker`, and simulated metrics come from the
  same simulator.

## Metrics

Each row records both the planned and the simulated result. The main comparison uses
simulated values. Total energy favours methods that do less, so comparisons use **energy per
completed task**; the totals remain in `raw.csv`.

`stranded_uavs` counts every UAV that flew and did not land, which includes a UAV that fails
by design in Scenario E. The safe-landing rate counts that UAV as not landing as well, which
is why it is about 80% for every algorithm there.

## Statistics

For each scenario and metric:

- Mean, standard deviation, median, IQR and a 95% percentile bootstrap CI of the mean.
- Paired Wilcoxon signed-rank test of the proposed method against each baseline and ablation,
  paired by seed.
- Holm–Bonferroni correction across the comparisons within one scenario and metric.
- Rank-biserial effect size, plus win/tie/loss counts.

With 5 seeds a two-sided Wilcoxon test cannot reach p < 0.05 at any effect size, so the quick
run is exploratory and its report says so. Use the 30-seed protocol for inference.

Optimality gaps are reported only for Scenario A instances where CP-SAT proved optimality,
and only for plans with no hard violations.

## Outputs

`results/<name>/` holds `raw.csv` (one row per run), `summary.csv`, `stats.csv`, `pareto.csv`,
`figures/*.png|svg` and `REPORT.md`, whose section "Where the integrated method does not help"
is filled in from the data. `results/sample/` is a committed quick run.

Planning times are measured while runs share the machine, so treat them as rough magnitudes.
They are the only values that differ between repeated runs.
