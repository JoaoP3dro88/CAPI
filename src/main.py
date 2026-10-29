from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))
REPO_ROOT = CURRENT_DIR.parent

from initial_state import (
    build_production_seeded_initial_state,
    generate_initial_state,
    write_initial_state,
)
from simulation import (
    ProductionSimulation,
    compute_tray_pieces,
    load_changeovers,
    load_machines,
    load_machines_from_dataframe,
    load_orders,
    load_orders_from_dataframe,
    load_restrictions,
    load_restrictions_from_dataframe,
)


def _resolve_path(data_dir: Path, filename: str) -> Path:
    path = data_dir / filename
    if not path.exists():
        raise FileNotFoundError(
            f"Missing required file: {path}. Use --data-dir to point to the folder containing CSVs."
        )
    return path


def _print_considered_orders(orders, order_meta: dict[str, dict]) -> None:
    print("Orders considered for simulation (order_id | part_number_id | quantity | due_date):")
    if not orders:
        print("- none")
        return

    for order in orders:
        meta = order_meta.get(order.order_id, {})
        part_number_id = meta.get("part_number_id") or order.part_number_id
        quantity = meta.get("quantity")
        if quantity is None:
            quantity = order.quantity
        due_date = meta.get("due_date") or "N/A"
        print(f"- {order.order_id} | {part_number_id} | {quantity} | {due_date}")




