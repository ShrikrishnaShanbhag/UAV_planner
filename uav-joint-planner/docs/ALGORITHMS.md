# Optimizing Routing, Task Allocation, and Energy Management in Collaborative UAV Systems: Algorithms

Notation: `n` tasks, `m` UAVs, `r` the longest route. One route evaluation costs `O(r)` and
is memoized by `(uav, sequence)`. Shortest paths come from the cached key-node matrix.

Every planner implements `plan(scenario, config) -> Plan` and is judged by the same
`FeasibilityChecker`. Baselines B4, B5 and the exact solver are described at the end.

## B1 Nearest-UAV (`allocation/nearest.py`)

```
for task in priority order:
    capable = UAVs with the required capability
    if none: mark unassigned
    assign to argmin over capable of straight_line(last position, task)
report violations afterwards
```

No energy check while planning. `O(n·m)`.

## B2 Greedy energy-aware (`allocation/greedy_energy.py`)

As B1, but keeps only UAVs whose route with the task appended passes the energy, return and
reachability checks. Never reorders. Deadlines are not part of its decision, so deadline
violations are reported afterwards. `O(n·m·r)`.

## B3 Independent allocation + routing (`allocation/independent.py`)

```
1. while tasks remain:
       Hungarian match of UAVs to tasks on path distance from each UAV's last position
       (non-capable or unreachable pairs forbidden; energy ignored)
2. for each UAV: order = nearest_neighbour + 2-opt, on distance alone
3. while a route is infeasible: drop its lowest-priority task (optional before mandatory)
```

`O((n/m) · max(n,m)³)` for allocation, `O(m·r³)` per routing pass.

## PROPOSED Integrated (`allocation/integrated.py`)

```
sort tasks by (priority desc, deadline asc, release asc, id)
for each task j:
    C = []
    for each available UAV i with the capability:
        for each position p in i's route:          # append only if use_insertion is off
            route' = route with j at p
            if route' passes every hard constraint: add (i, p, ΔD, ΔE, ΔT, ΔL) to C
    if C not empty: apply the lowest-cost insertion
    else if use_repair_move: try relocating one assigned task to make room
    else if allow_recharge: try adding a charging stop to a route that could take j
    else: mark j unassigned, with the reason per UAV

repeat up to twice:
    2-opt + or-opt on every route                  # use_local_search
    relocate, swap, 2-opt* between routes          # use_inter_route
    try to insert still-unassigned tasks           # capacity may have been freed

optional: LNS with a fixed iteration budget        # use_lns
if a mandatory task is still unassigned: mandatory rescue
```

A move is accepted only if it is strictly better under the lexicographic `PlanScore`, whose
first field counts hard violations, so a feasible plan can never become infeasible.

**Repair move.** For each UAV `d` that can do `j` and each task `t` on it: remove `t`, find
positions where `j` fits, and place `t` on another UAV. Picks the lowest total energy.
Bounded by `repair_budget` evaluations.

**Charging stop.** With `allow_recharge` on, a task no route can take is tried at every position,
with one stop at every position and station, up to `max_recharges` per route. The lowest-energy
feasible option wins. Stops made unnecessary by later improvements are removed.

**Mandatory rescue.** Backtracks over mandatory tasks only, starting from empty routes,
trying each UAV and position in order of energy; the other tasks are then inserted again.
Inserting tasks one at a time at every position generates every assignment and ordering,
so the search is complete when it finishes within `rescue_budget` evaluations. If the budget
runs out, the guarantee no longer applies and the earlier plan is kept.

**Ablation behaviour.** `use_energy_in_cost=False` sets `w_e` and `α` to zero during search,
and `use_workload_term=False` does the same for `w_l` and `δ`. Energy remains a hard
constraint in both cases, and the reported `Z` always uses the full weights.

**Complexity.** Construction is `O(n·m·r²)`. One improvement pass is `O(m·r³)` within routes
and `O(n·m·r²)` between routes, capped at `max_inter_route_moves`. Rescue is exponential in
the number of mandatory tasks but capped by its budget.

## B4 OR-Tools VRP (`allocation/ortools_vrp.py`)

Multi-depot VRP with one dummy end node per vehicle. Arc costs combine the energy and
distance terms of `Z`, and a global span cost on the time dimension approximates makespan.

- Energy dimension: capacity `B − reserve − takeoff`; the arc to the end node carries the
  return energy. Per-vehicle transit matrices handle heterogeneous UAVs.
- Time dimension: deadlines and release times as windows on service start.
- Capability through allowed-vehicle sets. Each task may be dropped at a penalty scaled by
  priority, 100 times larger for mandatory tasks.
- Guided local search, stopped by `solution_limit` (deterministic), with a wall-clock cap.
- Energy and time round up, capacities and deadlines round down, so solver routes pass the
  checker. A safety net sheds tasks if a case outside the VRP model appears, such as hover
  waiting energy, and counts them in `shed_tasks`.

## B5 Genetic algorithm (`allocation/genetic.py`)

Chromosome: a permutation of all tasks. Decoder: walk the tour and append each task to the
current UAV if feasible, otherwise to the next UAV that can take it, otherwise leave it
unassigned. Fitness: `violations·1e9 + mandatory_unassigned·1e6 + Z`. Tournament selection,
order crossover, swap or inversion mutation and elitism. Complexity is
`O(population · generations · n · m · r)`.

## Exact CP-SAT (`allocation/exact_cpsat.py`)

One circuit constraint per UAV with optional nodes, cumulative energy and time variables
linked by the arc literals, and the full objective including the quadratic `Var(L)` term.
Mandatory tasks carry a dominating weight to respect the lexicographic order. The integrated
plan is supplied as a hint only. Limited to small static instances. The plan records
`proven_optimal`, and a gap is reported only when that is true.

## Determinism

All tie-breaks are explicit and iteration runs over sorted ids. Search budgets are counts,
not wall-clock limits, and randomness uses `numpy.random.default_rng(seed)`.
