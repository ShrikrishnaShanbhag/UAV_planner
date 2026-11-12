# Optimizing Routing, Task Allocation, and Energy Management in Collaborative UAV Systems: API reference

Generated from the OpenAPI schema by `python scripts/dev.py openapi`. Interactive docs
are at `/docs` on a running server. All values are simulated and illustrative.

| Method | Path | Request | Response |
|---|---|---|---|
| GET | `/api/experiments` | - | list of `ExperimentSummary` |
| POST | `/api/experiments` | `ExperimentRequest` | `JobCreated` |
| GET | `/api/experiments/{experiment_id}/download` | - | - |
| GET | `/api/experiments/{experiment_id}/figures/{name}` | - | - |
| GET | `/api/experiments/{experiment_id}/results` | - | `ExperimentResults` |
| POST | `/api/feasibility/check` | `FeasibilityRequest` | `FeasibilityResult` |
| GET | `/api/health` | - | `Health` |
| DELETE | `/api/jobs/{job_id}` | - | `JobInfo` |
| GET | `/api/jobs/{job_id}` | - | `JobInfo` |
| GET | `/api/meta/algorithms` | - | list of `AlgorithmMeta` |
| GET | `/api/meta/presets` | - | list of `PresetMeta` |
| POST | `/api/paths/compare` | `PathCompareRequest` | `PathCompareResult` |
| POST | `/api/plans` | `PlanRequest` | `PlanResult` |
| POST | `/api/plans/compare` | `CompareRequest` | `CompareResult` |
| GET | `/api/runs` | - | list of `RunSummary` |
| GET | `/api/scenarios` | - | list of `ScenarioSummary` |
| POST | `/api/scenarios` | `Scenario` | `Scenario` |
| POST | `/api/scenarios/generate` | `GenerateRequest` | `Scenario` |
| POST | `/api/scenarios/import` | `Scenario` | `Scenario` |
| DELETE | `/api/scenarios/{scenario_id}` | - | - |
| GET | `/api/scenarios/{scenario_id}` | - | `Scenario` |
| PUT | `/api/scenarios/{scenario_id}` | `Scenario` | `Scenario` |
| GET | `/api/scenarios/{scenario_id}/export` | - | `Scenario` |
| POST | `/api/simulations` | `SimulationRequest` | `SimulationCreated` |
| GET | `/api/simulations/{sim_id}` | - | `SimulationState` |
| GET | `/api/simulations/{sim_id}/timeline` | - | `Timeline` |

## Examples

Generate the earthquake demo and save it:

```bash
curl -X POST localhost:8000/api/scenarios/generate \
  -H 'content-type: application/json' -d '{"preset": "DEMO", "save": true}'
```

Plan a saved scenario with the proposed method and a 15% reserve:

```bash
curl -X POST localhost:8000/api/plans -H 'content-type: application/json' \
  -d '{"scenario_id": "<id>", "algorithm": "integrated", "reserve_ratio": 0.15}'
```

Check a hand-edited plan:

```bash
curl -X POST localhost:8000/api/feasibility/check -H 'content-type: application/json' \
  -d '{"scenario_id": "<id>", "assignment": {"Scout-1": ["T1", "T6"]}}'
```

## WebSocket: `/ws/simulations/{id}`

Server to client frames:

```json
{"type": "frame", "t": 12.5, "end_time": 410.2, "playing": true,
 "uavs": [{"id": "Scout-1", "x": 7.2, "y": 40.1, "battery": 91.3, "status": "en_route"}],
 "events": [{"time": 12.0, "kind": "TASK_COMPLETED", "uav_id": "Scout-1", "task_id": "T1", "detail": ""}],
 "replan": {"diff": {"scope": "local", "moved": []}, "plan": {}, "time_ms": 18.4}}
```

`replan` appears only on a frame that crosses a replan. Client to server commands:

```json
{"cmd": "play"}
{"cmd": "pause"}
{"cmd": "speed", "payload": {"value": 20}}
{"cmd": "seek", "payload": {"t": 120}}
{"cmd": "inject_event", "payload": {"type": "UAV_UNAVAILABLE", "uav_id": "Scout-1"}}
```

An injected event without a `time` takes effect at the current playback time.

## Errors

Every error has the same shape:

```json
{"error": {"code": "invalid_scenario", "message": "The scenario failed validation.", "details": []}}
```
