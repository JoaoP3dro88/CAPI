"""
snapshot_now.py
===============
Salva uma "foto" completa do estado atual da produção, pronta para alimentar a
simulação ou o frontend.

O que é gerado
--------------
snapshot_now_output.json  — payload completo com:

  metadata          → horário da geração
  summary           → contagens resumidas (máquina    # Injetar fallbacks no cycle_map e cycle_times para que o initial_state
    # e a simulação encontrem os ciclos dos PNs que não tinham cadastro original
    for m in machines:
        state = m.get("state") or {}
        if state.get("activity") != "process":
            continue
        fallback_pn   = state.get("cycle_fallback_pn")
        fallback_secs = state.get("cycle_fallback_secs")
        pnid          = state.get("part_number_id") or ""
        mtype         = m.get("machine_type") or ""
        if fallback_pn and fallback_secs and pnid and mtype:
            cycle_map.setdefault(mtype, {})[pnid] = fallback_secs
            cycle_times[(mtype, pnid)] = fallback_secsrdens…)
  machines          → 50 máquinas AGIE com estado real:
                        activity, part_number_id, entry_datetime,
                        start_sec, end_sec, elapsed_sec, percent_complete
  laser_summary     → bandejas aguardando/concluídas na Laser por PN
  stock_levels      → buffer Laser→AGIE (bandejas na fila)
  finished_goods    → bandejas que saíram das AGIEs (supermarket de saída)
  he_market         → TypeNumbers destinados ao mercado HE
  packaging_market  → TypeNumbers destinados a Packaging
  backlog_orders    → ordens abertas
  produced_orders   → ordens produzidas no turno atual
  orders_by_id      → catálogo completo de ordens
  hard_stage_market → estoque Hard Stage agrupado por Body
  restrictions      → tabela completa PN × máquina × tempo de ciclo (seg/peça)
  cycle_time_map    → lookup rápido: "machine_type|part_number_id" → seg/peça
  initial_state     → objeto pronto para passar à simulação:
                        completed, edm_stock, machines (com percent_complete)

Usage
-----
  python snapshot_now.py
"""

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from data_gathering import (
    get_engine,
    fetch_current_state,
    fetch_orders,
    fetch_hard_stage_market,
    fetch_restrictions,
    fetch_data_last_updated,
)
from cycle_time_utils import lookup_cycle_time


# ── helpers ──────────────────────────────────────────────────────────────────

def _clean(obj):
    """Recursivamente torna obj JSON-serializável."""
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_clean(i) for i in obj]
    try:
        json.dumps(obj)
        return obj
    except (TypeError, ValueError):
        return str(obj) if obj is not None else None


def _build_orders_by_id(df) -> dict:
    result = {}
    for row in df.to_dict("records"):
        oid  = str(row.get("order_id")       or "").strip()
        pnid = str(row.get("part_number_id") or "").strip()
        qty  = row.get("quantity")
        dd   = row.get("due_date")
        if not oid:
            continue
        result[oid] = {
            "order_id":       oid,
            "part_number_id": pnid or None,
            "quantity":       int(float(qty)) if qty is not None else None,
            "due_date":       str(dd) if dd else None,
        }
    return result


def _build_hard_stage_market(df) -> list:
    bodies: dict[str, dict] = {}
    for row in df.to_dict("records"):
        pnid = str(row.get("part_number_id") or "").strip()
        body = str(row.get("body_number_id") or "").strip() or "__no_body__"
        qty  = row.get("qtty_hardstage", 0)
        if body not in bodies:
            bodies[body] = {
                "body_partnumber": body if body != "__no_body__" else None,
                "body_qtty":       0,
                "nozzles":         [],
            }
        try:
            bodies[body]["body_qtty"] += int(float(qty)) if qty == qty else 0
        except (TypeError, ValueError):
            pass
        if pnid:
            bodies[body]["nozzles"].append(pnid)
    return sorted(bodies.values(), key=lambda x: x["body_partnumber"] or "")


