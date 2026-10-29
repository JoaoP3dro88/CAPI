# Add A Metric End-To-End

Last updated: 2026-05-04

This document is the developer guide for adding a new metric end-to-end.
It defines the full sequence of required steps from metric definition to backend export, JSON validation, frontend typing/rendering, and documentation updates.
Use this workflow as the mandatory implementation checklist for any new metric.

## 1. Define The Metric Contract

Before coding, define the metric precisely.

Required definition:
- metric key name in JSON (snake_case)
- formula
- unit
- aggregation scope (global, per-line, per-machine, per-frame)
- destination payload (`dashboard.json`, `realtime.json`, or both)

Example:
- Key: `edm_waiting_ratio`
- Formula: `total_waiting_sec / total_time_sec`
- Unit: ratio `0..1`
- Scope: global
- Destination: `dashboard.json.kpis`

## 2. Confirm Source Data In Backend

Locate source fields in `SimulationResult`:
- `event_log`
- `stock_events`
- `step_summaries`
- `order_summary`

Relevant files:
- `src/simulation.py`
- `src/json_export.py`

If source data does not exist, add simulation-level accumulation first, then exporter logic.

## 3. Implement Exporter Changes

File:
- `src/json_export.py`

Implementation rules:
- Compute metric close to payload construction.
- Keep numeric fields as numbers.
- Apply deterministic rounding only where needed.
- Keep existing keys unchanged unless explicitly evolving contract.

## 4. Generate Fresh Payloads

```powershell
python launch.py --no-frontend
```

Inspect:
- `outputs/dashboard.json`
- `outputs/realtime.json`

PowerShell quick inspection:

```powershell
$d = Get-Content outputs\dashboard.json -Raw | ConvertFrom-Json
$d.kpis
```

Validate the generated files against the JSON contract:

```powershell
python src/validate_schema.py --all
```

## 5. Update TypeScript Contract

File:
- `Dashboard and Real-Time Playback/src/app/data.ts`

Update the relevant interface:
- `KPI`
- `RealtimeData`
- `RealtimeFrame`
- `MachineState`
- other related types

Field optionality rule:
- required if backend always emits it
- optional only if backend intentionally emits conditionally

## 6. Render Metric In UI

Files:
- `Dashboard and Real-Time Playback/src/app/components/Dashboard.tsx`
- `Dashboard and Real-Time Playback/src/app/components/RealtimePlayback.tsx`

Typical render targets:
- KPI card
- table column
- modal details
- chart tooltip
- realtime zone panel

Formatting rules:
- convert ratios to percent only in UI layer when needed
- keep label + unit consistent with documentation

## 7. Keep Visual Semantics Consistent

If metric drives color/status behavior:
- update thresholds in `data.ts`
- use `getTrafficColor(...)` when threshold semantics match existing pattern
- document metric meaning next to visualization when required

## 8. Validate End-To-End

Validation path:
1. Generate JSON from backend.
2. Load dashboard view and confirm metric values.
3. Load realtime view if impacted.
4. Verify values match formula and expected unit.

## 9. Update Documentation

Update the following as needed:
- `documentation/json-contract.md` (field definition and semantics)
- `documentation/frontend-guide.md` (where metric is rendered)
- `documentation/backend-architecture.md` (if simulation logic changed)

## 10. Worked Example

Metric: `edm_waiting_ratio`

1. Compute in exporter:
- `edm_waiting_ratio = total_waiting_sec / total_time_sec` (guard zero denominator)

2. Add to payload:
- `payload["kpis"]["edm_waiting_ratio"] = round(edm_waiting_ratio, 4)`

3. Update TS:
- Add `edm_waiting_ratio: number` to `KPI`

4. Render:
- Add dashboard KPI card and format as percent

5. Validate:
- Regenerate payloads and verify the field in JSON and UI
