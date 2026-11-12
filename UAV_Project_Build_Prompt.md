# BUILD PROMPT — Energy-Aware Joint Routing & Task Allocation for Collaborative UAV Systems

> Paste everything below into Claude Code or Google Antigravity (agent mode) at the root of an empty folder.
> For Claude Code, you can also save it as `PROJECT_SPEC.md` in the repo and start with:
> "Read PROJECT_SPEC.md fully, then execute it phase by phase."

---

## 0. Your role and ground rules

You are a senior engineer building a complete, working research-software project from a Phase 1 academic proposal. Build **everything**: the optimization core, the simulator, the experiment runner, a REST/WebSocket backend, a web frontend, tests, Docker, CI and documentation. The final output must be a project a student team can demo, run experiments with and write a final report from.

Rules:
1. **Plan first.** Before writing code, write `docs/IMPLEMENTATION_PLAN.md` with the phases below, turned into a checklist. Tick items off as you finish them. Keep that file up to date.
2. **Work phase by phase** (Section 9). At the end of each phase: run the tests, run the linters, fix all failures, and make a git commit (`feat(phaseN): ...`). Do not start the next phase while tests are red.
3. **No fake results.** Never hard-code metrics, fabricate numbers, or tune tests so they pass. Every figure and table must come from code that actually ran. If the proposed method does not beat a baseline in some scenario, report that honestly. The research question is *whether* joint optimization helps, not a claim that it does.
4. **Label units.** Energy is in **normalized energy units (EU)** by default, with an optional physical mode (J). Everything shown in the UI or reports must say that values are simulated and illustrative, not measured drone performance.
5. **Deterministic.** Every random process takes a seed. Same scenario + same seed + same algorithm = identical output, byte for byte.
6. **One source of truth for energy.** Only the `EnergyEstimator` computes energy. Allocation, routing, feasibility and the simulator all call it. Never duplicate energy math.
7. **Typed, documented, tested.** Use Python 3.11+ with full type hints, `mypy --strict` on the core package, `ruff` for lint/format, and pytest with ≥85% coverage on `core/`. Use TypeScript `strict` on the frontend.
8. If something in this spec is ambiguous, choose the simplest reasonable option, write it down in `docs/DECISIONS.md` and keep going. Don't stop to ask unless you can't continue at all.

---

## 1. Project summary (from the proposal)