def _build_restrictions_and_cycle_map(df_restr):
    """
    Retorna:
      restrictions  – lista de dicts {part_number_id, machine_type, process_type,
                                       process_time_seconds_per_part}
      cycle_map     – dict  machine_type → {part_number_id → secs_per_part}
      cycle_times   – dict  (machine_type, part_number_id) → secs  (formato lookup_cycle_time)
    """
    restrictions = []
    cycle_map: dict[str, dict[str, float]] = {}
    cycle_times: dict[tuple, float] = {}

    for row in df_restr.to_dict("records"):
        pnid     = str(row.get("part_number_id")              or "").strip()
        mtype    = str(row.get("machine_type")                or "").strip()
        ptype    = str(row.get("process_type")                or "").strip()
        secs_raw = row.get("process_time_seconds_per_part")
        secs     = float(secs_raw) if secs_raw is not None else None

        restrictions.append({
            "part_number_id":               pnid,
            "machine_type":                 mtype,
            "process_type":                 ptype,
            "process_time_seconds_per_part": secs,
        })

        # Só entra nos maps se o tempo for válido (> 0)
        if pnid and mtype and secs is not None and secs > 0:
            cycle_map.setdefault(mtype, {})[pnid] = secs
            cycle_times[(mtype, pnid)] = secs

    return restrictions, cycle_map, cycle_times


def _shift_start(now: datetime) -> datetime:
    """Retorna o início do turno atual (turnos: 07h, 15h, 23h)."""
    h = now.hour
    if 7 <= h < 15:
        return now.replace(hour=7,  minute=0, second=0, microsecond=0)
    elif 15 <= h < 23:
        return now.replace(hour=15, minute=0, second=0, microsecond=0)
    elif h >= 23:
        return now.replace(hour=23, minute=0, second=0, microsecond=0)
    else:  # 0 <= h < 7  → turno noturno começou ontem às 23h
        return (now - timedelta(days=1)).replace(hour=23, minute=0, second=0, microsecond=0)


def _enrich_machines(machines: list, cycle_times: dict[tuple, float], now: datetime) -> list:
    """
    Preenche start_sec, end_sec e percent_complete em cada máquina em processo.

    - start_sec / end_sec são segundos desde o início do turno atual
    - percent_complete é 0.0–1.0
    - Se o PN não tem ciclo cadastrado, usa lookup_cycle_time (fallback PN mais próximo)
      e registra cycle_fallback_pn + cycle_fallback_secs no state
    """
    shift_s = _shift_start(now)
    enriched = []

    for m in machines:
        state = dict(m.get("state") or {})

        if state.get("activity") == "process":
            pnid      = state.get("part_number_id") or ""
            mtype     = m.get("machine_type") or ""
            pieces    = state.get("pieces") or 0
            elapsed   = state.get("elapsed_sec") or 0.0
            entry_iso = state.get("entry_datetime")

            # Tempo de ciclo via lookup centralizado (com fallback automático)
            secs_per_part: float | None = None
            fallback_pn: str | None = None

            if pnid and mtype:
                try:
                    secs_per_part, fallback_pn = lookup_cycle_time(
                        pnid, mtype, cycle_times, use_fallback=True
                    )
                except ValueError:
                    secs_per_part = None

            if fallback_pn:
                state["cycle_fallback_pn"]   = fallback_pn
                state["cycle_fallback_secs"] = secs_per_part
            else:
                state["cycle_fallback_pn"]   = None
                state["cycle_fallback_secs"] = None

            # start_sec = segundos desde o início do turno até a Entrada
            start_sec: float | None = None
            if entry_iso:
                try:
                    entry_dt = datetime.fromisoformat(entry_iso)
                    start_sec = max(0.0, (entry_dt - shift_s).total_seconds())
                except Exception:
                    pass

            # Tempo total do processo e end_sec
            end_sec: float | None = None
            total_process_sec: float | None = None
            if secs_per_part is not None and pieces > 0:
                total_process_sec = secs_per_part * pieces
                if start_sec is not None:
                    end_sec = start_sec + total_process_sec

            # percent_complete
            percent_complete: float | None = None
            if total_process_sec and total_process_sec > 0 and elapsed is not None:
                percent_complete = max(0.0, min(1.0, elapsed / total_process_sec))

            state["start_sec"]        = round(start_sec,        2) if start_sec        is not None else None
            state["end_sec"]          = round(end_sec,          2) if end_sec          is not None else None
            state["percent_complete"] = round(percent_complete, 4) if percent_complete is not None else None

        enriched.append({**m, "state": state})

    return enriched


