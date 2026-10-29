"""Export real production history as a timeline from the DB.

Generates ``history.json`` with the same event format as ``dashboard.json``
timeline entries, enabling Plan vs Actual comparison in the Dash        hours before ref_dt to search for Entradas.  Runs that started before
        the simulation but were still in progress at t=0 appear starting at
        start_sec=0 (clamped in _build_timeline).  Default 48 h covers even
        multi-day EDM cycles and machines running since the previous shift.d.

Each completed EDM run (Entrada→Saída pair on stage '03 - Agie') becomes a
set of sub-events mirroring the simulation phases:

    changeover  – first N seconds after Entrada (if switching from a different PN)
    test        – next M seconds (release/first-piece check)
    process     – remaining duration until Saída

The changeover and test durations are resolved from ``changeover_data.csv``
using the same ``ChangeoverLookup`` logic as the Live View and the simulator.
If no entry exists for a given pair, the same defaults used by the Live View
are applied (changeover = 20 min, test = configurable, default 60 min).

The gap between a previous Saída and the current Entrada on the same machine
is emitted as a ``changeover`` event (machine was between runs — could be idle
or physical changeover prep; we cannot distinguish from the DB data alone).

In-progress trays (Entrada without Saída) produce running sub-events whose
combined end is clamped to *now*.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import text

log = logging.getLogger(__name__)

# Defaults used when a PN pair is not found in changeover_data.csv —
# identical to the defaults in data_gathering.py::_get_activity().
_CO_DEFAULT_SEC   = 20 * 60   # 20 min  (changeover / setup)
_TEST_DEFAULT_SEC = 60 * 60   # 60 min  (test / release) — overridable per call


# ---------------------------------------------------------------------------
# Changeover lookup loader
# ---------------------------------------------------------------------------

def _load_changeover_lookup():
    """Load ChangeoverLookup from changeover_data.csv, return None on failure."""
    try:
        from pathlib import Path as _P
        from simulation import load_changeovers
        csv_path = _P(__file__).resolve().parent.parent / "data" / "changeover_data.csv"
        if csv_path.exists():
            return load_changeovers(csv_path)
    except Exception as exc:
        log.debug("Could not load changeover CSV: %s", exc)
    return None


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _machine_to_line(machine_id: str) -> str:
    """AGIE 01-10 → Agie1, … AGIE 41-50 → Agie5."""
    parts = machine_id.strip().split()
    if len(parts) >= 2 and parts[0].upper() == "AGIE":
        try:
            num = int(parts[-1])
            corridor = (num - 1) // 10 + 1
            if 1 <= corridor <= 5:
                return f"Agie{corridor}"
        except ValueError:
            pass
    return "Unknown"


def _split_run_phases(
    start_sec: float,
    end_sec: float,
    prev_pn: str | None,
    curr_pn: str,
    changeover_lookup,
    default_test_sec: float,
    base_event: dict,
) -> list[dict[str, Any]]:
    """Split one Entrada→Saída span into changeover / test / process sub-events.

    Uses the same resolution logic as ``data_gathering.py::_get_activity()``:

    1.  Look up durations for the ``prev_pn → curr_pn`` transition in the
        ``ChangeoverLookup``.
    2.  If the lookup returns (0, 0) — no entry for the pair — fall back to
        ``_CO_DEFAULT_SEC`` / ``default_test_sec``.
    3.  Only apply changeover/test when ``prev_pn != curr_pn`` (same PN
        continuing = no setup needed).
    4.  Sub-intervals are clamped so their total never exceeds the run span.
        If the span is shorter than changeover + test (the machine is still in
        setup at snapshot time) only the phases that fit are emitted.

    Parameters
    ----------
    start_sec, end_sec : float
        Run boundaries in simulation-offset seconds.
    prev_pn : str | None
        Part number of the previous completed run on the same machine.
        ``None`` → first ever run, no changeover.
    curr_pn : str
        Part number of this run.
    changeover_lookup : ChangeoverLookup | None
        Loaded from ``changeover_data.csv``.  ``None`` → no split, emit
        entire span as ``process``.
    default_test_sec : float
        Fallback test duration when the CSV has no entry for this pair.
    base_event : dict
        Fields shared by all sub-events (machine_id, line_id, etc.).
        ``activity``, ``start_sec``, and ``end_sec`` are overwritten.

    Returns
    -------
    List of 1–3 event dicts (changeover, test, process — each only if > 0 s).
    """
    events: list[dict] = []
    co_sec   = 0.0
    test_sec = 0.0

    if changeover_lookup and prev_pn and curr_pn and prev_pn != curr_pn:
        durations = changeover_lookup.get(prev_pn, curr_pn)
        co_sec   = durations.edm_min * 60.0
        test_sec = durations.edm_test_min * 60.0
        # Fall back to defaults when the CSV has no entry for this pair
        if co_sec == 0.0 and test_sec == 0.0:
            co_sec   = _CO_DEFAULT_SEC
            test_sec = default_test_sec

    cursor = start_sec

    # ── Changeover phase ──────────────────────────────────────────────────
    if co_sec > 0.0:
        co_end = min(cursor + co_sec, end_sec)
        if co_end > cursor:
            events.append({
                **base_event,
                "activity":  "changeover",
                "start_sec": round(cursor, 2),
                "end_sec":   round(co_end, 2),
            })
            cursor = co_end

    # ── Test phase ────────────────────────────────────────────────────────
    if test_sec > 0.0 and cursor < end_sec:
        test_end = min(cursor + test_sec, end_sec)
        if test_end > cursor:
            events.append({
                **base_event,
                "activity":  "test",
                "start_sec": round(cursor, 2),
                "end_sec":   round(test_end, 2),
            })
            cursor = test_end

    # ── Process phase (remainder) ─────────────────────────────────────────
    if cursor < end_sec:
        events.append({
            **base_event,
            "activity":  "process",
            "start_sec": round(cursor, 2),
            "end_sec":   round(end_sec, 2),
        })

    # Safety: if no phases were emitted (no changeover lookup, same PN, etc.)
    # emit the full span as process.
    if not events:
        events.append({
            **base_event,
            "activity":  "process",
            "start_sec": round(start_sec, 2),
            "end_sec":   round(end_sec, 2),
        })

    return events


# ---------------------------------------------------------------------------
# DB query
# ---------------------------------------------------------------------------

def fetch_edm_timeline(
    engine,
    ref_dt: datetime,
    horizon_hours: int = 24,
    lookback_hours: int = 48,
) -> pd.DataFrame:
    """Fetch completed + in-progress EDM runs within a time window.

    Parameters
    ----------
    engine : sqlalchemy Engine
    ref_dt : datetime
        Reference start (simulation launch time, facility-local naive).
    horizon_hours : int
        Window length in hours (forward from ref_dt).
    lookback_hours : int
        Hours before ref_dt to search for Entradas.  Runs that started before
        the simulation but were still in progress at t=0 appear starting at
        start_sec=0 (clamped in _build_timeline).  Default 8 h covers even the
        longest typical EDM cycle.

    Returns
    -------
    DataFrame with columns:
        machine_id, part_number_id, serial_number, quantity,
        entrada_date, saida_date (NaT for in-progress), actual_sec
    """
    lookback_start = (ref_dt - timedelta(hours=lookback_hours)).strftime("%Y-%m-%d %H:%M:%S")
    cutoff_end     = (ref_dt + timedelta(hours=horizon_hours)).strftime("%Y-%m-%d %H:%M:%S")

    query = f"""
    WITH entradas AS (
        SELECT
            MACHINE         AS machine_id,
            PROCESS         AS machine_type,
            TYPE_NUMBER     AS part_number_id,
            SERIAL_NUMBER   AS serial_number,
            QUANTITY        AS quantity,
            DATE            AS entrada_date
        FROM silver_quality.b2b_machine_status
        WHERE STATE    = 'Entrada'
          AND STAGE    = '03 - Agie'
          AND MACHINE <> 'AGIE UNKNOWN'
          AND MACHINE IS NOT NULL
          AND QUANTITY <> 0
          AND SERIAL_NUMBER NOT LIKE '%[a-z]%'
          AND DATE    >= '{lookback_start}'
          AND DATE    <  '{cutoff_end}'
    ),
    -- For each Entrada, find the FIRST Saída that occurred AFTER it on the same
    -- serial — prevents pairing with a saída from a different historical run.
    -- No lower-bound date filter on saidas: a Saída from the lookback period is
    -- still needed to correctly close pre-existing runs (their end_sec will be
    -- negative and filtered out by _build_timeline via end_sec <= 0).
    -- NOTE: Saída records do NOT have MACHINE or STAGE populated in the DB —
    -- the operator only registers the serial number on exit. The join must rely
    -- solely on SERIAL_NUMBER + STATE + DATE order, never on STAGE or MACHINE.
    first_saida_per_run AS (
        SELECT
            e.serial_number,
            e.entrada_date,
            MIN(s.DATE) AS saida_date
        FROM entradas e
        LEFT JOIN silver_quality.b2b_machine_status s
            ON  s.SERIAL_NUMBER = e.serial_number
            AND s.STATE         = 'Sa\u00edda'
            AND s.QUANTITY     <> 0
            AND s.DATE          > e.entrada_date
        GROUP BY e.serial_number, e.entrada_date
    ),
    -- Implicit end: when a machine receives a NEW Entrada, the previous tray
    -- has physically left the machine even if the operator forgot to scan the
    -- Saída. This closes "ghost" open runs that would otherwise extend to now.
    -- Used only as fallback when no explicit Saída was registered.
    next_entrada_on_machine AS (
        SELECT
            e1.serial_number,
            e1.entrada_date,
            MIN(e2.entrada_date) AS implicit_saida_date
        FROM entradas e1
        JOIN entradas e2
            ON  e2.machine_id    = e1.machine_id
            AND e2.entrada_date  > e1.entrada_date
        GROUP BY e1.serial_number, e1.entrada_date
    )
    SELECT
        e.machine_id,
        e.machine_type,
        e.part_number_id,
        e.serial_number,
        e.quantity,
        e.entrada_date,
        -- Prefer explicit Saída; fall back to next Entrada time (implicit exit)
        COALESCE(sa.saida_date, nm.implicit_saida_date) AS saida_date,
        DATEDIFF(SECOND, e.entrada_date,
                 COALESCE(sa.saida_date, nm.implicit_saida_date, GETDATE())) AS actual_sec
    FROM entradas e
    LEFT JOIN first_saida_per_run sa
        ON  sa.serial_number = e.serial_number
        AND sa.entrada_date  = e.entrada_date
    LEFT JOIN next_entrada_on_machine nm
        ON  nm.serial_number = e.serial_number
        AND nm.entrada_date  = e.entrada_date
    -- Ghost timeout: drop stale open entries with no closure evidence and older
    -- than 24 h.  Mirrors the DATEADD(HOUR, -24, GETDATE()) guard in
    -- fetch_machine_status so Live View and History agree on ghost handling.
    WHERE (
        sa.saida_date            IS NOT NULL                        -- explicit Saída ✓
        OR nm.implicit_saida_date IS NOT NULL                       -- next Entrada on same machine ✓
        OR e.entrada_date        >= DATEADD(HOUR, -24, GETDATE())   -- recent in-progress (within 24 h) ✓
    )
    ORDER BY e.machine_id, e.entrada_date
    """
    return pd.read_sql(text(query), engine)


# ---------------------------------------------------------------------------
# timeline builder
# ---------------------------------------------------------------------------

def _build_timeline(
    df: pd.DataFrame,
    ref_dt: datetime,
    horizon_sec: float,
    now_offset_sec: float,
    changeover_lookup=None,
    default_test_sec: float = _TEST_DEFAULT_SEC,
) -> list[dict[str, Any]]:
    """Convert DB rows into timeline events (same schema as dashboard.json).

    Each Entrada→Saída span is split into up to three sub-events:
      - ``changeover``  — initial setup duration (if switching PN)
      - ``test``        — release / first-piece check duration
      - ``process``     — productive processing (remainder)

    The gap between consecutive trays on the same machine is emitted as a
    ``changeover`` event (machine idle between runs).

    ``now_offset_sec`` is the current wall-clock offset from ``ref_dt``.
    In-progress trays (no Saída) get their end clamped to this value.
    """

    events: list[dict[str, Any]] = []
    if df.empty:
        return events

    # Group by machine_id and sort by entrada_date
    grouped = df.sort_values("entrada_date").groupby("machine_id", sort=True)

    for machine_id, rows in grouped:
        line_id = _machine_to_line(str(machine_id))
        prev_end_sec: float | None = None
        prev_pn: str | None = None

        for _, row in rows.iterrows():
            entrada: datetime = pd.Timestamp(row["entrada_date"]).to_pydatetime()
            saida = row["saida_date"]
            pn = str(row["part_number_id"]) if pd.notna(row["part_number_id"]) else ""
            qty = int(row["quantity"]) if pd.notna(row["quantity"]) else 0

            # raw_start_sec < 0 means the run started BEFORE the simulation
            # reference time (it was already in progress at t=0).
            raw_start_sec = (entrada - ref_dt).total_seconds()
            started_before_ref = raw_start_sec < 0
            start_sec = max(0.0, raw_start_sec)

            if pd.notna(saida):
                saida_dt: datetime = pd.Timestamp(saida).to_pydatetime()
                end_sec = (saida_dt - ref_dt).total_seconds()
            else:
                # In-progress: end = current moment (never beyond now)
                end_sec = now_offset_sec

            # Clamp: nothing beyond the horizon, nothing beyond now
            end_sec   = min(end_sec,   horizon_sec, now_offset_sec)
            start_sec = min(start_sec, now_offset_sec)

            if start_sec >= horizon_sec or end_sec <= 0 or end_sec <= start_sec:
                prev_end_sec = end_sec
                prev_pn = pn
                continue

            # ── Inter-tray gap → emitted as changeover (machine was between runs) ──
            # Only emit gap if both runs started AFTER ref_dt — gaps before t=0
            # are in the lookback period and not meaningful on the visible timeline.
            if prev_end_sec is not None and start_sec > prev_end_sec + 1 and not started_before_ref:
                events.append({
                    "machine_id":     str(machine_id),
                    "process_type":   "EDM",
                    "line_id":        line_id,
                    "tray_id":        None,
                    "order_id":       None,
                    "part_number_id": pn,
                    "product_type":   pn,
                    "pieces":         0,
                    "activity":       "changeover",
                    "start_sec":      round(prev_end_sec, 2),
                    "end_sec":        round(start_sec, 2),
                })

            # ── Intra-tray span → split into changeover / test / process ──────────
            # If the run started BEFORE ref_dt it was already in process at t=0 —
            # changeover and test happened in the past and must NOT be re-applied.
            base = {
                "machine_id":     str(machine_id),
                "process_type":   "EDM",
                "line_id":        line_id,
                "tray_id":        None,
                "order_id":       None,
                "part_number_id": pn,
                "product_type":   pn,
                "pieces":         qty,
            }
            if started_before_ref:
                # Emit entire visible span as process — no setup phases
                events.append({
                    **base,
                    "activity":  "process",
                    "start_sec": round(start_sec, 2),
                    "end_sec":   round(end_sec, 2),
                })
            else:
                events.extend(
                    _split_run_phases(
                        start_sec=start_sec,
                        end_sec=end_sec,
                        prev_pn=prev_pn,
                        curr_pn=pn,
                        changeover_lookup=changeover_lookup,
                        default_test_sec=default_test_sec,
                        base_event=base,
                    )
                )

            prev_end_sec = end_sec
            prev_pn = pn

    return events


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------

def export_history_json(
    engine,
    ref_dt: datetime,
    output: Path,
    horizon_hours: int = 24,
    default_test_min: int = 60,
) -> Path:
    """Generate ``history.json`` with real EDM production events.

    Parameters
    ----------
    engine : sqlalchemy Engine
    ref_dt : datetime
        The simulation start time (may be UTC-aware). Converted to facility
        local time (America/Sao_Paulo) for offset calculations, since DB
        timestamps are naive in that timezone.
    output : Path
        Where to write the JSON file.
    horizon_hours : int
        Window to query (default 24 h, matching the simulation horizon).
    default_test_min : int
        Fallback test/release duration in minutes when a PN pair is not found
        in ``changeover_data.csv``.  Mirrors the ``default_test_min`` parameter
        used by ``fetch_current_state()`` in the Live View.

    Returns
    -------
    Path to the written file.
    """
    from zoneinfo import ZoneInfo

    facility_tz = ZoneInfo("America/Sao_Paulo")
    # Convert to facility-local and strip tzinfo so diffs with naive DB dates work
    if ref_dt.tzinfo is not None:
        ref_local = ref_dt.astimezone(facility_tz).replace(tzinfo=None)
    else:
        ref_local = ref_dt

    horizon_sec = horizon_hours * 3600.0

    # Load changeover lookup once — shared across all machines
    changeover_lookup = _load_changeover_lookup()
    if changeover_lookup:
        log.info("Changeover lookup loaded (%d pairs).", len(changeover_lookup.mapping))
    else:
        log.warning("Changeover CSV not found — intra-tray spans emitted as plain 'process'.")

    log.info("Fetching EDM timeline from DB (ref=%s, horizon=%dh)…", ref_local.isoformat(), horizon_hours)
    df = fetch_edm_timeline(engine, ref_local, horizon_hours)
    log.info("  → %d DB rows returned.", len(df))

    now_offset = min(horizon_sec, max(0.0, (datetime.now() - ref_local).total_seconds()))
    timeline = _build_timeline(
        df,
        ref_local,
        horizon_sec,
        now_offset,
        changeover_lookup=changeover_lookup,
        default_test_sec=default_test_min * 60.0,
    )

    payload = {
        "metadata": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "description": "Real EDM production events from DB",
            "snapshot_time_sec": round(now_offset, 2),
            "snapshot_time_label": ref_local.strftime("%H:%M"),
            "facility_timezone": "America/Sao_Paulo",
        },
        "timeline": timeline,
    }

    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, default=str)

    log.info("History JSON written to %s (%d events).", output, len(timeline))
    return output
