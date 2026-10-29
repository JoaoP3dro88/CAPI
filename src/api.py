"""
api.py — FastAPI server for the Live View pipeline.
====================================================

Exposes the live production floor data as HTTP endpoints consumed by the
React frontend (LiveVisualization.tsx). Replaces the file-based
livedata_export.py + scheduler.py pipeline for deployments where a
dedicated server is available.

Architecture
------------
  GET /api/livedata        — full livedata payload (orders + HSM + snapshot)
  GET /api/orders          — only orders_by_id
  GET /api/hard-stage      — only hard_stage_market
  GET /api/snapshot        — only the machine/stock snapshot frame
  GET /api/health          — server health check

The SQLAlchemy engine is created once at startup and reused across requests.
All heavy DB fetches are cached in memory for `CACHE_TTL_SECONDS` to avoid
hammering the DB on every poll cycle from the frontend.

Environment variables (same as the rest of the project):
  SIM_DB_SERVER, SIM_DB_NAME, SIM_DB_USER, SIM_DB_PASSWORD, SIM_DB_DRIVER

Usage
-----
  # Development (auto-reload):
  uvicorn src.api:app --reload --port 8000

  # Production:
  uvicorn src.api:app --host 0.0.0.0 --port 8000 --workers 2

  # From repo root (Windows PowerShell):
  uvicorn src.api:app --reload --port 8000
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

# ---------------------------------------------------------------------------
# Path bootstrap (allow running as `python src/api.py` or via uvicorn src.api)
# ---------------------------------------------------------------------------
CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

# When running as a PyInstaller frozen exe, __file__ resolves inside the
# temporary _MEIPASS extraction folder — which is wiped on every restart.
# Outputs (sim_ref_dt.json, history.json, etc.) must be written next to the
# exe itself (sys.executable), NOT inside _MEIPASS.
if getattr(sys, "frozen", False):
    # sys.executable = D:\...\CAPI\capi_api.exe
    # REPO_ROOT      = D:\...\CAPI\          ← persistent, survives restarts
    REPO_ROOT = Path(sys.executable).resolve().parent
else:
    # Dev mode: __file__ = src/api.py  → parent = src/  → parent = repo root
    REPO_ROOT = CURRENT_DIR.parent

# When running as a PyInstaller bundle, sys.executable points to the .exe,
# not to python.exe. Subprocesses (simulation, snapshot) must be invoked
# differently: we pass the extracted .py files directly to the bundled python
# interpreter located in the _MEIPASS temp dir, or use runpy via the exe itself.
# We detect this and expose a helper so subprocess calls use the right python.
if getattr(sys, "frozen", False):
    # Running inside PyInstaller bundle — _MEIPASS is the extracted temp dir
    _BUNDLE_DIR = Path(sys._MEIPASS)  # type: ignore[attr-defined]
    # The bundled Python DLL is in _MEIPASS; use sys.executable (the .exe)
    # with a special env var to signal it should run a script, not the API.
    # Simplest approach: ship python312.dll + use it via a wrapper script.
    # Best approach for subprocess: find python.exe on PATH or use stored path.
    import shutil
    _PYTHON_EXE = shutil.which("python") or shutil.which("python3") or sys.executable
else:
    _BUNDLE_DIR = CURRENT_DIR
    _PYTHON_EXE = sys.executable

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from pydantic import BaseModel, Field

from data_gathering import (
    fetch_current_state,
    fetch_data_last_updated,
    fetch_hard_stage_market,
    fetch_orders,
    get_engine,
)
from livedata_export import (
    _build_hard_stage_market,
    _build_orders_by_id,
    _snapshot_from_realtime,
)

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Production Simulation — Live View API",
    description=(
        "Serves real-time production floor data for the Live View tab. "
        "Orders and Hard Stage Market are fetched from the DB. "
        "Machine/stock snapshot is sourced from b2b_machine_status and "
        "b2b_supermarket_status (real-time EDM state + buffer stock). "
        "Falls back to realtime.json if the DB call fails."
    ),
    version="1.1.0",
)

# Allow the Vite dev server (localhost:5173) and any intranet origin.
# Tighten this list when deploying to production.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Compress JSON responses ≥ 1 KB — reduces payload 60-80% on typical livedata.
app.add_middleware(GZipMiddleware, minimum_size=1024)

# ---------------------------------------------------------------------------
# Startup: create DB engine once
# ---------------------------------------------------------------------------

_engine = None


@app.on_event("startup")
def _startup() -> None:
    global _engine
    try:
        _engine = get_engine()
        print("[api] DB engine created successfully.")
    except ValueError as exc:
        # Missing env vars — API starts but all DB endpoints return 503
        print(f"[api] WARNING: DB engine could not be created: {exc}")
        _engine = None

    # Start background history-refresh thread (every 30 min).
    # The thread fires immediately on first wake so that any gap caused by a
    # server crash (or restart) is closed as soon as the API comes back up —
    # rather than waiting up to 30 min before the first snapshot is taken.
    _history_refresh_thread = threading.Thread(
        target=_history_refresh_loop, daemon=True, name="history-refresh"
    )
    _history_refresh_thread.start()
    print("[api] History refresh background thread started (interval=1800s).")

    # Immediately take one snapshot + rebuild history on startup.
    # This closes any open spans that accumulated while the server was down,
    # preventing phantom "16h in process" bars on the history chart.
    def _startup_snapshot() -> None:
        try:
            ok = _refresh_history_now()
            if ok:
                print("[api] Startup snapshot captured and history.json rebuilt.")
            else:
                print("[api] Startup snapshot skipped (no sim_ref_dt or DB unavailable).")
        except Exception as exc:
            print(f"[api] WARNING: startup snapshot failed: {exc}")

    threading.Thread(target=_startup_snapshot, daemon=True, name="startup-snapshot").start()


# ---------------------------------------------------------------------------
# In-memory cache
# ---------------------------------------------------------------------------

CACHE_TTL_SECONDS          = 300  # 5 minutes — orders + hard_stage (slow-changing)
SNAPSHOT_CACHE_TTL_SECONDS = 60   # 60 seconds — snapshot (real-time EDM/laser state)
METADATA_CACHE_TTL_SECONDS = 30   # 30 seconds — metadata / data_last_updated

_cache: dict[str, Any] = {}
_cache_ts: dict[str, float] = {}


# ---------------------------------------------------------------------------
# Simulation jobs (on-demand backend run)
# ---------------------------------------------------------------------------

@dataclass
class SimulationJob:
    job_id: str
    status: str
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    data_source: str = "db"
    max_sim_hours: float = 24.0
    input_filter: str | None = None
    use_live_initial_state: bool = True
    excluded_machines: list[str] = None   # type: ignore[assignment]
    has_results: bool = False
    message: str | None = None
    error: str | None = None
    progress: float | None = None

    def __post_init__(self):
        if self.excluded_machines is None:
            self.excluded_machines = []


class SimulationRunRequest(BaseModel):
    data_source: str = Field(default="db", pattern="^(db|csv)$")
    max_sim_hours: float = Field(default=24.0, ge=0.1, le=72.0)
    input_filter: str | None = Field(default=None)
    use_live_initial_state: bool = Field(default=True)
    excluded_machines: list[str] = Field(
        default_factory=list,
        description="Machine IDs to exclude from simulation (e.g. manualOff from Live View settings).",
    )


_jobs_lock = threading.Lock()
_jobs: dict[str, SimulationJob] = {}
_active_job_id: str | None = None


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _outputs_exist() -> bool:
    dashboard = REPO_ROOT / "outputs" / "dashboard.json"
    realtime = REPO_ROOT / "outputs" / "realtime.json"
    return dashboard.exists() and realtime.exists()


# ---------------------------------------------------------------------------
# History refresh helpers
# ---------------------------------------------------------------------------

HISTORY_REFRESH_INTERVAL_SEC = 1800  # 30 minutes

_HISTORY_OUTPUT    = REPO_ROOT / "outputs" / "history.json"
_HISTORY_PUBLIC    = REPO_ROOT / "Dashboard and Real-Time Playback" / "public" / "history.json"
_SIM_REF_DT_PATH   = REPO_ROOT / "outputs" / "sim_ref_dt.json"
_LIVE_SNAPSHOTS_PATH = REPO_ROOT / "outputs" / "live_snapshots.json"


def _read_sim_ref_dt() -> "datetime | None":
    """Read the simulation reference datetime from sim_ref_dt.json."""
    if not _SIM_REF_DT_PATH.exists():
        return None
    try:
        data = json.loads(_SIM_REF_DT_PATH.read_text(encoding="utf-8"))
        return datetime.fromisoformat(data["ref_dt"])
    except Exception as exc:
        print(f"[history-refresh] Could not read sim_ref_dt.json: {exc}")
        return None


def _take_snapshot_and_rebuild(sim_ref_dt: datetime) -> bool:
    """
    Capture current machine states, append to live_snapshots.json,
    then rebuild history.json from all accumulated snapshots.

    Returns True on success, False on error.
    """
    if _engine is None:
        print("[snapshot] Skipping — DB engine not available.")
        return False

    try:
        from snapshot_accumulator import append as acc_append
        from history_export import export_history_json
        import shutil

        # 1. Capture current machine states
        snapshot_data = fetch_current_state(_engine)
        machines_raw = snapshot_data.get("machines", [])

        # 2. Keep appending to live_snapshots.json (retained for diagnostics)
        acc_append(_LIVE_SNAPSHOTS_PATH, machines_raw, sim_ref_dt)
        print(f"[snapshot] Captured {len(machines_raw)} machine states → {_LIVE_SNAPSHOTS_PATH}")

        # 3. Rebuild history.json by querying the DB directly for all
        #    Entrada→Saída pairs — this is authoritative and complete,
        #    unlike snapshot_accumulator which can miss runs that completed
        #    between two consecutive 30-min snapshots.
        export_history_json(
            engine=_engine,
            ref_dt=sim_ref_dt,
            output=_HISTORY_OUTPUT,
            horizon_hours=24,
        )
        n_events_written = json.loads(_HISTORY_OUTPUT.read_text(encoding="utf-8")).get("timeline", [])
        n_events = len(n_events_written)
        if _HISTORY_PUBLIC:
            shutil.copy2(_HISTORY_OUTPUT, _HISTORY_PUBLIC)
        print(f"[snapshot] history.json rebuilt from DB — {n_events} events → mirrored to public/")
        return True

    except Exception as exc:
        print(f"[snapshot] ERROR: {exc}")
        import traceback; traceback.print_exc()
        return False


def _refresh_history_now() -> bool:
    """Take a snapshot and rebuild history.json. Used by the background loop and API."""
    sim_ref_dt = _read_sim_ref_dt()
    if sim_ref_dt is None:
        print("[snapshot] Skipping — sim_ref_dt.json not found (run a simulation first).")
        return False
    return _take_snapshot_and_rebuild(sim_ref_dt)


def _history_refresh_loop() -> None:
    """Background thread: take a snapshot and rebuild history.json every 30 min.

    Sleeps AFTER each run so the interval is measured from the end of one
    execution to the start of the next (avoids drift on slow machines).
    The very first run is intentionally skipped here because _startup already
    fires a startup-snapshot thread that runs immediately on server start.
    """
    while True:
        time.sleep(HISTORY_REFRESH_INTERVAL_SEC)
        _refresh_history_now()


def _utf8_subprocess_env() -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["PYTHONUNBUFFERED"] = "1"
    return env


def _run_command_with_live_output(
    cmd: list[str], *, label: str, cwd: Path, on_line: "Callable[[str], None] | None" = None,
) -> tuple[int, str]:
    """Run a subprocess and stream combined stdout/stderr to API stdout."""
    tail = deque(maxlen=120)
    print(f"[api][{label}] Starting: {' '.join(cmd)}")
    process = subprocess.Popen(
        cmd,
        cwd=str(cwd),
        env=_utf8_subprocess_env(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )

    assert process.stdout is not None
    for raw_line in process.stdout:
        line = raw_line.rstrip("\r\n")
        if not line:
            continue
        tail.append(line)
        print(f"[api][{label}] {line}")
        if on_line is not None:
            on_line(line)

    return_code = process.wait()
    tail_text = "\n".join(tail)
    return return_code, tail_text


def _build_live_initial_state_file(
    on_line: "Callable[[str], None] | None" = None,
) -> Path | None:
    snapshot_script = REPO_ROOT / "snapshot_now.py"
    snapshot_out = REPO_ROOT / "snapshot_now_output.json"
    initial_state_out = REPO_ROOT / "outputs" / "initial_state_from_snapshot.json"

    if getattr(sys, "frozen", False):
        # Running as PyInstaller exe — no Python available to spawn subprocess.
        # Run snapshot_now.py directly in-process via runpy.
        import runpy, io, contextlib
        print(f"[api][snapshot] Running snapshot_now.py in-process (frozen mode)")
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                runpy.run_path(str(snapshot_script), run_name="__main__")
        except SystemExit:
            pass
        except Exception as exc:
            output = buf.getvalue()
            raise RuntimeError(f"snapshot_now.py failed in-process: {exc}\n{output}")
        output = buf.getvalue()
        for line in output.splitlines():
            print(f"[api][snapshot] {line}")
            if on_line:
                on_line(line)
    else:
        return_code, tail = _run_command_with_live_output(
            [_PYTHON_EXE, "-u", str(snapshot_script)],
            label="snapshot",
            cwd=REPO_ROOT,
            on_line=on_line,
        )
        if return_code != 0:
            raise RuntimeError(
                "snapshot_now.py failed before simulation. "
                f"output_tail={tail[-1200:]}"
            )

    if not snapshot_out.exists():
        raise RuntimeError("snapshot_now_output.json was not generated.")

    payload = json.loads(snapshot_out.read_text(encoding="utf-8"))
    initial_state = payload.get("initial_state")
    if not initial_state:
        raise RuntimeError("snapshot_now_output.json does not contain initial_state.")

    inactive_machines: list[str] = payload.get("inactive_machines") or []

    initial_state_out.parent.mkdir(parents=True, exist_ok=True)
    initial_state_out.write_text(json.dumps(initial_state, indent=2), encoding="utf-8")
    return initial_state_out, inactive_machines


def _run_simulation_job(job_id: str) -> None:
    global _active_job_id

    with _jobs_lock:
        job = _jobs[job_id]
        job.status = "running"
        job.started_at = _iso_now()
        job.message = "Simulation started"

    try:
        initial_state_file: Path | None = None
        inactive_from_snapshot: list[str] = []
        if job.data_source == "db" and job.use_live_initial_state:
            # ── Snapshot phase: 0 → 10 % ──────────────────────────────
            _SNAPSHOT_STEPS: list[tuple[str, float]] = [
                ("Connecting to database",   1.0),
                ("Fetching current state",   3.0),
                ("Fetching orders",          5.0),
                ("Fetching Hard Stage",      6.0),
                ("Fetching restrictions",    7.0),
                ("Enriching machine states", 8.0),
                ("Snapshot saved",          10.0),
            ]

            def _on_snapshot_line(line: str) -> None:
                for keyword, pct in _SNAPSHOT_STEPS:
                    if keyword in line:
                        with _jobs_lock:
                            job.progress = pct
                            job.message = f"Preparing… {pct:.0f}%"
                        break

            with _jobs_lock:
                job.message = "Preparing… 0%"
                job.progress = 0.0
            initial_state_file, inactive_from_snapshot = _build_live_initial_state_file(
                on_line=_on_snapshot_line,
            )

        # Merge excluded machines: inactive detected automatically + manualOff from UI.
        # Deduplicate and strip whitespace.
        excluded_set = {m.strip() for m in (job.excluded_machines or []) if m.strip()}
        excluded_set.update(m.strip() for m in inactive_from_snapshot if m.strip())
        all_excluded = sorted(excluded_set)
        if all_excluded:
            print(f"[api] Excluding {len(all_excluded)} machines from simulation: {all_excluded}")

        # ── Simulation phase: 10 → 99 % ──────────────────────────────
        _SIM_BASE = 10.0   # start of simulation range
        _SIM_TOP  = 99.0   # end of simulation range

        # Regex to parse progress lines like:
        # [t=3600s | 1.00h] trays 5/28 (17.9%) | laser busy ...
        _progress_re = re.compile(r"\(([\d.]+)%\)")
        _time_re = re.compile(r"\[t=([\d.]+)s")
        _max_sim_sec = job.max_sim_hours * 3600.0

        def _on_sim_line(line: str) -> None:
            m = _progress_re.search(line)
            if m:
                raw_pct = float(m.group(1))
                # Also check time-based progress as a floor
                mt = _time_re.search(line)
                if mt and _max_sim_sec > 0:
                    time_pct = float(mt.group(1)) / _max_sim_sec * 100.0
                    raw_pct = max(raw_pct, time_pct)
                mapped = _SIM_BASE + raw_pct / 100.0 * (_SIM_TOP - _SIM_BASE)
                with _jobs_lock:
                    job.progress = round(min(mapped, _SIM_TOP), 1)
                    job.message = f"Simulating… {job.progress:.0f}%"
            elif "Initializing simulation" in line:
                with _jobs_lock:
                    job.message = "Initializing… 10%"
                    job.progress = 10.0
            elif "Running simulation" in line:
                with _jobs_lock:
                    job.message = "Simulating… 10%"
                    job.progress = _SIM_BASE
            elif "Exporting" in line or "Writing" in line:
                with _jobs_lock:
                    job.message = "Exporting results…"
                    job.progress = 99.0   # stay at 99 — 100 is reserved for status=succeeded

        cmd = [
            _PYTHON_EXE,
            "-u",
            str(REPO_ROOT / "src" / "main.py"),
            "--data-source",
            job.data_source,
            "--max-sim-hours",
            str(job.max_sim_hours),
            "--export-json",
            "--export-figma-json",
            "--realtime-step-sec",
            "60",
            "--progress",
            "--summary-interval-sec",
            "300",
        ]
        if job.input_filter:
            cmd += ["--input-filter", job.input_filter]
        if initial_state_file is not None:
            cmd += ["--initial-state", str(initial_state_file)]
        if all_excluded:
            cmd += ["--excluded-machines"] + all_excluded

        if getattr(sys, "frozen", False):
            # Running as PyInstaller exe — run main.py in-process via runpy.
            import runpy, io, contextlib
            main_script = str(REPO_ROOT / "src" / "main.py")
            print(f"[api][sim-{job_id[:8]}] Running main.py in-process (frozen mode)")
            # Inject sys.argv so argparse inside main.py picks up the right args
            orig_argv = sys.argv[:]
            sys.argv = cmd[2:]  # strip python + "-u", keep main.py + args
            buf = io.StringIO()

            class _TeeWriter:
                """Write to both buf and stdout, calling on_line for each line."""
                def __init__(self):
                    self._pending = ""
                def write(self, s):
                    sys.__stdout__.write(s)
                    self._pending += s
                    while "\n" in self._pending:
                        line, self._pending = self._pending.split("\n", 1)
                        _on_sim_line(line)
                def flush(self):
                    sys.__stdout__.flush()

            tee = _TeeWriter()
            try:
                with contextlib.redirect_stdout(tee), contextlib.redirect_stderr(tee):
                    runpy.run_path(main_script, run_name="__main__")
            except SystemExit as e:
                if e.code not in (None, 0):
                    raise RuntimeError(f"Simulation exited with code {e.code}")
            except Exception as exc:
                raise RuntimeError(f"Simulation failed in-process: {exc}")
            finally:
                sys.argv = orig_argv
            return_code = 0
            tail = ""
        else:
            return_code, tail = _run_command_with_live_output(
                cmd,
                label=f"sim-{job_id[:8]}",
                cwd=REPO_ROOT,
                on_line=_on_sim_line,
            )

        if return_code != 0:
            raise RuntimeError(
                f"Simulation failed with exit code {return_code}. "
                f"output_tail={tail[-2000:]}"
            )

        # Subprocess finished — mark as 99% and "Finalizing…" while the
        # post-simulation snapshot is rebuilt.
        # The DB updates every 30 min and the simulation takes < 10 min, so the
        # data collected at sim-start (snapshot_now_output.json) is still current.
        # No second DB fetch needed — just reuse that data directly.
        with _jobs_lock:
            job.progress = 99.0
            job.message = "Finalizing… rebuilding history"

        try:
            sim_ref_dt = _read_sim_ref_dt()
            if sim_ref_dt is not None:
                snap_path = REPO_ROOT / "snapshot_now_output.json"
                if snap_path.exists():
                    payload = json.loads(snap_path.read_text(encoding="utf-8"))
                    machines_raw = (payload.get("initial_state") or {}).get("machines") or []
                    if machines_raw:
                        from snapshot_accumulator import reset as acc_reset, append as acc_append
                        from history_export import export_history_json
                        import shutil
                        acc_reset(_LIVE_SNAPSHOTS_PATH, sim_ref_dt)
                        acc_append(_LIVE_SNAPSHOTS_PATH, machines_raw, sim_ref_dt)
                        export_history_json(
                            engine=_engine,
                            ref_dt=sim_ref_dt,
                            output=_HISTORY_OUTPUT,
                            horizon_hours=24,
                        )
                        if _HISTORY_PUBLIC:
                            shutil.copy2(_HISTORY_OUTPUT, _HISTORY_PUBLIC)
                        print(f"[snapshot] history.json rebuilt from DB after simulation → mirrored to public/")
                    else:
                        print("[snapshot] WARNING: snapshot_now_output.json has no machines — keeping existing snapshots.")
                else:
                    print("[snapshot] WARNING: snapshot_now_output.json not found — keeping existing snapshots.")
        except Exception as exc:
            print(f"[snapshot] WARNING during post-sim snapshot: {exc}")

        # All done — mark succeeded at exactly 100%.
        with _jobs_lock:
            job.status = "succeeded"
            job.has_results = _outputs_exist()
            job.progress = 100.0
            job.message = "Simulation finished successfully"
            job.finished_at = _iso_now()
    except Exception as exc:
        with _jobs_lock:
            job.status = "failed"
            job.error = str(exc)
            job.message = "Simulation failed"
            job.finished_at = _iso_now()
    finally:
        with _jobs_lock:
            _active_job_id = None


def _is_stale(key: str) -> bool:
    last = _cache_ts.get(key, 0.0)
    return (time.monotonic() - last) > CACHE_TTL_SECONDS


def _set_cache(key: str, value: Any) -> None:
    _cache[key] = value
    _cache_ts[key] = time.monotonic()


# ---------------------------------------------------------------------------
# Internal data builders
# ---------------------------------------------------------------------------

def _require_engine():
    if _engine is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "Database engine is not available. "
                "Check SIM_DB_* environment variables and restart the server."
            ),
        )
    return _engine


def _get_orders(filter_csv: str | None = None) -> dict:
    """Fetch and cache orders_by_id. Cache key includes the filter path."""
    cache_key = f"orders:{filter_csv}"
    if not _is_stale(cache_key):
        return _cache[cache_key]

    engine = _require_engine()

    order_ids = None
    if filter_csv:
        try:
            from data_gathering import _read_filter_csv
            order_ids, _ = _read_filter_csv(filter_csv)
        except Exception as exc:
            print(f"[api] Warning: could not read filter CSV '{filter_csv}': {exc}")

    df = fetch_orders(engine, order_ids=order_ids)
    result = _build_orders_by_id(df)
    _set_cache(cache_key, result)
    return result


def _get_hard_stage() -> list:
    """Fetch and cache hard_stage_market."""
    cache_key = "hard_stage"
    if not _is_stale(cache_key):
        return _cache[cache_key]

    engine = _require_engine()
    df = fetch_hard_stage_market(engine)
    result = _build_hard_stage_market(df)
    _set_cache(cache_key, result)
    return result


def _get_snapshot(fallback_realtime: Path | None = None, oee_hours: int = 168, off_hours: int = 168,
                  market_timeout_hours: int = 24, default_test_min: int = 60) -> dict | None:
    """
    Return the current machine/stock snapshot from the DB.

    Cached for SNAPSHOT_CACHE_TTL_SECONDS (20 s) — short enough to feel live,
    long enough to absorb bursts of concurrent users/refreshes without hammering
    the DB with identical heavy queries.
    Returns None if DB is unavailable (caller must handle 503).
    """
    if _engine is None:
        return None

    cache_key = f"snapshot:{oee_hours}:{off_hours}:{market_timeout_hours}:{default_test_min}"
    last = _cache_ts.get(cache_key, 0.0)
    if (time.monotonic() - last) <= SNAPSHOT_CACHE_TTL_SECONDS and cache_key in _cache:
        return _cache[cache_key]

    snapshot = fetch_current_state(_engine, oee_hours=oee_hours, off_hours=off_hours,
                                   market_timeout_hours=market_timeout_hours,
                                   default_test_min=default_test_min)
    _cache[cache_key] = snapshot
    _cache_ts[cache_key] = time.monotonic()
    return snapshot


def _build_metadata(snapshot: dict | None) -> dict:
    from zoneinfo import ZoneInfo

    cache_key = "metadata"
    last = _cache_ts.get(cache_key, 0.0)
    if (time.monotonic() - last) <= METADATA_CACHE_TTL_SECONDS and cache_key in _cache:
        return _cache[cache_key]

    facility_tz = ZoneInfo("America/Sao_Paulo")
    now_utc = datetime.now(timezone.utc)
    now_facility = now_utc.astimezone(facility_tz)
    current_time_label = now_facility.strftime("%H:%M")
    data_last_updated = fetch_data_last_updated(_engine) if _engine is not None else None
    result = {
        "generated_at": now_utc.isoformat(),
        "data_last_updated": data_last_updated,
        "current_time_label": current_time_label,
        "facility_timezone": "America/Sao_Paulo",
    }
    _cache[cache_key] = result
    _cache_ts[cache_key] = time.monotonic()
    return result


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/api/health")
def health():
    """Health check — confirms the API is running and DB is reachable."""
    db_ok = _engine is not None
    return {
        "status": "ok" if db_ok else "degraded",
        "db_connected": db_ok,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/simulation/status")
def get_simulation_status():
    with _jobs_lock:
        active_job = _jobs.get(_active_job_id) if _active_job_id else None
        active_payload = asdict(active_job) if active_job else None
    sim_ref_dt = _read_sim_ref_dt()
    return {
        "has_results": _outputs_exist(),
        "active_job": active_payload,
        # ISO timestamp of when the simulation was run (t=0 anchor).
        # The frontend uses this to position the playback slider at "now"
        # so that anyone opening the page hours later sees the correct position.
        "sim_ref_dt": sim_ref_dt.isoformat() if sim_ref_dt is not None else None,
    }


@app.get("/api/simulation/jobs/{job_id}")
def get_simulation_job(job_id: str):
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Simulation job not found")
        return asdict(job)


@app.post("/api/simulation/run")
def run_simulation(request: SimulationRunRequest):
    global _active_job_id

    with _jobs_lock:
        if _active_job_id is not None:
            active = _jobs.get(_active_job_id)
            if active and active.status in {"queued", "running"}:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "message": "A simulation is already running.",
                        "active_job_id": _active_job_id,
                    },
                )

        job_id = str(uuid.uuid4())
        job = SimulationJob(
            job_id=job_id,
            status="queued",
            created_at=_iso_now(),
            data_source=request.data_source,
            max_sim_hours=request.max_sim_hours,
            input_filter=request.input_filter,
            use_live_initial_state=request.use_live_initial_state,
            excluded_machines=list(request.excluded_machines),
            message="Simulation queued",
        )
        _jobs[job_id] = job
        _active_job_id = job_id

    thread = threading.Thread(target=_run_simulation_job, args=(job_id,), daemon=True)
    thread.start()

    return {
        "job_id": job_id,
        "status": "queued",
        "job_url": f"/api/simulation/jobs/{job_id}",
    }


@app.get("/api/history")
def get_history(refresh: bool = Query(default=False, description="Force an immediate snapshot + rebuild before returning")):
    """
    Return the current history.json — real production timeline derived from
    live machine-state snapshots (captured every 30 min automatically).

    Shape matches HistoryData in data.ts:
    {
      "metadata": { "generated_at": "...", "snapshot_time_sec": 1234.5, ... },
      "timeline": [ { "machine_id": "...", "activity": "process", "start_sec": 0, "end_sec": 3600 }, ... ]
    }

    Pass ?refresh=true to force an immediate snapshot + rebuild before responding.
    """
    if refresh:
        _refresh_history_now()

    if not _HISTORY_OUTPUT.exists():
        # Return empty payload rather than 404, so the frontend doesn't crash.
        return {
            "metadata": {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "description": "No snapshots yet — run a simulation first",
                "snapshot_time_sec": 0,
                "facility_timezone": "America/Sao_Paulo",
            },
            "timeline": [],
        }

    return json.loads(_HISTORY_OUTPUT.read_text(encoding="utf-8"))


@app.get("/api/orders")
def get_orders(    input_filter: str | None = Query(
        default=None,
        description="Path to a CSV file to filter order IDs (same as --input-filter in the CLI).",
    ),
):
    """
    Return the orders_by_id lookup table from the DB.

    Each key is an order_id. Each value contains:
      order_id, part_number_id, quantity, due_date
    """
    return _get_orders(filter_csv=input_filter)


@app.get("/api/hard-stage")
def get_hard_stage():
    """
    Return the current Hard Stage Market inventory.

    Each item contains:
      body_partnumber, body_qtty, nozzles[]
    """
    return _get_hard_stage()


@app.get("/api/snapshot")
def get_snapshot(
    oee_hours: int = Query(default=168, ge=1, le=8760, description="OEE calculation window in hours"),
    off_hours: int = Query(default=168, ge=1, le=8760, description="Hours without activity to consider machine OFF"),
    default_test_min: int = Query(default=60, ge=0, le=480, description="Default test (release) time in minutes for type pairs not found in the changeover CSV"),
):
    snapshot = _get_snapshot(oee_hours=oee_hours, off_hours=off_hours,
                             default_test_min=default_test_min)
    if snapshot is None:
        raise HTTPException(status_code=503, detail="DB not available.")
    return snapshot


@app.get("/api/livedata")
def get_livedata(
    input_filter: str | None = Query(default=None),
    oee_hours: int = Query(default=168, ge=1, le=8760, description="OEE calculation window in hours"),
    off_hours: int = Query(default=168, ge=1, le=8760, description="Hours without activity to consider machine OFF"),
    market_timeout_hours: int = Query(default=24, ge=1, le=8760, description="Hours after which a TypeNumber with no open orders disappears from the final market"),
    default_test_min: int = Query(default=60, ge=0, le=480, description="Default test (release) time in minutes for type pairs not found in the changeover CSV"),
):
    """
    Return the full livedata payload consumed by LiveVisualization.tsx.

    Shape:
    {
      "metadata": { "generated_at": "...", "current_time_label": "15:00" },
      "orders_by_id": { "<order_id>": { ... }, ... },
      "hard_stage_market": [ { "body_partnumber": "...", "body_qtty": 0, "nozzles": [...] }, ... ],
      "snapshot": {
        "machines": [...],       // real EDM state from b2b_machine_status
        "stock_levels": [...],   // real WIP from b2b_supermarket_status
        "backlog_orders": [...],
        "produced_orders": [...]
      }
    }

    This endpoint is the direct replacement for reading public/livedata.json.
    Point VITE_LIVEDATA_JSON_URL to this URL before building the frontend.
    """
    # All four sub-fetches are independent — run them concurrently so the
    # total wall time equals max(each), not sum(each).
    # _build_metadata() does NOT actually use the snapshot arg — it can run
    # fully in parallel and we pass None as placeholder.
    from concurrent.futures import ThreadPoolExecutor

    _t_livedata_start = time.monotonic()

    def _timed_task(name, fn, *args):
        t0 = time.monotonic()
        result = fn(*args)
        print(f"[livedata:{name}] {time.monotonic() - t0:.2f}s")
        return result

    with ThreadPoolExecutor(max_workers=4) as _lp:
        _f_orders     = _lp.submit(_timed_task, "orders",     _get_orders,    input_filter)
        _f_hard_stage = _lp.submit(_timed_task, "hard_stage", _get_hard_stage)
        _f_snapshot   = _lp.submit(_timed_task, "snapshot",   _get_snapshot,
                                   None, oee_hours, off_hours, market_timeout_hours, default_test_min)
        _f_metadata   = _lp.submit(_timed_task, "metadata",   _build_metadata, None)

    orders_by_id      = _f_orders.result()
    hard_stage_market = _f_hard_stage.result()
    snapshot          = _f_snapshot.result()
    metadata          = _f_metadata.result()

    print(f"[livedata] 4-way parallel fetch done in {time.monotonic() - _t_livedata_start:.2f}s")

    if snapshot is None:
        raise HTTPException(status_code=503, detail="DB not available.")

    return {
        "metadata": metadata,
        "orders_by_id": orders_by_id,
        "hard_stage_market": hard_stage_market,
        "snapshot": snapshot,
    }


# ---------------------------------------------------------------------------
# Dev entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("api:app", host="0.0.0.0", port=8001, reload=True)