def _build_initial_state(machines: list, stock_levels: list) -> dict:
    """
    Monta o dicionário initial_state no formato que ProductionSimulation espera:

    {
      "completed": {"trays": 0, "parts": 0},
      "edm_stock": {
        "Agie1": [{"order_id": null, "part_number_id": "...", "trays": 1}, ...]
      },
      "machines": [
        {"machine_id": "AGIE 3", "order_id": null,
         "part_number_id": "...", "percent_complete": 0.35}
      ]
    }
    """
    # Máquinas em processo ou em setup (changeover / test)
    machines_state = []
    for m in machines:
        state = m.get("state") or {}
        activity = state.get("activity") or ""
        pnid = state.get("part_number_id") or ""
        if not pnid:
            continue

        if activity == "process":
            pct  = state.get("percent_complete")
            if not pnid:
                continue

            # Determine remaining_sec for the simulation seed.
            # When pct < 1.0: the cycle estimate is reliable — use it directly.
            # When pct >= 1.0: the machine has overrun its estimated cycle (common
            #   when cycle times come from a fallback PN). We don't know the true
            #   remaining time, so we use one full tray cycle as a conservative
            #   estimate — the machine is still running and will finish "sometime
            #   soon". This avoids the old min(0.99) approach which gave only
            #   ~1% of cycle (~90s), and the percent_complete>=1.0 guard which
            #   skipped the machine and assigned a wrong PN.
            start_sec_val   = state.get("start_sec")
            end_sec_val     = state.get("end_sec")
            total_cycle_sec = (
                (end_sec_val - start_sec_val)
                if (end_sec_val is not None and start_sec_val is not None)
                else None
            )
            if pct is not None and pct >= 1.0 and total_cycle_sec and total_cycle_sec > 0:
                # Overrun: seed with percent_complete=0.5 so remaining = 50% of
                # one tray cycle. That's a reasonable middle-ground estimate
                # (e.g. ~75 min for a 2.5h fallback cycle) without being
                # either trivially small or excessively long.
                seed_pct      = 0.5
                remaining_sec = total_cycle_sec * 0.5
            else:
                seed_pct      = min(0.99, pct) if pct is not None else 0.1
                remaining_sec = None  # simulation computes from percent_complete

            entry: dict = {
                "machine_id":       m["machine_id"],
                "order_id":         None,   # não disponível em tempo real
                "part_number_id":   pnid,
                "percent_complete": seed_pct,
            }
            if remaining_sec is not None:
                entry["remaining_sec"] = round(remaining_sec, 1)
            machines_state.append(entry)

        elif activity in ("changeover", "test"):
            # Machine is mid-setup: seed it so the simulation resumes the
            # in-progress phase with only the remaining duration instead of
            # starting a fresh full setup cycle.
            elapsed_sec    = float(state.get("elapsed_sec")    or 0.0)
            changeover_sec = float(state.get("changeover_sec") or 0.0)
            test_sec       = float(state.get("test_sec")       or 0.0)

            if activity == "changeover":
                # Changeover still running; full test phase still ahead.
                remaining_changeover = max(0.0, changeover_sec - elapsed_sec)
                remaining_test       = test_sec  # test hasn't started yet
            else:  # "test"
                # Changeover already done; test phase partially elapsed.
                remaining_changeover = 0.0
                remaining_test       = max(0.0, test_sec - elapsed_sec)

            entry = {
                "machine_id":             m["machine_id"],
                "order_id":               None,
                "part_number_id":         pnid,
                "percent_complete":       0.0,
                "activity":               activity,
                "remaining_changeover_sec": round(remaining_changeover, 1),
                "remaining_test_sec":       round(remaining_test,       1),
            }
            machines_state.append(entry)

    # EDM stock: bandejas do buffer (Laser → AGIE), agrupadas por line_id
    edm_stock: dict[str, list] = {}
    for line in stock_levels:
        lid     = line.get("line_id", "Unknown")
        details = line.get("tray_details", [])
        if lid not in edm_stock:
            edm_stock[lid] = []
        for tray in details:
            pnid = tray.get("part_number_id") or ""
            if not pnid:
                continue
            edm_stock[lid].append({
                "order_id":       None,
                "part_number_id": pnid,
                "trays":          1,
            })

    return {
        "completed": {"trays": 0, "parts": 0},
        "edm_stock": edm_stock,
        "machines":  machines_state,
    }