Several UAVs with limited batteries must complete tasks spread across a map (inspect a bridge, survey a field, inspect a power line, photograph a building). Each task has a location, a priority, a required capability, an execution energy, a duration and an optional deadline. The system must decide:
- **which UAV** does each task (allocation),
- **in what order** and **along which path** it visits them (routing, avoiding obstacles and no-fly zones),
- that **every route is energy-feasible**: route energy + task energy + return energy + safety reserve ≤ battery,
- and **how to repair the plan** when things change (a new urgent task, a blocked route, a UAV becomes unavailable, a sudden battery drop, a task's duration changes).

**Core contribution to implement:** a *joint* energy-aware allocation + routing algorithm that scores each candidate assignment by its effect on the UAV's **whole route and battery feasibility**, not just distance.
**Secondary contribution:** dynamic reassignment with local repair that widens its scope only when needed.
**Evidence:** multi-objective evaluation against baselines, ablation studies, and repeated randomized scenarios with statistics.

---

## 2. Tech stack

| Layer | Choice |
|---|---|
| Core algorithms | Python 3.11+, NumPy, NetworkX, SciPy |
| Exact / strong baselines | Google OR-Tools (routing solver + CP-SAT) |
| Backend API | FastAPI, Pydantic v2, Uvicorn, WebSockets |
| Persistence | SQLite via SQLModel (scenarios, runs, experiment results) |
| Background jobs | `concurrent.futures.ProcessPoolExecutor` + an in-process job registry (no Celery) |
| Frontend | React 18 + TypeScript + Vite, Tailwind CSS, Zustand (state), TanStack Query (API), react-konva (map canvas), Recharts (charts), KaTeX (equations) |
| Report figures | Matplotlib (PNG + SVG, 300 dpi) |
| Quick dashboard (the proposal named Streamlit) | Also ship a minimal `apps/streamlit_app.py` that imports `core` directly, so the Streamlit option from the proposal still exists |
| Testing | pytest, pytest-cov, hypothesis (property tests), Vitest + React Testing Library, Playwright (1–2 end-to-end smoke tests) |
| Tooling | uv or pip-tools, ruff, mypy, pre-commit, Docker + docker-compose, GitHub Actions CI, Makefile |

---

## 3. Repository layout

```
uav-joint-planner/
├── README.md
├── Makefile                       # setup, test, lint, run-api, run-web, experiments, report, docker
├── docker-compose.yml
├── .github/workflows/ci.yml
├── docs/
│   ├── IMPLEMENTATION_PLAN.md
│   ├── DECISIONS.md
│   ├── MODEL.md                   # full math formulation (matches the code exactly)
│   ├── ALGORITHMS.md              # pseudocode for every algorithm + complexity
│   ├── EXPERIMENTS.md             # protocol, scenarios, metrics, stats tests
│   ├── API.md                     # generated from OpenAPI + examples
│   ├── USER_GUIDE.md              # how to use the web app (with screenshots)
│   └── LIMITATIONS.md
├── backend/
│   ├── pyproject.toml
│   ├── src/uavplan/
│   │   ├── core/
│   │   │   ├── models.py          # dataclasses: UAV, Task, Station, Obstacle, Scenario, Route, Plan, Event
│   │   │   ├── environment.py     # EnvironmentManager: grid/graph build, obstacles, no-fly zones, path cache
│   │   │   ├── energy.py          # EnergyEstimator (normalized + physical modes)
│   │   │   ├── feasibility.py     # FeasibilityChecker (all hard constraints, returns reasons)
│   │   │   ├── cost.py            # normalized assignment cost C_ij and global objective Z
│   │   │   ├── state.py           # UAVStateManager, TaskManager, shared MissionState
│   │   │   ├── routing/
│   │   │   │   ├── paths.py       # Dijkstra, A*, energy-weighted A*, path cache
│   │   │   │   ├── construct.py   # nearest-neighbour, cheapest insertion
│   │   │   │   └── improve.py     # 2-opt, or-opt, inter-route relocate & swap
│   │   │   ├── allocation/
│   │   │   │   ├── base.py        # Planner protocol: plan(scenario, config) -> Plan
│   │   │   │   ├── nearest.py             # B1
│   │   │   │   ├── greedy_energy.py       # B2
│   │   │   │   ├── independent.py         # B3
│   │   │   │   ├── integrated.py          # PROPOSED
│   │   │   │   ├── ortools_vrp.py         # B4 strong baseline
│   │   │   │   ├── genetic.py             # B5 metaheuristic baseline
│   │   │   │   └── exact_cpsat.py         # exact for small instances
│   │   │   ├── replanning.py      # DynamicReplanningEngine
│   │   │   ├── simulator.py       # discrete-event mission simulator
│   │   │   ├── metrics.py         # all metrics
│   │   │   ├── generator.py       # seeded scenario generator + presets A–E
│   │   │   └── registry.py        # algorithm registry, name -> class + default config
│   │   ├── experiments/
│   │   │   ├── runner.py          # batch runner (parallel, resumable, seeded)
│   │   │   ├── ablation.py
│   │   │   ├── stats.py           # mean, std, 95% CI, Wilcoxon, Holm correction, effect size
│   │   │   ├── plots.py           # Matplotlib report figures
│   │   │   └── report.py          # builds results/REPORT.md with tables + figures
│   │   ├── api/
│   │   │   ├── main.py            # FastAPI app, CORS, routers
│   │   │   ├── schemas.py         # Pydantic request/response models
│   │   │   ├── routers/{scenarios,plans,simulations,experiments,meta}.py
│   │   │   ├── ws.py              # WebSocket simulation streaming
│   │   │   ├── jobs.py            # job registry, progress, cancellation
│   │   │   └── db.py              # SQLModel tables + session
│   │   └── cli.py                 # `uavplan plan|simulate|experiment|report|generate`
│   ├── tests/                     # unit, property, integration, regression
│   └── scenarios/                 # presets A–E as JSON + a hand-made earthquake demo
├── frontend/
│   ├── package.json, vite.config.ts, tailwind.config.ts, tsconfig.json
│   └── src/
│       ├── api/                   # typed client (generated from OpenAPI with openapi-typescript)
│       ├── store/                 # Zustand stores
│       ├── components/
│       │   ├── map/               # MapCanvas, GridLayer, ObstacleLayer, TaskMarker, UAVSprite, RouteLayer, StationMarker
│       │   ├── panels/            # UAVPanel (battery bars), TaskTable, FeasibilityPanel, EventLog, WeightSliders
│       │   ├── charts/            # MetricBar, BoxPlot, BatteryTimeline, GanttChart, ParetoScatter
│       │   └── ui/                # buttons, modals, toasts, tabs
│       ├── pages/
│       │   ├── Dashboard.tsx
│       │   ├── ScenarioBuilder.tsx
│       │   ├── Planner.tsx
│       │   ├── Simulation.tsx
│       │   ├── Compare.tsx
│       │   ├── Experiments.tsx
│       │   └── Model.tsx          # equations, explanation, the 25-vs-30 EU feasibility example
│       └── main.tsx, App.tsx, router.tsx
├── apps/streamlit_app.py
└── results/                       # gitignored except a committed sample run
```

---

## 4. Mathematical model (implement exactly; mirror it in docs/MODEL.md)

### 4.1 Environment
- 2D grid map, `W × H` cells (default 50 × 50, cell size `s` meters, default 20 m). Graph `G = (V, E)` over free cells, 8-connected; diagonal edges cost `√2·s`. Don't allow diagonals that cut obstacle corners.
- **Obstacles / no-fly zones:** rectangles and polygons. Cells inside are removed. A *blocked route* event removes a set of edges or cells while the mission is running.
- Each edge stores `e_ij = (d_ij, t_ij, E_ij)`, where `t = d / v_uav` and `E` comes from the EnergyEstimator. Because speed and power can differ per UAV, compute `t` and `E` per UAV type on demand and cache them.
- Key nodes: UAV starts, task locations, charging/landing stations (one or more; each UAV has a home station).
- Path search: Dijkstra; A* with an octile-distance heuristic (admissible); energy-weighted A* (edge weight = E_ij; heuristic = octile distance × the minimum energy per meter). Cache all-pairs shortest paths **between key nodes only**, and invalidate the cache when the map changes.

### 4.2 Entities
- UAV `U_i = (p_i, B_i, B_i^max, C_i, T_i, v_i, P_i^cruise, P_i^hover, status, home_i)`, status ∈ {idle, en_route, working, returning, charging, unavailable}.
- Task `J_j = (p_j, P_j ∈ {low=1, medium=2, high=3, critical=4}, S_j (required capability), E_task,j, dur_j, deadline_j | None, mandatory: bool, release_time_j)`.
- Capabilities: e.g. `{"camera", "thermal", "lidar", "payload"}`.

### 4.3 Energy model (`energy.py`)
- **Normalized mode (default):** travel energy `E_travel = k_i · d` (EU per meter, per UAV type), task energy = `E_task,j` (given), plus optional fixed takeoff/landing energies. Battery `B^max = 100 EU` by default.
- **Physical mode:** `E_ij = P_ij · t_ij` (J), with `P_cruise` and `P_hover` (W). Task energy = `P_hover · dur_j` + a task-specific overhead. Optional turn penalty: energy per radian of heading change. Optional climb term if 3D is added later. Example to put in a unit test: 100 W × 60 s = 6000 J = 6 kJ.
- **Return energy:** `E_return = min over reachable stations` of the energy along the path from the last task to that station (or only the home station, if that config flag is set).
- **Reserve:** `E_reserve = ρ · B^max` (default ρ = 0.10, configurable).
- **Route feasibility (hard):** for a UAV with route `[s, j1, …, jk, station]`, the **cumulative** energy at every prefix must stay ≥ the reserve, and
  `E_route + Σ E_task + E_return + E_reserve ≤ B_i`.
  Check at every prefix, not only the total: the UAV must be able to reach a station from **every** point on the route (strict safety mode, a config flag, default on).

### 4.4 Hard constraints (`feasibility.py`)
Return a structured `FeasibilityReport { feasible: bool, violations: [ {type, uav_id, task_id?, detail, margin} ] }`. Types:
`CAPABILITY`, `UNREACHABLE` (no path), `ENERGY_TOTAL`, `ENERGY_PREFIX`, `NO_RETURN`, `DEADLINE`, `UAV_UNAVAILABLE`.

### 4.5 Assignment cost (`cost.py`)
For inserting task `j` into UAV `i`'s route at its best position:
`C_ij = w_d·D̂_ij + w_e·Ê_ij + w_t·T̂_ij + w_l·L̂_ij`
- `D̂`: incremental distance, `Ê`: incremental energy (travel + task + change in return), `T̂`: increase in that UAV's completion time, `L̂`: increase in workload imbalance (the change in the coefficient of variation of UAV loads, measured as total time or energy).
- Normalize each term by dividing by its maximum over all candidates in the current step (min–max option too). Default weights: `w_d=0.2, w_e=0.4, w_t=0.3, w_l=0.1`, configurable from the API and UI.
- Higher-priority tasks are considered first. Priority also multiplies a penalty for leaving the task unassigned.

### 4.6 Global objective (`cost.py`)
`Z = α·ΣE_i + β·max_i T_i + γ·ΣD_i + δ·Var(L_i) + λ·Σ_{unassigned j} P_j·M`
Each term is normalized against a reference (the nearest-UAV baseline's values on the same scenario, or fixed scales). Defaults `α=0.35, β=0.35, γ=0.1, δ=0.2`; λ·M is large enough that leaving a mandatory task unfinished is never worth it for small gains elsewhere.
**Lexicographic priority, as in the proposal:** (1) satisfy safety and hard constraints, (2) complete mandatory tasks, (3) minimize time and energy, (4) balance workload. Implement a `compare_plans(a, b)` that follows this order, with `Z` as the tie-breaker at level 3/4.

---

## 5. Algorithms (one class per algorithm, all following the `Planner` protocol)

All planners return a `Plan`: per-UAV ordered task list, full node path, per-leg energy/time, cumulative battery profile, unassigned tasks with reasons, the feasibility report, objective breakdown and compute time.

- **B1 Nearest-UAV:** each task (in priority order) goes to the UAV whose current position (start or last task) is closest by straight-line distance. Visit order = order of assignment. **No energy check while planning.** Run the feasibility checker afterwards and report the violations (this shows what goes wrong without energy awareness).
- **B2 Greedy energy-aware:** like B1, but only considers UAVs that can still do the task standalone with energy enough to return, using the path energy. Appends to the end of the route; no reordering.
- **B3 Independent allocation + routing:** Step 1: allocate with capacity-balanced clustering (capability-aware k-medoids over path distance, or balanced Hungarian rounds), ignoring route energy. Step 2: for each UAV, order its tasks with nearest-neighbour + 2-opt. Step 3: if a route is infeasible, drop the lowest-priority tasks until it is feasible, and report them as unassigned. This is the "separate decisions" baseline the proposal's research question is about.
- **PROPOSED Integrated (joint):**
  1. Sort unassigned tasks by (priority desc, deadline asc, release time asc).
  2. For each task: candidate UAVs = those with the capability that are available. For each candidate, find the **best insertion position** in its current route (every position), and compute the incremental distance/energy/time and the new return energy using the EnergyEstimator.
  3. Throw out candidates whose new route fails **any** hard constraint (FeasibilityChecker).
  4. Score the rest with `C_ij`; assign to the lowest. If none is feasible, try **one repair move**: move one task already on another UAV to make room (relocate), then try again. If it is still infeasible, mark the task unassigned with a reason.
  5. After the construction pass: improve each route with **2-opt + or-opt**, then do **inter-route local search** (relocate, swap and 2-opt* between UAVs). Accept only moves that keep everything feasible and improve the result under `compare_plans`.
  6. Optional (flag): a short **simulated-annealing** or Large-Neighbourhood-Search phase under a time budget (default 2 s).
  Config flags (needed for the ablations): `use_energy_in_cost`, `use_workload_term`, `use_insertion` (vs append), `use_local_search`, `use_inter_route`, `use_repair_move`, `strict_prefix_safety`.
- **B4 OR-Tools VRP (strong baseline):** a multi-depot VRP with the path-distance matrix, an energy dimension (capacity = B − reserve, with the return arc included), a time dimension with deadlines as time windows, capability handled through allowed-vehicle sets, and dropping a task allowed at a penalty that scales with priority. Run GUIDED_LOCAL_SEARCH under a time limit.
- **B5 Genetic algorithm:** giant-tour + split encoding, order crossover, swap/inversion mutation, fitness = `compare_plans`-compatible scalar with feasibility penalties. Seeded, with the population and generations configurable.
- **Exact (small instances, ≤ ~3 UAVs × 10 tasks):** a CP-SAT model of the same problem, used to measure the **optimality gap** of the heuristics on Scenario A. Time limit 60 s; report the gap only when the solver proves optimality.

Write the pseudocode and complexity of each one in `docs/ALGORITHMS.md`.

---

## 6. Dynamic replanning and simulation

### 6.1 Discrete-event simulator (`simulator.py`)
- Simulation time advances leg by leg (event queue). Each UAV moves along its node path at speed `v_i`, its battery drains through the EnergyEstimator, it works for `dur_j` at each task, and it returns to a station.
- Optional noise (seeded): energy consumption × N(1, σ), default σ = 0, so the system can be tested against estimation error.
- Records a **timeline** (position, battery and status per UAV per tick, default tick 1 s, downsampled for streaming) and an **event log**.

### 6.2 Events (`models.Event`) to support
`NEW_TASK(task, time)`, `BLOCK_REGION(polygon|cells, time)`, `UAV_UNAVAILABLE(uav_id, time)`, `BATTERY_DROP(uav_id, amount, time)`, `TASK_DURATION_CHANGE(task_id, new_dur, time)`, `TASK_CANCELLED(task_id, time)`, `UAV_JOINS(uav, time)`.
Events can be scripted in the scenario JSON or injected live from the UI over the WebSocket.

### 6.3 DynamicReplanningEngine (`replanning.py`)
Follow the six steps from the proposal:
1. Update the mission state (freeze the steps already done; current positions and battery become the new starts; tasks in progress either finish or are abandoned, as configured).
2. Find the affected UAVs and tasks (e.g. a route crosses the blocked region; tasks belonging to the failed UAV; the UAV whose battery dropped).
3. Re-check feasibility of every remaining plan.
4. **Local repair:** remove the affected tasks and reinsert them with the integrated insertion over all available UAVs; re-optimize only the affected routes.
5. **Escalation:** if feasibility or mandatory-task completion is still not restored, widen the scope to all UAVs (a full integrated replan from the current state).
6. Record the updated plan, the diff (moved tasks, dropped tasks), replanning time and the objective change.
Also trigger a replan when the plan becomes *materially suboptimal* (objective worse by more than a threshold, default 15%) — this is configurable.
Measure and expose the trade-off: `local_only`, `local_then_global` (default) and `always_global`.

---

## 7. Metrics and experiments

### 7.1 Metrics (`metrics.py`), computed for both the planned and the simulated result
Total energy; mission completion time (makespan); task completion rate (all tasks, mandatory only, weighted by priority); total travel distance; energy feasibility rate (% of UAV plans satisfying all energy constraints); minimum battery margin at return; workload imbalance (CV and max−min of per-UAV time); number of unassigned tasks and why; number of deadline violations; initial computation time; replanning time; number of replans; tasks moved per replan; optimality gap (where an exact result exists); scalability (compute time vs number of tasks and UAVs).

### 7.2 Scenarios (`generator.py` + `scenarios/*.json`)
| ID | Setup | Tests |
|---|---|---|
| A | 3 UAVs, 10 tasks, static | correctness, optimality gap vs exact |
| B | 5 UAVs, 30 tasks, varied priorities | scalability, allocation quality |
| C | 4 UAVs, 20 tasks, starting batteries 40–100 EU | energy feasibility, return reserve |
| D | 4 UAVs, 20 tasks + 5 urgent tasks arriving | dynamic adaptation |
| E | 5 UAVs, 25 tasks, one UAV fails mid-mission | recovery & reassignment |
| F (extra) | scaling sweep: UAVs ∈ {2,4,8,12}, tasks ∈ {10,25,50,100} | scalability curves |
| G (extra) | energy-estimation noise σ ∈ {0, 0.05, 0.1, 0.2} | robustness of the reserve |
| Demo | hand-made "earthquake" map: collapsed buildings, a river, a no-fly zone, 4 UAVs | showcase |

Task-location distributions: uniform, clustered (Gaussian blobs) and edge-heavy. Obstacle density is configurable.

### 7.3 Protocol (`experiments/`)
- 30 seeds per (scenario, algorithm), default. Every algorithm gets the **same** scenarios and event sequences.
- Ablations of PROPOSED: full; −energy-in-cost; −workload term; −insertion (append only); −local search; −inter-route; −dynamic replanning (in D/E: no replanning, or only an always-global replan).
- Stats: mean ± std, median/IQR, 95% bootstrap CI; paired **Wilcoxon signed-rank** of PROPOSED vs each baseline per metric, with **Holm–Bonferroni** correction; effect size (rank-biserial). Win/tie/loss counts.
- Output: `results/<timestamp>/raw.csv` (one row per run), `summary.csv`, `stats.csv`, `figures/*.png|svg`, and `REPORT.md` with auto-generated tables and figures. It must include a section **"Where the integrated method does not help"**, filled in from the data.
- Figures: grouped bar chart per metric per scenario; box plots; compute time vs size (log-log); battery-over-time for one demo run; Gantt chart of UAV schedules; route maps for each algorithm on the same scenario; Pareto scatter (energy vs makespan) from a weight sweep; ablation waterfall/bar.
- The runner can be resumed (skips finished rows), runs in parallel across processes, shows progress, and can be cancelled.
- CLI: `uavplan experiment --scenarios A,B,C,D,E --algorithms all --seeds 30 --out results/`.

---

## 8. Backend API (FastAPI) and frontend

### 8.1 REST endpoints (all typed with Pydantic; OpenAPI at `/docs`)
- `GET /api/meta/algorithms` (names, descriptions, config schema) · `GET /api/meta/presets`
- `POST /api/scenarios/generate` (params + seed) · `GET/POST/PUT/DELETE /api/scenarios[/{id}]` · `POST /api/scenarios/import` · `GET /api/scenarios/{id}/export`
- `POST /api/plans` `{scenario_id | scenario, algorithm, config, weights}` → Plan (synchronous below a size threshold, otherwise a job)
- `POST /api/plans/compare` `{scenario, algorithms[]}` → plans + metrics side by side
- `POST /api/feasibility/check` `{scenario, plan}` → FeasibilityReport (used by the UI when the user edits routes by hand)
- `POST /api/simulations` → sim_id · `GET /api/simulations/{id}` · `GET /api/simulations/{id}/timeline`
- `WS /ws/simulations/{id}`: server → client frames `{t, uavs:[{id,x,y,battery,status}], events:[...], replan?:{diff, plan, time_ms}}`; client → server `{cmd: play|pause|speed|seek|inject_event, payload}`
- `POST /api/experiments` → job_id · `GET /api/jobs/{id}` (progress) · `DELETE /api/jobs/{id}` (cancel) · `GET /api/experiments/{id}/results` · `GET /api/experiments/{id}/download` (zip)
- `GET /api/health`
Errors use a consistent `{error:{code,message,details}}` shape. Validate inputs (e.g. no task inside an obstacle, capabilities exist, battery > reserve).

### 8.2 Frontend pages (clean, modern, responsive, light/dark mode)
1. **Dashboard:** project overview, quick start ("Load earthquake demo"), recent scenarios and runs, a short version of the proposal's central idea.
2. **Scenario Builder:** grid canvas with tools (draw obstacle rectangle/polygon, no-fly zone, place task, place UAV, place station, erase), zoom and pan, a properties side panel (task priority/capability/energy/duration/deadline/mandatory; UAV battery/speed/capabilities/home), a random generator form (seed, counts, distribution, obstacle density), preset loader (A–E, Demo), import/export JSON, and live validation messages.
3. **Planner:** pick an algorithm, weight sliders (w_d, w_e, w_t, w_l, plus the reserve %), Run. Shows routes coloured per UAV with numbered visit order; a battery bar per UAV showing planned use split into travel / task / return / reserve; feasibility panel with every violation, click to highlight it on the map; unassigned tasks with reasons; objective breakdown; compute time. **"Explain this assignment"**: click a task to see the candidate UAVs, their insertion costs, and which were rejected and why. This is important for the viva/demo.
4. **Simulation:** play/pause/speed/seek timeline; animated UAVs with battery rings; an event-injection toolbar (add urgent task by clicking the map, draw a blocked region, fail a UAV, drop a battery); an event log; on a replan, show the old route faded and the new route bold, with the diff and replanning time; live battery-over-time chart; Gantt view.
5. **Compare:** run several algorithms on the same scenario; small route maps side by side; metrics table with the best value highlighted; bar charts; the same weights apply to all.
6. **Experiments:** set up a batch (scenarios × algorithms × seeds × ablations), launch it, progress bar, then results: summary tables, box plots, significance table (p-values after Holm correction, effect size), Pareto plot, and downloads (CSV, figures, REPORT.md).
7. **Model:** the equations rendered with KaTeX (energy model, feasibility constraint, C_ij, Z), the lexicographic priority, the interactive **25 EU vs 30 EU example** from the proposal (sliders for available energy, task energy and return energy, showing feasible/infeasible), plus assumptions and limitations.

UX details: colour-blind-safe UAV palette; legend; tooltips on everything; keyboard shortcuts in the builder; toasts for errors; empty and loading states; a footer note on every page saying values are simulated and in normalized units.

---

## 9. Phases (execute in order; each ends with green tests + a commit)

**Phase 0 – Scaffold.** Repo layout, pyproject, package.json, ruff/mypy/pre-commit, Makefile, Docker skeleton, CI workflow, `IMPLEMENTATION_PLAN.md`, `DECISIONS.md`.
*Done when:* `make setup && make test && make lint` pass on an empty skeleton, and CI is green.

**Phase 1 – Problem modelling.** `models.py`, `environment.py`, `energy.py`, `feasibility.py`, `cost.py`, `state.py`, `generator.py`, presets A–E + Demo, `docs/MODEL.md`.
*Tests:* graph construction and obstacle removal; corner-cutting forbidden; A* == Dijkstra cost on random grids (hypothesis); energy formula unit tests including 100 W × 60 s = 6 kJ; the **25 EU / 20 + 10 = 30 EU → infeasible** example as a named test; prefix-safety catches a route that is feasible in total but unsafe in the middle; generator determinism.

**Phase 2 – Baselines + routing.** `paths.py`, `construct.py`, `improve.py`, B1, B2, B3, `metrics.py`, `simulator.py` (static missions), CLI `plan`/`simulate`.
*Tests:* 2-opt never makes a route worse and keeps it feasible; B1 is allowed to produce infeasible plans but must report them; B2 never produces energy violations; simulation energy == planned energy when σ = 0 (within float tolerance).

**Phase 3 – Proposed integrated algorithm.** `integrated.py` with all flags, inter-route local search, repair move, optional SA/LNS; `compare_plans`; `docs/ALGORITHMS.md`.
*Tests:* PROPOSED plans are always feasible (property test over 200 random scenarios); never leaves a mandatory task unassigned when the exact solver proves a feasible full assignment exists (Scenario-A-size instances); deterministic for a given seed; every ablation flag actually changes the behaviour.

**Phase 4 – Strong baselines.** OR-Tools VRP, GA, CP-SAT exact.
*Tests:* exact ≤ every heuristic on objective for tiny instances; OR-Tools output passes our FeasibilityChecker (a cross-check that both models agree).

**Phase 5 – Dynamic adaptation.** Events, `replanning.py`, simulator with events and live injection, the three replanning modes.
*Tests:* after `UAV_UNAVAILABLE`, none of its tasks stay assigned to it; after `BLOCK_REGION`, no route crosses the region; after `BATTERY_DROP`, the plan is feasible again or the drop is reported; the escalation path is exercised.

**Phase 6 – Experiments & stats.** Runner, ablations, stats, plots, report builder, scenarios F and G.
*Done when:* `make experiments-quick` (5 seeds, A–E) finishes in under ~10 min on a laptop and produces REPORT.md + figures; `make experiments-full` runs the 30-seed protocol. Commit a sample `results/sample/` from the quick run.

**Phase 7 – Backend API.** All endpoints, WebSocket streaming, jobs, SQLite persistence, OpenAPI → `docs/API.md`.
*Tests:* pytest + httpx integration tests for every endpoint, including the WebSocket play/inject flow and job cancellation.

**Phase 8 – Frontend.** All 7 pages, typed API client generated from OpenAPI, state stores, charts, canvas.
*Tests:* Vitest for stores and components; Playwright smoke tests: (1) load demo → plan with PROPOSED → see routes and battery bars; (2) start simulation → inject UAV failure → see a replan in the event log.

**Phase 9 – Packaging & docs.** `docker-compose up` brings up the API on :8000 and the web app on :5173 (or nginx on :80); Streamlit app; README with screenshots/GIF, quick start, architecture diagram (Mermaid), and how to reproduce results; USER_GUIDE, EXPERIMENTS and LIMITATIONS docs.
*Done when:* a fresh clone + `docker compose up` gives a working demo with no manual steps.

**Phase 10 – Final verification.** Run the full test suite, lint and type checks, the quick experiments and both Playwright tests. Write `docs/FINAL_CHECKLIST.md` mapping **each objective and claim in the proposal** (objectives 1–6, the 5 baselines, 8 metrics, scenarios A–E, ablations, dynamic events, visual interface) to the file, test and figure that delivers it. Anything not delivered must be listed honestly.

---

## 10. Extras worth including (already built into the phases above)
- An "Explain this assignment" panel (helps a lot in the viva).
- An exact solver and optimality gap on small instances (gives the heuristic results a reference point).
- An OR-Tools VRP baseline (a fair, strong comparison; the proposal said "a genetic algorithm or other established optimizer can be added").
- Energy-noise robustness experiment (justifies the reserve ρ).
- Pareto sweep over the weights (the proposal lists Pareto optimization as an advanced extension).
- Scaling curves (the proposal asks for performance as fleet size grows).
- Auto-generated REPORT.md with a "where it does not help" section (matches the proposal's integrity requirement).
- Streamlit mini-app (keeps the proposal's original tool choice).

**Out of scope for now** (put in LIMITATIONS.md as future work): 3D flight with climb energy, wind, real drone hardware/ROS/PX4 integration, reinforcement-learning planners, learned energy prediction, distributed onboard execution with event-triggered communication. Design the interfaces (`Planner`, `EnergyEstimator`) so these can be added later without rewrites.

---

## 11. Definition of done
- `make test` → all backend and frontend tests pass; core coverage ≥ 85%; `mypy --strict` clean on `core/`; ruff clean; `tsc --noEmit` clean.
- `docker compose up` → a working web app; the earthquake demo plans, simulates, accepts injected events and replans visibly.
- `make experiments-quick` → REPORT.md with real tables, figures and significance tests.
- `docs/FINAL_CHECKLIST.md` traces every proposal item to code, a test and evidence.
- Every number in the UI and reports is generated, seeded, reproducible, and labelled as simulated.

Start now with Phase 0. After each phase, print a short summary: what was built, test results, and what comes next.
