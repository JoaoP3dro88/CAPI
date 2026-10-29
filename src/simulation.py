from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Generator, List, Optional, Tuple, cast

import simpy


def compute_tray_pieces(
    quantity: int,
    max_tray_size: int = 150,
    partial_tray_size: int = 75,
) -> List[int]:
    if quantity <= 0:
        return []
    if max_tray_size <= 0:
        raise ValueError("max_tray_size must be positive")
    if partial_tray_size <= 0 or partial_tray_size > max_tray_size:
        raise ValueError("partial_tray_size must be positive and <= max_tray_size")

    full_trays = quantity // max_tray_size
    remainder = quantity % max_tray_size
    pieces = [max_tray_size for _ in range(full_trays)]
    if remainder:
        pieces.append(partial_tray_size if remainder <= partial_tray_size else max_tray_size)
    return pieces


@dataclass(frozen=True)
class Order:
    order_id: str
    part_number_id: str
    product_type: str
    quantity: int


@dataclass(frozen=True)
class MachineSpec:
    machine_id: str
    process_type: str
    machine_type: str
    line_id: str


@dataclass(frozen=True)
class ChangeoverDurations:
    laser_min: float
    edm_min: float
    edm_test_min: float


@dataclass
class Tray:
    tray_id: int
    order_id: str
    part_number_id: str
    product_type: str
    pieces: int


@dataclass(frozen=True)
class EventLog:
    machine_id: str
    process_type: str
    line_id: str
    tray_id: int
    order_id: str
    part_number_id: str
    product_type: str
    pieces: int
    activity: str
    start_sec: float
    end_sec: float


@dataclass(frozen=True)
class StockEvent:
    line_id: str
    tray_id: int
    order_id: str
    part_number_id: str
    product_type: str
    pieces: int
    action: str
    time_sec: float


class Machine:
    def __init__(
        self,
        env: simpy.Environment,
        spec: MachineSpec,
        restrictions: "Restrictions",
        changeover_lookup: "ChangeoverLookup",
        event_log: List[EventLog],
        edm_changeover_resource: Optional[simpy.Resource] = None,
    ) -> None:
        self.env = env
        self.spec = spec
        self.restrictions = restrictions
        self.changeover_lookup = changeover_lookup
        self.event_log = event_log
        self.edm_changeover_resource = edm_changeover_resource
        self.last_part_number: Optional[str] = None
        self.is_busy = False
        self.idle_event = env.event()
        self.idle_event.succeed()
        self.busy_time = 0.0
        self.current_tray: Optional[Tray] = None
        self.current_activity: Optional[str] = None
        self.current_activity_start_sec: Optional[float] = None

    def can_process(self, tray: Tray, restrictions: "Restrictions") -> bool:
        return restrictions.allows(
            part_number_id=tray.part_number_id,
            process_type=self.spec.process_type,
            machine_type=self.spec.machine_type,
            machine_id=self.spec.machine_id,
        )

    def start_process(
        self,
        tray: Tray,
        on_complete,
        remaining_process_sec: Optional[float] = None,
        force_edm_startup: bool = False,
        startup_changeover_sec: float = 0.0,
        startup_test_sec: float = 0.0,
    ):
        if self.is_busy:
            raise RuntimeError(f"Machine {self.spec.machine_id} is already busy")
        self.is_busy = True
        self.current_tray = tray
        self.current_activity = None
        self.current_activity_start_sec = None
        self.idle_event = self.env.event()
        return self.env.process(
            self._run(
                tray,
                on_complete,
                remaining_process_sec,
                force_edm_startup=force_edm_startup,
                startup_changeover_sec=startup_changeover_sec,
                startup_test_sec=startup_test_sec,
            )
        )

    def _run(
        self,
        tray: Tray,
        on_complete,
        remaining_process_sec: Optional[float] = None,
        force_edm_startup: bool = False,
        startup_changeover_sec: float = 0.0,
        startup_test_sec: float = 0.0,
    ) -> Generator:
        durations = self.changeover_lookup.get(self.last_part_number, tray.product_type)
        changeover_sec = 0.0
        test_sec = 0.0
        if self.spec.process_type.upper() == "LASER":
            changeover_sec = durations.laser_min * 60.0
        elif self.spec.process_type.upper() == "EDM":
            changeover_sec = durations.edm_min * 60.0
            test_sec = durations.edm_test_min * 60.0
        if (
            self.spec.process_type.upper() == "EDM"
            and force_edm_startup
            and remaining_process_sec is None
        ):
            # Override/inject startup phase durations. This path is taken both
            # for a cold first-run (last_part_number is None) and when resuming
            # a machine that was mid-changeover or mid-test in the initial state
            # (in which case the caller already set last_part_number to
            # tray.product_type so the lookup above returns 0, and the startup
            # params carry exactly the remaining phase durations).
            changeover_sec = max(changeover_sec, max(0.0, startup_changeover_sec))
            test_sec = max(test_sec, max(0.0, startup_test_sec))
        time_per_piece = self.restrictions.get_cycle_time(self.spec.machine_id, tray.part_number_id)
        process_sec = time_per_piece * tray.pieces
        if remaining_process_sec is not None:
            changeover_sec = 0.0
            test_sec = 0.0
            process_sec = max(0.0, remaining_process_sec)

        if changeover_sec > 0:
            if self.spec.process_type.upper() == "EDM" and self.edm_changeover_resource is not None:
                wait_start = self.env.now
                self.current_activity = "waiting"
                self.current_activity_start_sec = wait_start
                with self.edm_changeover_resource.request() as req:
                    yield req
                    wait_end = self.env.now
                    if wait_end > wait_start:
                        self.event_log.append(
                            EventLog(
                                machine_id=self.spec.machine_id,
                                process_type=self.spec.process_type.upper(),
                                line_id=self.spec.line_id,
                                tray_id=tray.tray_id,
                                order_id=tray.order_id,
                                part_number_id=tray.part_number_id,
                                product_type=tray.product_type,
                                pieces=tray.pieces,
                                activity="waiting",
                                start_sec=wait_start,
                                end_sec=wait_end,
                            )
                        )
                    self.current_activity = None
                    self.current_activity_start_sec = None
                    start = self.env.now
                    self.current_activity = "changeover"
                    self.current_activity_start_sec = start
                    yield self.env.timeout(changeover_sec)
                    end = self.env.now
            else:
                start = self.env.now
                self.current_activity = "changeover"
                self.current_activity_start_sec = start
                yield self.env.timeout(changeover_sec)
                end = self.env.now
            self.current_activity = None
            self.current_activity_start_sec = None
            self.event_log.append(
                EventLog(
                    machine_id=self.spec.machine_id,
                    process_type=self.spec.process_type.upper(),
                    line_id=self.spec.line_id,
                    tray_id=tray.tray_id,
                    order_id=tray.order_id,
                    part_number_id=tray.part_number_id,
                    product_type=tray.product_type,
                    pieces=tray.pieces,
                    activity="changeover",
                    start_sec=start,
                    end_sec=end,
                )
            )

        if test_sec > 0:
            start = self.env.now
            self.current_activity = "test"
            self.current_activity_start_sec = start
            yield self.env.timeout(test_sec)
            end = self.env.now
            self.current_activity = None
            self.current_activity_start_sec = None
            self.event_log.append(
                EventLog(
                    machine_id=self.spec.machine_id,
                    process_type=self.spec.process_type.upper(),
                    line_id=self.spec.line_id,
                    tray_id=tray.tray_id,
                    order_id=tray.order_id,
                    part_number_id=tray.part_number_id,
                    product_type=tray.product_type,
                    pieces=tray.pieces,
                    activity="test",
                    start_sec=start,
                    end_sec=end,
                )
            )

        start = self.env.now
        self.current_activity = "process"
        self.current_activity_start_sec = start
        yield self.env.timeout(process_sec)
        end = self.env.now
        self.current_activity = None
        self.current_activity_start_sec = None
        self.event_log.append(
            EventLog(
                machine_id=self.spec.machine_id,
                process_type=self.spec.process_type.upper(),
                line_id=self.spec.line_id,
                tray_id=tray.tray_id,
                order_id=tray.order_id,
                part_number_id=tray.part_number_id,
                product_type=tray.product_type,
                pieces=tray.pieces,
                activity="process",
                start_sec=start,
                end_sec=end,
            )
        )

        self.busy_time += changeover_sec + test_sec + process_sec
        self.last_part_number = tray.product_type
        self.is_busy = False
        self.current_tray = None
        self.current_activity = None
        self.current_activity_start_sec = None
        self.idle_event.succeed()
        on_complete(tray, self)


