# Production Simulation (CtP Hackathon)

This repository contains a discrete‑event production simulation (SimPy), a Live View of the current production floor, and a dashboard UI for playback and KPIs. The simulation models a two‑stage flow (LASER → stock → EDM), changeovers, tray batching, and capacity limits, and exports JSON for visualization.

## 1) Project overview

**Goal:** emulate daily production sequencing and compute KPIs (total time, utilization/OEE proxy, changeovers, throughput). The model is **not** an optimizer; it is a digital‑twin‑style simulator.

**Process flow:** Hard Stage Supermarket → Laser Marking → Stock LM/EDM → EDM → Finish.

**Key assumptions:**
- Trays are batched with a fixed size (default 150 pieces). Quantities are rounded up to full trays.
- Changeovers are symmetric between product types and zero when the product type is unchanged.
- EDM has a test (release) time after changeover.
- Stocks are per EDM line, with capacity limits in trays and parts.
- No scrap.

## 2) Repository structure

- [launch.py](launch.py): top-level orchestrator — runs simulation, refreshes live data, starts the frontend.
- [src/main.py](src/main.py): simulation CLI; loads inputs, runs the simulation, exports JSON.
- [src/simulation.py](src/simulation.py): core simulation engine (SimPy).
- [src/data_gathering.py](src/data_gathering.py): DB input pipeline (orders, restrictions, machines, Hard Stage Market, real-time machine state, final markets).
- [src/api.py](src/api.py): FastAPI server — real-time live data API (`/api/livedata` and related endpoints).
- [src/json_export.py](src/json_export.py): JSON export for dashboard and realtime playback.
- [src/livedata_export.py](src/livedata_export.py): generates `livedata.json` from live DB data (file-based mode, alternative to API).
- [src/scheduler.py](src/scheduler.py): hourly scheduler that refreshes `livedata.json` during the shift (file-based mode only).
- [data/](data/): CSV inputs for local runs.
- [outputs/](outputs/): JSON outputs and reports.
- [Dashboard and Real-Time Playback/](Dashboard%20and%20Real-Time%20Playback/): Vite + React UI.

Related background documents:
- [README_simulation_description.md](README_simulation_description.md)
- [README_concept_updated.md](README_concept_updated.md)
- [README_visualization_description.md](README_visualization_description.md)
- [documentation/backend-architecture.md](documentation/backend-architecture.md)
- [documentation/frontend-guide.md](documentation/frontend-guide.md)
- [documentation/json-contract.md](documentation/json-contract.md)
- [documentation/live-visualization.md](documentation/live-visualization.md) ← **Live View technical reference**

## 3) How to run

### Quick start (interactive menu)

Simply run `launch.py` with no arguments. An interactive menu lets you choose the mode:

```powershell
python launch.py
```

```
╔══════════════════════════════════════════════════════════╗
║              Production Simulation Launcher             ║
╠══════════════════════════════════════════════════════════╣
║  [1] Production (DB + API + Frontend)  [recommended]    ║
║  [2] Production (DB + file-based Live View + Frontend)  ║
║  [3] Simulation only — DB (no frontend)                 ║
║  [4] Test data — CSV (no DB required)                   ║
║  [5] Test data — CSV, no frontend                       ║
╚══════════════════════════════════════════════════════════╝

Select mode [1-5] (default 1):
```

Press **Enter** for the recommended production mode (DB + API on `0.0.0.0:8001` + Frontend).

> **Note:** The interactive menu is skipped when you pass explicit flags like `--api`, `--livedata`, `--data-source`, or `--no-interactive`.

### 3.1-A Recommended — full stack from DB (file-based Live View)

Runs the simulation, refreshes `livedata.json` from DB, then opens the frontend:

```powershell
python launch.py --data-source db --export-figma-json --livedata
```

In a **second terminal**, start the hourly Live View refresh for the duration of the shift:

```powershell
python src/scheduler.py
```

The browser opens automatically at `http://localhost:5173`.

### 3.1-B Recommended — full stack with API Live View (server deployment)

Runs the simulation and starts the FastAPI server for the Live View:

```powershell
python launch.py --data-source db --export-figma-json --api --api-host 0.0.0.0 --api-port 8001
```

The API is available at `http://localhost:8001`. Interactive docs at `http://localhost:8001/docs`.

> **Note:** `launch.py` automatically creates `Dashboard and Real-Time Playback/.env.local` to point the frontend at the API (with a 30s poll interval). No manual `.env.local` setup is needed.

### 3.1-C API server standalone (no simulation)

```powershell
cd "c:\Users\klj1ct\Desktop\CAPI\CtP-Hackathon"
C:/Users/klj1ct/AppData/Local/Programs/Python/Python312/python.exe `
  -m uvicorn src.api:app --host 127.0.0.1 --port 8001
```

### 3.2 Simulation only (no frontend)

```powershell
# From DB:
python launch.py --data-source db --no-frontend

