# Improvement Backlog

Last updated: 2026-05-04

This file tracks future technical improvements. Runtime behavior documentation remains in the other files.

## 0. Recently Completed

- ~~**Hard Stage Market production constraint**~~ — **Done** (2026-04-27, branch `Hardstage-priority`).
  Simulation now only produces PNs present in the Hard Stage Supermarket inventory.
  HSM stock quantity acts as a production budget consumed during enqueue.
  After SAP orders are exhausted, remaining HSM stock is produced as optimized continuation.
  Files: `src/simulation.py` (`_enqueue_orders`, `_apply_initial_state`), `src/main.py` (`hsm_stock_by_pn`).

- ~~**`fetch_current_state()`**~~ — **Done** (2026-05).
  Real-time machine status and stock queries from DB are fully implemented in `src/data_gathering.py`.
  `src/api.py` uses it directly; `src/livedata_export.py` also calls it. No more `realtime.json` fallback.

- ~~**`history_export.py`**~~ — **Done** (2026-05).
  Plan vs Actual history is generated from real DB production events (`b2b_machine_status` Entrada/Saída pairs).
  `main.py` calls `export_history_json()` at the end of every DB-mode simulation run.
  `src/api.py` rebuilds `history.json` every 30 min via `snapshot_accumulator.py` in a background thread.
  Files: `src/history_export.py`, `src/snapshot_accumulator.py`, `src/api.py`.

- ~~Add automated schema validation for generated JSON.~~ — **Done**: `src/validate_schema.py` + `npm run validate`.

## 1. Pending Features

### 1.1 Roteamento HE vs. Packaging — STUB

`fetch_destination_routing(engine)` in `src/data_gathering.py` returns an empty DataFrame.
All TypeNumbers go to **Packaging** by default.
When the routing table is available in the DB, replace the stub query.
`_build_final_markets()` is already complete and will consume the routing map automatically.

### 1.2 Search bar — HE Market and Packaging Market not filtered

`heMarket` and `packagingMarket` do not have filtered variants in `LiveVisualization.tsx`'s search `useMemo`.
Badge count also does not include them.

### 1.3 Final market timeout — no visual warning before expiry

TypeNumbers near timeout disappear without warning.
Add indicator (e.g. yellow badge "expira em Xh") when `now - oldest_entry_date > 0.75 * market_timeout_hours`.

### 1.4 Active vs. Backlog Orders — business rule validation

`Active Orders` uses `due_date >= today` as the separation criterion (client-side).
Orders without `due_date` are treated as Active. Validate with business whether this rule is correct
or if it should be based on another field (production status, priority, etc.).

## 2. Payload And Playback

- Add optional frame thinning/downsampling policy for long horizons.
- Add backend switch to export per-zone summary-only realtime payload.
- Reduce realtime payload size for long simulations and high frame density.
- Add chunked/incremental frame loading strategy to lower client memory footprint during playback.

## 3. Frontend Rendering

- Add timeline virtualization for very large machine/event sets.
- Add list virtualization for large backlog and produced-order panels.
- Add optional incremental frame loading strategy for playback.

## 4. Backend Simulation

- Add explicit metrics for policy decisions (campaign picks, LASER shift-off activations).
- Add structured trace output for dispatch scoring decisions.
- Add optional experiment mode to compare policy parameter sets in one run.

## 5. Tooling

- Add CI check that verifies exporter payload keys against TypeScript interfaces.
- Add benchmark script for simulation runtime and payload size trends.
