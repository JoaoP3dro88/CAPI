from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from simulation import MachineSpec, Order, Restrictions, compute_tray_pieces


@dataclass
class InitialState:
    completed: Dict[str, int]
    edm_stock: Dict[str, List[Dict[str, object]]]
    machines: List[Dict[str, object]]

    def to_dict(self) -> dict:
        return {
            "completed": self.completed,
            "edm_stock": self.edm_stock,
            "machines": self.machines,
        }


def _parse_due_sort_value(value: object) -> str:
    if value is None:
        return "9999-12-31"
    text_value = str(value).strip()
    if not text_value or text_value.lower() in {"nan", "none", "null"}:
        return "9999-12-31"
    return text_value


def build_production_seeded_initial_state(
    *,
    orders: List[Order],
    machine_specs: List[MachineSpec],
    restrictions: Restrictions,
    live_machine_rows: List[dict],
    live_buffer_rows: List[dict],
    order_meta: Dict[str, dict],
    tray_size: int,
    stock_capacity_trays: int,
    simulation_ref_dt: datetime | None = None,
) -> tuple[InitialState, Dict[str, int], Dict[str, int]]:
    edm_machine_specs = {
        spec.machine_id: spec
        for spec in machine_specs
        if spec.process_type.upper() == "EDM"
    }
    edm_lines = sorted({spec.line_id for spec in edm_machine_specs.values()})

    part_processes: Dict[str, set[str]] = {}
    for process_type, _machine_type, _machine_id, part_number_id in restrictions.allowed:
        part_processes.setdefault(part_number_id, set()).add(process_type.upper())
    eligible_parts = {
        part_number_id
        for part_number_id, processes in part_processes.items()
        if "LASER" in processes and "EDM" in processes
    }

    tray_pieces_by_order: Dict[str, List[int]] = {
        order.order_id: list(compute_tray_pieces(order.quantity, tray_size))
        for order in orders
    }
    order_by_id: Dict[str, Order] = {order.order_id: order for order in orders}

    order_ids_by_part: Dict[str, List[str]] = {}
    for order in orders:
        order_ids_by_part.setdefault(order.part_number_id, []).append(order.order_id)
    for part_number_id, order_ids in order_ids_by_part.items():
        order_ids.sort(
            key=lambda order_id: (
                _parse_due_sort_value(order_meta.get(order_id, {}).get("due_date")),
                order_id,
            )
        )

    seeded_pieces_by_order: Dict[str, int] = {}
    seeded_trays_by_order: Dict[str, int] = {}
    skipped: Dict[str, int] = {
        "machine_non_eligible": 0,
        "machine_no_order": 0,
        "machine_not_in_specs": 0,
        "machine_no_order_fallback": 0,
        "buffer_non_eligible": 0,
        "buffer_no_order": 0,
        "buffer_line_full": 0,
    }

    def _normalize_text(value: object) -> str:
        if value is None:
            return ""
        text_value = str(value).strip()
        if not text_value or text_value.lower() in {"nan", "none", "null"}:
            return ""
        return text_value

    def _consume_order_tray(part_number_id: str, preferred_order_id: str | None) -> tuple[str, int] | None:
        if preferred_order_id:
            pref = preferred_order_id.strip()
            order = order_by_id.get(pref)
            if order and order.part_number_id == part_number_id and tray_pieces_by_order.get(pref):
                pieces = tray_pieces_by_order[pref].pop(0)
                return pref, pieces
        for candidate_order_id in order_ids_by_part.get(part_number_id, []):
            remaining = tray_pieces_by_order.get(candidate_order_id)
            if remaining:
                pieces = remaining.pop(0)
                return candidate_order_id, pieces
        return None

    machines_state: List[Dict[str, object]] = []
    used_machine_ids: set[str] = set()

    for row in live_machine_rows:
        machine_id = _normalize_text(row.get("machine_id"))
        part_number_id = _normalize_text(row.get("part_number_id"))
        if not machine_id or machine_id in used_machine_ids:
            continue
        spec = edm_machine_specs.get(machine_id)
        if spec is None:
            skipped["machine_not_in_specs"] += 1
            continue
        # Note: we do NOT filter by eligible_parts here.
        # If a machine is currently processing a PN that is not in the eligible
        # set (e.g. a finished order or a rework PN), we still seed it so the
        # simulation respects the current tray finishing before moving on.
        # The simulation's fallback-tray mechanism handles unknown PNs.
        match = _consume_order_tray(part_number_id, _normalize_text(row.get("order_id")) or None)
        if not match:
            # No open order for the PN currently loaded on this machine.
            # Seed the machine anyway so the simulation respects the current
            # tray finishing before assigning a new order (the simulation's
            # fallback-tray mechanism handles order_id=None gracefully).
            order_id = None
            pieces = int(float(row.get("quantity") or 0)) or tray_size
            skipped["machine_no_order_fallback"] += 1
        else:
            order_id, pieces = match
            seeded_pieces_by_order[order_id] = seeded_pieces_by_order.get(order_id, 0) + pieces
            seeded_trays_by_order[order_id] = seeded_trays_by_order.get(order_id, 0) + 1

        # --- Calculate percent_complete from elapsed time vs total cycle ---
        pct = 0.0
        event_date = row.get("event_date")
        if simulation_ref_dt is not None and event_date is not None:
            try:
                if isinstance(event_date, datetime):
                    ev_dt = event_date
                else:
                    ev_dt = datetime.fromisoformat(str(event_date))
                # Make both tz-aware (UTC) for comparison
                if ev_dt.tzinfo is None:
                    ev_dt = ev_dt.replace(tzinfo=timezone.utc)
                ref = simulation_ref_dt if simulation_ref_dt.tzinfo else simulation_ref_dt.replace(tzinfo=timezone.utc)
                elapsed_sec = max(0.0, (ref - ev_dt).total_seconds())
                cycle_per_piece = restrictions.get_cycle_time(machine_id, part_number_id)
                quantity = int(float(row.get("quantity") or 0)) or pieces
                total_sec = cycle_per_piece * quantity
                if total_sec > 0:
                    # Cap at 0.99 so the machine is always seeded with the
                    # current PN. A value of 1.0 would cause _apply_initial_state
                    # to skip the machine entirely (percent_complete >= 1.0
                    # guard), losing the live PN and letting the scheduler
                    # assign a different PN as the first job.
                    pct = min(0.99, elapsed_sec / total_sec)
            except (ValueError, KeyError, TypeError):
                pct = 0.0

        machines_state.append(
            {
                "machine_id": machine_id,
                "order_id": order_id,
                "part_number_id": part_number_id,
                "percent_complete": pct,
            }
        )
        used_machine_ids.add(machine_id)

    edm_stock: Dict[str, List[Dict[str, object]]] = {line_id: [] for line_id in edm_lines}
    for row in live_buffer_rows:
        line_id = _normalize_text(row.get("line_id"))
        if line_id not in edm_stock:
            continue
        if len(edm_stock[line_id]) >= stock_capacity_trays:
            skipped["buffer_line_full"] += 1
            continue

        part_number_id = _normalize_text(row.get("part_number_id"))
        if part_number_id not in eligible_parts:
            skipped["buffer_non_eligible"] += 1
            continue
        match = _consume_order_tray(part_number_id, _normalize_text(row.get("order_id")) or None)
        if not match:
            skipped["buffer_no_order"] += 1
            continue
        order_id, pieces = match
        seeded_pieces_by_order[order_id] = seeded_pieces_by_order.get(order_id, 0) + pieces
        seeded_trays_by_order[order_id] = seeded_trays_by_order.get(order_id, 0) + 1
        edm_stock[line_id].append(
            {
                "order_id": order_id,
                "part_number_id": part_number_id,
                "trays": 1,
            }
        )

    return (
        InitialState(
            completed={"trays": 0, "parts": 0},
            edm_stock=edm_stock,
            machines=machines_state,
        ),
        seeded_pieces_by_order,
        skipped,
    )