class Restrictions:
    def __init__(
        self,
        allowed: set[Tuple[str, str, str, str]],
        cycle_times: Dict[Tuple[str, str], float],
    ) -> None:
        self.allowed = allowed
        self.cycle_times = cycle_times

    def allows(
        self,
        part_number_id: str,
        process_type: str,
        machine_type: str,
        machine_id: str,
    ) -> bool:
        key = (
            process_type.upper(),
            machine_type,
            machine_id,
            part_number_id,
        )
        return key in self.allowed

    def get_cycle_time(self, machine_id: str, part_number_id: str) -> float:
        from cycle_time_utils import lookup_cycle_time
        secs, fallback_pn = lookup_cycle_time(
            part_number_id, machine_id, self.cycle_times, use_fallback=True
        )
        if fallback_pn:
            # Inject into cache so subsequent calls are O(1)
            self.cycle_times[(machine_id, part_number_id)] = secs
        return secs

    def inject_edm_fallbacks(
        self,
        machine_specs: "List[MachineSpec]",
        extra_pns: "set[str] | None" = None,
    ) -> int:
        """
        For every PN that has a LASER restriction but no EDM restriction (or that
        is completely absent from the table but present in `extra_pns`), inject
        synthetic LASER+EDM entries using the closest known cycle times.

        `extra_pns` — optional set of part-number IDs known to be in the real
        buffer/machines but absent from the restrictions table entirely
        (e.g. from initial_state buffer rows).

        Returns the number of PN × machine pairs injected.
        """
        from cycle_time_utils import closest_cycle_time

        # ── collect existing sets ──────────────────────────────────────────
        laser_pns: set[str] = set()
        edm_pns:   set[str] = set()
        for process_type, _mtype, _mid, pn in self.allowed:
            if process_type == "LASER":
                laser_pns.add(pn)
            elif process_type == "EDM":
                edm_pns.add(pn)

        # PNs that need EDM injected (have LASER or are in extra_pns)
        need_edm = (laser_pns - edm_pns)
        # PNs that need both LASER and EDM (completely absent)
        need_both: set[str] = set()
        if extra_pns:
            all_known = laser_pns | edm_pns
            need_both = extra_pns - all_known
            need_edm  = need_edm | (extra_pns & laser_pns - edm_pns)

        if not need_edm and not need_both:
            return 0

        # ── build reference cycle-time pools ─────────────────────────────
        # EDM: pn → cycle time
        edm_pn_to_machine: dict[str, str] = {}
        for pt, _mt, mid, pn in self.allowed:
            if pt == "EDM" and pn not in edm_pn_to_machine:
                edm_pn_to_machine[pn] = mid
        edm_cycle_by_pn: dict[str, float] = {}
        for pn, mid in edm_pn_to_machine.items():
            secs = self.cycle_times.get((mid, pn))
            if secs and secs > 0:
                edm_cycle_by_pn[pn] = secs

        # LASER: pn → cycle time (for need_both case)
        laser_pn_to_machine: dict[str, str] = {}
        for pt, _mt, mid, pn in self.allowed:
            if pt == "LASER" and pn not in laser_pn_to_machine:
                laser_pn_to_machine[pn] = mid
        laser_cycle_by_pn: dict[str, float] = {}
        for pn, mid in laser_pn_to_machine.items():
            secs = self.cycle_times.get((mid, pn))
            if secs and secs > 0:
                laser_cycle_by_pn[pn] = secs

        if not edm_cycle_by_pn:
            return 0

        edm_specs   = [s for s in machine_specs if s.process_type.upper() == "EDM"]
        laser_specs = [s for s in machine_specs if s.process_type.upper() == "LASER"]

        injected = 0

        def _inject_edm(pn: str) -> None:
            nonlocal injected
            fallback_secs, _ = closest_cycle_time(pn, edm_cycle_by_pn)
            if fallback_secs is None:
                return
            for spec in edm_specs:
                key = (spec.machine_id, pn)
                if key not in self.cycle_times:
                    self.allowed.add(("EDM", spec.machine_type, spec.machine_id, pn))
                    self.cycle_times[key] = fallback_secs
                    injected += 1

        def _inject_laser(pn: str) -> None:
            nonlocal injected
            fallback_secs, _ = closest_cycle_time(pn, laser_cycle_by_pn)
            if fallback_secs is None:
                return
            for spec in laser_specs:
                key = (spec.machine_id, pn)
                if key not in self.cycle_times:
                    self.allowed.add(("LASER", spec.machine_type, spec.machine_id, pn))
                    self.cycle_times[key] = fallback_secs
                    injected += 1

        for pn in sorted(need_edm):
            _inject_edm(pn)

        for pn in sorted(need_both):
            _inject_laser(pn)
            _inject_edm(pn)

        return injected


class ChangeoverLookup:
    def __init__(self, mapping: Dict[Tuple[str, str], ChangeoverDurations]) -> None:
        self.mapping = mapping

    def get(self, previous: Optional[str], current: str) -> ChangeoverDurations:
        if previous is None or previous == current:
            return ChangeoverDurations(0.0, 0.0, 0.0)
        key = (previous, current) if previous < current else (current, previous)
        return self.mapping.get(key, ChangeoverDurations(0.0, 0.0, 0.0))


class Stock:
    def __init__(self, env: simpy.Environment, capacity_trays: int, capacity_parts: int) -> None:
        self.store = simpy.Store(env, capacity=capacity_trays)
        self.capacity_trays = capacity_trays
        self.capacity_parts = capacity_parts
        self.current_parts = 0

    def has_space(self, tray: Tray) -> bool:
        # Use the configured steady-state capacity, not the (possibly expanded)
        # simpy.Store capacity which may have been inflated during seed.
        return len(self.store.items) < self.capacity_trays

    def put(self, tray: Tray):
        self.current_parts += tray.pieces
        return self.store.put(tray)

    def remove_tray(self, tray: Tray) -> None:
        self.current_parts = max(0, self.current_parts - tray.pieces)

    def add_tray_direct(self, tray: Tray) -> None:
        self.store.items.append(tray)
        self.current_parts += tray.pieces


@dataclass
class EdmLine:
    line_id: str
    stock: Stock
    machines: List[Machine]

    def can_process(self, tray: Tray, restrictions: Restrictions) -> bool:
        return any(m.can_process(tray, restrictions) for m in self.machines)


@dataclass
class SimulationResult:
    total_time_sec: float
    total_trays: int
    machine_utilization: Dict[str, float]
    average_utilization: float
    event_log: List[EventLog]
    stock_final: Dict[str, int]
    order_summary: List[Dict[str, object]]
    stock_events: List[StockEvent]
    step_summaries: List["StepSummary"]


@dataclass(frozen=True)
class StepSummary:
    time_sec: float
    lm_queue_trays: int
    edm_stock_trays: Dict[str, int]
    busy_laser_machines: int
    busy_edm_machines: int
    completed_trays: int
    total_trays: int
    completed_parts: int
    machine_status: Dict[str, Tuple[Optional[str], Optional[int]]]


