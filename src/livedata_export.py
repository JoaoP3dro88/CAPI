"""
livedata_export.py
==================
Generates livedata.json for the Live View frontend.

Unlike realtime.json (multi-frame simulation playback), livedata.json is a
**single-frame snapshot** of the current production floor state, updated
periodically (e.g. every hour by a scheduler).

Structure produced
------------------
{
  "metadata": {
    "generated_at": "<ISO timestamp>",
    "current_time_label": "15:00"      # human-readable shift time
  },
  "orders_by_id": {                    # from fetch_orders() — same query as simulation
    "<order_id>": {
      "order_id": "...",
      "part_number_id": "...",
      "quantity": 123,
      "due_date": "2026-03-31"
    }, ...
  },
  "hard_stage_market": [               # from fetch_hard_stage_market()
    {
      "body_partnumber": "24386S0464",
      "body_qtty": 5338,
      "nozzles": ["0433171855", ...]
    }, ...
  ],
  "snapshot": {                        # from fetch_current_state() — NOT YET IMPLEMENTED
    "time_sec": 32400.0,
    "backlog_orders": [...],
    "produced_orders": [...],
    "stock_levels": [...],
    "machines": [...]
  }
}

Usage
-----
# Export to both outputs/ and public/ using DB:
python src/livedata_export.py

# Export to a custom path:
python src/livedata_export.py --output path/to/livedata.json

# Dry-run: print JSON to stdout only
python src/livedata_export.py --dry-run

Notes
-----
- All snapshot data (machines, buffers, laser summary) is sourced exclusively
  from live DB queries via fetch_current_state().
- The scheduler (scheduler.py) calls this script on a cron/interval basis.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))
REPO_ROOT = CURRENT_DIR.parent

from data_gathering import (
    fetch_hard_stage_market,
    fetch_orders,
    fetch_data_last_updated,
    fetch_current_state,
    get_engine,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_orders_by_id(df_orders) -> dict:
    """Convert the orders DataFrame into the orders_by_id dict."""
    import pandas as pd

    orders_by_id: dict = {}
    for row in df_orders.to_dict("records"):
        oid = str(row.get("order_id", "") or "").strip()
        if not oid:
            continue
        raw_date = row.get("update_date")
        due = None
        if raw_date is not None:
            try:
                due = str(pd.Timestamp(raw_date).date())
            except Exception:
                due = str(raw_date)[:10]
        orders_by_id[oid] = {
            "order_id": oid,
            "part_number_id": str(row.get("part_number_id", "") or "").strip(),
            "quantity": int(float(row.get("real_qtty") or 0)),
            "due_date": due,
        }
    return orders_by_id


def _build_hard_stage_market(df_hard_stage) -> list:
    """
    Group nozzle part numbers by body number.

    Each row in silver_production.inventory is one nozzle PN with its body PN.
    We aggregate: one body entry with a list of all its nozzle PNs and the
    max stock quantity seen for that body.

    Returns a list sorted by body_partnumber.
    """
    body_map: dict[str, dict] = {}
    for row in df_hard_stage.to_dict("records"):
        body = str(row.get("body_number_id") or "").strip()
        pn   = str(row.get("part_number_id")  or "").strip()
        _raw_qtty = row.get("qtty_hardstage")
        qtty = 0 if (_raw_qtty is None or (_raw_qtty != _raw_qtty)) else int(float(_raw_qtty))
        if not body or not pn:
            continue
        if body not in body_map:
            body_map[body] = {"body_partnumber": body, "body_qtty": qtty, "nozzles": []}
        else:
            # Take the maximum quantity seen across rows for this body
            body_map[body]["body_qtty"] = max(body_map[body]["body_qtty"], qtty)
        if pn not in body_map[body]["nozzles"]:
            body_map[body]["nozzles"].append(pn)
    return sorted(body_map.values(), key=lambda x: x["body_partnumber"])


def _current_shift(now: datetime | None = None) -> tuple[int, datetime]:
    """Return (shift_start_hour, shift_start_datetime) for the active shift.

    Three shifts per day:
      T1  06:00 – 14:59  (start hour = 6)
      T2  15:00 – 23:59  (start hour = 15)
      T3  00:00 – 05:59  (start hour = 0, but belongs to the *previous* calendar
                           day logically — still uses hour=0 of the current day)

    Returns
    -------
    shift_start_hour : int
        The hour at which the current shift started (0, 6, or 15).
    shift_start : datetime
        The exact datetime of the shift start (minutes/seconds zeroed).
    """
    if now is None:
        now = datetime.now()
    h = now.hour
    if 6 <= h < 15:
        start_hour = 6
    elif 15 <= h <= 23:
        start_hour = 15
    else:          # 00:00 – 05:59  (night shift, T3)
        start_hour = 0
    shift_start = now.replace(hour=start_hour, minute=0, second=0, microsecond=0)
    return start_hour, shift_start


def _shift_time_label(time_sec: float, shift_start_hour: int | None = None) -> str:
    """Convert seconds-from-shift-start to a HH:MM wall-clock string.

    If ``shift_start_hour`` is None (default) the current shift is detected
    automatically from the wall clock.  The result wraps correctly past
    midnight (e.g. T2 + 9 h = 00:00 next day).
    """
    if shift_start_hour is None:
        shift_start_hour, _ = _current_shift()
    total_minutes = int(time_sec // 60)
    total_wall_minutes = shift_start_hour * 60 + total_minutes
    h = (total_wall_minutes // 60) % 24   # wrap at midnight
    m = total_wall_minutes % 60
    return f"{h:02d}:{m:02d}"


def _snapshot_from_realtime(realtime_path: Path) -> dict | None:
    """
    Fallback: read the frame from realtime.json that is closest to the current
    wall-clock time within the active shift.

    Supports all three shifts (T1 06-15, T2 15-00, T3 00-06).
    Returns the frame dict, or None if realtime.json cannot be read.
    """
    if not realtime_path.exists():
        return None
    try:
        rt = json.loads(realtime_path.read_text(encoding="utf-8"))
    except Exception:
        return None

    frames = rt.get("frames", [])
    if not frames:
        return None

    now = datetime.now()
    _, shift_start = _current_shift(now)
    elapsed = (now - shift_start).total_seconds()

    if elapsed < 0:
        # Clock is before the computed shift start — use first frame
        return frames[0]

    total_time = rt.get("metadata", {}).get("total_time_sec") or frames[-1]["time_sec"]
    elapsed = max(0.0, min(elapsed, total_time))
    best = min(frames, key=lambda f: abs(f["time_sec"] - elapsed))
    return best


# ---------------------------------------------------------------------------
# Main export function
# ---------------------------------------------------------------------------

def export_livedata_json(
    *,
    engine=None,
    output: Path,
    dry_run: bool = False,
    filter_csv: str | None = None,
) -> Path:
    """
    Fetch live data exclusively from the DB and write livedata.json.

    The Live View must reflect only real production floor data — never
    simulation output. No realtime.json fallback, no simulation frames,
    no accumulated/projected stock. Every field comes directly from a DB query.

    Parameters
    ----------
    engine:
        SQLAlchemy engine. If None, one is created using env vars.
    output:
        Destination path for livedata.json.
    dry_run:
        If True, print JSON to stdout instead of writing to disk.
    filter_csv:
        Optional CSV path to filter orders.
    """
    from data_gathering import _read_filter_csv  # local import to avoid circular

    if engine is None:
        engine = get_engine()

    # ── 1. Orders ────────────────────────────────────────────────────────────
    order_ids = None
    if filter_csv and Path(filter_csv).exists():
        order_ids, _ = _read_filter_csv(filter_csv)
    df_orders = fetch_orders(engine, order_ids=order_ids)
    orders_by_id = _build_orders_by_id(df_orders)

    # ── 2. Hard Stage Market ─────────────────────────────────────────────────
    df_hard_stage = fetch_hard_stage_market(engine)
    hard_stage_market = _build_hard_stage_market(df_hard_stage)

    # ── 3. Live snapshot — 100% from DB ──────────────────────────────────────
    # fetch_current_state pulls: machine status, buffer stock, laser summary,
    # finished goods, backlog orders, produced orders — all from real DB queries.
    live_snapshot = fetch_current_state(engine)

    # ── 4. Metadata ──────────────────────────────────────────────────────────
    now_utc = datetime.now(timezone.utc)
    data_last_updated = fetch_data_last_updated(engine)
    from zoneinfo import ZoneInfo
    now_facility = now_utc.astimezone(ZoneInfo("America/Sao_Paulo"))
    current_time_label = now_facility.strftime("%H:%M")

    # ── 5. Assemble payload ───────────────────────────────────────────────────
    payload: dict = {
        "metadata": {
            "generated_at":      now_utc.isoformat(),
            "data_last_updated": data_last_updated,
            "current_time_label": current_time_label,
            "facility_timezone": "America/Sao_Paulo",
        },
        "orders_by_id":    orders_by_id,
        "hard_stage_market": hard_stage_market,
        # snapshot = photo of the real production floor right now.
        # The frontend reads all Live View data from here.
        # Nothing here comes from the simulation.
        "snapshot": {
            "time_sec":       0,   # real-time: not a simulation offset
            "stock_levels":   live_snapshot.get("stock_levels",  []),
            "machines":       live_snapshot.get("machines",      []),
            "laser_summary":  live_snapshot.get("laser_summary", []),
            "finished_goods": live_snapshot.get("finished_goods",[]),
            "backlog_orders": live_snapshot.get("backlog_orders", []),
            "produced_orders":live_snapshot.get("produced_orders",[]),
        },
    }

    # ── 6. Write / print ─────────────────────────────────────────────────────
    json_str = json.dumps(payload, indent=2, ensure_ascii=False)

    if dry_run:
        print(json_str)
    else:
        output = Path(output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json_str, encoding="utf-8")

    return output


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate livedata.json for the Live View frontend.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help=(
            "Destination path for livedata.json. "
            "Defaults to both outputs/livedata.json and "
            "\"Dashboard and Real-Time Playback/public/livedata.json\"."
        ),
    )
    parser.add_argument(
        "--input-filter",
        default="data/input_data.csv",
        help="CSV to filter order IDs (same as simulation --input-filter).",
    )
    parser.add_argument(
        "--db-server",   default=None, help="DB server (or SIM_DB_SERVER env var)")
    parser.add_argument(
        "--db-name",     default=None, help="DB name (or SIM_DB_NAME env var)")
    parser.add_argument(
        "--db-user",     default=None, help="DB user (or SIM_DB_USER env var)")
    parser.add_argument(
        "--db-password", default=None, help="DB password (or SIM_DB_PASSWORD env var)")
    parser.add_argument(
        "--db-driver",   default=None, help="ODBC driver (or SIM_DB_DRIVER env var)")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print JSON to stdout instead of writing files.",
    )
    args = parser.parse_args()

    try:
        engine = get_engine(
            server=args.db_server,
            database=args.db_name,
            username=args.db_user,
            password=args.db_password,
            driver=args.db_driver,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    # Resolve filter CSV
    filter_csv = args.input_filter
    if filter_csv and not Path(filter_csv).exists():
        # Try relative to REPO_ROOT
        candidate = REPO_ROOT / filter_csv
        filter_csv = str(candidate) if candidate.exists() else None

    if args.output:
        # Single custom output path
        out = export_livedata_json(
            engine=engine,
            output=Path(args.output),
            dry_run=args.dry_run,
            filter_csv=filter_csv,
        )
        if not args.dry_run:
            print(f"livedata.json written: {out}")
    else:
        # Default: write to both outputs/, public/ and dist/
        outputs = [
            REPO_ROOT / "outputs" / "livedata.json",
            REPO_ROOT / "Dashboard and Real-Time Playback" / "public" / "livedata.json",
            REPO_ROOT / "Dashboard and Real-Time Playback" / "dist" / "livedata.json",
        ]
        for dest in outputs:
            out = export_livedata_json(
                engine=engine,
                output=dest,
                dry_run=args.dry_run,
                filter_csv=filter_csv,
            )
            if not args.dry_run:
                print(f"livedata.json written: {out}")


if __name__ == "__main__":
    main()