def main() -> None:
    parser = argparse.ArgumentParser(description="Production simulation based on README_simulation_description.")
    parser.add_argument("--data-dir", default="data", help="Directory containing input CSV files")
    parser.add_argument(
        "--data-source",
        choices=["csv", "db"],
        default="db",
        help="Input source for orders/machines/restrictions",
    )
    parser.add_argument(
        "--params-file",
        default="data/test_params.json",
        help="Optional JSON params file to override defaults",
    )
    parser.add_argument("--initial-state", default=None, help="Optional initial state JSON file")
    parser.add_argument(
        "--generate-initial-state",
        action="store_true",
        help="Generate an initial state JSON based on orders",
    )
    parser.add_argument(
        "--initial-state-output",
        default="outputs/initial_state_generated.json",
        help="Path to write generated initial state JSON",
    )
    parser.add_argument("--orders", default="orders.csv", help="Orders CSV filename")
    parser.add_argument("--machines", default="machines.csv", help="Machines CSV filename")
    parser.add_argument(
        "--machine-restrictions",
        default="restrictions_filtered.csv",
        help="Machine restrictions CSV filename",
    )
    parser.add_argument(
        "--changeovers",
        default="changeover_data.csv",
        help="Changeover data CSV filename",
    )
    parser.add_argument(
        "--input-filter",
        default="input_data.csv",
        help="CSV filename used to filter orders/restrictions when data-source is db",
    )
    parser.add_argument("--db-server", default=None, help="DB server (or set SIM_DB_SERVER)")
    parser.add_argument("--db-name", default=None, help="DB name (or set SIM_DB_NAME)")
    parser.add_argument("--db-user", default=None, help="DB user (or set SIM_DB_USER)")
    parser.add_argument("--db-password", default=None, help="DB password (or set SIM_DB_PASSWORD)")
    parser.add_argument(
        "--db-driver",
        default=None,
        help="ODBC driver (or set SIM_DB_DRIVER)",
    )
    parser.add_argument("--tray-size", type=int, default=150, help="Pieces per tray")
    parser.add_argument(
        "--order-parallel-qty",
        type=int,
        default=1500,
        help="Quantity step to allow additional parallel machines per order",
    )
    parser.add_argument(
        "--campaign-mode",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Enable catalog campaign prioritization on EDM lines",
    )
    parser.add_argument(
        "--laser-diverse-queue-threshold",
        type=int,
        default=3,
        help="Distinct LASER orders threshold to start limiting parallelism per order",
    )
    parser.add_argument(
        "--laser-parallel-cap-diverse-queue",
        type=int,
        default=2,
        help="Max LASER parallel machines per order when queue diversity threshold is reached",
    )
    parser.add_argument(
        "--laser-mid-stock-fill-ratio",
        type=float,
        default=0.6,
        help="EDM stock fill ratio threshold for medium LASER throttling",
    )
    parser.add_argument(
        "--laser-high-stock-fill-ratio",
        type=float,
        default=0.85,
        help="EDM stock fill ratio threshold for strong LASER throttling",
    )
    parser.add_argument(
        "--laser-parallel-cap-mid-stock",
        type=int,
        default=2,
        help="Max LASER parallel machines per order at medium stock fill",
    )
    parser.add_argument(
        "--laser-parallel-cap-high-stock",
        type=int,
        default=1,
        help="Max LASER parallel machines per order at high stock fill",
    )
    parser.add_argument(
        "--laser-shift-off-policy",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Allow turning OFF a full LASER shift when EDM stock is sufficiently high",
    )
    parser.add_argument(
        "--laser-shift-off-stock-fill-ratio",
        type=float,
        default=0.7,
        help="Minimum EDM stock fill ratio to allow full LASER shift OFF",
    )
    parser.add_argument(
        "--laser-shift-off-max-queue-trays",
        type=int,
        default=40,
        help="Maximum LASER queue trays to still allow full LASER shift OFF",
    )
    parser.add_argument(
        "--laser-shift-off-min-completion-ratio",
        type=float,
        default=0.35,
        help="Minimum completed-trays ratio before full LASER shift OFF is allowed",
    )
    parser.add_argument(
        "--laser-shift-off-max-remaining-ratio",
        type=float,
        default=0.25,
        help="Maximum remaining LASER queue ratio to allow full LASER shift OFF",
    )
    parser.add_argument(
        "--laser-shift-off-guard-window-sec",
        type=float,
        default=3600.0,
        help="Seconds before shift end to prepare a clean LASER full-shift OFF (no spillover starts)",
    )
    parser.add_argument("--max-orders", type=int, default=None, help="Limit number of orders loaded")
    parser.add_argument("--max-machines", type=int, default=None, help="Limit number of machines loaded")
    parser.add_argument(
        "--excluded-machines",
        nargs="*",
        default=[],
        metavar="MACHINE_ID",
        help=(
            "Machine IDs to exclude entirely from the simulation (e.g. 'AGIE 22'). "
            "Accepts multiple values: --excluded-machines 'AGIE 22' 'AGIE 06'. "
            "Excluded machines are removed from machine_specs before the simulation starts."
        ),
    )
    parser.add_argument(
        "--stock-capacity-trays",
        type=int,
        default=20,
        help="Stock capacity per EDM line in trays",
    )
    parser.add_argument(
        "--stock-capacity-parts",
        type=int,
        default=3000,
        help="Stock capacity per EDM line in parts",
    )
    parser.add_argument(
        "--poll-interval-sec",
        type=float,
        default=0.1,
        help="Polling interval when waiting for machines or stock",
    )
    parser.add_argument(
        "--summary-interval-sec",
        type=float,
        default=7200.0,
        help="Interval for progress updates (seconds)",
    )
    parser.add_argument(
        "--edm-changeover-operators",
        type=int,
        default=5,
        help="Maximum concurrent EDM changeovers (operators)",
    )
    parser.add_argument(
        "--max-sim-hours",
        type=float,
        default=24.0,
        help="Maximum simulated hours (0 for no limit)",
    )
    parser.add_argument(
        "--progress",
        action="store_true",
        help="Enable progress logging",
    )
    parser.add_argument(
        "--export-json",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Export JSON payloads for visualization",
    )
    parser.add_argument(
        "--export-figma-json",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Also export JSON into the Figma app public folder",
    )
    parser.add_argument(
        "--realtime-step-sec",
        type=float,
        default=60.0,
        help="Seconds per step for realtime JSON playback",
    )
    parser.add_argument(
        "--compress-realtime",
        action="store_true",
        default=False,
        help="Write realtime.json.gz (gzip compressed) instead of plain JSON. "
             "Reduces file size by ~80%% for large simulations.",
    )
    args = parser.parse_args()

    if args.params_file:
        params_path = Path(args.params_file)
        if params_path.exists():
            params = json.loads(params_path.read_text(encoding="utf-8"))
            cli_flags = {
                token.split("=", 1)[0]
                for token in sys.argv[1:]
                if token.startswith("--")
            }
            for key, value in params.items():
                if hasattr(args, key):
                    cli_flag = f"--{key.replace('_', '-')}"
                    cli_no_flag = f"--no-{key.replace('_', '-')}"
                    if cli_flag in cli_flags or cli_no_flag in cli_flags:
                        continue
                    setattr(args, key, value)

    data_dir = Path(args.data_dir)
    changeovers_path = _resolve_path(data_dir, args.changeovers)
    changeover_lookup = load_changeovers(changeovers_path)

    if args.data_source == "db":
        try:
            from data_gathering import (
                fetch_all_filtered,
                fetch_production_seed_rows,
                get_eligible_part_numbers,
                get_engine,
            )
        except ModuleNotFoundError as exc:
            missing = exc.name or str(exc)
            raise SystemExit(
                "DB mode requires additional dependencies. "
                "Install them with: pip install -r requirements.txt "
                f"(missing module: {missing})"
            ) from exc

        try:
            engine = get_engine(
                server=args.db_server,
                database=args.db_name,
                username=args.db_user,
                password=args.db_password,
                driver=args.db_driver,
            )
        except ValueError as exc:
            raise SystemExit(
                f"{exc}. If you want to run with test CSV files, use --data-source csv."
            ) from exc
        df_orders, df_restrictions, df_machines, df_hard_stage = fetch_all_filtered(engine, None)
        eligible_part_numbers = get_eligible_part_numbers(df_restrictions)
        live_machine_rows, live_buffer_rows = fetch_production_seed_rows(
            engine,
            eligible_part_numbers=eligible_part_numbers,
        )
        machine_specs = load_machines_from_dataframe(df_machines.to_dict("records"))

        if args.max_machines:
            machine_specs = machine_specs[: args.max_machines]
        restrictions = load_restrictions_from_dataframe(
            df_restrictions.to_dict("records"),
            machine_specs,
        )
        orders = load_orders_from_dataframe(df_orders.to_dict("records"))

        # Build order metadata lookup (due_date comes from END_DATE in the DB query)
        order_meta: dict[str, dict] = {}
        for row in df_orders.to_dict("records"):
            oid = str(row.get("order_id", "") or "").strip()
            if not oid:
                continue
            raw_date = row.get("update_date")
            due = None
            if raw_date is not None:
                try:
                    import pandas as pd
                    due = str(pd.Timestamp(raw_date).date())
                except Exception:
                    due = str(raw_date)[:10]
            _raw_real_qtty = row.get("real_qtty")
            _real_qtty = int(float(_raw_real_qtty)) if _raw_real_qtty is not None and not (isinstance(_raw_real_qtty, float) and math.isnan(_raw_real_qtty)) else 0
            order_meta[oid] = {
                "order_id": oid,
                "part_number_id": str(row.get("part_number_id", "") or "").strip(),
                "quantity": _real_qtty,
                "due_date": due,
            }

        # Build hard_stage_market: group nozzle part numbers by body number
        hard_stage_market: list[dict] = []
        body_map: dict[str, dict] = {}
        for row in df_hard_stage.to_dict("records"):
            body = str(row.get("body_number_id") or "").strip()
            pn = str(row.get("part_number_id") or "").strip()
            _raw_qtty = row.get("qtty_hardstage")
            qtty = int(float(_raw_qtty)) if _raw_qtty is not None and not (isinstance(_raw_qtty, float) and math.isnan(_raw_qtty)) else 0
            if not body or not pn:
                continue
            if body not in body_map:
                body_map[body] = {"body_partnumber": body, "body_qtty": qtty, "nozzles": []}
            else:
                body_map[body]["body_qtty"] = max(body_map[body]["body_qtty"], qtty)
            if pn not in body_map[body]["nozzles"]:
                body_map[body]["nozzles"].append(pn)
        hard_stage_market = sorted(body_map.values(), key=lambda x: x["body_partnumber"])

        # Build per-nozzle HSM stock for simulation constraint.
        hsm_stock_by_pn: dict[str, int] = {}
        for row in df_hard_stage.to_dict("records"):
            pn = str(row.get("part_number_id") or "").strip()
            _raw_qty = row.get("qtty_hardstage")
            qty = int(float(_raw_qty)) if _raw_qty is not None and not (isinstance(_raw_qty, float) and math.isnan(_raw_qty)) else 0
            if pn and qty > 0:
                hsm_stock_by_pn[pn] = hsm_stock_by_pn.get(pn, 0) + qty
    else:
        orders_path = _resolve_path(data_dir, args.orders)
        machines_path = _resolve_path(data_dir, args.machines)
        restrictions_path = _resolve_path(data_dir, args.machine_restrictions)
        machine_specs = load_machines(machines_path)
        if args.max_machines:
            machine_specs = machine_specs[: args.max_machines]
        restrictions = load_restrictions(restrictions_path, machine_specs)
        orders = load_orders(orders_path)
        # No due_date available from CSV — build minimal meta from the Order objects
        order_meta: dict[str, dict] = {
            order.order_id: {
                "order_id": order.order_id,
                "part_number_id": order.part_number_id,
                "quantity": order.quantity,
                "due_date": None,
            }
            for order in orders
        }
        hard_stage_market = []
        hsm_stock_by_pn: dict[str, int] = {}
        live_machine_rows = []
        live_buffer_rows = []

    # Inject synthetic EDM cycle-time entries for PNs that have LASER
    # restrictions but no EDM entry, and for PNs in the live buffer that are
    # completely absent from the restrictions table.
    # Uses the closest matching PN (by prefix/similarity) as cycle-time source.
    extra_pns: set[str] = set()
    if args.data_source == "db":
        # live_buffer_rows was already filtered by eligible_part_numbers, so PNs
        # without any restriction were dropped.  Re-read directly from the buffer
        # query (no eligibility filter) to capture those missing PNs too.
        try:
            from data_gathering import fetch_buffer_stock
            df_buf_all = fetch_buffer_stock(engine)
            for row in df_buf_all.to_dict("records"):
                pn = str(row.get("part_number_id") or "").strip()
                if pn:
                    extra_pns.add(pn)
        except Exception as _exc:
            pass  # non-fatal — fallback injection will simply be skipped
    n_injected = restrictions.inject_edm_fallbacks(machine_specs, extra_pns=extra_pns or None)
    if n_injected:
        print(f"EDM fallback injection: {n_injected} PN×machine entries added from closest-match cycle times.")

    # Remove excluded machines from restrictions so they never receive work.
    # The machines stay in machine_specs — the simulation will fill their entire
    # timeline with "inactive" events, and they appear correctly in the dashboard.
    if args.excluded_machines:
        excluded_set = {m.strip() for m in args.excluded_machines if m.strip()}
        before = len(restrictions.allowed)
        restrictions.allowed = {
            entry for entry in restrictions.allowed
            if entry[2] not in excluded_set  # entry[2] is machine_id
        }
        removed = before - len(restrictions.allowed)
        print(f"[main] Marked {len(excluded_set)} machines as inactive (removed {removed} restriction entries): {sorted(excluded_set)}")

    part_processes: dict[str, set[str]] = {}
    for process_type, _machine_type, _machine_id, part_number_id in restrictions.allowed:
        part_processes.setdefault(part_number_id, set()).add(process_type.upper())
    eligible_parts = {p for p, procs in part_processes.items() if "LASER" in procs and "EDM" in procs}
    filtered_orders = [order for order in orders if order.part_number_id in eligible_parts]
    if len(filtered_orders) != len(orders):
        print(
            f"Filtered orders without LASER+EDM: {len(orders) - len(filtered_orders)} removed; {len(filtered_orders)} remaining."
        )
    orders = filtered_orders
    all_eligible_orders = list(orders)

    seeded_part_numbers: set[str] = set()
    if args.data_source == "db":
        for row in [*live_machine_rows, *live_buffer_rows]:
            part_number_id = str(row.get("part_number_id") or "").strip()
            if part_number_id and part_number_id in eligible_parts:
                seeded_part_numbers.add(part_number_id)

    target_order_ids = None
    forced_seed_order_ids: set[str] = set()

    if args.max_orders:
        extra_orders = max(1, int(round(args.max_orders * 0.1)))
        orders = orders[: args.max_orders + extra_orders]
        target_order_ids = {order.order_id for order in orders[: args.max_orders]}

        # Seeded in-line/buffer work must be finishable even if outside the base active slice.
        if seeded_part_numbers:
            selected_ids = {order.order_id for order in orders}
            forced_seed_orders = [
                order
                for order in all_eligible_orders
                if order.part_number_id in seeded_part_numbers and order.order_id not in selected_ids
            ]
            if forced_seed_orders:
                orders.extend(forced_seed_orders)
                forced_seed_order_ids = {order.order_id for order in forced_seed_orders}
                if target_order_ids is not None:
                    target_order_ids.update(forced_seed_order_ids)

    initial_state = None
    seeded_pieces_by_order: dict[str, int] = {}
    seeded_skip_stats: dict[str, int] = {}

    from datetime import datetime as _dt, timezone as _tz
    simulation_ref_dt = _dt.now(_tz.utc)   # UTC wall-clock anchor for plan vs actual

    if args.data_source == "db" and not args.initial_state:
        seeded_initial_state_obj, seeded_pieces_by_order, seeded_skip_stats = build_production_seeded_initial_state(
            orders=orders,
            machine_specs=machine_specs,
            restrictions=restrictions,
            live_machine_rows=live_machine_rows,
            live_buffer_rows=live_buffer_rows,
            order_meta=order_meta,
            tray_size=args.tray_size,
            stock_capacity_trays=args.stock_capacity_trays,
            simulation_ref_dt=simulation_ref_dt,
        )
        seeded_pending_orders = {
            order.order_id
            for order in orders
            if seeded_pieces_by_order.get(order.order_id, 0) > 0
            and seeded_pieces_by_order.get(order.order_id, 0) < order.quantity
        }
        if seeded_pending_orders:
            orders = [
                *[order for order in orders if order.order_id in seeded_pending_orders],
                *[order for order in orders if order.order_id not in seeded_pending_orders],
            ]
        initial_state = seeded_initial_state_obj.to_dict()

    if args.generate_initial_state:
        if args.data_source == "db" and not args.initial_state:
            initial_state_obj = seeded_initial_state_obj
        else:
            initial_state_obj = generate_initial_state(
                orders=orders,
                machine_specs=machine_specs,
                restrictions=restrictions,
                stock_capacity_trays=args.stock_capacity_trays,
                tray_size=args.tray_size,
                stock_fill_ratio=1.0,
            )
        output_path = Path(args.initial_state_output)
        write_initial_state(initial_state_obj, output_path)
        initial_state = initial_state_obj.to_dict()
        print(f"Generated initial state: {output_path}")
    elif args.initial_state:
        init_path = Path(args.initial_state)
        if init_path.exists():
            initial_state = json.loads(init_path.read_text(encoding="utf-8"))
    elif initial_state is None:
        initial_state_obj = generate_initial_state(
            orders=orders,
            machine_specs=machine_specs,
            restrictions=restrictions,
            stock_capacity_trays=args.stock_capacity_trays,
            tray_size=args.tray_size,
            stock_fill_ratio=1.0,
        )
        initial_state = initial_state_obj.to_dict()

    if seeded_pieces_by_order:
        seeded_machine_trays = len(initial_state.get("machines", [])) if isinstance(initial_state, dict) else 0
        seeded_buffer_trays = 0
        if isinstance(initial_state, dict):
            edm_stock = initial_state.get("edm_stock", {})
            if isinstance(edm_stock, dict):
                seeded_buffer_trays = sum(len(items or []) for items in edm_stock.values())
        seeded_orders = len([order_id for order_id, pieces in seeded_pieces_by_order.items() if pieces > 0])
        print(
            "Initial state seeded from production: "
            f"machines={seeded_machine_trays}, buffer_trays={seeded_buffer_trays}, orders={seeded_orders}"
        )
        if forced_seed_order_ids:
            print(
                "Seeded force inclusion applied: "
                f"part_numbers={len(seeded_part_numbers)}, forced_orders={len(forced_seed_order_ids)}"
            )
        print(
            "Initial state skips: "
            f"machine_non_eligible={seeded_skip_stats.get('machine_non_eligible', 0)}, "
            f"machine_no_order={seeded_skip_stats.get('machine_no_order', 0)}, "
            f"machine_no_order_fallback={seeded_skip_stats.get('machine_no_order_fallback', 0)} (seeded without order_id), "
            f"machine_not_in_specs={seeded_skip_stats.get('machine_not_in_specs', 0)}, "
            f"buffer_non_eligible={seeded_skip_stats.get('buffer_non_eligible', 0)}, "
            f"buffer_no_order={seeded_skip_stats.get('buffer_no_order', 0)}, "
            f"buffer_line_full={seeded_skip_stats.get('buffer_line_full', 0)}"
        )

    total_quantity = sum(order.quantity for order in orders)
    process_types = {m.process_type.upper() for m in machine_specs}
    machine_types = {m.machine_type for m in machine_specs}
    line_ids = {m.line_id for m in machine_specs}
    total_changeover_pairs = len(changeover_lookup.mapping)
    total_process_time_entries = len(restrictions.cycle_times)
    total_restrictions = len(restrictions.allowed)

    def progress_report(summary):
        pct = (summary.completed_trays / summary.total_trays * 100.0) if summary.total_trays else 0.0
        hours = summary.time_sec / 3600.0
        print(
            f"[t={summary.time_sec:.0f}s | {hours:.2f}h] trays {summary.completed_trays}/{summary.total_trays} "
            f"({pct:.1f}%) | laser busy {summary.busy_laser_machines} | edm busy {summary.busy_edm_machines}",
            flush=True,
        )

    _print_considered_orders(orders, order_meta)
    print("Initializing simulation...", flush=True)
    print(f"- Orders loaded: {len(orders)}")
    print(f"- Machines loaded: {len(machine_specs)}")
    print(f"- Summary interval: {args.summary_interval_sec:.0f}s")
    if hsm_stock_by_pn:
        hsm_pns = set(hsm_stock_by_pn.keys())
        hsm_eligible = hsm_pns & eligible_parts
        hsm_total_pieces = sum(hsm_stock_by_pn.values())
        order_pns_in_hsm = {o.part_number_id for o in orders} & hsm_pns
        print(f"- Hard Stage Market: {len(hsm_pns)} nozzle PNs, {hsm_total_pieces} total pieces")
        print(f"- HSM PNs eligible (LASER+EDM): {len(hsm_eligible)}")
        print(f"- Order PNs covered by HSM: {len(order_pns_in_hsm)} / {len({o.part_number_id for o in orders})}")
    else:
        print("- Hard Stage Market: not available (CSV mode)")

    max_time_sec = args.max_sim_hours * 3600.0 if args.max_sim_hours and args.max_sim_hours > 0 else None

    simulation = ProductionSimulation(
        orders=orders,
        machine_specs=machine_specs,
        restrictions=restrictions,
        changeover_lookup=changeover_lookup,
        tray_size=args.tray_size,
        order_parallel_qty=args.order_parallel_qty,
        stock_capacity_trays=args.stock_capacity_trays,
        stock_capacity_parts=args.stock_capacity_parts,
        poll_interval_sec=args.poll_interval_sec,
        summary_interval_sec=args.summary_interval_sec,
        progress_hook=progress_report if args.progress else None,
        initial_state=initial_state,
        hsm_stock=hsm_stock_by_pn if hsm_stock_by_pn else None,
        target_order_ids=target_order_ids,
        max_time_sec=max_time_sec,
        edm_changeover_operators=args.edm_changeover_operators,
        campaign_mode=args.campaign_mode,
        laser_diverse_queue_threshold=args.laser_diverse_queue_threshold,
        laser_parallel_cap_diverse_queue=args.laser_parallel_cap_diverse_queue,
        laser_mid_stock_fill_ratio=args.laser_mid_stock_fill_ratio,
        laser_high_stock_fill_ratio=args.laser_high_stock_fill_ratio,
        laser_parallel_cap_mid_stock=args.laser_parallel_cap_mid_stock,
        laser_parallel_cap_high_stock=args.laser_parallel_cap_high_stock,
        laser_shift_off_policy=args.laser_shift_off_policy,
        laser_shift_off_stock_fill_ratio=args.laser_shift_off_stock_fill_ratio,
        laser_shift_off_max_queue_trays=args.laser_shift_off_max_queue_trays,
        laser_shift_off_min_completion_ratio=args.laser_shift_off_min_completion_ratio,
        laser_shift_off_max_remaining_ratio=args.laser_shift_off_max_remaining_ratio,
        laser_shift_off_guard_window_sec=args.laser_shift_off_guard_window_sec,
    )
    simulation.build()
    print("Running simulation...", flush=True)
    start_wall = time.perf_counter()
    result = simulation.run()
    end_wall = time.perf_counter()
    wall_seconds = end_wall - start_wall

    print("Simulation description")
    print("- Process flow: Hard Stage Supermarket -> Laser Marking -> Stock LM/EDM -> EDM -> Finish")
    if args.campaign_mode:
        print("- Dispatch policy: catalog campaign priority + stock-aware LASER parallel throttling")
        if args.laser_shift_off_policy:
            print("- LASER policy: full-shift OFF allowed when EDM stock is high (corridor-level)")
            print("- LASER policy: pre-shift drain window avoids spillover into OFF shift")
    else:
        print("- FIFO order sequence (no optimization)")
    print("- Tray sizes: 75 or 150 pieces (rounded up per order)")
    print("- Stock per EDM line: 20 trays")
    print("- Changeovers are symmetric; no changeover between same product type")
    print("- EDM includes test time after changeover")
    print("- No scrap assumed")
    print("Data summary")
    print(f"- Orders: {len(orders)}")
    print(f"- Total order quantity (pieces): {total_quantity}")
    print(f"- Machines: {len(machine_specs)}")
    print(f"- Lines: {len(line_ids)}")
    print(f"- Process types: {', '.join(sorted(process_types))}")
    print(f"- Machine types: {', '.join(sorted(machine_types))}")
    print(f"- Changeover pairs: {total_changeover_pairs}")
    print(f"- Process time entries: {total_process_time_entries}")
    print(f"- Restrictions: {total_restrictions}")
    total_time_sec = result.total_time_sec
    total_time_hours = total_time_sec / 3600.0 if total_time_sec else 0.0
    total_time_days = total_time_hours / 24.0 if total_time_hours else 0.0
    part_types = {order.part_number_id for order in orders}
    total_pieces = result.step_summaries[-1].completed_parts if result.step_summaries else 0
    pieces_per_hour = (total_pieces / total_time_hours) if total_time_hours else 0.0
    pieces_per_day = (total_pieces / total_time_days) if total_time_days else 0.0

    machine_parts: dict[str, int] = {}
    for event in result.event_log:
        if event.activity != "process":
            continue
        if event.process_type.upper() != "EDM":
            continue
        machine_parts[event.machine_id] = machine_parts.get(event.machine_id, 0) + event.pieces

    print("Simulation results")
    print(f"Total trays processed: {result.total_trays}")
    print(f"Total production time (sec): {total_time_sec:.2f}")
    print(f"Total production time (hours): {total_time_hours:.2f}")
    print(f"Total production time (days): {total_time_days:.2f}")
    print(f"Part types produced: {len(part_types)}")
    print(f"Total pieces produced: {total_pieces}")
    print(f"Pieces per hour: {pieces_per_hour:.2f}")
    print(f"Pieces per day: {pieces_per_day:.2f}")
    print(f"Execution time (wall): {wall_seconds:.2f}s")
    print(f"Average machine utilization: {result.average_utilization:.2%}")

    if args.export_json:
        from json_export import export_dashboard_json, export_realtime_json

        compress = getattr(args, "compress_realtime", False)
        rt_suffix = ".json.gz" if compress else ".json"

        dashboard_path = export_dashboard_json(
            result=result,
            orders=orders,
            machine_specs=machine_specs,
            output=REPO_ROOT / "outputs" / "dashboard.json",
        )
        realtime_path = export_realtime_json(
            result=result,
            orders=orders,
            machine_specs=machine_specs,
            order_meta=order_meta,
            hard_stage_market=hard_stage_market,
            step_sec=args.realtime_step_sec,
            output=REPO_ROOT / "outputs" / f"realtime{rt_suffix}",
            compress=compress,
        )

        print(f"JSON exported: {dashboard_path}")
        print(f"JSON exported: {realtime_path}")

        # --- schema validation ---
        from validate_schema import validate_file
        _schema_errors: dict[str, list[str]] = {
            str(dashboard_path): validate_file(dashboard_path, "dashboard"),
            str(realtime_path): validate_file(realtime_path, "realtime"),
        }
        _any_error = False
        for _fpath, _errs in _schema_errors.items():
            if _errs:
                _any_error = True
                print(f"\n[validate_schema] ❌ {_fpath}")
                for _e in _errs:
                    print(f"  • {_e}")
            else:
                print(f"[validate_schema] ✅ {_fpath}")
        if _any_error:
            print("[validate_schema] WARNING: schema validation failed — outputs may be incomplete.")

        if args.export_figma_json:
            figma_public = REPO_ROOT / "Dashboard and Real-Time Playback" / "public"
            figma_public.mkdir(parents=True, exist_ok=True)
            figma_dashboard = export_dashboard_json(
                result=result,
                orders=orders,
                machine_specs=machine_specs,
                output=figma_public / "dashboard.json",
            )
            figma_realtime = export_realtime_json(
                result=result,
                orders=orders,
                machine_specs=machine_specs,
                order_meta=order_meta,
                hard_stage_market=hard_stage_market,
                step_sec=args.realtime_step_sec,
                output=figma_public / f"realtime{rt_suffix}",
                compress=compress,
            )
            print(f"JSON exported: {figma_dashboard}")
            print(f"JSON exported: {figma_realtime}")

        # --- history (real production from DB) ---
        if args.data_source == "db":
            try:
                from history_export import export_history_json

                history_path = export_history_json(
                    engine=engine,
                    ref_dt=simulation_ref_dt,
                    output=REPO_ROOT / "outputs" / "history.json",
                )
                print(f"JSON exported: {history_path}")
                if args.export_figma_json:
                    figma_history = export_history_json(
                        engine=engine,
                        ref_dt=simulation_ref_dt,
                        output=figma_public / "history.json",
                    )
                    print(f"JSON exported: {figma_history}")

                # Persist ref_dt so the API background task can refresh history.json
                # every 30 min without needing to re-run the simulation.
                ref_dt_path = REPO_ROOT / "outputs" / "sim_ref_dt.json"
                ref_dt_path.write_text(
                    json.dumps({"ref_dt": simulation_ref_dt.isoformat()}, indent=2),
                    encoding="utf-8",
                )
                print(f"Simulation ref_dt persisted: {ref_dt_path}")
            except Exception as exc:
                print(f"[history_export] WARNING: {exc}")

    report_path = Path("outputs") / "simulation_report.txt"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with report_path.open("w", encoding="utf-8") as report:
        report.write("Simulation Report\n")
        report.write("=================\n\n")
        report.write("Data Summary\n")
        report.write(f"- Orders: {len(orders)}\n")
        report.write(f"- Total order quantity (pieces): {total_quantity}\n")
        report.write(f"- Machines: {len(machine_specs)}\n")
        report.write(f"- Lines: {len(line_ids)}\n")
        report.write(f"- Process types: {', '.join(sorted(process_types))}\n")
        report.write(f"- Machine types: {', '.join(sorted(machine_types))}\n")
        report.write(f"- Changeover pairs: {total_changeover_pairs}\n")
        report.write(f"- Process time entries: {total_process_time_entries}\n")
        report.write(f"- Restrictions: {total_restrictions}\n\n")

        report.write("KPIs\n")
        report.write(f"- Total trays processed: {result.total_trays}\n")
        report.write(f"- Total production time (sec): {total_time_sec:.2f}\n")
        report.write(f"- Total production time (hours): {total_time_hours:.2f}\n")
        report.write(f"- Total production time (days): {total_time_days:.2f}\n")
        report.write(f"- Part types produced: {len(part_types)}\n")
        report.write(f"- Total pieces produced: {total_pieces}\n")
        report.write(f"- Pieces per hour: {pieces_per_hour:.2f}\n")
        report.write(f"- Pieces per day: {pieces_per_day:.2f}\n")
        report.write(f"- Execution time (wall): {wall_seconds:.2f}s\n")
        report.write(f"- Average machine utilization: {result.average_utilization:.2%}\n\n")

        report.write("Machine Utilization and Output\n")
        for machine_id, util in sorted(result.machine_utilization.items()):
            parts = machine_parts.get(machine_id, 0)
            parts_per_hour_machine = (parts / total_time_hours) if total_time_hours else 0.0
            report.write(
                f"- {machine_id}: {util:.2%} | parts {parts} | parts/hr {parts_per_hour_machine:.2f}\n"
            )
        report.write("\n")

        report.write("Orders Summary (planned)\n")
        for order in orders:
            trays_needed = len(compute_tray_pieces(order.quantity, args.tray_size))
            report.write(
                f"- {order.order_id}: part {order.part_number_id} | qty {order.quantity} | trays {trays_needed}\n"
            )
        report.write("\n")

        report.write("Snapshots (first 10)\n")
        for summary in result.step_summaries[:10]:
            report.write(
                f"- t={summary.time_sec:.0f}s | lm_queue={summary.lm_queue_trays} | "
                f"edm_stock={summary.edm_stock_trays} | busy_laser={summary.busy_laser_machines} | "
                f"busy_edm={summary.busy_edm_machines} | completed_trays={summary.completed_trays}\n"
            )



if __name__ == "__main__":
    main()
