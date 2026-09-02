# Model Routing

`config/model-routing.json` uses logical stage names rather than unverified
runtime model IDs.

- `LUNA_MAX`: screenshot extraction, metric collection, and normal public-source retrieval.
- `TERRA`: bounded semantic factor interpretation, conflict context, regime explanation, and final Chinese prose.
- `SOL`: conditional major-event/thesis-risk analysis and high-impact final critique.
- `PYTHON`: validation, history, metrics, Facts, scoring, regime, allocation, risk, rebalance, and execution.

Any Luna-family stage must target exactly `LUNA_MAX`. Sol is skipped for an
ordinary `SNAPSHOT_REVIEW` containing only HOLD/NO_TRADE outcomes with no
material event, conflict, critical failure, thesis change, target change, or
risk escalation. Python decides whether Sol is required before dispatch.
The configured `sol_thresholds` control what counts as a material core
reduction or target-weight change.
