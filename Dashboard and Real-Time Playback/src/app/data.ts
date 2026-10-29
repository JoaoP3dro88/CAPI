
// Bosch Color Palette
export const BOSCH_COLORS = {
  red: "#FF3732",      // Bosch Red 50
  blue: "#007BC0",     // Bosch Blue 50
  lightBlue: "#0096D6", // Bosch Blue 40 (Lighter)
  turquoise: "#18837E", // Bosch Turquoise 50
  green: "#00884A",    // Bosch Green 50
  purple: "#963886",   // Bosch Purple 50
  gray: "#BFC0C2",     // Bosch Gray 50
  darkGray: "#525F6B", // Bosch Gray 80 (Darker)
  black: "#000000",
  white: "#FFFFFF",
  lightGray: "#EFF1F2", // Bosch Gray 10
  
  // Specific applications
  chart: {
    process: "#007BC0", // Blue for main process
    changeover: "#FFCF00", // Bosch Yellow/Warning for changeover
    test: "#963886",    // Purple for test
    waiting: "#BFC0C2", // Bosch Gray for waiting
    inactive: "#6E7B85", // Darker gray for inactive
    idle: "#EFF1F2",    // Light gray for idle
  },
  
  traffic: {
    green: "#00884A",
    yellow: "#FFCF00",
    red: "#FF3732",
  }
};

export const KPI_THRESHOLDS = {
  oee: {
    green: 0.75,
    yellow: 0.6,
  },
  changeover_loss_percentage: {
    green: 10,
    yellow: 20,
  },
};

export function getTrafficColor(
  value: number,
  thresholds: { green: number; yellow: number },
  options?: { higherIsBetter?: boolean }
): string {
  const higherIsBetter = options?.higherIsBetter ?? true;
  if (higherIsBetter) {
    if (value >= thresholds.green) return BOSCH_COLORS.traffic.green;
    if (value >= thresholds.yellow) return BOSCH_COLORS.traffic.yellow;
    return BOSCH_COLORS.traffic.red;
  }

  if (value <= thresholds.green) return BOSCH_COLORS.traffic.green;
  if (value <= thresholds.yellow) return BOSCH_COLORS.traffic.yellow;
  return BOSCH_COLORS.traffic.red;
}

// --- DATA TYPES & JSON DOCUMENTATION ---

export interface KPI {
  total_time_sec: number;
  total_trays: number;
  total_parts: number;
  parts_per_hour: number;
  global_oee: number; // OEE proxy
  changeover_loss_percentage: number; // (Total Changeover Time / (Total Time * Machines)) * 100
  waiting_loss_percentage: number; // (Total Waiting Time / (Total Time * Machines)) * 100
  orders_count: number;
  machines_count: number;
  lines_count: number;
  total_changeover_sec: number;
  total_waiting_sec: number;
  total_changeovers: number;
  changeovers_per_machine: number;
  total_test_sec: number;
  total_process_sec: number;
}

export interface MachineInfo {
  machine_id: string;
  process_type: "LASER" | "EDM";
  line_id: string;
  machine_type: string;
  static_oee: number; // End-of-shift OEE
}

export interface TimelineEvent {
  machine_id: string;
  process_type: "LASER" | "EDM";
  line_id: string;
  tray_id: number;
  order_id: string;
  part_number_id: string;
  product_type: string;
  activity: "process" | "changeover" | "waiting" | "test" | "inactive" | "idle";
  start_sec: number;
  end_sec: number;
}

export interface TrayDetail {
  tray_id: number;
  order_id: string;
  part_number_id: string;
  product_type: string;
  pieces?: number; // actual piece count for this tray (from simulation)
}

export interface StockInfo {
  line_id: string;
  trays: number;
  parts?: number;
  tray_details: TrayDetail[];
}

export interface OrderInfo {
  order_id: string;
  part_number_id: string;
  product_type: string; // e.g., "CRIN" (HE), "PLV" (Assembly)
  quantity: number;
  trays: number;
  produced_pieces: number;
  completed_trays?: number;
  completion_pct?: number; // 0..1
}

// Optional: additional order metadata (outside frames) to support due-date based UI.
// This is a lookup table keyed by order_id.
export interface OrderMeta {
  order_id: string;
  due_date?: string; // ISO date (YYYY-MM-DD)
  title?: string;
  part_number_id?: string;
  quantity?: number;
}

export interface DashboardData {
  metadata?: {
    facility_timezone?: string;
  };
  kpis: KPI;
  machines: MachineInfo[];
  timeline: TimelineEvent[];
  stock_final: StockInfo[];
  orders: OrderInfo[];
}

export interface MachineState {
  activity: "process" | "changeover" | "waiting" | "test" | "inactive" | "idle";
  order_id?: string;
  part_number_id?: string;
  product_type?: string;
  tray_id?: number;
  start_sec?: number;
  end_sec?: number;
  current_oee: number | null;
  rfid_id?: string | null; // Last RFID card scanned (LASER machines only)
}

export interface RealtimeMachine extends MachineInfo {
  state: MachineState;
}

