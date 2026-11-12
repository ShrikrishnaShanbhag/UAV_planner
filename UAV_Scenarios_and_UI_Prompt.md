# UPDATE PROMPT — Add real-world mission scenarios + redesign the frontend look

> Paste this into Claude Code / Antigravity at the root of the **existing** project.
> This is an **incremental update**: keep the architecture, APIs and working features. Don't rebuild.

---

## Ground rules
1. Read the codebase first: the scenario model, `generator.py`, the `scenarios/*.json` presets, the existing earthquake demo, and the frontend pages and styles. Reuse what exists and extend the existing scenario schema; don't create a second one.
2. Commit after each part (`feat(scenarios): …`, `style(ui): …`). Keep all existing tests passing and add tests for new code.
3. All scenario values are **illustrative simulation settings**, not real measurements, and the UI must say so. Use real-world place types (river, hospital, power line) but **no real place names, organisation names or logos**.
4. Every scenario is seeded and deterministic, and passes validation: no task or station inside an obstacle, every task reachable, every capability available on at least one UAV.

---

## Part A — Scenarios

### A1. Extend the scenario schema (backwards compatible; all new fields optional)
- `meta`: `id, name, category, description, story` (2–3 sentences of mission context), `icon` (lucide icon name), `theme` (map theme key, see B4), `difficulty` (easy/medium/hard), `tags[]`, `energy_focus` (one line on what this scenario tests about energy).
- `terrain_layers`: typed regions drawn on the map — `water, forest, urban, field, road, rubble, fire, smoke, hill, coast` — as polygons or cell masks. Each layer has `blocked: bool` and an optional `energy_multiplier` (e.g. smoke 1.2, hill 1.15), applied through the existing `EnergyEstimator`. Don't add a second energy path.
- `wind`: optional uniform vector `{speed, direction_deg}`. Energy per leg is scaled by `1 + k_w · (headwind component / v_uav)`, clamped to ≥ 0.6. Default off; add a unit test that a headwind leg costs more than the same leg with a tailwind.
- `events[]`: the existing event types, scripted with times.
- `station_outage` event (new, small): a charging station becomes unavailable at time t.
- Unknown or new fields must not break old scenarios. Add a test that every existing preset still loads and plans the same as before.

### A2. Add these scenario presets (JSON in `scenarios/`, plus generator functions with seed and size)