# From CSV (local, no DB required):
python launch.py --data-source csv --no-frontend
```

### 3.3 Refresh live data manually (single update, file-based mode)

```powershell
python src/livedata_export.py
```

### 3.4 Start the API server standalone

```powershell
uvicorn src.api:app --reload --port 8000
```

### 3.5 Simulation only, direct CLI

```powershell
python src/main.py --data-source db --export-figma-json
```

## 4) Environment variables (DB connection)

Required when using `--data-source db`, `livedata_export.py`, or `scheduler.py`:

| Variable | Description |
|---|---|
| `SIM_DB_SERVER` | SQL Server hostname or IP |
| `SIM_DB_NAME` | Database name |
| `SIM_DB_USER` | Username |
| `SIM_DB_PASSWORD` | Password |
| `SIM_DB_DRIVER` | ODBC driver (default: `ODBC Driver 17 for SQL Server`) |

Store these in a `.env` file at the repo root (loaded automatically by `data_gathering.py`).

## 5) Inputs

The simulation can read input from **CSV** or **SQL Server**.

### 5.1 CSV input (local)
Located in [data/](data/). Used when `--data-source csv`.

**orders.csv** — `order_id`, `part_number_id`, `product_type`, `quantity`

**machines.csv** — `machine_id`, `process_type`, `machine_type`, `line_id`

**restrictions_filtered.csv** — `part_number_id`, `process_type`, `machine_type`, `machine_id`, `cycle_time`

**changeover_data.csv** — `type_a`, `type_b`, `changeover_laser_duration_min`, `changeover_edm_duration_min`, `test_edm_duration_min`

### 5.2 DB input
When `--data-source db`, [src/data_gathering.py](src/data_gathering.py) fetches:
- `silver_orders.orders_10digit` — orders
- `silver_type_number.nozzle_restrictions` + `bronze_Excel.restriction_laser_marking_sheet1` + `gold.simulation_agie_process_time` — restrictions and machines
- `silver_production.inventory` — Hard Stage Market (body + nozzle stock)

Optional input filter (`data/input_data.csv`) narrows orders and restrictions by `order_id` / `part_number_id` without editing DB queries.

## 6) CLI reference

### `launch.py` (recommended entry point)

| Flag | Default | Description |
|---|---|---|
| *(no flags)* | — | Shows interactive mode selector menu |
| `--no-interactive` | off | Skip the interactive menu, use CLI flags only |
| `--data-source` | `db` | `csv` or `db` |
| `--export-figma-json` | off | Copy JSONs to `public/` for the frontend |
| `--livedata` | off | Run `livedata_export.py` before starting the frontend |
| `--livedata-input-filter` | — | CSV filter forwarded to `livedata_export.py` |
| `--no-frontend` | off | Skip frontend, only run simulation |
| `--no-open-browser` | off | Don't open browser automatically |
| `--realtime-step-sec` | `60.0` | Playback frame interval |
| `--python` | current | Python executable |
| `--npm` | `npm` | npm command |
| `--api` | off | Start FastAPI server (`src/api.py`) instead of file-based livedata pipeline |
| `--api-host` | `127.0.0.1` | Host for the API server |
| `--api-port` | `8000` | Port for the API server |

### `src/api.py` (FastAPI Live View server)

Start standalone:

```powershell
uvicorn src.api:app --host 0.0.0.0 --port 8000
```

| Endpoint | Description |
|---|---|
| `GET /api/health` | Health check — DB connectivity status |
| `GET /api/orders` | `orders_by_id` payload from DB |
| `GET /api/hard-stage` | `hard_stage_market` payload from DB |
| `GET /api/snapshot` | Current machine/stock frame (from `realtime.json` fallback) |
| `GET /api/livedata` | Full payload for `LiveVisualization.tsx` |

Interactive docs: `http://server:8000/docs`

### `src/scheduler.py` (file-based mode only)

| Flag | Default | Description |
|---|---|---|
| `--interval-sec` | `3600` | Seconds between `livedata_export` runs (1 hour) |
| `--run-once` | off | Single export then exit (for cron / Task Scheduler) |
| `--input-filter` | — | Forwarded: CSV to filter orders |
| `--python` | current | Python executable |

### `src/livedata_export.py` (file-based mode only)

| Flag | Default | Description |
|---|---|---|
| `--output` | both `outputs/` and `public/` | Custom destination path |
| `--input-filter` | — | CSV to filter order IDs |
| `--fallback-realtime` | `outputs/realtime.json` | Snapshot source (until `fetch_current_state()` is implemented) |
| `--dry-run` | off | Print JSON to stdout without writing files |

## 7) Simulation engine (core behavior)

The core logic lives in [src/simulation.py](src/simulation.py). Main objects:

- `Order`: order_id, part_number_id, product_type, quantity
- `MachineSpec`: machine_id, process_type, machine_type, line_id
- `Tray`: the batch unit (fixed size)
- `Machine`: processes trays and emits `EventLog` entries
- `Stock`: per-line buffer between LASER and EDM
- `ProductionSimulation`: orchestrator (SimPy environment)