export interface RealtimeFrame {
  time_sec: number;
  backlog_orders: string[];
  produced_orders: OrderInfo[];
  stock_levels: StockInfo[];
  machines: RealtimeMachine[];
}

// Hard Stage Market (optional payload in realtime.json)
export interface HardStageMarketItem {
  body_partnumber: string;
  body_qtty: number;
  nozzles: string[]; // list of nozzle partnumbers compatible with this body
}

export interface RealtimeData {
  metadata: {
    step_sec: number;
    total_time_sec: number;
    generated_at: string;
    facility_timezone?: string;
  };
  // Optional: lookup for order metadata.
  // Example: orders_by_id["1372875248"].due_date = "2026-02-23"
  orders_by_id?: Record<string, OrderMeta>;
  // Optional: current stock of the Hard Stage Market.
  // If provided, it should represent the *current* stock (not per-frame).
  // Example:
  // "hard_stage_market": [{ "body_partnumber": "0433172203", "body_qtty": 120, "nozzles": ["...", "..."] }]
  hard_stage_market?: HardStageMarketItem[];
  frames: RealtimeFrame[];
}
// --- LIVE DATA TYPES ---

/**
 * LiveData is a single-frame snapshot of the production floor, updated in real time.
 * Unlike RealtimeData (which contains many frames for playback), LiveData always
 * represents the current state of the factory — as if you took ONE frame from realtime.json
 * and served it directly from the database.
 *
 * Example livedata.json structure:
 * {
 *   "metadata": {
 *     "generated_at": "2026-02-24T14:32:00Z",
 *     "current_time_label": "14:32"
 *   },
 *   "orders_by_id": { ... },
 *   "hard_stage_market": [ ... ],
 *   "snapshot": {
 *     "time_sec": 52320,
 *     "backlog_orders": ["0632980875", "0632326261"],
 *     "produced_orders": [ ... ],
 *     "stock_levels": [ ... ],
 *     "machines": [ ... ]
 *   }
 * }
 */
export interface LiveMetadata {
  generated_at: string;          // ISO timestamp of when this snapshot was generated
  data_last_updated?: string;    // ISO timestamp of the most recent DT_UPDATE in the source DB
  current_time_label?: string;   // Human-readable time label, e.g. "14:32"
  facility_timezone?: string;    // IANA timezone of the production facility, e.g. "America/Sao_Paulo"
}

export interface LaserSummaryItem {
  part_number_id: string;
  waiting_trays: number;   // trays that entered Laser but haven't left yet
  done_trays: number;      // trays that left Laser this shift
  waiting_pieces: number;  // pieces still at Laser
  done_pieces: number;     // pieces that already passed through Laser
}

/** One open order linked to a TypeNumber in a final market. */
export interface FinalMarketOrder {
  order_id: string;
  quantity: number;
  due_date: string;          // YYYY-MM-DD
  remaining_needed: number;  // pieces still needed to fulfil this order
}

/**
 * One TypeNumber row inside HE Market or Packaging Market.
 * `total_pieces` is the REMAINING pieces after fulfilled orders have been subtracted.
 * `pending_orders` are open orders sorted oldest-first.
 */
export interface FinalMarketItem {
  part_number_id: string;
  total_pieces: number;
  oldest_entry_date: string | null;  // ISO — when the first tray arrived
  pending_orders: FinalMarketOrder[];
}

export interface LiveData {
  metadata: LiveMetadata;
  orders_by_id?: Record<string, OrderMeta>;
  hard_stage_market?: HardStageMarketItem[];
  // A single frame representing the current state
  snapshot: RealtimeFrame & {
    laser_summary?: LaserSummaryItem[];  // real-time Laser flow (not in simulation frames)
    shift_start_hour?: number;           // 0, 6, or 15
    finished_goods?: StockInfo[];        // trays in the output supermarket (after AGIE Saída) — legacy
    he_market?: FinalMarketItem[];       // TypeNumbers destined for HE
    packaging_market?: FinalMarketItem[]; // TypeNumbers destined for Packaging
  };
}

export interface HistoryData {
  metadata: {
    generated_at: string;
    description?: string;
    snapshot_time_sec: number;   // seconds from shift start at which the snapshot was taken
    snapshot_time_label?: string; // e.g. "15:00"
    facility_timezone?: string;  // IANA timezone, e.g. "America/Sao_Paulo"
  };
  timeline: TimelineEvent[];
}

const BASE_URL = import.meta.env.BASE_URL ?? '/';
const DASHBOARD_URL = import.meta.env.VITE_DASHBOARD_JSON_URL ?? `${BASE_URL}dashboard.json`;
const REALTIME_URL  = import.meta.env.VITE_REALTIME_JSON_URL  ?? `${BASE_URL}realtime.json`;
const HISTORY_URL   = import.meta.env.VITE_HISTORY_JSON_URL   ?? `${BASE_URL}history.json`;
const SIMULATION_API_BASE = import.meta.env.VITE_SIMULATION_API_BASE_URL ?? '';

