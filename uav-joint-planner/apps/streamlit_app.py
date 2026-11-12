"""Minimal Streamlit dashboard that imports the core package directly.

Run from the backend folder, which has the dependencies:
    uv run --extra streamlit streamlit run ../apps/streamlit_app.py
"""

from __future__ import annotations

import matplotlib.pyplot as plt
import streamlit as st

from uavplan.core.generator import PRESET_IDS, preset
from uavplan.core.metrics import plan_metrics, simulation_metrics
from uavplan.core.registry import ALGORITHMS, get_planner
from uavplan.core.simulator import Simulator
from uavplan.experiments.plots import _draw_map, SERIES, apply_style

st.set_page_config(page_title="Optimizing Routing, Task Allocation, and Energy Management in Collaborative UAV Systems", layout="wide")
st.title("Optimizing Routing, Task Allocation, and Energy Management in Collaborative UAV Systems")
st.caption("All values are simulated and illustrative, in normalized energy units (EU).")

with st.sidebar:
    preset_id = st.selectbox("Scenario", PRESET_IDS, index=len(PRESET_IDS) - 1)
    seed = st.number_input("Seed", value=42, step=1)
    names = [n for n in ALGORITHMS if n != "exact_cpsat"]
    algorithm = st.selectbox("Algorithm", names, index=names.index("integrated"), format_func=lambda n: ALGORITHMS[n].label)

scenario = preset(preset_id, int(seed))
plan = get_planner(algorithm).plan(scenario)
result = Simulator(scenario, plan).run()

apply_style()
left, right = st.columns([3, 2])
with left:
    figure, axis = plt.subplots(figsize=(7, 7))
    _draw_map(axis, scenario)
    colors = {u.id: SERIES[i % len(SERIES)] for i, u in enumerate(scenario.uavs)}
    for route in plan.routes:
        for leg in route.legs:
            xs, ys = zip(*leg.path, strict=True)
            axis.plot(xs, ys, color=colors[route.uav_id], alpha=0.5 if leg.kind == "return" else 1.0)
    for task in scenario.tasks:
        axis.scatter(*task.cell, s=18, color="#0b0b0b", zorder=3)
    for station in scenario.stations:
        axis.scatter(*station.cell, s=50, marker="s", color="#0b0b0b", zorder=4)
    st.pyplot(figure)

with right:
    st.subheader("Battery")
    for route in plan.routes:
        used = route.total_energy / max(route.battery_start, 1e-9)
        st.progress(min(used, 1.0), text=f"{route.uav_id}: {route.total_energy:.1f} of {route.battery_start:.1f} EU")
    st.subheader("Feasibility")
    if plan.report.feasible:
        st.success("Every route satisfies all hard constraints.")
    else:
        for violation in plan.report.violations:
            st.error(f"{violation.type.value}: {violation.detail}")
    for item in plan.unassigned:
        st.warning(f"{item.task_id} unassigned: {item.reason}")

planned, simulated = plan_metrics(plan, scenario), simulation_metrics(result, scenario)
st.subheader("Metrics")
st.table(
    {
        "Metric": sorted(set(planned) & set(simulated)),
        "Planned": [round(planned[k], 3) for k in sorted(set(planned) & set(simulated))],
        "Simulated": [round(simulated[k], 3) for k in sorted(set(planned) & set(simulated))],
    }
)
