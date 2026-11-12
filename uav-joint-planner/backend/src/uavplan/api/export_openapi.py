"""Writes the OpenAPI schema and the generated ``docs/API.md``.

Usage: ``python -m uavplan.api.export_openapi <openapi.json> <API.md>``
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Any

from uavplan.api.main import create_app

EXAMPLES = """## Examples

Generate the earthquake demo and save it:

```bash
curl -X POST localhost:8000/api/scenarios/generate \\
  -H 'content-type: application/json' -d '{"preset": "DEMO", "save": true}'
```

Plan a saved scenario with the proposed method and a 15% reserve:

```bash
curl -X POST localhost:8000/api/plans -H 'content-type: application/json' \\
  -d '{"scenario_id": "<id>", "algorithm": "integrated", "reserve_ratio": 0.15}'
```

Check a hand-edited plan:

```bash
curl -X POST localhost:8000/api/feasibility/check -H 'content-type: application/json' \\
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
"""


def _reference(schema: dict[str, Any] | None) -> str:
    if not schema:
        return "-"
    if "$ref" in schema:
        return f"`{schema['$ref'].rsplit('/', 1)[-1]}`"
    if schema.get("type") == "array":
        return f"list of {_reference(schema.get('items'))}"
    return f"`{schema.get('type', 'object')}`"


def to_markdown(spec: dict[str, Any]) -> str:
    lines = [
        "# Optimizing Routing, Task Allocation, and Energy Management in Collaborative UAV Systems: API reference",
        "",
        "Generated from the OpenAPI schema by `python scripts/dev.py openapi`. Interactive docs",
        "are at `/docs` on a running server. All values are simulated and illustrative.",
        "",
        "| Method | Path | Request | Response |",
        "|---|---|---|---|",
    ]
    for path, operations in sorted(spec["paths"].items()):
        for method, operation in sorted(operations.items()):
            body = operation.get("requestBody", {}).get("content", {}).get("application/json", {})
            success = next(
                (
                    operation["responses"][c]
                    for c in ("200", "201", "202")
                    if c in operation["responses"]
                ),
                {},
            )
            response = success.get("content", {}).get("application/json", {})
            lines.append(
                f"| {method.upper()} | `{path}` | {_reference(body.get('schema'))} "
                f"| {_reference(response.get('schema'))} |"
            )
    return "\n".join(lines) + "\n\n" + EXAMPLES


def main() -> None:
    target_json, target_md = Path(sys.argv[1]), Path(sys.argv[2])
    with tempfile.TemporaryDirectory() as folder:
        app = create_app(f"sqlite:///{folder}/schema.db", folder)
        spec = app.openapi()
        app.state.engine.dispose()
    target_json.write_text(json.dumps(spec, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    target_md.write_text(to_markdown(spec), encoding="utf-8", newline="\n")
    print(f"wrote {target_json} and {target_md}")


if __name__ == "__main__":
    main()
