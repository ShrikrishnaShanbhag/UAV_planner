# Optimizing Routing, Task Allocation, and Energy Management in Collaborative UAV Systems: Decisions

Choices made where the build spec was ambiguous or where its parts conflicted.

| # | Decision | Why |
|---|---|---|
| 1 | `scripts/dev.py` holds every task; the Makefile delegates to it. | The development machine is Windows without `make`. One implementation serves both. |
| 2 | Work happens on the `develop` branch. | The tooling convention here avoids committing straight to the default branch. |
| 3 | Search budgets are **deterministic counts** (iterations, solutions, CP-SAT deterministic time), with wall-clock limits only as safety caps. | Rule 5 requires identical output for identical input. A wall-clock budget, like the spec's "2 s" LNS default, cannot satisfy that. |
| 4 | "Byte for byte" excludes wall-clock fields. `Plan.canonical_json()` omits `compute_time_s`. | Measured compute time differs between runs by nature. |
| 5 | Added `energy_zone` regions: rectangles or polygons with an energy multiplier on the edges inside them. | Without them, energy is proportional to distance on every edge, so energy-weighted A* would equal shortest path and the proposal's point that "the shortest route is not necessarily the lowest-energy route" could not be shown. This is not a wind model: wind stays out of scope. |
| 6 | Every planner has the signature `plan(scenario, config)`. Mid-mission replans pass a derived `Scenario` holding current positions and batteries, with blocked regions added as obstacles. | Planners stay stateless, and baselines can replan through the same interface. |
| 7 | B1 considers only capable UAVs. | Ignoring capability would make it a strawman: its purpose is to show the cost of ignoring *energy*. |
| 8 | Energy rates are 0.014–0.021 EU/m, set from the proposal's example of a 10 EU return trip (about 500–700 m on the 1 km map). | The first calibration (0.028–0.042 EU/m) left even Scenario A largely unsolvable: the best planner completed 77% of tasks. **This change was made after seeing that first result.** It rescales energy for every algorithm alike and was not chosen by which method it favours. |
| 9 | The mandatory-task guarantee comes from an added bounded backtracking step ('mandatory rescue'). | A greedy heuristic alone cannot satisfy the spec's Phase 3 test. The guarantee holds only when the search finishes within its budget. |
| 10 | The mandatory-vs-exact test lives with the Phase 4 tests. | It needs the CP-SAT solver, which is built in Phase 4. |
| 11 | Baselines replan in D and E by re-running themselves from the current state. | Comparing against baselines that never adapt would mostly measure the absence of adaptation. |
| 12 | Injecting a live event re-runs the deterministic mission with that event added. | The past stays identical and seeking is free, without needing simulator rollback. |
| 13 | Added `energy_per_completed_task` and compare on it instead of total energy. | Total energy rewards a planner for doing less work. |
| 14 | Added `GET /api/runs`, `GET /api/experiments` and a figure endpoint beyond the listed API. | The Dashboard and Experiments pages need them. |
| 15 | The energy audit was written before any edit. | The update prompt's ground rules require it, although its step numbering lists the audit third. |
| 16 | The folder, npm and Python package names stay `uav-joint-planner` and `uavplan`. | Renaming them would restructure the project, which the update forbids. Only displayed titles changed. |
| 17 | No "Task Allocation" nav item or card existed, so none was removed. The Planner's algorithm selector, `w_d`, `w_t`, `w_l` sliders and Explain panel were. | The Planner now uses the proposed planner silently. |
| 18 | Left in place though now unused by the Planner: `ExplainPanel.tsx`, `setAlgorithm` in the mission store, and the `explanations` field in the API. Compare, Experiments and Streamlit keep their algorithm choices. | The update says to keep backend modules and leave unused code. Comparing algorithms is the evidence for the study, and the update names only the Planner's controls. |
| 19 | Early return-to-home has a flag, `early_rth`, off by default. | The update makes only charging stops explicitly optional, but always-on RTH would change every noise and baseline result. |
| 20 | Charging stops are stored inside a route as `@charge:<station>` entries and never appear in `task_ids`. | The planner's search moves keep working without a second route format. |
| 21 | The test in `e2e/smoke.spec.ts` that read the removed algorithm selector now asserts it is gone. | The update removes that control, so the old assertion could not stay. |
| 22 | The navbar shows the short name "UAV Energy Planner"; the full title stays in the Dashboard heading, browser title, README and docs, and appears as the navbar tooltip. | The full title wrapped the navigation onto two rows. |
