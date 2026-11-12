# Optimizing Routing, Task Allocation, and Energy Management in Collaborative UAV Systems: Implementation plan

Status legend: `[x]` done and verified, `[ ]` not done. Each phase ends with green tests,
green linters and one `feat(phaseN): ...` commit.

## Phase 0 – Scaffold
- [x] Repository layout, `pyproject.toml`, `package.json`
- [x] ruff, mypy, pre-commit, Makefile (`scripts/dev.py` for Windows)
- [x] Docker skeleton, CI workflow
- [x] `IMPLEMENTATION_PLAN.md`, `DECISIONS.md`

## Phase 1 – Problem modelling
- [x] `models.py`, `environment.py`, `energy.py`, `feasibility.py`, `cost.py`, `state.py`, `generator.py`
- [x] Presets A–E + Demo as JSON
- [x] `docs/MODEL.md`
- [x] Tests: graph/obstacles, corner cutting, A* == Dijkstra (hypothesis), 6 kJ, 25/30 EU, prefix safety, generator determinism

## Phase 2 – Baselines + routing
- [x] `paths.py`, `construct.py`, `improve.py`, B1, B2, B3, `metrics.py`, `simulator.py` (static), CLI `plan`/`simulate`
- [x] Tests: 2-opt monotone and feasible, B1 reports infeasibility, B2 never energy-violates, sim energy == planned energy

## Phase 3 – Proposed integrated algorithm
- [x] `integrated.py` with all flags, inter-route search, repair move, SA/LNS, `compare_plans`, `docs/ALGORITHMS.md`
- [x] Tests: always feasible (200 random), no missed mandatory when exact proves full assignment, deterministic, each ablation flag changes behaviour

## Phase 4 – Strong baselines
- [x] OR-Tools VRP, GA, CP-SAT exact
- [x] Tests: exact ≤ heuristics on tiny instances, OR-Tools passes FeasibilityChecker

## Phase 5 – Dynamic adaptation
- [x] Events, `replanning.py`, simulator with events and live injection, three replanning modes
- [x] Tests: UAV_UNAVAILABLE, BLOCK_REGION, BATTERY_DROP, escalation path

## Phase 6 – Experiments and statistics
- [x] Runner, ablations, stats, plots, report, scenarios F and G
- [x] `experiments-quick` under ~10 min producing REPORT.md + figures; `results/sample/` committed

## Phase 7 – Backend API
- [x] All endpoints, WebSocket streaming, jobs, SQLite, `docs/API.md`
- [x] Integration tests for every endpoint, WS play/inject, job cancellation

## Phase 8 – Frontend
- [x] 7 pages, generated typed client, stores, charts, canvas
- [x] Vitest and 2 Playwright smoke tests

## Phase 9 – Packaging and docs
- [x] `docker compose up` working demo, Streamlit app, README, USER_GUIDE, EXPERIMENTS, LIMITATIONS

## Phase 10 – Final verification
- [x] Full suite, lint, types, quick experiments, Playwright, `docs/FINAL_CHECKLIST.md`