### 7.1 Tray creation
Each order is converted into $\lceil\frac{quantity}{tray\_size}\rceil$ trays and added to the LASER input queue.

### 7.2 Machine eligibility
A tray can be processed only if its `part_number_id` is allowed for the machine (process type + machine type + machine id). Cycle time is read from the same source.

### 7.3 Changeovers and test times
For each tray, the machine checks the previous product type. If it differs, a changeover is applied. EDM additionally applies test time.

### 7.4 Stock between LASER and EDM
Each EDM line has its own stock with tray and part capacity. LASER routes completed trays to the best eligible line based on compatibility, fill ratio, and active campaign.

### 7.5 Scheduling policy
Campaign-priority dispatch with stock-aware LASER throttling and LASER full-shift OFF policy. See [documentation/backend-architecture.md](documentation/backend-architecture.md) for full details.

## 8) Outputs

| File | Producer | Description |
|---|---|---|
| `outputs/dashboard.json` | `src/json_export.py` | KPIs, timeline, stock snapshot, orders summary |
| `outputs/realtime.json` | `src/json_export.py` | Time-stepped frames for playback (+ `orders_by_id`, `hard_stage_market`) |
| `outputs/livedata.json` | `src/livedata_export.py` | Single-frame live snapshot (orders + HSM + current state) |
| `outputs/simulation_report.txt` | `src/main.py` | Text summary of simulation run |
| `Dashboard and Real-Time Playback/public/*.json` | mirrors of above | Served statically to the frontend |

## 9) Dashboard UI

The frontend lives in [Dashboard and Real-Time Playback/](Dashboard%20and%20Real-Time%20Playback/) (Vite + React). It reads the JSON outputs and renders:

- **Dashboard** — KPI cards, Gantt timeline per machine, stock summary per EDM line, orders table
- **Realtime Playback** — step-based simulation state (backlog, produced orders, stock, machine status)
- **Live View** — current production floor snapshot: active orders, machine states, EDM stock levels, Hard Stage Market with body → nozzle correlation

## 10) Known limitations

- No optimization — campaign-priority dispatch, not a scheduler optimizer.
- No scrap, no machine breakdowns/failures.
- `fetch_current_state()` not yet implemented — Live View snapshot is sourced from the last `realtime.json` until real-time DB queries are available.
- `history.json` (Plan vs Actual timeline) is currently static mock data — `history_export.py` not yet implemented.
- Tray size is fixed across all orders.

## 11) Extending the model

See [documentation/backend-architecture.md](documentation/backend-architecture.md) for the full extension workflow.

Common extensions:
- Alternative dispatching rules (priority queues, due dates)
- Additional process steps and machine breakdowns
- Variable tray sizes
- `fetch_current_state()` — real-time machine + stock queries for Live View
- `history_export.py` — Plan vs Actual history from DB production events

## 12) Deployment

The backend is **not an API** — it is a script that runs once per shift and writes static JSON files.
The frontend is a Vite + React app that reads those JSON files at runtime.

### Development (local machine)

Use `launch.py` — runs the simulation, copies JSONs to `public/`, and starts the Vite dev server:

```powershell
python launch.py --data-source db --export-figma-json --livedata
```

The browser opens automatically at `http://localhost:5173`.

### Production (static build)

Build the frontend into static assets:

```powershell
cd "Dashboard and Real-Time Playback"
npm install
npm run build
```

This generates a `dist/` folder with HTML, JS, and CSS. Copy the JSON files into `dist/` and serve the folder with any static file server:

- **IIS** on Windows — map `dist/` as a website
- **Nginx / Apache** — serve `dist/` as document root
- `python -m http.server` — minimal option for internal use

**Updating JSON files after each shift:**

1. Run the simulation: `python launch.py --data-source db --export-figma-json --no-frontend`
2. Copy `outputs/*.json` (or the `public/*.json` mirrors) into `dist/`.
3. No rebuild needed — the JS bundle is unchanged.

**Live View updates during the shift:**

Run `scheduler.py` on the same machine that has DB access. It writes `livedata.json` directly to the served folder:

```powershell
python src/scheduler.py --interval-sec 3600
```

If the served folder is not `public/`, pass the output path explicitly:

```powershell
python src/livedata_export.py --output path\to\dist\livedata.json
```

**Frontend URL overrides (optional):**

If the JSON files are hosted on a different server or path, set these variables in a `.env.local` file inside `Dashboard and Real-Time Playback/` before building:

```
VITE_DASHBOARD_JSON_URL=http://server/data/dashboard.json
VITE_REALTIME_JSON_URL=http://server/data/realtime.json
VITE_LIVEDATA_JSON_URL=http://server/data/livedata.json
VITE_HISTORY_JSON_URL=http://server/data/history.json
```

No code changes are required — `src/app/data.ts` already resolves these at build time.