| # | Scenario | Story | Map & terrain | Fleet | Tasks | Scripted events | Energy focus |
|---|---|---|---|---|---|---|---|
| 1 | **Earthquake response** (keep the existing one, migrate it to the new schema) | City district after a quake; find survivors and assess buildings | Urban blocks, rubble (blocked), collapsed bridge, no-fly zone over a damaged tower | 4 UAVs (camera, thermal) | Building checks, thermal survivor scans (critical) | Aftershock blocks a road at t=300 s; new survivor-signal task | Prefix safety in dense obstacles |
| 2 | **Flood relief** | River has overflowed; locate stranded people and drop supplies | Wide water body, partly flooded urban area, raised road, relief camp station | 5 UAVs (camera, payload) | Rooftop scans, medical-kit drops (payload, extra task energy) | River rises: flooded region expands at t=400 s; rescue boat request | Long legs over water, payload energy |
| 3 | **Wildfire monitoring** | Forest fire spreading; map the fire front and check villages | Forest, fire (blocked) and smoke (energy ×1.2) regions, ridge/hills | 4 UAVs (thermal, camera) | Fire-front perimeter points, village checks | Fire zone grows every 200 s; wind shifts direction | Wind + smoke energy, a growing no-fly area |
| 4 | **Coastal cyclone aftermath** | Cyclone has passed; assess coastline, shelters and the power grid | Coastline, sea, damaged villages, strong onshore wind | 5 UAVs | Shelter checks, embankment inspections, power-pole checks | One station goes out (`station_outage`); one battery drop | Strong headwind/tailwind asymmetry, losing a station |
| 5 | **Power-line inspection after a storm** | Inspect a long transmission corridor for faults | Long, narrow corridor across fields and hills; few stations | 3 UAVs (camera, lidar) | 20–40 tower inspections spread along the line | Fault report adds 3 high-priority towers | Long distances; recharge stops needed (uses `allow_recharge` if it exists) |
| 6 | **Mountain search & rescue** | Missing trekkers in hilly terrain | Hills (energy ×1.15), forest, river valley, a base camp station | 4 UAVs (thermal, camera) | Grid-sector sweeps, last-known-location checks (critical) | Phone signal ping adds a critical task; one UAV fails | Terrain energy, reserve under uncertainty |
| 7 | **Medical supply delivery** | Hospital hub sends urgent supplies to rural clinics | Town, roads, fields, hospital-hub station, two clinic stations | 4 UAVs (payload) | Deliveries with deadlines (blood, vaccines) | New emergency delivery; a deadline gets tighter | Deadlines vs battery, multi-station returns |
| 8 | **Precision agriculture survey** | Survey crop health and spray patches across farmland | Large fields, irrigation canals, farmhouse station | 3 UAVs (camera, payload) | Field survey cells (many, low priority), spray tasks (high energy) | Rain front: some tasks cancelled | Many small tasks; energy balance across the fleet |
| 9 | **Industrial gas-leak inspection** | Gas leak alarm at an industrial plant | Plant buildings, chimneys (blocked), hazard zone (no-fly), perimeter | 3 UAVs (camera, gas-sensor) | Sensor sweeps around the hazard, stack checks | Hazard zone expands; a new sensor alarm | Tight no-fly geometry, return paths around a growing hazard |
| 10 | **Oil spill / maritime monitoring** | Track an oil slick spreading at sea | Mostly sea, a coastline, one shore station, an offshore platform station | 3 UAVs (camera, thermal) | Slick boundary points that drift over time | Slick drifts (tasks move); wind change | Very long legs, few stations: return energy dominates |
| 11 | **Large-event crowd safety** | Monitor a stadium and its exits during an event | Stadium, roads, parking, a no-fly zone over the stage | 4 UAVs (camera) | Repeated checks of gates and exits, incident checks | Incident reports added during the mission | Repeated tasks; short legs with lots of hovering |
| 12 | **Landslide on a hill road** | Landslide cuts a highway; assess the damage and find trapped vehicles | Hill road, landslide debris (blocked), river below | 3 UAVs | Road-segment checks, vehicle scans | A second slide blocks another segment | Re-routing when the only road path is blocked |

Each preset gets a **small** (fast demo) and a **standard** size. The generator can vary the seed and task count while keeping the scenario's look and story.

### A3. Backend and experiments
- `GET /api/meta/presets` returns the meta for every scenario (name, category, description, story, icon, theme, difficulty, tags, energy_focus, sizes).
- `POST /api/scenarios/generate` accepts `{preset_id, size, seed}`.
- Experiments can take any preset as a scenario. Add an `experiments-scenarios` Make target that runs the current default planner and the existing energy baselines on all 12 presets (5 seeds) and adds a "Results by mission type" table and chart to REPORT.md.
- Tests: every preset loads, validates and plans with the default planner; the proposed planner's plans stay energy-feasible on all of them; scripted events fire and trigger replanning; the wind test; the station-outage test.

### A4. Frontend: scenario gallery
- New **Missions** page (and a section on the homepage): a grid of cards, one per scenario, showing an icon, name, category chip, difficulty, a 2-line story, the energy-focus line, and a small **auto-generated SVG thumbnail** of the map, drawn from the scenario's terrain layers in its theme colours.
- Filters by category (Disaster, Infrastructure, Delivery, Agriculture, Safety, Maritime) and difficulty, plus search.
- Clicking a card opens a detail drawer (story, fleet, tasks, scripted events, what it tests) with buttons: **Plan it**, **Simulate it**, **Open in builder**.
- The Scenario Builder gets terrain-layer brushes (water, forest, urban, rubble, fire, smoke, hill…) and a wind control (direction dial + speed).
- The simulation shows growing or moving regions (fire, flood, slick) animating at their event times, and a wind arrow overlay.

---

## Part B — Visual redesign (focus: background and overall polish)

The current styling looks average. Give the app a polished, distinctive **"mission control"** look that works in both dark (the default) and light themes. Don't change the layout logic or component behaviour, only the visuals.

### B1. Design tokens
- Define every colour as CSS variables in `:root` (light) and `[data-theme="dark"]`, and connect them to Tailwind (`theme.extend.colors` uses the variables). No hard-coded hex values in components.
- Dark palette: deep navy/ink base (`#070B14` → `#0B1220`), surfaces `#0F172A` with transparency, accent **cyan/teal** (`#22D3EE` / `#2DD4BF`) as primary, **amber** for energy and warnings, **rose** for critical/infeasible, **emerald** for feasible. Light palette: soft slate-white base with the same accent family, darkened enough to meet contrast.
- Typography: Google Fonts — **Space Grotesk** (headings), **Inter** (body), **JetBrains Mono** (numbers, telemetry, battery %). Use tabular numbers for all metrics.

