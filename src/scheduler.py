"""
scheduler.py
============
Hourly scheduler for the Live View pipeline.

Runs `livedata_export.py` once at startup and then every hour while the
production shift is active, keeping `livedata.json` fresh for the frontend.

Shift model
-----------
  T1  06:00 – 14:59  (morning)
  T2  15:00 – 23:59  (afternoon)
  T3  00:00 – 05:59  (night)
  Interval    : 3600 s by default (configurable via --interval-sec)

The scheduler runs continuously across all three shifts (24/7).
It stops gracefully when:
  - A KeyboardInterrupt (Ctrl+C) is received.
  - `--run-once` flag is passed (single export then exit).

Usage
-----
  # Run for the full shift, updating every hour:
  python src/scheduler.py

  # Custom interval (e.g. every 30 minutes):
  python src/scheduler.py --interval-sec 1800

  # Single run then exit (useful for cron / Task Scheduler):
  python src/scheduler.py --run-once

  # Pass extra args through to livedata_export.py:
  python src/scheduler.py --input-filter data/input_data.csv

  # Run without opening livedata_export CLI (import mode):
  from scheduler import run_once
  run_once(livedata_args=["--input-filter", "data/input_data.csv"])
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

CURRENT_DIR = Path(__file__).resolve().parent
REPO_ROOT = CURRENT_DIR.parent

SHIFT_BOUNDARIES = [6, 15, 0]   # T1=06:00, T2=15:00, T3=00:00
SHIFT_DURATION_H = 9            # each shift is 9 hours (6→15, 15→00, 00→06)
DEFAULT_INTERVAL_SEC = 3600  # 1 hour


# ---------------------------------------------------------------------------
# Core helpers
# ---------------------------------------------------------------------------

def _current_shift_end() -> datetime:
    """Return the end datetime of the currently active shift.

    Shifts:
      T1  06:00 – 14:59  → ends at 15:00
      T2  15:00 – 23:59  → ends at 00:00 next day
      T3  00:00 – 05:59  → ends at 06:00
    """
    now = datetime.now()
    h = now.hour
    if 6 <= h < 15:
        return now.replace(hour=15, minute=0, second=0, microsecond=0)
    elif 15 <= h <= 23:
        return (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    else:   # 00:00 – 05:59
        return now.replace(hour=6, minute=0, second=0, microsecond=0)


def _within_shift() -> bool:
    """Always True — the factory runs 24/7 across three shifts."""
    return True


def _fmt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------------
# Single export invocation
# ---------------------------------------------------------------------------

def run_once(
    python_exe: str = sys.executable,
    livedata_args: list[str] | None = None,
) -> int:
    """
    Invoke livedata_export.py as a subprocess.

    Parameters
    ----------
    python_exe:
        Python interpreter to use.
    livedata_args:
        Extra CLI args forwarded to livedata_export.py (e.g. --input-filter).

    Returns
    -------
    int
        Exit code of the subprocess (0 = success).
    """
    cmd = [
        python_exe,
        str(CURRENT_DIR / "livedata_export.py"),
        *(livedata_args or []),
    ]
    print(f"[scheduler] {_fmt(datetime.now())} — running livedata_export …")
    result = subprocess.run(cmd, cwd=str(REPO_ROOT))
    if result.returncode == 0:
        print(f"[scheduler] {_fmt(datetime.now())} — livedata.json updated ✓")
    else:
        print(
            f"[scheduler] {_fmt(datetime.now())} — livedata_export exited with "
            f"code {result.returncode} ✗",
            file=sys.stderr,
        )
    return result.returncode


# ---------------------------------------------------------------------------
# Scheduler loop
# ---------------------------------------------------------------------------

def run_loop(
    interval_sec: float = DEFAULT_INTERVAL_SEC,
    python_exe: str = sys.executable,
    livedata_args: list[str] | None = None,
) -> None:
    """
    Run livedata_export.py on a fixed interval until the shift ends.

    Executes immediately at startup (t=0), then waits `interval_sec` between
    subsequent runs. Exits cleanly when the shift window is over or on
    Ctrl+C.

    Parameters
    ----------
    interval_sec:
        Seconds between exports. Default is 3600 (1 hour).
    python_exe:
        Python interpreter to use.
    livedata_args:
        Extra CLI args forwarded to livedata_export.py.
    """
    shift_end = _current_shift_end()
    print(
        f"[scheduler] Starting. Shift ends at {_fmt(shift_end)}. "
        f"Interval: {interval_sec:.0f}s."
    )

    try:
        while _within_shift():
            run_once(python_exe=python_exe, livedata_args=livedata_args)

            next_run = datetime.now() + timedelta(seconds=interval_sec)
            if next_run >= _current_shift_end():
                print(f"[scheduler] Next run would be after shift end — stopping.")
                break

            print(
                f"[scheduler] Next run at {_fmt(next_run)} "
                f"(in {interval_sec:.0f}s). Sleeping …"
            )
            # Sleep in small chunks so Ctrl+C is always responsive
            deadline = time.monotonic() + interval_sec
            while time.monotonic() < deadline:
                if not _within_shift():
                    break
                time.sleep(min(10.0, deadline - time.monotonic()))

    except KeyboardInterrupt:
        print(f"\n[scheduler] {_fmt(datetime.now())} — interrupted by user.")

    print(f"[scheduler] {_fmt(datetime.now())} — scheduler stopped.")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Hourly scheduler: refreshes livedata.json during the production shift.",
    )
    parser.add_argument(
        "--interval-sec",
        type=float,
        default=DEFAULT_INTERVAL_SEC,
        help=f"Seconds between exports (default: {DEFAULT_INTERVAL_SEC} = 1 hour).",
    )
    parser.add_argument(
        "--run-once",
        action="store_true",
        help="Run livedata_export once and exit (no loop). Useful for cron / Task Scheduler.",
    )
    parser.add_argument(
        "--python",
        default=sys.executable,
        help="Python executable to use when spawning livedata_export.py.",
    )
    # ── livedata_export.py pass-through args ────────────────────────────────
    parser.add_argument(
        "--input-filter",
        default=None,
        help="Forwarded to livedata_export.py: CSV to filter order IDs.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Forwarded to livedata_export.py: custom output path.",
    )
    parser.add_argument(
        "--db-server",   default=None, help="Forwarded: DB server.")
    parser.add_argument(
        "--db-name",     default=None, help="Forwarded: DB name.")
    parser.add_argument(
        "--db-user",     default=None, help="Forwarded: DB user.")
    parser.add_argument(
        "--db-password", default=None, help="Forwarded: DB password.")
    parser.add_argument(
        "--db-driver",   default=None, help="Forwarded: ODBC driver.")

    args = parser.parse_args()

    # Build pass-through arg list for livedata_export.py
    forward: list[str] = []
    if args.input_filter:
        forward += ["--input-filter", args.input_filter]
    if args.output:
        forward += ["--output", args.output]
    if args.db_server:
        forward += ["--db-server", args.db_server]
    if args.db_name:
        forward += ["--db-name", args.db_name]
    if args.db_user:
        forward += ["--db-user", args.db_user]
    if args.db_password:
        forward += ["--db-password", args.db_password]
    if args.db_driver:
        forward += ["--db-driver", args.db_driver]

    if args.run_once:
        code = run_once(python_exe=args.python, livedata_args=forward)
        raise SystemExit(code)
    else:
        run_loop(
            interval_sec=args.interval_sec,
            python_exe=args.python,
            livedata_args=forward,
        )


if __name__ == "__main__":
    main()