# ── main ─────────────────────────────────────────────────────────────────────

def main():
    print("Connecting to database...")
    engine = get_engine()
    print("Connected.\n")

    now = datetime.now()

    print("Fetching current state (machines, markets, orders)...")
    snapshot = fetch_current_state(engine)

    print("Fetching orders catalogue...")
    df_orders        = fetch_orders(engine)
    orders_by_id     = _build_orders_by_id(df_orders)

    print("Fetching Hard Stage Market...")
    df_hs            = fetch_hard_stage_market(engine)
    hard_stage_market = _build_hard_stage_market(df_hs)

    print("Fetching restrictions (cycle times por máquina × PN)...")
    df_restr                              = fetch_restrictions(engine)
    restrictions_list, cycle_map, cycle_times = _build_restrictions_and_cycle_map(df_restr)

    # ── campos do snapshot ────────────────────────────────────────────────────
    machines_raw = snapshot.get("machines", [])
    laser        = snapshot.get("laser_summary", [])
    stock        = snapshot.get("stock_levels", [])
    fg           = snapshot.get("finished_goods", [])
    he           = snapshot.get("he_market", [])
    packaging    = snapshot.get("packaging_market", [])
    backlog      = snapshot.get("backlog_orders", [])
    produced     = snapshot.get("produced_orders", [])

    # ── enriquecer máquinas ───────────────────────────────────────────────────
    print("Enriching machine states with start_sec / end_sec / percent_complete...")
    machines = _enrich_machines(machines_raw, cycle_times, now)

    n_running    = sum(1 for m in machines if (m.get("state") or {}).get("activity") == "process")
    n_with_start = sum(1 for m in machines if (m.get("state") or {}).get("start_sec") is not None)
    n_with_pct   = sum(1 for m in machines if (m.get("state") or {}).get("percent_complete") is not None)
    n_fallback   = sum(1 for m in machines if (m.get("state") or {}).get("cycle_fallback_pn") is not None)
    print(f"  Running: {n_running}  |  start_sec: {n_with_start}  |  percent_complete: {n_with_pct}  |  fallback usado: {n_fallback}")

    # Injetar fallbacks no cycle_map para que o initial_state e a simulação
    # encontrem os ciclos dos PNs que não tinham cadastro original
    for m in machines:
        state = m.get("state") or {}
        if state.get("activity") != "process":
            continue
        fallback_pn   = state.get("cycle_fallback_pn")
        fallback_secs = state.get("cycle_fallback_secs")
        pnid          = state.get("part_number_id") or ""
        mtype         = m.get("machine_type") or ""
        if fallback_pn and fallback_secs and pnid and mtype:
            # Registrar o ciclo do PN real usando o valor do fallback
            cycle_map.setdefault(mtype, {})[pnid] = fallback_secs

    # ── initial_state ─────────────────────────────────────────────────────────
    initial_state = _build_initial_state(machines, stock)

    # ── summary por linha ─────────────────────────────────────────────────────
    lines: dict[str, dict] = {}
    for m in machines:
        lid      = m.get("line_id", "Unknown")
        activity = (m.get("state") or {}).get("activity", "idle")
        if lid not in lines:
            lines[lid] = {"line_id": lid, "total": 0, "process": 0, "idle": 0, "inactive": 0}
        lines[lid]["total"] += 1
        lines[lid][activity] = lines[lid].get(activity, 0) + 1

    # ── avisos de fallback ────────────────────────────────────────────────────
    cycle_fallback_warnings = []
    for m in machines:
        state = m.get("state") or {}
        if state.get("cycle_fallback_pn"):
            cycle_fallback_warnings.append({
                "machine_id":       m["machine_id"],
                "machine_type":     m.get("machine_type"),
                "part_number_id":   state.get("part_number_id"),
                "fallback_pn_used": state["cycle_fallback_pn"],
                "fallback_secs":    state["cycle_fallback_secs"],
            })

    # ── assemble ──────────────────────────────────────────────────────────────
    now_utc = datetime.now(timezone.utc)
    data_last_updated = fetch_data_last_updated(engine)

    payload = {
        "metadata": {
            "generated_at":       now_utc.isoformat(),
            "generated_at_local": now.strftime("%Y-%m-%d %H:%M:%S"),
            "data_last_updated":  data_last_updated,
            "shift_start":        _shift_start(now).strftime("%Y-%m-%d %H:%M:%S"),
            "snapshot_type":      "live",
        },
        "summary": {
            "total_machines":            len(machines),
            "machines_in_process":       n_running,
            "machines_idle":             sum(1 for m in machines if (m.get("state") or {}).get("activity") == "idle"),
            "machines_inactive":         sum(1 for m in machines if (m.get("state") or {}).get("activity") == "inactive"),
            "start_sec_resolved":        n_with_start,
            "percent_complete_resolved": n_with_pct,
            "cycle_fallback_count":      n_fallback,
            "lines":                     sorted(lines.values(), key=lambda x: x["line_id"]),
            "buffer_stock_trays":        sum(s.get("trays", 0) for s in stock),
            "buffer_stock_parts":        sum(s.get("parts", 0) for s in stock),
            "finished_goods_trays":      sum(f.get("trays", 0) for f in fg),
            "finished_goods_parts":      sum(f.get("parts", 0) for f in fg),
            "he_market_items":           len(he),
            "packaging_market_items":    len(packaging),
            "open_orders":               len(backlog),
            "produced_orders":           len(produced),
            "hard_stage_bodies":         len(hard_stage_market),
            "hard_stage_nozzles":        sum(len(b.get("nozzles", [])) for b in hard_stage_market),
            "restriction_entries":       len(restrictions_list),
            "initial_state_machines":    len(initial_state["machines"]),
            "initial_state_stock_trays": sum(len(v) for v in initial_state["edm_stock"].values()),
        },
        "cycle_fallback_warnings": cycle_fallback_warnings,
        "machines":          machines,
        "laser_summary":     laser,
        "stock_levels":      stock,
        "finished_goods":    fg,
        "he_market":         he,
        "packaging_market":  packaging,
        "backlog_orders":    backlog,
        "produced_orders":   produced,
        "orders_by_id":      orders_by_id,
        "hard_stage_market": hard_stage_market,
        "restrictions":      restrictions_list,
        # cycle_time_map: "machine_type|part_number_id" → secs_per_part
        "cycle_time_map": {
            f"{mtype}|{pn}": secs
            for mtype, pn_map in cycle_map.items()
            for pn, secs in pn_map.items()
        },
        "initial_state": initial_state,
        # List of machine_ids currently inactive (no active tray, no recent OEE history).
        # Used by api.py to automatically exclude these machines from the simulation.
        "inactive_machines": [
            m["machine_id"]
            for m in machines
            if (m.get("state") or {}).get("activity") == "inactive"
        ],
    }

    payload = _clean(payload)

    out_path = Path(__file__).parent / "snapshot_now_output.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print(f"\nSnapshot saved to: {out_path}")

    s = payload["summary"]
    print("\n── Summary ────────────────────────────────────────────────────────")
    print(f"  Generated at         : {payload['metadata']['generated_at_local']}")
    print(f"  Shift start          : {payload['metadata']['shift_start']}")
    print(f"  Machines             : {s['total_machines']} total  |  {s['machines_in_process']} running  |  {s['machines_idle']} idle  |  {s['machines_inactive']} inactive")
    print(f"  start_sec resolved   : {s['start_sec_resolved']} / {s['machines_in_process']} running machines")
    print(f"  percent_complete     : {s['percent_complete_resolved']} / {s['machines_in_process']} running machines")
    print(f"  cycle fallback usado : {s['cycle_fallback_count']} / {s['machines_in_process']} running machines")
    print(f"  Buffer stock         : {s['buffer_stock_trays']} trays / {s['buffer_stock_parts']} parts")
    print(f"  Finished goods       : {s['finished_goods_trays']} trays / {s['finished_goods_parts']} parts")
    print(f"  HE market            : {s['he_market_items']} items")
    print(f"  Packaging            : {s['packaging_market_items']} items")
    print(f"  Open orders          : {s['open_orders']}")
    print(f"  Produced today       : {s['produced_orders']} PNs")
    print(f"  Hard Stage           : {s['hard_stage_bodies']} bodies / {s['hard_stage_nozzles']} nozzles")
    print(f"  Restriction entries  : {s['restriction_entries']}")
    print(f"  initial_state        : {s['initial_state_machines']} machines | {s['initial_state_stock_trays']} buffer trays")
    print()
    print("  Lines breakdown:")
    for line in s["lines"]:
        print(f"    {line['line_id']:8s}  total={line['total']}  running={line.get('process',0)}  idle={line.get('idle',0)}  inactive={line.get('inactive',0)}")

    # ── detalhe máquinas em processo ─────────────────────────────────────────
    print("\n── Máquinas em processo ────────────────────────────────────────────")
    print(f"  {'Machine':10s}  {'PN':14s}  {'Entry':19s}  {'start':>8}  {'end':>8}  {'pct':>6}  {'pieces':>6}")
    print(f"  {'-'*10}  {'-'*14}  {'-'*19}  {'-'*8}  {'-'*8}  {'-'*6}  {'-'*6}")
    for m in machines:
        state = m.get("state") or {}
        if state.get("activity") != "process":
            continue
        mid         = m["machine_id"]
        pnid        = state.get("part_number_id", "-")
        entry       = (state.get("entry_datetime") or "-")[:19]
        ss          = state.get("start_sec")
        es          = state.get("end_sec")
        pct         = state.get("percent_complete")
        pcs         = state.get("pieces") or "-"
        fallback_pn = state.get("cycle_fallback_pn")
        fallback_note = f"  [fallback: {fallback_pn}]" if fallback_pn else ""
        print(f"  {mid:10s}  {pnid:14s}  {entry:19s}  "
              f"{f'{ss:.0f}s' if ss is not None else 'N/A':>8}  "
              f"{f'{es:.0f}s' if es is not None else 'N/A':>8}  "
              f"{f'{pct:.1%}' if pct is not None else 'N/A':>6}  "
              f"{str(pcs):>6}{fallback_note}")

    if cycle_fallback_warnings:
        print("\n-- Avisos de fallback de ciclo -----------------------------------")
        print(f"  {'Machine':10s}  {'PN real':14s}  {'PN usado (fallback)':14s}  {'secs':>6}")
        print(f"  {'-'*10}  {'-'*14}  {'-'*18}  {'-'*6}")
        for w in cycle_fallback_warnings:
            print(f"  {w['machine_id']:10s}  {w['part_number_id']:14s}  "
                  f"{w['fallback_pn_used']:18s}  {w['fallback_secs']:>6}")


if __name__ == "__main__":
    main()
