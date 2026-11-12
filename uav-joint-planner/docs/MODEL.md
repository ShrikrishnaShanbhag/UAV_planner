# Optimizing Routing, Task Allocation, and Energy Management in Collaborative UAV Systems: Mathematical model

This document matches the code in `backend/src/uavplan/core/`. Energy is in normalized
energy units (EU) by default, or joules (J) in physical mode. All values are simulated and
illustrative. None are measured drone performance.

## 1. Environment (`environment.py`)

The map is a grid of `W × H` cells (default 50 × 50) with cell size `s` metres (default 20).
The graph `G = (V, E)` has one node per free cell and is 8-connected.

- A straight edge has length `s`, a diagonal edge `√2·s`.
- A diagonal between two cells exists only if both orthogonally adjacent cells are free, so
  no path can cut an obstacle corner.
- `obstacle` and `no_fly` regions (rectangles, polygons, or explicit cells) remove cells.
- `energy_zone` regions keep cells flyable and apply a multiplier `m(c) ≥ 1`.

Each edge `(a, b)` has distance `d_ab` and **effort** `ε_ab = d_ab · (m(a) + m(b)) / 2`.
Time and energy are derived per UAV on demand and cached.

Path search metric (`EnergyConfig.path_metric`, default `energy`) selects whether legs
minimize effort or distance. With no energy zones the two coincide.

| Search (`routing/paths.py`) | Edge weight | Heuristic |
|---|---|---|
| Dijkstra | `d` or `ε` | 0 |
| A* | `d` | octile distance (admissible) |
| Energy-weighted A* | `ε` (scaled by `k_i` if given) | octile × minimum multiplier |

Shortest paths are cached between key nodes (UAV starts, tasks, stations), one tree per
source, and the cache is invalidated whenever the map changes.

## 2. Entities (`models.py`)

- UAV `U_i = (p_i, B_i, B_i^max, C_i, T_i, v_i, P_i^cruise, P_i^hover, status, home_i)`,
  with status in {idle, en_route, working, returning, charging, unavailable}.
- Task `J_j = (p_j, P_j, S_j, E_task,j, dur_j, deadline_j | None, mandatory, release_j)`,
  with `P_j` in {low=1, medium=2, high=3, critical=4}.

## 3. Energy (`energy.py`, the only module that computes energy)

| Quantity | Normalized mode (EU) | Physical mode (J) |
|---|---|---|
| Travel | `E = k_i · ε` | `E = P_i^cruise · ε / v_i + c_turn · θ` |
| Task | `E_task,j` | `P_i^hover · dur_j + E_task,j` |
| Hover while waiting | `h_i · Δt` | `P_i^hover · Δt` |
| Takeoff / landing | fixed per UAV | fixed per UAV |

Travel time is always `t = d / v_i`. `θ` is the total heading change along a leg; `c_turn`
is optional and defaults to 0.

- **Return energy:** `E_return = min` over reachable stations of the path energy from the
  last task to the station, or to the home station only if `home_only_return` is set.
- **Reserve:** `E_reserve = ρ · B_i^max` (default `ρ = 0.10`).

**Adaptive reserve (optional, `adaptive_reserve`, off by default):**
`ρ_eff = ρ + k · σ · √f`, where `σ` is the assumed energy-estimation error (`adaptive_sigma`), `k` is
`adaptive_k`, and `f` is the fraction of the route's distance still ahead. The total-energy check uses
`f = 1`. Later checkpoints use a smaller `f`, so less is held back as less error can accumulate.

**Charging stops (optional, the planner's `allow_recharge`, off by default):** a stop adds
`min(B^max − level, charge_rate · charge_time)` and takes `charge_time` seconds. The UAV must reach
the station with its reserve intact. Landing and takeoff energy are charged at each stop.

## 4. Hard constraints (`feasibility.py`)

For a route `[s, j1, …, jk, station]`:

```
E_route + Σ E_task + E_return + E_reserve ≤ B_i                         (ENERGY_TOTAL)
```

With strict safety on (default), after each task `j_m` with `m < k`, where `spent_m` is the
energy used so far:

```
B_i − spent_m − E_return(j_m) − E_landing ≥ E_reserve                   (ENERGY_PREFIX)
```

With strict safety off, the check is only `B_i − spent_m ≥ E_reserve`.

Other violations: `CAPABILITY`, `UNREACHABLE`, `NO_RETURN`, `DEADLINE` (a task must *finish*
by its deadline) and `UAV_UNAVAILABLE`.

**When strict safety matters.** If legs follow minimum-energy paths and task energies are
non-negative, the total constraint implies the prefix constraint: flying the rest of the
route is itself one way of reaching a station, so `E_return(j_m)` can be no larger than the
remaining route energy. The prefix check therefore only rejects extra routes when that
argument fails, which happens with `path_metric = distance` over energy zones or with a
turn penalty. The named test `test_prefix_safety_catches_route_safe_in_total_only`
constructs such a case. Under the default settings the `strict_prefix_safety` flag is a
safety net and should not be expected to change results.

The same argument covers points in the middle of a leg, so checking at task nodes is enough.

**Worked example from the proposal:** 25 EU available, a 20 EU task, a 10 EU return.
`20 + 10 = 30 > 25`, so the assignment is infeasible. A closer station can make it feasible.
Both cases are named tests.

## 5. Task assignment

Tasks are assigned by an internal energy-feasible insertion heuristic.

## 6. Global objective `Z` (`cost.py`)

```
Z = α·ΣE_i/S_E + β·max_i T_i/S_T + γ·ΣD_i/S_D + δ·Var(L_i)/S_L² + λ·Σ_{unassigned j} P_j·M
```

Defaults: `α=0.35, β=0.35, γ=0.1, δ=0.2, λ=1, M=10`. The scales `S` come from the
nearest-UAV baseline on the same scenario, falling back to fixed scales. Each normalized
term is about 1 or below, so one unassigned task (penalty of at least 10) outweighs any
ordinary saving elsewhere.

**Lexicographic order (`compare_plans`):** (1) fewer hard-constraint violations, (2) fewer
unassigned mandatory tasks, (3/4) lower `Z`, which combines time, energy and balance.

## 7. Energy during execution (`simulator.py`)

- **Noise:** each leg and task's actual energy is its estimate × `N(1, σ)`, seeded.
- **Planned vs actual:** each UAV's planned and actual energy are both recorded, including across
  replans. `energy_estimation_error` is the fleet's absolute difference as a percentage of planned.
- **Margin alert:** `MARGIN_ALERT` is logged, once per plan and UAV, when
  `battery − E_return − E_landing − E_reserve < 0` after an outbound leg or a task.
- **Early return-to-home (`early_rth`, off by default):** on the same condition the UAV abandons its
  route and flies home, an `RTH` event is logged, and its unfinished tasks are replanned.

## 8. Simplifying assumptions

By default each UAV flies one sortie with no mid-route recharging. 2D flight, constant speed, no wind, and
collision avoidance between UAVs is not modelled. See `LIMITATIONS.md`.