### B2. The background (main ask)
Build an `<AppBackground />` component, fixed behind everything (`z-index: -1`, `pointer-events: none`), made of layers:
1. **Base gradient:** a deep radial/linear gradient (ink → navy, with a faint teal glow at the top-left and a violet glow at the bottom-right).
2. **Aurora / mesh glow:** 2–3 large blurred colour blobs (cyan, teal, indigo, at 10–20% opacity) slowly drifting with CSS keyframes (40–60 s loops).
3. **Topographic contour lines:** a tiled inline SVG pattern of contour lines at ~4–6% opacity. It gives a terrain-map feel that fits UAVs.
4. **Fine tactical grid:** 1 px grid lines every 40 px at ~3% opacity, fading out toward the edges with a radial mask.
5. **Radar sweep (hero only):** a subtle rotating conic-gradient sweep behind the homepage title, with a few faint pulsing "UAV" dots.
6. **Noise/grain:** a tiny SVG feTurbulence grain overlay at ~3% to avoid gradient banding.
- The light theme uses the same layers with inverted, softer colours (pale sky → white, light contour lines).
- Respect `prefers-reduced-motion`: all animation stops and the layers stay static.
- Performance: CSS and inline SVG only, no canvas loop and no large images; it must stay smooth (no layout shift; GPU-friendly `transform`/`opacity` animations only).
- Pages can pass a variant (`hero | default | minimal`). The simulation and map pages use `minimal`, so the map stays readable.

### B3. Surfaces and components
- **Glass cards:** translucent surface (`bg-surface/60`), `backdrop-blur-md`, a 1 px border with a subtle gradient (white 8% → transparent), a soft inner highlight at the top, large radius (16 px), and a hover lift with an accent glow on interactive cards.
- **Navbar:** a floating glass bar with the new title/logo mark (a simple original drone-and-orbit SVG mark; no third-party logos), the active page as an accent pill, and a theme toggle.
- **Homepage hero:** the title "Optimizing Routing, Task Allocation, and Energy Management in Collaborative UAV Systems" in large Space Grotesk, with a gradient-text accent on "Energy Management"; the energy subtitle; two CTAs (Explore missions / Open planner); 3–4 stat tiles (scenarios, algorithms, metrics, tests) in mono numbers; the radar background.
- **Buttons:** primary = solid cyan with a dark label and a glow on hover; secondary = glass; consistent focus rings for accessibility.
- **Battery visuals:** segmented battery bars with a green → amber → rose gradient by state of charge, the reserve zone hatched, and a pulse when below the reserve.
- **Charts (Recharts):** themed axes and grid from the tokens, a colour-blind-safe UAV palette (8 colours), glass tooltips, mono numbers.
- **Micro-interactions:** 150–250 ms transitions, page fade/slide-in, skeleton loaders with a shimmer, toasts styled to match.

### B4. Map themes (per scenario)
Draw the canvas map with theme colours per terrain layer — water (deep blue with subtle animated ripples), forest (deep green with a texture), urban (slate blocks), rubble (brown hatch), fire (orange-red glow with flicker), smoke (grey translucent), field (olive), hill (contour shading), coast (sand). Obstacles and no-fly zones get red hatching with a dashed border. UAV routes are glowing polylines in the UAV's colour, with direction arrows; UAVs are small original drone icons with a battery ring. There are theme sets for dark and light.

### B5. Quality bar
- Check every page at 375 px, 768 px and 1440 px: no horizontal scroll, readable text over the background (WCAG AA contrast on all text).
- Take Playwright screenshots of the Home, Missions, Planner and Simulation pages in both themes, save them to `docs/screenshots/`, and use them in the README.
- Update the existing Playwright smoke tests if any selectors changed; add a test that opens a mission card and plans it.

---

## Wrap-up
Update `README.md` (missions list, screenshots), `docs/USER_GUIDE.md` (the Missions page, terrain brushes, wind) and `docs/DECISIONS.md`. Run all tests, lint and type checks. Finish with a short summary of the scenarios added, the schema fields added, and the visual changes, with screenshot paths.