class ProductionSimulation:
    def __init__(
        self,
        orders: List[Order],
        machine_specs: List[MachineSpec],
        restrictions: Restrictions,
        changeover_lookup: ChangeoverLookup,
        tray_size: int = 150,
        order_parallel_qty: int = 1500,
        stock_capacity_trays: int = 20,
        stock_capacity_parts: int = 3000,
        poll_interval_sec: float = 0.1,
        summary_interval_sec: float = 3600.0,
        progress_hook: Optional[Callable[["StepSummary"], None]] = None,
        initial_state: Optional[dict] = None,
        hsm_stock: Optional[Dict[str, int]] = None,
        target_order_ids: Optional[set[str]] = None,
        max_time_sec: Optional[float] = None,
        edm_changeover_operators: int = 5,
        campaign_mode: bool = True,
        laser_diverse_queue_threshold: int = 3,
        laser_parallel_cap_diverse_queue: int = 2,
        laser_mid_stock_fill_ratio: float = 0.6,
        laser_high_stock_fill_ratio: float = 0.85,
        laser_parallel_cap_mid_stock: int = 2,
        laser_parallel_cap_high_stock: int = 1,
        laser_shift_off_policy: bool = True,
        laser_shift_off_stock_fill_ratio: float = 0.7,
        laser_shift_off_max_queue_trays: int = 40,
        laser_shift_off_min_completion_ratio: float = 0.35,
        laser_shift_off_max_remaining_ratio: float = 0.25,
        laser_shift_off_guard_window_sec: float = 3600.0,
    ) -> None:
        self.orders = orders
        self.machine_specs = machine_specs
        self.restrictions = restrictions
        self.changeover_lookup = changeover_lookup
        self.initial_buffer_tray_ids: set[int] = set()
        self.initial_machine_ids: set[str] = set()
        edm_changeover_values_sec = [
            d.edm_min * 60.0 for d in self.changeover_lookup.mapping.values() if d.edm_min > 0.0
        ]
        edm_test_values_sec = [
            d.edm_test_min * 60.0
            for d in self.changeover_lookup.mapping.values()
            if d.edm_test_min > 0.0
        ]
        self.edm_startup_changeover_sec = (
            sum(edm_changeover_values_sec) / len(edm_changeover_values_sec)
            if edm_changeover_values_sec
            else 0.0
        )
        self.edm_startup_test_sec = (
            sum(edm_test_values_sec) / len(edm_test_values_sec)
            if edm_test_values_sec
            else 0.0
        )
        self.tray_size = tray_size
        self.order_parallel_qty = max(1, int(order_parallel_qty))
        self.stock_capacity_trays = stock_capacity_trays
        self.stock_capacity_parts = stock_capacity_parts
        self.poll_interval_sec = poll_interval_sec
        self.summary_interval_sec = summary_interval_sec
        self.progress_hook = progress_hook
        self.initial_state = initial_state
        self.hsm_stock: Dict[str, int] = dict(hsm_stock) if hsm_stock else {}
        self.target_order_ids = target_order_ids
        self.max_time_sec = max_time_sec
        self.edm_changeover_operators = edm_changeover_operators
        self.campaign_mode = campaign_mode
        self.laser_diverse_queue_threshold = max(1, int(laser_diverse_queue_threshold))
        self.laser_parallel_cap_diverse_queue = max(1, int(laser_parallel_cap_diverse_queue))
        self.laser_mid_stock_fill_ratio = max(0.0, min(1.0, float(laser_mid_stock_fill_ratio)))
        self.laser_high_stock_fill_ratio = max(
            self.laser_mid_stock_fill_ratio,
            min(1.0, float(laser_high_stock_fill_ratio)),
        )
        self.laser_parallel_cap_mid_stock = max(1, int(laser_parallel_cap_mid_stock))
        self.laser_parallel_cap_high_stock = max(1, int(laser_parallel_cap_high_stock))
        self.laser_shift_off_policy = laser_shift_off_policy
        self.laser_shift_off_stock_fill_ratio = max(
            0.0,
            min(1.0, float(laser_shift_off_stock_fill_ratio)),
        )
        self.laser_shift_off_max_queue_trays = max(0, int(laser_shift_off_max_queue_trays))
        self.laser_shift_off_min_completion_ratio = max(
            0.0,
            min(1.0, float(laser_shift_off_min_completion_ratio)),
        )
        self.laser_shift_off_max_remaining_ratio = max(
            0.0,
            min(1.0, float(laser_shift_off_max_remaining_ratio)),
        )
        self.laser_shift_off_guard_window_sec = max(0.0, float(laser_shift_off_guard_window_sec))
        self.target_trays = 0
        self.completed_target_trays = 0
        self.total_trays_all = 0

        self.env = simpy.Environment()
        self.lm_input = simpy.Store(self.env)
        self.edm_changeover_resource = simpy.Resource(
            self.env,
            capacity=max(1, int(self.edm_changeover_operators)),
        )
        self.edm_lines: Dict[str, EdmLine] = {}
        self.lm_machines: List[Machine] = []
        self.total_trays = 0
        self.completed_trays = 0
        self.completed_parts = 0
        self.done_event = self.env.event()
        self.event_log: List[EventLog] = []
        self.order_trays: Dict[str, int] = {}
        self.order_pieces: Dict[str, int] = {}
        self.order_max_parallel: Dict[str, int] = {
            order.order_id: max(1, int(math.ceil(order.quantity / self.order_parallel_qty)))
            for order in self.orders
        }
        self.order_active_machines: Dict[Tuple[str, str], set[str]] = {}
        self.order_machine_pins: Dict[Tuple[str, str], set[str]] = {}
        self.part_preferred_machine: Dict[Tuple[str, str], str] = {}
        self.edm_line_shift_campaign: Dict[Tuple[str, int, int], str] = {}
        self.laser_shift_off_decision: Dict[Tuple[int, int], bool] = {}
        self.stock_events: List[StockEvent] = []
        self.step_summaries: List[StepSummary] = []

    def build(self) -> None:
        for spec in self.machine_specs:
            machine = Machine(
                self.env,
                spec,
                self.restrictions,
                self.changeover_lookup,
                self.event_log,
                edm_changeover_resource=self.edm_changeover_resource,
            )

            if spec.process_type.upper() == "LASER":
                self.lm_machines.append(machine)
            elif spec.process_type.upper() == "EDM":
                line = self.edm_lines.get(spec.line_id)
                if line is None:
                    line = EdmLine(
                        line_id=spec.line_id,
                        stock=Stock(
                            self.env,
                            self.stock_capacity_trays,
                            self.stock_capacity_parts,
                        ),
                        machines=[],
                    )
                    self.edm_lines[spec.line_id] = line
                line.machines.append(machine)
            else:
                raise ValueError(f"Unknown process_type {spec.process_type}")

        if not self.lm_machines:
            raise ValueError("No LASER machines found")
        if not self.edm_lines:
            raise ValueError("No EDM machines found")

        self.env.process(
            self._dispatcher(
                input_store=self.lm_input,
                machines=self.lm_machines,
                on_complete=self._on_lm_complete,
                label="LASER",
            )
        )

        for line in self.edm_lines.values():
            self.env.process(
                self._dispatcher(
                    input_store=line.stock.store,
                    machines=line.machines,
                    on_complete=self._on_edm_complete,
                    label=f"EDM line {line.line_id}",
                    stock_line_id=line.line_id,
                )
            )

        self.env.process(self._snapshotter())

    def run(self) -> SimulationResult:
        self._enqueue_orders()
        if self.initial_state:
            self._apply_initial_state(self.initial_state)
        if self.max_time_sec and self.max_time_sec > 0:
            stop_event = self.env.any_of([self.done_event, self.env.timeout(self.max_time_sec)])
            self.env.run(until=stop_event)
        else:
            self.env.run(until=self.done_event)

        total_time_sec = self.env.now
        if self.max_time_sec and self.max_time_sec > 0 and not self.done_event.triggered:
            self._finalize_open_events()
        if self.summary_interval_sec > 0 and (
            not self.step_summaries or self.step_summaries[-1].time_sec < total_time_sec
        ):
            self._record_summary()
        shift_windows = [
            (0.0, 9 * 3600.0),
            (9 * 3600.0, 18 * 3600.0),
            (18 * 3600.0, 24 * 3600.0),
        ]
        all_machines = self._all_machines()
        machine_work_activities = {"process", "changeover", "test", "waiting"}
        inactive_time_by_machine: Dict[str, float] = {
            machine.spec.machine_id: 0.0 for machine in all_machines
        }

        # Repeat the shift windows for each simulated day.
        shift_windows_expanded: List[Tuple[float, float]] = []
        day_offset_sec = 0.0
        while day_offset_sec < total_time_sec:
            for start_rel_sec, end_rel_sec in shift_windows:
                start_sec = day_offset_sec + start_rel_sec
                if start_sec >= total_time_sec:
                    continue
                end_sec = min(day_offset_sec + end_rel_sec, total_time_sec)
                if end_sec > start_sec:
                    shift_windows_expanded.append((start_sec, end_sec))
            day_offset_sec += 24 * 3600.0

        for start_sec, end_sec in shift_windows_expanded:
            window_duration = end_sec - start_sec
            for machine in all_machines:
                machine_id = machine.spec.machine_id
                worked_in_shift = any(
                    event.machine_id == machine_id
                    and event.activity in machine_work_activities
                    and event.start_sec < end_sec
                    and event.end_sec > start_sec
                    for event in self.event_log
                )
                if worked_in_shift:
                    continue

                inactive_time_by_machine[machine_id] += window_duration
                self.event_log.append(
                    EventLog(
                        machine_id=machine.spec.machine_id,
                        process_type=machine.spec.process_type.upper(),
                        line_id=machine.spec.line_id,
                        tray_id=0,
                        order_id="",
                        part_number_id="",
                        product_type="",
                        pieces=0,
                        activity="inactive",
                        start_sec=start_sec,
                        end_sec=end_sec,
                    )
                )

        machine_utilization: Dict[str, float] = {}
        busy_sum = 0.0
        available_sum = 0.0
        for machine in all_machines:
            inactive_time = inactive_time_by_machine.get(machine.spec.machine_id, 0.0)
            available_time = max(0.0, total_time_sec - inactive_time)
            utilization = machine.busy_time / available_time if available_time > 0 else 0.0
            machine_utilization[machine.spec.machine_id] = utilization
            busy_sum += machine.busy_time
            available_sum += available_time

        total_machines = len(machine_utilization)
        average_utilization = (
            busy_sum / available_sum if available_sum > 0 and total_machines > 0 else 0.0
        )

        return SimulationResult(
            total_time_sec=total_time_sec,
            total_trays=self.total_trays,
            machine_utilization=machine_utilization,
            average_utilization=average_utilization,
            event_log=self.event_log,
            stock_final={line_id: len(line.stock.store.items) for line_id, line in self.edm_lines.items()},
            order_summary=[
                {
                    "order_id": order.order_id,
                    "part_number_id": order.part_number_id,
                    "product_type": order.product_type,
                    "quantity": order.quantity,
                    "trays": self.order_trays.get(order.order_id, 0),
                    "produced_pieces": self.order_pieces.get(order.order_id, 0),
                }
                for order in self.orders
            ],
            stock_events=self.stock_events,
            step_summaries=self.step_summaries,
        )

    def _enqueue_orders(self) -> None:
        tray_id = 1
        target_order_ids = self.target_order_ids or set()

        # HSM budget: remaining producible pieces per part_number_id.
        hsm_active = bool(self.hsm_stock)
        hsm_remaining: Dict[str, int] = dict(self.hsm_stock) if hsm_active else {}

        for order in self.orders:
            pn = order.part_number_id

            # Determine how many pieces can be produced for this order.
            if hsm_active:
                available = hsm_remaining.get(pn, 0)
                if available <= 0:
                    continue
                producible = min(order.quantity, available)
                hsm_remaining[pn] = available - producible
            else:
                producible = order.quantity

            tray_pieces = compute_tray_pieces(producible, self.tray_size)
            trays_needed = len(tray_pieces)
            if trays_needed == 0:
                continue

            if target_order_ids and order.order_id in target_order_ids:
                self.target_trays += trays_needed
            for pieces in tray_pieces:
                tray = Tray(
                    tray_id=tray_id,
                    order_id=order.order_id,
                    part_number_id=pn,
                    product_type=order.product_type,
                    pieces=pieces,
                )
                self.lm_input.put(tray)
                tray_id += 1
            self.order_trays[order.order_id] = trays_needed
            self.order_pieces[order.order_id] = sum(tray_pieces)

        # HSM continuation: keep producing remaining HSM stock after real orders.
        if hsm_active:
            for pn in sorted(hsm_remaining):
                remaining_pieces = hsm_remaining[pn]
                if remaining_pieces <= 0:
                    continue
                hsm_order_id = f"HSM_{pn}"
                tray_pieces = compute_tray_pieces(remaining_pieces, self.tray_size)
                for pieces in tray_pieces:
                    tray = Tray(
                        tray_id=tray_id,
                        order_id=hsm_order_id,
                        part_number_id=pn,
                        product_type=pn,
                        pieces=pieces,
                    )
                    self.lm_input.put(tray)
                    tray_id += 1
                self.order_trays[hsm_order_id] = len(tray_pieces)
                self.order_pieces[hsm_order_id] = sum(tray_pieces)
                self.order_max_parallel[hsm_order_id] = max(
                    1, int(math.ceil(remaining_pieces / self.order_parallel_qty))
                )

        self._next_tray_id = tray_id
        self.total_trays_all = tray_id - 1
        self.total_trays = self.target_trays if target_order_ids else self.total_trays_all

        if self.total_trays == 0:
            self.done_event.succeed()

    def _take_trays(self, order_id: Optional[str], part_number_id: str, count: int) -> List[Tray]:
        selected: List[Tray] = []
        remaining: List[Tray] = []
        for tray in list(self.lm_input.items):
            if (
                len(selected) < count
                and tray.part_number_id == part_number_id
                and (order_id is None or tray.order_id == order_id)
            ):
                selected.append(tray)
            else:
                remaining.append(tray)
        self.lm_input.items = remaining
        return selected

    def _apply_initial_state(self, initial_state: dict) -> None:
        completed = initial_state.get("completed", {}) if isinstance(initial_state, dict) else {}
        self.completed_trays = int(completed.get("trays", 0) or 0)
        self.completed_parts = int(completed.get("parts", 0) or 0)

        fallback_count = 0

        edm_stock = initial_state.get("edm_stock", {}) if isinstance(initial_state, dict) else {}
        for line_id, items in edm_stock.items() if isinstance(edm_stock, dict) else []:
            line = self.edm_lines.get(str(line_id))
            if not line:
                continue
            for item in items or []:
                raw_oid = item.get("order_id")
                oid_str = str(raw_oid).strip() if raw_oid is not None else ""
                item_order_id: Optional[str] = None if (not oid_str or oid_str.lower() in {"none", "null", "nan"}) else oid_str
                part_number_id = str(item.get("part_number_id"))
                trays_count = int(item.get("trays", 1) or 1)
                found_trays = self._take_trays(item_order_id, part_number_id, trays_count)
                # Fallback: create ad-hoc trays for buffer items not in the queue
                # (e.g. PNs already in the buffer but filtered out by HSM).
                while len(found_trays) < trays_count:
                    fallback = Tray(
                        tray_id=self._next_tray_id,
                        order_id=item_order_id or f"INITIAL_BUF_{line_id}",
                        part_number_id=part_number_id,
                        product_type=part_number_id,
                        pieces=self.tray_size,
                    )
                    self._next_tray_id += 1
                    fallback_count += 1
                    found_trays.append(fallback)
                for tray in found_trays:
                    # During initial state seeding we always accept the tray
                    # regardless of capacity — the live buffer is the ground
                    # truth and may exceed the model's steady-state limit.
                    # We expand the simpy.Store capacity if needed so it can
                    # physically hold all initial trays, then let the
                    # dispatcher drain it naturally over time.
                    if len(line.stock.store.items) >= line.stock.store.capacity:
                        line.stock.store._capacity = len(line.stock.store.items) + 1
                    line.stock.add_tray_direct(tray)
                    self.initial_buffer_tray_ids.add(tray.tray_id)
                    self.stock_events.append(
                        StockEvent(
                            line_id=str(line_id),
                            tray_id=tray.tray_id,
                            order_id=tray.order_id,
                            part_number_id=tray.part_number_id,
                            product_type=tray.product_type,
                            pieces=tray.pieces,
                            action="put",
                            time_sec=0.0,
                        )
                    )

        machines_state = initial_state.get("machines", []) if isinstance(initial_state, dict) else []
        machines_by_id = {m.spec.machine_id: m for m in self.lm_machines}
        machines_by_id.update({m.spec.machine_id: m for line in self.edm_lines.values() for m in line.machines})
        for item in machines_state:
            machine_id = str(item.get("machine_id"))
            self.initial_machine_ids.add(machine_id)
            raw_oid = item.get("order_id")
            oid_str = str(raw_oid).strip() if raw_oid is not None else ""
            machine_order_id: Optional[str] = None if (not oid_str or oid_str.lower() in {"none", "null", "nan"}) else oid_str
            part_number_id = str(item.get("part_number_id"))
            percent_complete = float(item.get("percent_complete", 0.0) or 0.0)
            machine = machines_by_id.get(machine_id)
            if not machine:
                continue
            trays = self._take_trays(machine_order_id, part_number_id, 1)
            if not trays:
                # Fallback: the PN may not be in the queue (e.g. non-HSM work
                # in progress or no open order for the current PN).
                # Always create the ad-hoc tray — get_cycle_time already has
                # its own fallback hierarchy (exact → closest PN same machine
                # → closest PN any machine), so never skip.
                fallback = Tray(
                    tray_id=self._next_tray_id,
                    order_id=machine_order_id or f"INITIAL_{machine_id}",
                    part_number_id=part_number_id,
                    product_type=part_number_id,
                    pieces=self.tray_size,
                )
                self._next_tray_id += 1
                fallback_count += 1
                trays = [fallback]
            tray = trays[0]
            time_per_piece = self.restrictions.get_cycle_time(machine.spec.machine_id, tray.part_number_id)
            process_sec = time_per_piece * tray.pieces
            # Use explicit remaining_sec from initial state if provided
            # (covers overrun machines where percent_complete >= 1.0 but the
            # tray is still physically inside — remaining_sec drives the actual
            # wait, defaulting to 0 so the machine unblocks immediately).
            explicit_remaining = item.get("remaining_sec")
            if explicit_remaining is not None and float(explicit_remaining) > 0:
                remaining_sec = float(explicit_remaining)
            elif percent_complete >= 1.0:
                # Overrun: tray is still inside but cycle time already elapsed.
                # Set remaining = 0 so the machine completes at t=0 in the sim
                # and immediately becomes available for the next job, rather than
                # being skipped (which would make it appear idle and receive a
                # second job on top of the existing one).
                remaining_sec = 0.0
            else:
                remaining_sec = process_sec * (1.0 - max(0.0, min(1.0, percent_complete)))
            machine.last_part_number = tray.product_type
            self._reserve_order_machine(tray, machine)
            activity = str(item.get("activity") or "process").lower()
            on_complete = self._on_lm_complete if machine.spec.process_type.upper() == "LASER" else self._on_edm_complete
            if activity in ("changeover", "test") and machine.spec.process_type.upper() == "EDM":
                # Machine is mid-setup in the real world: resume the current
                # phase with only the remaining duration, then continue with
                # the normal test/process phases.
                # We already set last_part_number = tray.product_type above, so
                # the changeover_lookup inside _run will return 0 for both
                # changeover_sec and test_sec (same PN → no new setup needed).
                # The startup_* params then inject exactly the remaining times.
                remaining_changeover = float(item.get("remaining_changeover_sec") or 0.0)
                remaining_test_f     = float(item.get("remaining_test_sec")       or 0.0)
                machine.start_process(
                    tray,
                    on_complete,
                    remaining_process_sec=None,
                    force_edm_startup=True,
                    startup_changeover_sec=remaining_changeover,
                    startup_test_sec=remaining_test_f,
                )
            else:
                machine.start_process(tray, on_complete, remaining_process_sec=remaining_sec)

        # Adjust totals for any fallback trays created.
        if fallback_count > 0:
            self.total_trays_all += fallback_count
            if not self.target_order_ids:
                self.total_trays += fallback_count

    def _on_lm_complete(self, tray: Tray, machine: Optional[Machine] = None) -> None:
        if machine is not None:
            self._release_order_machine(tray, machine)
        self.env.process(self._route_to_edm_stock(tray))

    def _on_edm_complete(self, tray: Tray, machine: Optional[Machine] = None) -> None:
        if machine is not None:
            self._release_order_machine(tray, machine)
        self.completed_trays += 1
        if self.target_order_ids:
            if tray.order_id in self.target_order_ids:
                self.completed_target_trays += 1
                self.completed_parts += tray.pieces
            if self.completed_target_trays >= self.total_trays:
                if not self.done_event.triggered:
                    self.done_event.succeed()
        else:
            self.completed_parts += tray.pieces
            if self.completed_trays >= self.total_trays:
                if not self.done_event.triggered:
                    self.done_event.succeed()

    def _max_parallel_for_order(self, order_id: str) -> int:
        # Seed/fallback order IDs (INITIAL_* prefix) are ad-hoc and not in
        # order_max_parallel. Allow all machines to work on them in parallel
        # so the initial buffer drains naturally instead of being throttled to 1.
        if order_id.startswith("INITIAL_"):
            return 9999
        return max(1, int(self.order_max_parallel.get(order_id, 1)))

    def _shift_key(self, time_sec: float) -> Tuple[int, int]:
        day_sec = 24 * 3600.0
        day_idx = int(time_sec // day_sec)
        sec_in_day = time_sec - (day_idx * day_sec)
        if sec_in_day < 9 * 3600.0:
            return day_idx, 0
        if sec_in_day < 18 * 3600.0:
            return day_idx, 1
        return day_idx, 2

    def _line_campaign_key(self, line_id: str, time_sec: float) -> Tuple[str, int, int]:
        day_idx, shift_idx = self._shift_key(time_sec)
        return line_id, day_idx, shift_idx

    def _shift_bounds(self, time_sec: float) -> Tuple[float, float]:
        day_sec = 24 * 3600.0
        day_idx = int(time_sec // day_sec)
        day_start = day_idx * day_sec
        sec_in_day = time_sec - day_start
        if sec_in_day < 9 * 3600.0:
            return day_start, day_start + 9 * 3600.0
        if sec_in_day < 18 * 3600.0:
            return day_start + 9 * 3600.0, day_start + 18 * 3600.0
        return day_start + 18 * 3600.0, day_start + 24 * 3600.0

    def _seconds_until_next_shift(self, time_sec: float) -> float:
        _shift_start, shift_end = self._shift_bounds(time_sec)
        return max(self.poll_interval_sec, shift_end - time_sec)

    def _get_line_campaign(self, line_id: str, time_sec: float) -> Optional[str]:
        return self.edm_line_shift_campaign.get(self._line_campaign_key(line_id, time_sec))

    def _set_line_campaign(self, line_id: str, time_sec: float, product_type: str) -> None:
        self.edm_line_shift_campaign[self._line_campaign_key(line_id, time_sec)] = product_type

    def _line_stock_fill_ratio(self, line: EdmLine) -> float:
        tray_capacity = max(1, int(line.stock.capacity_trays))
        part_capacity = max(1, int(line.stock.capacity_parts))
        tray_ratio = len(line.stock.store.items) / tray_capacity
        part_ratio = line.stock.current_parts / part_capacity
        return max(tray_ratio, part_ratio)

    def _global_edm_stock_fill_ratio(self) -> float:
        if not self.edm_lines:
            return 0.0
        return max(self._line_stock_fill_ratio(line) for line in self.edm_lines.values())

    def _compute_laser_shift_off_decision(self) -> bool:
        stock_fill_ratio = self._global_edm_stock_fill_ratio()
        laser_queue_trays = len(self.lm_input.items)
        edm_stock_trays = sum(len(line.stock.store.items) for line in self.edm_lines.values())
        completion_ratio = (
            self.completed_trays / max(1, self.total_trays)
            if self.total_trays > 0
            else 0.0
        )
        remaining_ratio = laser_queue_trays / max(1, self.total_trays_all)
        return (
            stock_fill_ratio >= self.laser_shift_off_stock_fill_ratio
            and laser_queue_trays <= self.laser_shift_off_max_queue_trays
            and completion_ratio >= self.laser_shift_off_min_completion_ratio
            and remaining_ratio <= self.laser_shift_off_max_remaining_ratio
            and edm_stock_trays > 0
        )

    def _laser_shift_off_likely_next_shift(self, time_sec: float) -> bool:
        if not self.laser_shift_off_policy:
            return False
        seconds_to_shift_end = self._seconds_until_next_shift(time_sec)
        if seconds_to_shift_end > self.laser_shift_off_guard_window_sec:
            return False
        return self._compute_laser_shift_off_decision()

    def _laser_step_duration_sec(self, machine: Machine, tray: Tray) -> float:
        durations = self.changeover_lookup.get(machine.last_part_number, tray.product_type)
        changeover_sec = durations.laser_min * 60.0
        process_sec = self.restrictions.get_cycle_time(machine.spec.machine_id, tray.part_number_id) * tray.pieces
        return changeover_sec + process_sec

    def _should_turn_off_laser_shift(self, time_sec: float) -> bool:
        if not self.laser_shift_off_policy:
            return False

        shift_key = self._shift_key(time_sec)
        cached = self.laser_shift_off_decision.get(shift_key)
        if cached is not None:
            return cached

        should_off = self._compute_laser_shift_off_decision()
        self.laser_shift_off_decision[shift_key] = should_off
        return should_off

    def _effective_max_parallel(
        self,
        order_id: str,
        process_type: str,
        time_sec: Optional[float] = None,
    ) -> int:
        base_parallel = self._max_parallel_for_order(order_id)
        process_upper = process_type.upper()
        if process_upper != "LASER":
            return base_parallel

        # Near a likely full LASER-off shift, relax stock throttling and finish in-shift.
        if time_sec is not None and self._laser_shift_off_likely_next_shift(time_sec):
            return base_parallel

        # If there are many distinct LASER orders waiting, spread capacity to avoid over-concentrating.
        distinct_laser_orders = len({tray.order_id for tray in self.lm_input.items})
        if distinct_laser_orders >= self.laser_diverse_queue_threshold:
            base_parallel = min(base_parallel, self.laser_parallel_cap_diverse_queue)

        # Protect intermediate EDM stock from saturation.
        stock_fill_ratio = self._global_edm_stock_fill_ratio()
        if stock_fill_ratio >= self.laser_high_stock_fill_ratio:
            return min(base_parallel, self.laser_parallel_cap_high_stock)
        if stock_fill_ratio >= self.laser_mid_stock_fill_ratio:
            return min(base_parallel, self.laser_parallel_cap_mid_stock)
        return base_parallel

    def _choose_line_campaign(
        self,
        line_id: str,
        trays: List[Tray],
    ) -> Optional[str]:
        line = self.edm_lines.get(line_id)
        if line is None or not trays:
            return None

        trays_by_product: Dict[str, List[Tray]] = {}
        for tray in trays:
            trays_by_product.setdefault(tray.product_type, []).append(tray)

        best_product: Optional[str] = None
        best_score = float("-inf")
        for product_type, product_trays in trays_by_product.items():
            eligible_machine_count = 0
            for machine in line.machines:
                if any(machine.can_process(tray, self.restrictions) for tray in product_trays):
                    eligible_machine_count += 1
            score = (len(product_trays) * 10.0) + (eligible_machine_count * 5.0)
            if score > best_score:
                best_score = score
                best_product = product_type
        return best_product

    def _reserve_order_machine(self, tray: Tray, machine: Machine) -> None:
        key = (tray.order_id, machine.spec.process_type.upper())
        active = self.order_active_machines.setdefault(key, set())
        active.add(machine.spec.machine_id)

        pins = self.order_machine_pins.setdefault(key, set())
        max_parallel = self._max_parallel_for_order(tray.order_id)
        if machine.spec.machine_id in pins or len(pins) < max_parallel:
            pins.add(machine.spec.machine_id)

        part_key = (tray.part_number_id, machine.spec.process_type.upper())
        if part_key not in self.part_preferred_machine:
            self.part_preferred_machine[part_key] = machine.spec.machine_id

    def _release_order_machine(self, tray: Tray, machine: Machine) -> None:
        key = (tray.order_id, machine.spec.process_type.upper())
        active = self.order_active_machines.get(key)
        if active is None:
            return
        active.discard(machine.spec.machine_id)
        if not active:
            self.order_active_machines.pop(key, None)

    def _route_to_edm_stock(self, tray: Tray) -> Generator:
        eligible_lines = [
            line for line in self.edm_lines.values() if line.can_process(tray, self.restrictions)
        ]
        if not eligible_lines:
            raise ValueError(f"No EDM line can process part_number_id {tray.part_number_id}")

        while True:
            feasible_lines = [line for line in eligible_lines if line.stock.has_space(tray)]
            if feasible_lines:
                best_line: Optional[EdmLine] = None
                best_score = float("-inf")
                for line in feasible_lines:
                    campaign = self._get_line_campaign(line.line_id, self.env.now)
                    compatible_machine_count = sum(
                        1 for machine in line.machines if machine.can_process(tray, self.restrictions)
                    )
                    same_product_in_stock = sum(
                        1 for stock_tray in line.stock.store.items if stock_tray.product_type == tray.product_type
                    )
                    fill_ratio = self._line_stock_fill_ratio(line)
                    # Count idle machines that can handle this tray — lines with
                    # more free capacity should receive work before lines that are
                    # already busy/stocked. This prevents all AGIE OP2 trays from
                    # piling up in Agie2/Agie3 while Agie1 starves.
                    idle_compatible = sum(
                        1 for machine in line.machines
                        if not machine.is_busy and machine.can_process(tray, self.restrictions)
                    )

                    score = 0.0
                    score += compatible_machine_count * 5.0
                    score += min(6.0, float(same_product_in_stock))
                    score -= fill_ratio * 10.0
                    score += idle_compatible * 8.0  # strongly favour lines with free machines
                    if campaign == tray.product_type:
                        score += 20.0
                    elif campaign is not None:
                        score -= 6.0

                    if score > best_score:
                        best_score = score
                        best_line = line

                if best_line is not None:
                    if self.campaign_mode and self._get_line_campaign(best_line.line_id, self.env.now) is None:
                        self._set_line_campaign(best_line.line_id, self.env.now, tray.product_type)

                    yield best_line.stock.put(tray)
                    self.stock_events.append(
                        StockEvent(
                            line_id=best_line.line_id,
                            tray_id=tray.tray_id,
                            order_id=tray.order_id,
                            part_number_id=tray.part_number_id,
                            product_type=tray.product_type,
                            pieces=tray.pieces,
                            action="put",
                            time_sec=self.env.now,
                        )
                    )
                    return
            yield self.env.timeout(self.poll_interval_sec)

    def _dispatcher(
        self,
        input_store: simpy.Store,
        machines: List[Machine],
        on_complete,
        label: str,
        stock_line_id: Optional[str] = None,
    ) -> Generator:
        while True:
            process_type = machines[0].spec.process_type.upper() if machines else ""
            if process_type == "LASER" and self._should_turn_off_laser_shift(self.env.now):
                yield self.env.timeout(self._seconds_until_next_shift(self.env.now))
                continue

            if not input_store.items:
                yield self.env.timeout(self.poll_interval_sec)
                continue

            free_machines = [m for m in machines if not m.is_busy]
            if not free_machines:
                yield simpy.AnyOf(self.env, [m.idle_event for m in machines])
                continue

            selected_index = None
            selected_machine = None
            selected_score = float("-inf")
            is_edm_line = process_type == "EDM" and stock_line_id is not None
            laser_shift_off_next = False
            laser_seconds_to_shift_end = 0.0
            if process_type == "LASER":
                laser_seconds_to_shift_end = self._seconds_until_next_shift(self.env.now)
                laser_shift_off_next = self._laser_shift_off_likely_next_shift(self.env.now)
            line_campaign: Optional[str] = None
            line_campaign_has_eligible = False
            if self.campaign_mode and is_edm_line:
                line_campaign = self._get_line_campaign(str(stock_line_id), self.env.now)
                if line_campaign is None:
                    planned_campaign = self._choose_line_campaign(
                        str(stock_line_id),
                        list(input_store.items),
                    )
                    if planned_campaign is not None:
                        self._set_line_campaign(str(stock_line_id), self.env.now, planned_campaign)
                        line_campaign = planned_campaign

                if line_campaign is not None:
                    for candidate in list(input_store.items):
                        if candidate.product_type != line_campaign:
                            continue
                        eligible = [m for m in free_machines if m.can_process(candidate, self.restrictions)]
                        if not eligible:
                            continue
                        order_key = (candidate.order_id, process_type)
                        active_machines = self.order_active_machines.get(order_key, set())
                        max_parallel = self._effective_max_parallel(
                            candidate.order_id,
                            process_type,
                            time_sec=self.env.now,
                        )
                        if len(active_machines) < max_parallel:
                            line_campaign_has_eligible = True
                            break

            stock_fill_ratio = self._global_edm_stock_fill_ratio() if process_type == "LASER" else 0.0
            for idx, candidate in enumerate(list(input_store.items)):
                eligible = [m for m in free_machines if m.can_process(candidate, self.restrictions)]
                if not eligible:
                    continue

                order_key = (candidate.order_id, process_type)
                pinned = self.order_machine_pins.get(order_key, set())
                active_machines = self.order_active_machines.get(order_key, set())
                max_parallel = self._effective_max_parallel(
                    candidate.order_id,
                    process_type,
                    time_sec=self.env.now,
                )
                if len(active_machines) >= max_parallel:
                    continue

                if (
                    self.campaign_mode
                    and line_campaign_has_eligible
                    and line_campaign is not None
                    and candidate.product_type != line_campaign
                ):
                    continue

                part_key = (candidate.part_number_id, process_type)
                preferred_id = self.part_preferred_machine.get(part_key)
                parallel_pressure = len(active_machines) / max(1, max_parallel)

                for machine in eligible:
                    if process_type == "LASER" and laser_shift_off_next:
                        # Keep LASER shift OFF aligned: avoid starting work that would spill into next off shift.
                        step_duration_sec = self._laser_step_duration_sec(machine, candidate)
                        if step_duration_sec > laser_seconds_to_shift_end:
                            continue

                    score = 0.0

                    if machine.last_part_number == candidate.product_type:
                        score += 120.0
                    if machine.spec.machine_id in pinned:
                        score += 35.0
                    if preferred_id and machine.spec.machine_id == preferred_id:
                        score += 12.0

                    if self.campaign_mode and line_campaign is not None:
                        if candidate.product_type == line_campaign:
                            score += 80.0
                        else:
                            score -= 45.0

                    # Prevent single-order monopolization when many alternatives exist.
                    score -= parallel_pressure * 18.0

                    # LASER uses stock-aware throttling to avoid overfilling EDM buffers.
                    if process_type == "LASER":
                        score += (1.0 - stock_fill_ratio) * 8.0

                    # Keep FIFO tendency in tie-breaks.
                    score -= idx * 0.02

                    if score > selected_score:
                        selected_score = score
                        selected_index = idx
                        selected_machine = machine

            if selected_index is None or selected_machine is None:
                yield self.env.timeout(self.poll_interval_sec)
                continue

            tray = cast(Tray, input_store.items.pop(selected_index))
            if self.campaign_mode and is_edm_line and line_campaign is None:
                self._set_line_campaign(str(stock_line_id), self.env.now, tray.product_type)

            if stock_line_id is not None:
                line = self.edm_lines.get(str(stock_line_id))
                if line is not None:
                    line.stock.remove_tray(tray)
            if stock_line_id is not None:
                self.stock_events.append(
                    StockEvent(
                        line_id=stock_line_id,
                        tray_id=tray.tray_id,
                        order_id=tray.order_id,
                        part_number_id=tray.part_number_id,
                        product_type=tray.product_type,
                        pieces=tray.pieces,
                        action="get",
                        time_sec=self.env.now,
                    )
                )
            force_edm_startup = bool(
                stock_line_id is not None
                and tray.tray_id in self.initial_buffer_tray_ids
                and selected_machine.spec.process_type.upper() == "EDM"
                and selected_machine.last_part_number is None
                and selected_machine.spec.machine_id not in self.initial_machine_ids
            )
            if force_edm_startup:
                self.initial_buffer_tray_ids.discard(tray.tray_id)
            self._reserve_order_machine(tray, selected_machine)
            selected_machine.start_process(
                tray,
                on_complete,
                force_edm_startup=force_edm_startup,
                startup_changeover_sec=self.edm_startup_changeover_sec,
                startup_test_sec=self.edm_startup_test_sec,
            )

    def _all_machines(self) -> List[Machine]:
        return self.lm_machines + [m for line in self.edm_lines.values() for m in line.machines]

    def _finalize_open_events(self) -> None:
        end_sec = self.env.now
        busy_activities = {"process", "changeover", "test"}
        for machine in self._all_machines():
            if not machine.is_busy or machine.current_tray is None:
                continue
            if machine.current_activity is None or machine.current_activity_start_sec is None:
                continue
            if machine.current_activity_start_sec >= end_sec:
                continue

            tray = machine.current_tray
            self.event_log.append(
                EventLog(
                    machine_id=machine.spec.machine_id,
                    process_type=machine.spec.process_type.upper(),
                    line_id=machine.spec.line_id,
                    tray_id=tray.tray_id,
                    order_id=tray.order_id,
                    part_number_id=tray.part_number_id,
                    product_type=tray.product_type,
                    pieces=tray.pieces,
                    activity=machine.current_activity,
                    start_sec=machine.current_activity_start_sec,
                    end_sec=end_sec,
                )
            )
            if machine.current_activity in busy_activities:
                machine.busy_time += end_sec - machine.current_activity_start_sec

            machine.current_activity = None
            machine.current_activity_start_sec = None

    def _snapshotter(self) -> Generator:
        if self.summary_interval_sec <= 0:
            return

        self._record_summary()

        while not self.done_event.triggered:
            timeout_event = self.env.timeout(self.summary_interval_sec)
            yield simpy.AnyOf(self.env, [timeout_event, self.done_event])
            self._record_summary()

    def _record_summary(self) -> None:
        edm_stock_trays = {line_id: len(line.stock.store.items) for line_id, line in self.edm_lines.items()}
        busy_laser = sum(1 for m in self.lm_machines if m.is_busy)
        busy_edm = sum(1 for line in self.edm_lines.values() for m in line.machines if m.is_busy)
        machine_status: Dict[str, Tuple[Optional[str], Optional[int]]] = {}
        for machine in self._all_machines():
            if machine.current_tray is None:
                machine_status[machine.spec.machine_id] = (None, None)
            else:
                machine_status[machine.spec.machine_id] = (
                    machine.current_tray.part_number_id,
                    machine.current_tray.tray_id,
                )

        summary = StepSummary(
            time_sec=self.env.now,
            lm_queue_trays=len(self.lm_input.items),
            edm_stock_trays=edm_stock_trays,
            busy_laser_machines=busy_laser,
            busy_edm_machines=busy_edm,
            completed_trays=self.completed_trays,
            total_trays=self.total_trays,
            completed_parts=self.completed_parts,
            machine_status=machine_status,
        )
        self.step_summaries.append(summary)
        if self.progress_hook is not None:
            self.progress_hook(summary)


def _read_csv(path: Path) -> List[dict]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        header = file.readline()
        delimiter = ";" if ";" in header and "," not in header else ","
        file.seek(0)
        return list(csv.DictReader(file, delimiter=delimiter))


def _is_missing(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    if isinstance(value, str):
        cleaned = value.strip()
        if cleaned == "":
            return True
        if cleaned.lower() in {"nan", "none", "null"}:
            return True
    return False


def _parse_float(value: object) -> float:
    if _is_missing(value):
        raise ValueError("Missing numeric value")
    if isinstance(value, (int, float)):
        return float(value)
    cleaned = str(value).strip().replace(",", ".")
    return float(cleaned)


def _load_orders_from_rows(rows: List[dict]) -> List[Order]:
    orders: List[Order] = []
    for row in rows:
        order_id_raw = row.get("order_id") or row.get("Order") or row.get("ORDER_ID")
        part_number_id_raw = row.get("part_number_id") or row.get("PartNumber") or row.get("partnumber")
        if _is_missing(order_id_raw) or _is_missing(part_number_id_raw):
            continue

        quantity_raw = row.get("quantity")
        if _is_missing(quantity_raw):
            quantity_raw = row.get("real_qtty")
        if _is_missing(quantity_raw):
            quantity_raw = row.get("real_qqty")
        if _is_missing(quantity_raw):
            continue
        try:
            quantity = int(float(cast(float | int | str, quantity_raw)))
        except (TypeError, ValueError):
            continue
        if quantity <= 0:
            continue

        order_id = str(order_id_raw).strip()
        part_number_id = str(part_number_id_raw).strip()
        product_type_raw = row.get("product_type")
        product_type = part_number_id if _is_missing(product_type_raw) else str(product_type_raw).strip()

        orders.append(
            Order(
                order_id=order_id,
                part_number_id=part_number_id,
                product_type=product_type,
                quantity=quantity,
            )
        )
    return orders


def load_orders(path: Path) -> List[Order]:
    rows = _read_csv(path)
    return _load_orders_from_rows(rows)


def _load_machines_from_rows(rows: List[dict]) -> List[MachineSpec]:
    specs: List[MachineSpec] = []
    for row in rows:
        machine_id_raw = row.get("machine_id") or row.get("Machine") or row.get("MACHINE_ID")
        process_type_raw = row.get("process_type") or row.get("Process") or row.get("PROCESS_TYPE")
        machine_type_raw = row.get("machine_type") or row.get("MachineType") or row.get("MACHINE_TYPE")
        line_id_raw = row.get("line_id") or row.get("Line") or row.get("LINE_ID")
        if (
            _is_missing(machine_id_raw)
            or _is_missing(process_type_raw)
            or _is_missing(machine_type_raw)
            or _is_missing(line_id_raw)
        ):
            continue

        machine_id = str(machine_id_raw).strip()
        process_type = str(process_type_raw).strip()
        machine_type = str(machine_type_raw).strip()
        line_id = str(line_id_raw).strip()

        specs.append(
            MachineSpec(
                machine_id=machine_id,
                process_type=process_type,
                machine_type=machine_type,
                line_id=line_id,
            )
        )
    return specs


def load_machines(path: Path) -> List[MachineSpec]:
    rows = _read_csv(path)
    return _load_machines_from_rows(rows)


def _load_restrictions_from_rows(
    rows: List[dict],
    machine_specs: Optional[List[MachineSpec]] = None,
) -> Restrictions:
    allowed: set[Tuple[str, str, str, str]] = set()
    cycle_times: Dict[Tuple[str, str], float] = {}
    for row in rows:
        part_number_id_raw = row.get("part_number_id")
        process_type_raw = row.get("process_type")
        machine_type_raw = row.get("machine_type")

        if _is_missing(part_number_id_raw) or _is_missing(process_type_raw) or _is_missing(machine_type_raw):
            continue

        part_number_id = str(part_number_id_raw).strip()
        process_type = str(process_type_raw).strip().upper()
        machine_type = str(machine_type_raw).strip()

        machine_id_raw = row.get("machine_id")
        if not _is_missing(machine_id_raw):
            machine_id = str(machine_id_raw).strip()
            cycle_time_raw = row.get("cycle_time")
            if _is_missing(cycle_time_raw):
                cycle_time_raw = row.get("process_time_seconds_per_part")
            if _is_missing(cycle_time_raw):
                continue
            allowed.add(
                (
                    process_type,
                    machine_type,
                    machine_id,
                    part_number_id,
                )
            )
            cycle_times[(machine_id, part_number_id)] = _parse_float(cycle_time_raw)
        else:
            if machine_specs is None:
                raise ValueError("machine_specs required for restrictions without machine_id")
            line_id_raw = row.get("line_id")
            line_id = None if _is_missing(line_id_raw) else str(line_id_raw).strip()
            process_time_raw = row.get("process_time_seconds_per_part")
            if _is_missing(process_time_raw):
                process_time_raw = row.get("cycle_time")
            if _is_missing(process_time_raw):
                continue
            for spec in machine_specs:
                if (
                    spec.process_type.upper() == process_type
                    and spec.machine_type == machine_type
                    and (line_id is None or spec.line_id == line_id)
                ):
                    allowed.add(
                        (
                            process_type,
                            machine_type,
                            spec.machine_id,
                            part_number_id,
                        )
                    )
                    cycle_times[(spec.machine_id, part_number_id)] = _parse_float(process_time_raw)
    return Restrictions(allowed, cycle_times)


def load_restrictions(path: Path, machine_specs: Optional[List[MachineSpec]] = None) -> Restrictions:
    rows = _read_csv(path)
    return _load_restrictions_from_rows(rows, machine_specs)


def load_orders_from_dataframe(rows: List[dict]) -> List[Order]:
    return _load_orders_from_rows(rows)


def load_machines_from_dataframe(rows: List[dict]) -> List[MachineSpec]:
    return _load_machines_from_rows(rows)


def load_restrictions_from_dataframe(
    rows: List[dict],
    machine_specs: Optional[List[MachineSpec]] = None,
) -> Restrictions:
    return _load_restrictions_from_rows(rows, machine_specs)


def load_changeovers(path: Path) -> ChangeoverLookup:
    import pandas as pd

    df = pd.read_csv(path, sep=";", dtype=str)
    df.columns = df.columns.str.strip()
    required = {"from_type", "to_type", "laser_changeover_duration_min",
                "edm_changeover_duration_min", "edm_test_duration_min"}
    if not required.issubset(df.columns):
        # Fall back to comma delimiter
        df = pd.read_csv(path, sep=",", dtype=str)
        df.columns = df.columns.str.strip()

    df = df.dropna(subset=["from_type", "to_type"])
    df = df[df["from_type"].str.strip() != ""]
    df = df[df["to_type"].str.strip() != ""]

    for col in ["laser_changeover_duration_min", "edm_changeover_duration_min", "edm_test_duration_min"]:
        df[col] = df[col].str.strip().str.replace(",", ".").astype(float)

    from_vals = df["from_type"].str.strip().values
    to_vals = df["to_type"].str.strip().values
    laser = df["laser_changeover_duration_min"].values
    edm = df["edm_changeover_duration_min"].values
    edm_test = df["edm_test_duration_min"].values

    mapping: Dict[Tuple[str, str], ChangeoverDurations] = {}
    for i in range(len(df)):
        a, b = from_vals[i], to_vals[i]
        key = (a, b) if a < b else (b, a)
        mapping[key] = ChangeoverDurations(
            laser_min=float(laser[i]),
            edm_min=float(edm[i]),
            edm_test_min=float(edm_test[i]),
        )
    return ChangeoverLookup(mapping)
