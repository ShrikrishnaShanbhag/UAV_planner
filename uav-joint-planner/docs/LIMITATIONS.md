# Optimizing Routing, Task Allocation, and Energy Management in Collaborative UAV Systems: Limitations

This is a simulation study. All values are simulated and illustrative, and none of the
energy or timing figures are measured drone performance.

## Modelling

- **2D flight on a grid.** No altitude, climb energy or terrain following. Paths follow
  8-connected cells, so they are not smooth trajectories.
- **Constant speed and simple energy.** Travel energy is proportional to weighted distance.
  Acceleration, payload mass, battery ageing and temperature are not modelled.
- **Energy zones are not wind.** They are static multipliers used to show that the shortest
  path is not always the lowest-energy path. Direction and time variation are not modelled.
- **Recharging is simple and optional.** Off by default, a UAV flies one sortie. With
  `allow_recharge` on, charging is linear, one stop per route by default, and stations have no
  queues or capacity limits. Only the proposed planner uses it. Batteries do not recharge on landing.
- **No UAV-to-UAV interaction.** There is no collision avoidance, airspace deconfliction or
  communication model. Coordination is centralized and instantaneous.
- **Replans take effect at commit points.** A UAV finishes its current grid edge, and by
  default its current task, before a new plan applies. Compute time does not delay the mission.

- **Energy extras are not part of the published comparison.** Charging stops, the adaptive reserve
  and early return-to-home are tested but off in the experiment protocol, so their value has not
  been measured across scenarios.

## Algorithms

- **The proposed method is a heuristic.** It has no optimality guarantee. In the experiments
  the OR-Tools baseline often assigns more tasks when given far more compute time.
- **The mandatory-task guarantee is conditional.** The backtracking rescue is complete only
  if it finishes within its evaluation budget.
- **The strict prefix-safety check rarely binds.** With minimum-energy paths it is implied by
  the total-energy constraint (see `MODEL.md`), so that ablation has no effect in the presets.
- **The exact solver is small-scale.** It handles static instances up to 4 UAVs and 12 tasks,
  rounds energy and time up to integers, and may stop without proving optimality. Gaps are
  reported only for instances it proved.
- **The OR-Tools baseline has simplified objectives.** Its arc costs approximate `Z` and do
  not include the workload-variance term, and hover waiting energy is outside its model.

## Experiments

- Scenarios are synthetic. Results describe these generators and parameter ranges only.
- The energy scale was recalibrated once after seeing first results (see `DECISIONS.md`, #8).
- Baselines in dynamic scenarios replan by re-running themselves, one reasonable choice among
  several.
- Planning times were measured with runs sharing one machine, so they are rough magnitudes.
- OR-Tools stops on a deterministic solution limit, but its 20 s safety cap is reached at the
  largest Scenario F sizes, so those particular runs may differ between repeats.
- A quick 5-seed run cannot reach statistical significance. Use the 30-seed protocol.
- The novelty of the method has not been established against closely related published work.
  The proposal itself asks for a targeted literature review before any such claim.

## Software

- Simulation sessions and job progress live in memory and do not survive a server restart.
  Scenarios, run history and finished experiment results are persisted.
- The API has no authentication and allows all CORS origins. It is meant for local use.

## Future work

3D flight with climb energy; wind; real drone hardware, ROS and PX4 integration;
reinforcement-learning planners; learned energy prediction; and distributed onboard execution
with event-triggered communication. The `Planner` protocol and `EnergyEstimator` are the
extension points for these.