// Live data source.
// File-based mode (default):  points to public/livedata.json served by Vite / IIS / Nginx.
// API mode:                   set VITE_LIVEDATA_JSON_URL=http://server:8000/api/livedata
//                             before building. No other changes are needed.
const LIVEDATA_URL  = import.meta.env.VITE_LIVEDATA_JSON_URL  ?? `${BASE_URL}livedata.json`;

function deriveSimulationApiBaseFromLiveDataUrl(): string {
  const marker = '/api/livedata';
  const idx = LIVEDATA_URL.indexOf(marker);
  if (idx === -1) return '';
  return LIVEDATA_URL.slice(0, idx).replace(/\/$/, '');
}

function simulationApiUrl(path: string): string {
  const resolvedBase = (SIMULATION_API_BASE || deriveSimulationApiBaseFromLiveDataUrl()).replace(/\/$/, '');
  if (!resolvedBase) return path;
  return `${resolvedBase}${path}`;
}

export type SimulationJobState = 'queued' | 'running' | 'succeeded' | 'failed';

export interface SimulationJob {
  job_id: string;
  status: SimulationJobState;
  created_at: string;
  started_at?: string | null;
  finished_at?: string | null;
  data_source: 'db' | 'csv';
  max_sim_hours: number;
  input_filter?: string | null;
  use_live_initial_state: boolean;
  has_results: boolean;
  message?: string | null;
  error?: string | null;
  progress?: number | null;
}

export interface SimulationStatusResponse {
  has_results: boolean;
  active_job: SimulationJob | null;
  /** ISO timestamp of when the simulation was run (t=0 anchor for the playback slider). */
  sim_ref_dt: string | null;
}

export interface RunSimulationRequest {
  data_source?: 'db' | 'csv';
  max_sim_hours?: number;
  input_filter?: string;
  use_live_initial_state?: boolean;
  /** Machine IDs to exclude from simulation (e.g. manualOff from Live View settings). */
  excluded_machines?: string[];
}

export async function fetchSimulationStatus(): Promise<SimulationStatusResponse> {
  const url = simulationApiUrl('/api/simulation/status');
  const response = await fetch(url, { cache: 'no-store' });
  if (!response.ok) {
    throw new Error(`Failed to load simulation status: ${response.status} (${url})`);
  }
  return response.json();
}

export async function fetchSimulationJob(jobId: string): Promise<SimulationJob> {
  const url = simulationApiUrl(`/api/simulation/jobs/${encodeURIComponent(jobId)}`);
  const response = await fetch(url, { cache: 'no-store' });
  if (!response.ok) {
    throw new Error(`Failed to load simulation job: ${response.status} (${url})`);
  }
  return response.json();
}

export async function runSimulation(request?: RunSimulationRequest): Promise<{ job_id: string; status: string; job_url: string }> {
  const url = simulationApiUrl('/api/simulation/run');
  const response = await fetch(url, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      data_source: request?.data_source ?? 'db',
      max_sim_hours: request?.max_sim_hours ?? 24,
      input_filter: request?.input_filter,
      use_live_initial_state: request?.use_live_initial_state ?? true,
      excluded_machines: request?.excluded_machines ?? [],
    }),
  });
  if (!response.ok) {
    const errorBody = await response.text();
    throw new Error(`Failed to run simulation: ${response.status} (${url}) ${errorBody}`);
  }
  return response.json();
}

export async function fetchDashboardData(): Promise<DashboardData> {
  const response = await fetch(DASHBOARD_URL, { cache: 'no-store' });
  if (!response.ok) {
    throw new Error(`Failed to load dashboard data: ${response.status}`);
  }
  return response.json();
}

export async function fetchRealtimeData(): Promise<RealtimeData> {
  const response = await fetch(REALTIME_URL, { cache: 'no-store' });
  if (!response.ok) {
    throw new Error(`Failed to load realtime data: ${response.status}`);
  }
  return response.json();
}

export async function fetchLiveData(params?: { oeeHours?: number; offHours?: number; marketTimeoutHours?: number; defaultTestMin?: number }): Promise<LiveData> {
  let url = LIVEDATA_URL;
  if (params) {
    const q = new URLSearchParams();
    if (params.oeeHours !== undefined) q.set('oee_hours', String(params.oeeHours));
    if (params.offHours !== undefined) q.set('off_hours', String(params.offHours));
    if (params.marketTimeoutHours !== undefined) q.set('market_timeout_hours', String(params.marketTimeoutHours));
    if (params.defaultTestMin !== undefined) q.set('default_test_min', String(params.defaultTestMin));
    const qs = q.toString();
    if (qs) {
      const sep = url.includes('?') ? '&' : '?';
      url = `${url}${sep}${qs}`;
    }
  }
  const response = await fetch(url, { cache: 'no-store' });
  if (!response.ok) {
    throw new Error(`Failed to load live data: ${response.status}`);
  }
  return response.json();
}

export async function fetchHistoryData(): Promise<HistoryData> {
  const response = await fetch(HISTORY_URL, { cache: 'no-store' });
  if (!response.ok) {
    throw new Error(`Failed to load history data: ${response.status}`);
  }
  return response.json();
}
