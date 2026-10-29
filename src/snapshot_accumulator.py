"""snapshot_accumulator.py
=========================
Accumulates live machine-state snapshots (captured every 30 min) and derives
a continuous "real" production timeline for the Plan vs Actual view.

File structure — outputs/live_snapshots.json
--------------------------------------------
{
  "sim_ref_dt": "2026-04-28T14:28:46+00:00",   // simulation t=0 (UTC ISO)
  "snapshots": [
    {
      "captured_at": "2026-04-28T14:28:46+00:00",
      "offset_sec":  0.0,                        // (captured_at - sim_ref_dt) seconds
      "machines": [
        {
          "machine_id":     "AGIE 01",
          "line_id":        "Agie1",
          "process_type":   "EDM",
          "activity":       "process",
          "part_number_id": "0433175501",
          "tray_id":        "SN123456",
          "entry_datetime": "2026-04-28T14:00:00",  // absolute ISO when activity started
          "start_sec":      -1720.0                 // (entry_datetime - sim_ref_dt) seconds
        }, ...
      ]
    }, ...
  ]
}

Real timeline derivation
------------------------
For each machine, iterate snapshots sorted by offset_sec.
Detect state changes by comparing (activity, tray_id, entry_datetime).
When a state change is detected at snapshot S_new:
  - emit previous event from prev_start_sec → new_start_sec
Last (ongoing) event extends to min(now_offset_sec, expected_end_sec).
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# File helpers
# ---------------------------------------------------------------------------

def _load(path: Path) -> dict:
    if not path.exists():
        return {"sim_ref_dt": None, "snapshots": []}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"sim_ref_dt": None, "snapshots": []}


def _save(data: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def reset(path: Path, sim_ref_dt: datetime) -> None:
    """Wipe all snapshots and record the new simulation reference time."""
    _save({"sim_ref_dt": sim_ref_dt.isoformat(), "snapshots": []}, path)


def append(path: Path, machines_raw: list[dict], sim_ref_dt: datetime) -> None:
    """
    Append one snapshot entry built from the current machine states.

    Parameters
    ----------
    path : Path
        Output file path (outputs/live_snapshots.json).
    machines_raw : list[dict]
        The ``machines`` list returned by ``fetch_current_state()``.
        Each entry has:  machine_id, line_id, process_type, machine_type,
                         state: { activity, part_number_id, tray_id, entry_datetime, ... }
    sim_ref_dt : datetime
        The simulation reference time (t=0).  Used to compute start_sec offsets.
        Must be timezone-aware (UTC).
    """
    now_utc = datetime.now(timezone.utc)
    offset_sec = (now_utc - sim_ref_dt).total_seconds()

    entries: list[dict] = []
    for m in machines_raw:
        mid      = m.get("machine_id", "")
        line_id  = m.get("line_id", "Unknown")
        ptype    = m.get("process_type", "EDM")
        state    = m.get("state") or {}
        activity = state.get("activity") or "idle"
        pn       = state.get("part_number_id") or None
        tray_id  = state.get("tray_id") or None
        entry_iso = state.get("entry_datetime") or None  # absolute ISO when activity started

        # Convert entry_datetime → start_sec (seconds from sim_ref_dt)
        start_sec: float | None = None
        if entry_iso:
            try:
                entry_dt = datetime.fromisoformat(entry_iso)
                if entry_dt.tzinfo is None:
                    # DB timestamps are naive local (America/Sao_Paulo)
                    from zoneinfo import ZoneInfo
                    entry_dt = entry_dt.replace(tzinfo=ZoneInfo("America/Sao_Paulo"))
                start_sec = (entry_dt - sim_ref_dt).total_seconds()
            except Exception:
                pass

        entries.append({
            "machine_id":     mid,
            "line_id":        line_id,
            "process_type":   ptype,
            "activity":       activity,
            "part_number_id": pn,
            "tray_id":        tray_id,
            "entry_datetime": entry_iso,
            "start_sec":      round(start_sec, 2) if start_sec is not None else None,
        })

    data = _load(path)
    data.setdefault("snapshots", []).append({
        "captured_at": now_utc.isoformat(),
        "offset_sec":  round(offset_sec, 2),
        "machines":    entries,
    })
    _save(data, path)


# ---------------------------------------------------------------------------
# Timeline derivation
# ---------------------------------------------------------------------------

def build_real_timeline(path: Path, now_offset_sec: float) -> list[dict[str, Any]]:
    """
    Derive a list of TimelineEvent dicts from the accumulated snapshots.

    Returns the same schema as dashboard.json timeline entries so the
    frontend Dashboard can display them in the "Real" row without changes:
      { machine_id, process_type, line_id, tray_id, order_id,
        part_number_id, product_type, pieces, activity, start_sec, end_sec }
    """
    data = _load(path)
    snapshots = sorted(data.get("snapshots", []), key=lambda s: s.get("offset_sec", 0.0))
    if not snapshots:
        return []

    # Maximum gap (seconds) between the last snapshot and "now" before we
    # consider the server to have been down.  If this threshold is exceeded we
    # close all open segments at the last snapshot's offset instead of `now`,
    # avoiding phantom multi-hour bars after a crash/restart.
    # Value = 2.5 × refresh interval (1800 s) = 4500 s ≈ 75 min.
    _STALE_GAP_SEC = 4500.0

    last_snapshot_offset = snapshots[-1].get("offset_sec", 0.0)
    gap_to_now = max(0.0, now_offset_sec - last_snapshot_offset)
    # If the server was down for longer than the stale threshold, cap the
    # "open" end of all segments at the last known snapshot offset.  This
    # prevents a 8-hour crash from being rendered as "8h in process".
    open_end_sec = last_snapshot_offset if gap_to_now > _STALE_GAP_SEC else now_offset_sec

    # Build per-machine history: list of (offset_sec, entry_dict)
    machine_history: dict[str, list[tuple[float, dict]]] = {}
    for snap in snapshots:
        off = snap.get("offset_sec", 0.0)
        for m in snap.get("machines", []):
            mid = m.get("machine_id", "")
            if not mid:
                continue
            machine_history.setdefault(mid, []).append((off, m))

    events: list[dict] = []

    def _emit_segment(
        events: list,
        machine_id: str,
        ptype: str,
        line: str,
        tray: Any,
        pn: str | None,
        activity: str | None,
        start_sec: float,
        end_sec: float,
    ) -> None:
        """Append one timeline event if the segment has positive duration.

        All values are passed explicitly — no closure over mutable loop
        variables, so this is safe to call at any point in time.
        """
        if activity is None or end_sec <= start_sec:
            return
        events.append({
            "machine_id":     machine_id,
            "process_type":   ptype,
            "line_id":        line,
            "tray_id":        tray,
            "order_id":       None,
            "part_number_id": pn,
            "product_type":   pn,
            "pieces":         0,
            "activity":       activity,
            "start_sec":      round(start_sec, 2),
            "end_sec":        round(end_sec, 2),
        })

    for mid, history in machine_history.items():
        history.sort(key=lambda x: x[0])

        # We track the "current segment" being built:
        # cur_activity, cur_pn, cur_tray, cur_entry_iso, cur_start_sec, cur_line, cur_ptype
        cur_activity:   str | None = None
        cur_pn:         str | None = None
        cur_tray:       Any        = None
        cur_entry_iso:  str | None = None
        cur_start_sec:  float      = 0.0
        cur_line:       str        = "Unknown"
        cur_ptype:      str        = "EDM"

        for snap_offset, m in history:
            activity  = m.get("activity") or "idle"
            pn        = m.get("part_number_id")
            tray_id   = m.get("tray_id")
            entry_iso = m.get("entry_datetime")
            start_sec = m.get("start_sec")  # may be None for first snap before sim ran
            line_id   = m.get("line_id", "Unknown")
            ptype     = m.get("process_type", "EDM")

            # Fallback: if start_sec unknown, use the snapshot offset itself
            if start_sec is None:
                start_sec = snap_offset

            if cur_activity is None:
                # First observation — initialise segment
                cur_activity  = activity
                cur_pn        = pn
                cur_tray      = tray_id
                cur_entry_iso = entry_iso
                cur_start_sec = start_sec
                cur_line      = line_id
                cur_ptype     = ptype
                continue

            # Detect state change: different activity OR different tray/entry
            state_changed = (
                activity != cur_activity
                or tray_id != cur_tray
                or entry_iso != cur_entry_iso
            )

            if state_changed:
                # Previous segment ended when the new one started
                _emit_segment(events, mid, cur_ptype, cur_line, cur_tray, cur_pn,
                              cur_activity, cur_start_sec, start_sec)
                cur_activity  = activity
                cur_pn        = pn
                cur_tray      = tray_id
                cur_entry_iso = entry_iso
                cur_start_sec = start_sec
                cur_line      = line_id
                cur_ptype     = ptype
            # else: same segment, nothing to do

        # Close the last (ongoing) segment.
        # Use open_end_sec (which equals last_snapshot_offset when a stale gap
        # was detected) so that a server crash does not produce phantom bars
        # extending to the present moment.
        _emit_segment(events, mid, cur_ptype, cur_line, cur_tray, cur_pn,
                      cur_activity, cur_start_sec, open_end_sec)

    return events


# ---------------------------------------------------------------------------
# Export history.json from accumulated snapshots
# ---------------------------------------------------------------------------

def export_history_from_snapshots(
    snapshots_path: Path,
    history_output: Path,
    now_offset_sec: float,
    sim_ref_dt: datetime,
    mirror_path: Path | None = None,
) -> int:
    """
    Rebuild history.json from accumulated live snapshots.

    Returns the number of timeline events written.
    """
    timeline = build_real_timeline(snapshots_path, now_offset_sec)

    payload = {
        "metadata": {
            "generated_at":       datetime.now(timezone.utc).isoformat(),
            "description":        "Real production timeline derived from live snapshots",
            "snapshot_time_sec":  round(now_offset_sec, 2),
            "snapshot_time_label": _sec_to_hhmm(now_offset_sec, sim_ref_dt),
            "facility_timezone":  "America/Sao_Paulo",
        },
        "timeline": timeline,
    }

    history_output.parent.mkdir(parents=True, exist_ok=True)
    history_output.write_text(
        json.dumps(payload, indent=2, default=str),
        encoding="utf-8",
    )

    if mirror_path:
        mirror_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(history_output, mirror_path)

    return len(timeline)


def _sec_to_hhmm(offset_sec: float, sim_ref_dt: datetime) -> str:
    try:
        from zoneinfo import ZoneInfo
        ts = sim_ref_dt + __import__("datetime").timedelta(seconds=offset_sec)
        ts_local = ts.astimezone(ZoneInfo("America/Sao_Paulo"))
        return ts_local.strftime("%H:%M")
    except Exception:
        h = int(offset_sec // 3600) % 24
        m = int((offset_sec % 3600) // 60)
        return f"{h:02d}:{m:02d}"
