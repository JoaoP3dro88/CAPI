from __future__ import annotations

import gzip
import json
from bisect import bisect_right
from datetime import datetime, timezone
from dataclasses import asdict
from pathlib import Path
from typing import Dict, List, Tuple

from simulation import EventLog, MachineSpec, Order, SimulationResult, StockEvent


def _write_json(payload: object, output: Path, compress: bool = False) -> Path:
    """Serialize *payload* to *output*.

    When *compress* is True the file is written as gzip (``output`` path is
    returned unchanged — the caller should use a ``.json.gz`` suffix if
    desired).  Otherwise a plain UTF-8 JSON file is written.

    Uses streaming json.dump (file object) instead of json.dumps (string) to
    avoid allocating the entire serialized payload in memory at once.
    """
    output.parent.mkdir(parents=True, exist_ok=True)
    if compress:
        import io
        buf = io.BytesIO()
        with io.TextIOWrapper(buf, encoding="utf-8") as tw:
            json.dump(payload, tw, indent=None)
            tw.flush()
        output.write_bytes(gzip.compress(buf.getvalue()))
    else:
        with output.open("w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
    return output


def export_dashboard_json(
    *,
    result: SimulationResult,
    orders: List[Order],
    machine_specs: List[MachineSpec],
    output: Path,
) -> Path:
    total_changeover_sec = _sum_activity(result.event_log, "changeover")
    total_waiting_sec = _sum_activity(result.event_log, "waiting")
    total_changeovers = sum(1 for event in result.event_log if event.activity == "changeover")
    total_test_sec = _sum_activity(result.event_log, "test")
    total_process_sec = _sum_activity(result.event_log, "process")
    total_time_sec = result.total_time_sec or 0.0
    machines_count = len(machine_specs)
    changeover_loss_percentage = (
        (total_changeover_sec / (total_time_sec * machines_count)) * 100.0
        if total_time_sec > 0 and machines_count > 0
        else 0.0
    )
    waiting_loss_percentage = (
        (total_waiting_sec / (total_time_sec * machines_count)) * 100.0
        if total_time_sec > 0 and machines_count > 0
        else 0.0
    )
    changeover_count_per_machine = (
        total_changeovers / machines_count if machines_count > 0 else 0.0
    )

    machines = [
        {
            "machine_id": spec.machine_id,
            "process_type": spec.process_type.upper(),
            "line_id": spec.line_id,
            "machine_type": spec.machine_type,
            "static_oee": result.machine_utilization.get(spec.machine_id, 0.0),
        }
        for spec in machine_specs
    ]

    timeline = [asdict(event) for event in result.event_log]

    stock_final = _build_stock_details(result.stock_events, result.order_summary)

    total_parts = 0
    if result.step_summaries:
        total_parts = int(result.step_summaries[-1].completed_parts)
    total_time_hours = (total_time_sec / 3600.0) if total_time_sec > 0 else 0.0
    parts_per_hour = (total_parts / total_time_hours) if total_time_hours > 0 else 0.0
    payload = {
        "metadata": {
            "facility_timezone": "America/Sao_Paulo",
        },
        "kpis": {
            "total_time_sec": result.total_time_sec,
            "total_trays": result.total_trays,
            "total_parts": int(round(total_parts)),
            "parts_per_hour": round(parts_per_hour, 2),
            "global_oee": result.average_utilization,
            "changeover_loss_percentage": round(changeover_loss_percentage, 2),
            "waiting_loss_percentage": round(waiting_loss_percentage, 2),
            "orders_count": len(orders),
            "machines_count": machines_count,
            "lines_count": len({spec.line_id for spec in machine_specs}),
            "total_changeover_sec": total_changeover_sec,
            "total_waiting_sec": total_waiting_sec,
            "total_changeovers": total_changeovers,
            "changeovers_per_machine": round(changeover_count_per_machine, 2),
            "total_test_sec": total_test_sec,
            "total_process_sec": total_process_sec,
        },
        "machines": machines,
        "timeline": timeline,
        "stock_final": stock_final,
        "orders": result.order_summary,
    }

    _write_json(payload, output)
    return output


def export_realtime_json(
    *,
    result: SimulationResult,
    orders: List[Order],
    machine_specs: List[MachineSpec],
    step_sec: float,
    output: Path,
    order_meta: Dict[str, dict] | None = None,
    hard_stage_market: List[dict] | None = None,
    compress: bool = False,
) -> Path:
    events_by_machine = _index_events(result.event_log)
    order_times = _compute_order_times(result.event_log)
    edm_completions = _compute_edm_completions(result.event_log)
    line_ids = sorted({spec.line_id for spec in machine_specs if spec.process_type.upper() == "EDM"})

    total_time_sec = result.total_time_sec
    step_sec = max(step_sec, 1.0)

    summary_lookup = {order["order_id"]: order for order in result.order_summary}
    frames = []
    for time_sec in _frange(0.0, total_time_sec, step_sec):
        backlog_orders = []
        produced_orders = []
        for order in orders:
            start_sec, end_sec = order_times.get(order.order_id, (None, None))
            if start_sec is None or start_sec > time_sec:
                backlog_orders.append(order.order_id)
            else:
                completion_times = edm_completions.get(order.order_id, [])
                completed_trays = bisect_right(completion_times, time_sec)
                if completed_trays > 0:
                    summary = summary_lookup.get(
                        order.order_id,
                        {
                            "order_id": order.order_id,
                            "part_number_id": order.part_number_id,
                            "product_type": order.product_type,
                            "quantity": order.quantity,
                            "trays": 0,
                            "produced_pieces": 0,
                        },
                    )
                    raw_trays = summary.get("trays", 0)
                    total_trays = int(raw_trays) if isinstance(raw_trays, (int, float, str)) else 0
                    raw_pieces = summary.get("produced_pieces", 0)
                    produced_pieces_total = (
                        float(raw_pieces) if isinstance(raw_pieces, (int, float, str)) else 0.0
                    )
                    pieces_per_tray = produced_pieces_total / total_trays if total_trays > 0 else 0.0
                    completion_pct = (
                        min(1.0, completed_trays / total_trays) if total_trays > 0 else 0.0
                    )
                    produced_orders.append(
                        {
                            **summary,
                            "completed_trays": completed_trays,
                            "completion_pct": round(completion_pct, 4),
                            "produced_pieces": int(round(completed_trays * pieces_per_tray)),
                        }
                    )

        stock_levels = _compute_stock_levels(result.stock_events, line_ids, time_sec, result.order_summary)

        machines = []
        for spec in machine_specs:
            machine_events = events_by_machine.get(spec.machine_id, [])
            current_oee = _compute_current_oee(machine_events, time_sec)
            state = _machine_state_at(machine_events, time_sec, current_oee)
            machines.append(
                {
                    "machine_id": spec.machine_id,
                    "process_type": spec.process_type.upper(),
                    "line_id": spec.line_id,
                    "machine_type": spec.machine_type,
                    "static_oee": result.machine_utilization.get(spec.machine_id, 0.0),
                    "state": state,
                }
            )

        frames.append(
            {
                "time_sec": time_sec,
                "backlog_orders": backlog_orders,
                "produced_orders": produced_orders,
                "stock_levels": stock_levels,
                "machines": machines,
            }
        )

    orders_by_id = {}
    for order in orders:
        meta = (order_meta or {}).get(order.order_id, {})
        orders_by_id[order.order_id] = {
            "order_id": order.order_id,
            "part_number_id": meta.get("part_number_id") or order.part_number_id,
            "quantity": meta.get("quantity") or next(
                (o["quantity"] for o in result.order_summary if o["order_id"] == order.order_id),
                order.quantity,
            ),
            "due_date": meta.get("due_date"),
        }

    payload = {
        "metadata": {
            "step_sec": step_sec,
            "total_time_sec": total_time_sec,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "facility_timezone": "America/Sao_Paulo",
        },
        "orders_by_id": orders_by_id,
        "hard_stage_market": hard_stage_market or [],
        "frames": frames,
    }

    _write_json(payload, output, compress=compress)
    return output


def _sum_activity(events: List[EventLog], activity: str) -> float:
    return sum(event.end_sec - event.start_sec for event in events if event.activity == activity)


def _index_events(events: List[EventLog]) -> Dict[str, List[EventLog]]:
    by_machine: Dict[str, List[EventLog]] = {}
    for event in events:
        by_machine.setdefault(event.machine_id, []).append(event)
    for machine_events in by_machine.values():
        machine_events.sort(key=lambda e: e.start_sec)
    return by_machine


def _compute_order_times(events: List[EventLog]) -> Dict[str, Tuple[float | None, float | None]]:
    order_times: Dict[str, Tuple[float | None, float | None]] = {}
    for event in events:
        if event.activity != "process":
            continue
        start, end = order_times.get(event.order_id, (None, None))
        if start is None or event.start_sec < start:
            start = event.start_sec
        if event.process_type.upper() == "EDM":
            if end is None or event.end_sec > end:
                end = event.end_sec
        order_times[event.order_id] = (start, end)
    return order_times


def _compute_edm_completions(events: List[EventLog]) -> Dict[str, List[float]]:
    completions: Dict[str, List[float]] = {}
    for event in events:
        if event.activity != "process":
            continue
        if event.process_type.upper() != "EDM":
            continue
        completions.setdefault(event.order_id, []).append(event.end_sec)
    for times in completions.values():
        times.sort()
    return completions


def _compute_stock_levels(
    stock_events: List[StockEvent],
    line_ids: List[str],
    time_sec: float,
    order_summary: List[Dict[str, object]],
) -> List[Dict[str, object]]:
    levels: Dict[str, List[Dict[str, object]]] = {line_id: [] for line_id in line_ids}
    for event in stock_events:
        if event.time_sec <= time_sec:
            if event.action == "put":
                levels.setdefault(event.line_id, []).append(
                    {
                        "tray_id": event.tray_id,
                        "order_id": event.order_id,
                        "part_number_id": event.part_number_id,
                        "product_type": event.product_type,
                        "pieces": event.pieces,
                    }
                )
            elif event.action == "get":
                trays = levels.get(event.line_id, [])
                levels[event.line_id] = [tray for tray in trays if tray["tray_id"] != event.tray_id]

    payload = []
    for line_id in line_ids:
        trays = levels.get(line_id, [])
        parts = sum(int(tray.get("pieces", 0)) for tray in trays)
        payload.append(
            {
                "line_id": line_id,
                "trays": len(trays),
                "parts": parts,
                "tray_details": trays,
            }
        )
    return payload


def _build_stock_details(
    stock_events: List[StockEvent],
    order_summary: List[Dict[str, object]],
) -> List[Dict[str, object]]:
    lines = sorted({event.line_id for event in stock_events})
    if not lines:
        return []
    return _compute_stock_levels(stock_events, lines, float("inf"), order_summary)


def _machine_state_at(
    events: List[EventLog],
    time_sec: float,
    current_oee: float,
) -> Dict[str, object]:
    match: EventLog | None = None
    for event in events:
        if event.start_sec <= time_sec < event.end_sec:
            if match is None or event.start_sec > match.start_sec:
                match = event
    if match is not None:
        return {
            "activity": match.activity,
            "order_id": match.order_id,
            "part_number_id": match.part_number_id,
            "product_type": match.product_type,
            "tray_id": match.tray_id,
            "start_sec": match.start_sec,
            "end_sec": match.end_sec,
            "current_oee": current_oee,
        }
    return {"activity": "idle", "current_oee": current_oee}


def _compute_current_oee(events: List[EventLog], time_sec: float) -> float:
    if time_sec <= 0:
        return 0.0
    busy_activities = {"process", "changeover", "test"}
    busy = 0.0
    for event in events:
        if event.activity not in busy_activities:
            continue
        start = max(0.0, event.start_sec)
        end = min(time_sec, event.end_sec)
        if end > start:
            busy += end - start
    return min(1.0, busy / time_sec)


def _frange(start: float, stop: float, step: float) -> List[float]:
    values = []
    current = start
    while current <= stop:
        values.append(current)
        current += step
    if values and values[-1] < stop:
        values.append(stop)
    return values