def generate_initial_state(
    *,
    orders: List[Order],
    machine_specs: List[MachineSpec],
    restrictions: Restrictions,
    stock_capacity_trays: int,
    tray_size: int,
    stock_fill_ratio: float = 1.0,
) -> InitialState:
    remaining_trays: Dict[str, int] = {}
    for order in orders:
        trays_needed = len(compute_tray_pieces(order.quantity, tray_size))
        remaining_trays[order.order_id] = trays_needed

    machine_allowed_parts: Dict[str, List[str]] = {}
    for process_type, _machine_type, machine_id, part_number_id in restrictions.allowed:
        machine_allowed_parts.setdefault(machine_id, []).append(part_number_id)

    edm_lines = sorted({m.line_id for m in machine_specs if m.process_type.upper() == "EDM"})
    edm_line_machines: Dict[str, List[str]] = {}
    for spec in machine_specs:
        if spec.process_type.upper() == "EDM":
            edm_line_machines.setdefault(spec.line_id, []).append(spec.machine_id)

    edm_stock: Dict[str, List[Dict[str, object]]] = {line_id: [] for line_id in edm_lines}

    line_allowed_parts: Dict[str, List[str]] = {}
    for line_id in edm_lines:
        allowed_parts: List[str] = []
        for machine_id in edm_line_machines.get(line_id, []):
            allowed_parts.extend(machine_allowed_parts.get(machine_id, []))
        line_allowed_parts[line_id] = list({p for p in allowed_parts})

    def pick_next_order(allowed_parts: List[str]) -> Optional[Order]:
        for order in orders:
            if order.part_number_id in allowed_parts and remaining_trays.get(order.order_id, 0) > 0:
                return order
        return None

    trays_to_add = max(1, int(stock_capacity_trays * stock_fill_ratio))
    for line_id in edm_lines:
        allowed_parts = line_allowed_parts.get(line_id, [])
        if not allowed_parts:
            continue
        for _ in range(trays_to_add):
            order = pick_next_order(allowed_parts)
            if not order:
                continue
            remaining_trays[order.order_id] -= 1
            edm_stock[line_id].append(
                {
                    "order_id": order.order_id,
                    "part_number_id": order.part_number_id,
                    "trays": 1,
                }
            )

    machines_state: List[Dict[str, object]] = []
    for spec in machine_specs:
        allowed_parts = machine_allowed_parts.get(spec.machine_id, [])
        if not allowed_parts:
            continue
        order = pick_next_order(allowed_parts)
        if not order:
            continue
        remaining_trays[order.order_id] -= 1
        machines_state.append(
            {
                "machine_id": spec.machine_id,
                "order_id": order.order_id,
                "part_number_id": order.part_number_id,
                "percent_complete": 0.1,
            }
        )

    return InitialState(
        completed={"trays": 0, "parts": 0},
        edm_stock=edm_stock,
        machines=machines_state,
    )


def write_initial_state(initial_state: InitialState, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(initial_state.to_dict(), indent=2), encoding="utf-8")
