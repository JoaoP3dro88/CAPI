import React, { useState, useEffect, useMemo, useCallback, useRef } from 'react';
import {
  BOSCH_COLORS,
  HardStageMarketItem,
  KPI_THRESHOLDS,
  getTrafficColor,
  LaserSummaryItem,
  FinalMarketItem,
  OrderMeta,
  LiveData,
  RealtimeMachine,
  StockInfo,
  fetchLiveData,
} from '../data';
import { Card } from './ui/Card';
import { StockDetailModal } from './ui/StockDetailModal';
import { SettingsModal, LiveSettings, loadSettings, saveSettings } from './ui/SettingsModal';
import { VirtualList } from './ui/VirtualList';
import { RefreshCw, Clock, Maximize2, X, Grid, Activity, Layers, Wifi, WifiOff, ChevronDown, Settings, Search } from 'lucide-react';
import { motion, AnimatePresence } from 'motion/react';
import clsx from 'clsx';
import { createPortal } from 'react-dom';

// How often (ms) to re-fetch live data from the server.
// File-based mode (livedata.json): data is refreshed hourly by scheduler.py, so polling every
// hour avoids redundant requests.
// API mode (src/api.py): the endpoint is always fresh, so a shorter interval is appropriate.
// Override at build time with VITE_LIVE_POLL_INTERVAL_MS (value in milliseconds).
const POLL_INTERVAL_MS: number =
  Number(import.meta.env.VITE_LIVE_POLL_INTERVAL_MS) || 60 * 60 * 1000; // default: 1 hour

const formatTimeSec = (seconds: number) => {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
};

const getCompletionColor = (pct: number) => {
  if (pct >= 0.8) return BOSCH_COLORS.traffic.green;
  if (pct >= 0.5) return BOSCH_COLORS.traffic.yellow;
  return BOSCH_COLORS.traffic.red;
};

const normalizeLiveText = (value?: string | null) => {
  const normalized = value?.trim();
  if (!normalized) return null;
  return ['nan', 'none', 'null'].includes(normalized.toLowerCase()) ? null : normalized;
};

const getMachinePrimaryLabel = (machine: RealtimeMachine) => (
  normalizeLiveText(machine.state.part_number_id) ?? normalizeLiveText(machine.state.order_id)
);

const getEffectiveMachineActivity = (machine: RealtimeMachine) => {
  if (machine.state.activity !== 'process') return machine.state.activity;
  return getMachinePrimaryLabel(machine) ? 'process' : 'idle';
};

// --- Sub-components (identical visuals to RealtimePlayback) ---

const MachineCard = ({
  machine,
  dense = false,
}: {
  machine: RealtimeMachine;
  dense?: boolean;
}) => {
  const { state } = machine;
  const effectiveActivity = getEffectiveMachineActivity(machine);
  const primaryLabel = getMachinePrimaryLabel(machine);

  // Format elapsed seconds into "Xh Ym" or "Ym" label
  const elapsedLabel = (() => {
    const sec = (state as any).elapsed_sec as number | undefined;
    if (!sec || sec <= 0) return null;
    const h = Math.floor(sec / 3600);
    const m = Math.floor((sec % 3600) / 60);
    if (h > 0) return `${h}h ${m}m`;
    if (m > 0) return `${m}m`;
    return '<1m';
  })();

  const displayMachineId = (id: string) => {
    if (id.startsWith('EDM')) {
      const number = id.replace('EDM', '').trim();
      return `Agie ${number}`;
    }
    return id;
  };

  let statusColor = BOSCH_COLORS.gray;
  let statusText = 'IDLE';

  if (effectiveActivity === 'process') {
    statusColor = BOSCH_COLORS.chart.process;
    statusText = 'RUNNING';
  } else if (effectiveActivity === 'changeover') {
    statusColor = BOSCH_COLORS.chart.changeover;
    statusText = 'SETUP';
  } else if (effectiveActivity === 'waiting') {
    statusColor = BOSCH_COLORS.chart.waiting;
    statusText = 'WAIT';
  } else if (effectiveActivity === 'inactive') {
    statusColor = BOSCH_COLORS.chart.inactive;
    statusText = 'OFF';
  } else if (effectiveActivity === 'test') {
    statusColor = BOSCH_COLORS.chart.test;
    statusText = 'TEST';
  }

  return (
    <motion.div
      layout
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      className={clsx(
        'bg-white border shadow-sm relative overflow-hidden transition-all hover:shadow-md cursor-default',
        dense ? 'p-2' : 'p-3'
      )}
      style={{ borderLeft: `4px solid ${statusColor}` }}
    >
      <div className="flex justify-between items-start mb-1">
        <span className={clsx('font-bold text-[#000000]', dense ? 'text-xs' : 'text-sm')}>
          {displayMachineId(machine.machine_id)}
        </span>
        {!dense && (
          <div className="flex items-center gap-2">
            {state.current_oee != null ? (
              <span
                className="text-[10px] font-mono px-1 rounded"
                style={{
                  color: getTrafficColor(state.current_oee, KPI_THRESHOLDS.oee, { higherIsBetter: true }),
                  backgroundColor: BOSCH_COLORS.lightGray,
                }}
              >
                OEE: {(state.current_oee * 100).toFixed(0)}%
              </span>
            ) : (
              <span
                className="text-[10px] font-mono px-1 rounded"
                style={{ color: BOSCH_COLORS.gray, backgroundColor: BOSCH_COLORS.lightGray }}
              >
                OEE: —
              </span>
            )}
            <span
              className="text-[10px] font-bold px-1.5 py-0.5 rounded text-white"
              style={{ backgroundColor: statusColor }}
            >
              {statusText}
            </span>
          </div>
        )}
      </div>

      <div className="space-y-1">
        {effectiveActivity !== 'idle' && primaryLabel ? (
          <>
            <div className={clsx('font-medium text-[#007BC0] truncate', dense ? 'text-[10px]' : 'text-xs')}>
              {primaryLabel}
            </div>
            {!dense && machine.process_type === 'LASER' && state.rfid_id && (
              <div className="text-[10px] text-[#525F6B] truncate">
                RFID: {state.rfid_id}
              </div>
            )}
            {!dense && machine.process_type !== 'LASER' && state.order_id && (
              <div className="text-[10px] text-[#525F6B] truncate">{state.order_id}</div>
            )}
            {/* Elapsed time badge — replaces the progress bar (no end_sec in live mode) */}
            {elapsedLabel && !dense && (
              <div className="text-[9px] text-[#525F6B] font-mono mt-1">
                ⏱ {elapsedLabel}
              </div>
            )}
          </>
        ) : (
          <div
            className={clsx(
              'flex items-center text-[#BFC0C2] italic',
              dense ? 'h-4 text-[9px]' : 'h-8 text-[10px] justify-center'
            )}
          >
            {dense ? 'Idle' : 'Waiting...'}
          </div>
        )}
      </div>
    </motion.div>
  );
};

const ZoneSummaryCard = ({
  lineId,
  machines,
  onClick,
}: {
  lineId: string;
  machines: RealtimeMachine[];
  onClick: () => void;
}) => {
  const displayLineId = (id: string) => {
    if (id.startsWith('Agie')) {
      const number = id.replace('Agie', '').trim();
      return `Corredor ${number}`;
    }
    return id;
  };
  const displayMachineId = (id: string) => {
    if (id.startsWith('EDM')) {
      const number = id.replace('EDM', '').trim();
      return `Agie ${number}`;
    }
    return id;
  };

  const total = machines.length;
  const running = machines.filter((m) => getEffectiveMachineActivity(m) === 'process').length;
  const setup = machines.filter((m) => getEffectiveMachineActivity(m) === 'changeover').length;
  const idle = machines.filter((m) => getEffectiveMachineActivity(m) === 'idle').length;
  const inactive = machines.filter((m) => getEffectiveMachineActivity(m) === 'inactive').length;
  const testing = machines.filter((m) => getEffectiveMachineActivity(m) === 'test').length;
  // Utilization = machines actively working (running + setup) out of total
  const utilization = total > 0 ? Math.round(((running + setup) / total) * 100) : 0;
  const avgOee = (() => {
    const runningMachines = machines.filter((m) => getEffectiveMachineActivity(m) === 'process' && (m.state.current_oee ?? 0) > 0);
    if (runningMachines.length === 0) return 0;
    return runningMachines.reduce((sum, m) => sum + (m.state.current_oee ?? 0), 0) / runningMachines.length;
  })();

  return (
    <div
      onClick={onClick}
      className="bg-white border border-[#BFC0C2] hover:border-[#007BC0] shadow-sm p-4 rounded cursor-pointer group transition-all relative overflow-hidden"
    >
      <div className="flex justify-between items-center mb-3">
        <h3 className="font-bold text-lg text-[#000000] flex items-center gap-2">
          {displayLineId(lineId)}
          <Maximize2 size={14} className="text-[#007BC0] opacity-0 group-hover:opacity-100 transition-opacity" />
        </h3>
        <div className="flex items-center gap-2">
          <div
            className="text-xs font-mono px-2 py-1 rounded flex items-center gap-1"
            style={{
              color: getTrafficColor(avgOee, KPI_THRESHOLDS.oee, { higherIsBetter: true }),
              backgroundColor: BOSCH_COLORS.lightGray,
            }}
          >
            <Activity size={10} /> OEE {(avgOee * 100).toFixed(1)}%
          </div>
          <div className="text-xs font-mono bg-[#EFF1F2] px-2 py-1 rounded text-[#525F6B]">
            {total} MCS
          </div>
        </div>
      </div>

      {/* Mini Heatmap */}
      <div className="grid grid-cols-10 gap-1 mb-4">
        {machines.map((m) => {
          const effectiveActivity = getEffectiveMachineActivity(m);
          let color = BOSCH_COLORS.lightGray;
          if (effectiveActivity === 'process') color = BOSCH_COLORS.chart.process;
          if (effectiveActivity === 'changeover') color = BOSCH_COLORS.chart.changeover;
          if (effectiveActivity === 'waiting') color = BOSCH_COLORS.chart.waiting;
          if (effectiveActivity === 'inactive') color = BOSCH_COLORS.chart.inactive;
          if (effectiveActivity === 'test') color = BOSCH_COLORS.chart.test;
          return (
            <div
              key={m.machine_id}
              className="h-2 w-full rounded-sm"
              style={{ backgroundColor: color }}
              title={`${displayMachineId(m.machine_id)}: ${effectiveActivity} (OEE: ${m.state.current_oee != null ? (m.state.current_oee * 100).toFixed(0) + '%' : '—'})`}
            />
          );
        })}
      </div>

      {/* Stats Row */}
      <div className="flex justify-between text-xs text-[#525F6B] border-t border-[#EFF1F2] pt-2">
        <div className="flex flex-col items-center">
          <span className="font-bold text-[#000000]">{utilization}%</span>
          <span>Active</span>
        </div>
        <div className="flex flex-col items-center">
          <span className="font-bold text-[#E8A500]">{setup}</span>
          <span>Setup</span>
        </div>
        <div className="flex flex-col items-center">
          <span className="font-bold" style={{ color: BOSCH_COLORS.chart.test }}>
            {testing}
          </span>
          <span>Test</span>
        </div>
        <div className="flex flex-col items-center">
          <span className="font-bold text-[#007BC0]">{running}</span>
          <span>Run</span>
        </div>
        <div className="flex flex-col items-center">
          <span className="font-bold text-[#BFC0C2]">{idle}</span>
          <span>Waiting</span>
        </div>
        <div className="flex flex-col items-center">
          <span className="font-bold" style={{ color: BOSCH_COLORS.chart.inactive }}>
            {inactive}
          </span>
          <span>Off</span>
        </div>
      </div>
    </div>
  );
};

const ZoneDetailModal = ({
  lineId,
  machines,
  currentTimeSec,
  onClose,
}: {
  lineId: string;
  machines: RealtimeMachine[];
  currentTimeSec: number;
  onClose: () => void;
}) => {
  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-8">
      <motion.div
        initial={{ opacity: 0, scale: 0.95 }}
        animate={{ opacity: 1, scale: 1 }}
        exit={{ opacity: 0, scale: 0.95 }}
        className="bg-[#EFF1F2] w-full h-full max-w-[1800px] max-h-[95vh] rounded-lg shadow-2xl flex flex-col overflow-hidden"
      >
        <div className="bg-white px-6 py-4 border-b border-[#BFC0C2] flex justify-between items-center shadow-sm">
          <div className="flex items-center gap-4">
            <h2 className="text-2xl font-bold text-[#000000] flex items-center gap-2">
              <Grid className="text-[#007BC0]" />
              {lineId} Details
            </h2>
            <div className="h-6 w-px bg-[#BFC0C2] mx-2"></div>
            <div className="text-sm text-[#525F6B]">
              <strong>{machines.length}</strong> Machines Total
            </div>
          </div>
          <button onClick={onClose} className="p-2 hover:bg-[#EFF1F2] rounded-full transition-colors">
            <X size={24} className="text-[#525F6B]" />
          </button>
        </div>
        <div className="flex-grow p-6 overflow-y-auto">
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
            {machines.map((m) => (
              <MachineCard key={m.machine_id} machine={m} />
            ))}
          </div>
        </div>
      </motion.div>
    </div>,
    document.body
  );
};

// (HardStageMarketDetailModal removed — nozzles now shown inline in the card)

// --- Laser Zone (summary of waiting/done this shift) ---

const LaserZone: React.FC<{ items: LaserSummaryItem[] }> = ({ items }) => {
  const totalTrays  = items.reduce((s, i) => s + i.waiting_trays,  0);
  const totalPieces = items.reduce((s, i) => s + i.waiting_pieces, 0);

  return (
    <div className="flex flex-col gap-2 flex-grow min-h-0">
      {/* Totals pill */}
      {items.length > 0 && (
        <div className="flex items-center gap-2 flex-shrink-0 px-1">
          <Layers size={11} className="text-[#E8A500]" />
          <span className="text-[10px] font-bold text-[#E8A500]">{totalTrays} tray{totalTrays !== 1 ? 's' : ''}</span>
          <span className="text-[10px] text-[#525F6B]">·</span>
          <span className="text-[10px] font-mono text-[#525F6B]">{totalPieces} pcs</span>
        </div>
      )}

      {/* Per-PN list */}
      <div className="flex-grow min-h-0 overflow-y-auto space-y-1.5">
        {items.length === 0 && (
          <div className="text-center text-[#BFC0C2] text-xs mt-6">Nenhuma tray em processamento</div>
        )}
        {items.map((item) => (
          <div key={item.part_number_id} className="bg-white border p-2 shadow-sm">
            <div className="font-mono text-[10px] font-bold text-[#007BC0] truncate mb-1">
              {item.part_number_id}
            </div>
            <div className="flex items-center justify-between text-[9px]">
              <span className="text-[#525F6B]">Trays em proc.:</span>
              <span className="font-bold font-mono text-[#E8A500]">{item.waiting_trays}</span>
            </div>
            <div className="flex items-center justify-between text-[9px] mt-0.5">
              <span className="text-[#525F6B]">Peças:</span>
              <span className="font-bold font-mono text-[#000000]">{item.waiting_pieces}</span>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
};

// --- Final Market Column (HE / Packaging) ---

const FinalMarketColumn: React.FC<{ items: FinalMarketItem[]; accentColor: string }> = ({ items, accentColor }) => {
  if (items.length === 0) {
    return (
      <div className="h-full p-2 flex items-center justify-center">
        <span className="text-[#BFC0C2] text-[10px]">Vazio</span>
      </div>
    );
  }

  return (
    <div className="h-full p-2 space-y-2 overflow-y-auto">
      {items.map((item) => {
        const hasOrders = item.pending_orders.length > 0;
        const oldestOrder = hasOrders ? item.pending_orders[0] : null;
        // Progress toward oldest order
        const progressPct = oldestOrder
          ? Math.min(100, Math.round((item.total_pieces / oldestOrder.quantity) * 100))
          : null;

        return (
          <div
            key={item.part_number_id}
            className="bg-white p-2 shadow-sm border border-transparent"
            style={{ borderLeft: `3px solid ${accentColor}` }}
          >
            {/* PN + total pieces */}
            <div className="flex items-center justify-between gap-1 mb-1">
              <div className="font-mono text-[10px] font-bold text-[#007BC0] truncate">{item.part_number_id}</div>
              <div className="text-[10px] font-mono font-bold shrink-0" style={{ color: accentColor }}>
                {item.total_pieces.toLocaleString()} pcs
              </div>
            </div>

            {/* Progress bar toward oldest order */}
            {oldestOrder && progressPct !== null && (
              <div className="mb-1.5">
                <div className="flex justify-between text-[8px] text-[#525F6B] mb-0.5">
                  <span>{oldestOrder.order_id}</span>
                  <span>{progressPct}%</span>
                </div>
                <div className="h-1 w-full bg-[#EFF1F2] rounded-full overflow-hidden">
                  <div
                    className="h-full rounded-full transition-all"
                    style={{
                      width: `${progressPct}%`,
                      backgroundColor: progressPct >= 100 ? BOSCH_COLORS.traffic.green
                        : progressPct >= 50 ? BOSCH_COLORS.traffic.yellow
                        : BOSCH_COLORS.traffic.red,
                    }}
                  />
                </div>
                <div className="flex justify-between text-[8px] text-[#BFC0C2] mt-0.5">
                  <span>Meta: {oldestOrder.quantity.toLocaleString()} pcs</span>
                  <span>Faltam: {oldestOrder.remaining_needed.toLocaleString()}</span>
                </div>
              </div>
            )}

            {/* Remaining open orders (compact) */}
            {item.pending_orders.length > 1 && (
              <div className="pt-1 border-t border-[#EFF1F2] space-y-0.5 mt-1">
                <div className="text-[8px] uppercase font-bold text-[#BFC0C2] tracking-wide">
                  + {item.pending_orders.length - 1} ordem{item.pending_orders.length - 1 > 1 ? 's' : ''} pendente{item.pending_orders.length - 1 > 1 ? 's' : ''}
                </div>
                {item.pending_orders.slice(1).map((o) => (
                  <div key={o.order_id} className="flex justify-between text-[8px] text-[#525F6B]">
                    <span className="font-mono truncate">{o.order_id}</span>
                    <span className="font-mono shrink-0 ml-1">{o.quantity.toLocaleString()} pcs · {o.due_date}</span>
                  </div>
                ))}
              </div>
            )}

            {/* No open orders — show timeout hint */}
            {!hasOrders && item.oldest_entry_date && (
              <div className="text-[8px] text-[#BFC0C2] pt-1 border-t border-[#EFF1F2] mt-1">
                Sem ordens abertas
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
};

// --- Main Component ---

export const LiveVisualization: React.FC = () => {
  const [data, setData] = useState<LiveData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [lastRefreshed, setLastRefreshed] = useState<Date | null>(null);
  const [isLive, setIsLive] = useState(true);

  const [selectedZone, setSelectedZone] = useState<string | null>(null);
  const [selectedStock, setSelectedStock] = useState<StockInfo | null>(null);
  // EDM line dropdown: null = "All corridors" (summary cards), string = specific line (machine list)
  const [selectedEdmLine, setSelectedEdmLine] = useState<string | null>(null);
  const [showSettings, setShowSettings] = useState(false);
  const [settings, setSettings] = useState<LiveSettings>(() => loadSettings());
  const [searchQuery, setSearchQuery] = useState('');

  const loadData = useCallback(async () => {
    setIsRefreshing(true);
    try {
      const payload = await fetchLiveData({ oeeHours: settings.oeeHours, offHours: settings.offHours, marketTimeoutHours: settings.marketTimeoutHours, defaultTestMin: settings.defaultTestMin });
      setData(payload);
      setLastRefreshed(new Date());
      setError(null);
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Failed to load live data';
      setError(message);
    } finally {
      setIsRefreshing(false);
    }
  }, [settings.oeeHours, settings.offHours, settings.marketTimeoutHours, settings.defaultTestMin]);

  // Initial load
  useEffect(() => {
    loadData();
  }, [loadData]);

  // Auto-poll while live mode is on
  useEffect(() => {
    if (!isLive) return;
    const interval = setInterval(loadData, POLL_INTERVAL_MS);
    return () => clearInterval(interval);
  }, [isLive, loadData]);

  // Derived
  const snapshot = data?.snapshot ?? null;

  const ordersById: Record<string, OrderMeta> = useMemo(() => {
    return data?.orders_by_id ?? {};
  }, [data]);

  const frameMachines = Array.isArray(snapshot?.machines) ? snapshot!.machines : [];
  const laserMachines = frameMachines.filter((m) => m.process_type === 'LASER');

  // Apply manual OFF overrides from settings
  const manualOffSet = useMemo(() => new Set(settings.manualOff), [settings.manualOff]);
  const edmMachines = frameMachines
    .filter((m) => m.process_type === 'EDM')
    .map((m) =>
      manualOffSet.has(m.machine_id)
        ? { ...m, state: { ...m.state, activity: 'inactive' as const, current_oee: null } }
        : m
    );

  const groupedEdm = useMemo(() => {
    return edmMachines.reduce(
      (acc, m) => {
        if (!acc[m.line_id]) acc[m.line_id] = [];
        acc[m.line_id].push(m);
        return acc;
      },
      {} as Record<string, typeof edmMachines>
    );
  }, [edmMachines]);

  const producedOrders = Array.isArray(snapshot?.produced_orders) ? snapshot!.produced_orders : [];
  // No CRIN/PLV split — show all produced PNs in a single list
  const laserSummary: LaserSummaryItem[] = Array.isArray((snapshot as any)?.laser_summary)
    ? (snapshot as any).laser_summary
    : [];

  const finishedGoods: StockInfo[] = Array.isArray((snapshot as any)?.finished_goods)
    ? (snapshot as any).finished_goods
    : [];

  const heMarket: FinalMarketItem[] = Array.isArray((snapshot as any)?.he_market)
    ? (snapshot as any).he_market
    : [];

  const packagingMarket: FinalMarketItem[] = Array.isArray((snapshot as any)?.packaging_market)
    ? (snapshot as any).packaging_market
    : [];

  const todayIso = useMemo(() => new Date().toISOString().slice(0, 10), []);

  const todayOrderIds = useMemo(() => {
    const ids = Array.isArray(snapshot?.backlog_orders) ? snapshot!.backlog_orders : [];
    return ids.filter((orderId) => {
      const due = ordersById[orderId]?.due_date;
      if (!due) return true;
      return due >= todayIso;
    });
  }, [snapshot, ordersById, todayIso]);

  const backlogByDueDate = useMemo(() => {
    const backlogIds = Array.isArray(snapshot?.backlog_orders) ? snapshot!.backlog_orders : [];
    const groups = backlogIds.reduce(
      (acc, orderId) => {
        const due = ordersById[orderId]?.due_date ?? 'Unknown date';
        if (!acc[due]) acc[due] = [];
        acc[due].push(orderId);
        return acc;
      },
      {} as Record<string, string[]>
    );
    const keys = Object.keys(groups).sort((a, b) => {
      if (a === 'Unknown date') return 1;
      if (b === 'Unknown date') return -1;
      return a.localeCompare(b);
    });
    return keys.map((k) => ({ due_date: k, orderIds: groups[k] }));
  }, [snapshot, ordersById]);

  const backlogNonTodayByDueDate = useMemo(() => {
    return backlogByDueDate
      .map((g) => ({ ...g, orderIds: g.orderIds.filter((id) => !todayOrderIds.includes(id)) }))
      .filter((g) => g.orderIds.length > 0);
  }, [backlogByDueDate, todayOrderIds]);

  const hardStageMarketItems = useMemo(() => {
    const items = Array.isArray(data?.hard_stage_market) ? data!.hard_stage_market : [];
    return [...items].sort((a, b) => (b.body_qtty ?? 0) - (a.body_qtty ?? 0));
  }, [data]);

  // --- Global search filter ---
  const q = searchQuery.trim().toLowerCase();

  /**
   * Expanded search context.
   * Given any raw query (order ID, type number, or body number), resolves the full
   * set of related identifiers so that all columns filter consistently:
   *
   *   order ID   → type number (via ordersById)  → body number (via HSM nozzles)
   *   type number → orders (via ordersById)       → body number (via HSM nozzles)
   *   body number → nozzles = type numbers (HSM)  → orders (via ordersById)
   */
  const searchContext = useMemo(() => {
    if (!q) return null;

    const matchedTypeNumbers   = new Set<string>();
    const matchedBodyNumbers   = new Set<string>();
    const matchedOrderIds      = new Set<string>();
    // Bodies hit DIRECTLY by the query string (not expanded from a TN).
    // Only these trigger the full body → all-nozzles expansion.
    const directlyMatchedBodies = new Set<string>();

    // ── Pass 1 — direct string matches ────────────────────────────────────
    // Orders: order_id or part_number_id directly contains the query
    Object.values(ordersById).forEach((meta) => {
      if (
        (meta.order_id ?? '').toLowerCase().includes(q) ||
        (meta.part_number_id ?? '').toLowerCase().includes(q)
      ) {
        if (meta.order_id)       matchedOrderIds.add(meta.order_id);
        if (meta.part_number_id) matchedTypeNumbers.add(meta.part_number_id);
      }
    });

    // HSM: direct hit on body_partnumber or on a specific nozzle string
    hardStageMarketItems.forEach((item) => {
      const bodyHit = item.body_partnumber.toLowerCase().includes(q);
      if (bodyHit) {
        directlyMatchedBodies.add(item.body_partnumber);
        matchedBodyNumbers.add(item.body_partnumber);
      }
      if (Array.isArray(item.nozzles)) {
        item.nozzles.forEach((n) => {
          if (n.toLowerCase().includes(q)) {
            matchedTypeNumbers.add(n);
            // Show the parent body row, but don't treat it as a "direct body match"
            matchedBodyNumbers.add(item.body_partnumber);
          }
        });
      }
    });

    // ── Pass 2 — TN → body (display only) + TN → orders ──────────────────
    // A matched TN makes its parent body visible in HSM, but does NOT
    // pull in sibling nozzles of that body (avoids cross-contamination).
    hardStageMarketItems.forEach((item) => {
      if (Array.isArray(item.nozzles) && item.nozzles.some((n) => matchedTypeNumbers.has(n))) {
        matchedBodyNumbers.add(item.body_partnumber);
      }
    });

    // Matched TNs → expand their orders
    Object.values(ordersById).forEach((meta) => {
      if (meta.part_number_id && matchedTypeNumbers.has(meta.part_number_id)) {
        if (meta.order_id) matchedOrderIds.add(meta.order_id);
      }
    });

    // ── Pass 3 — ONLY directly-matched bodies → all nozzles → orders ──────
    // This fires when the user typed a body number explicitly, showing
    // everything related to that body across all columns.
    hardStageMarketItems.forEach((item) => {
      if (directlyMatchedBodies.has(item.body_partnumber)) {
        (item.nozzles ?? []).forEach((n) => {
          matchedTypeNumbers.add(n);
          Object.values(ordersById).forEach((meta) => {
            if (meta.part_number_id === n && meta.order_id) matchedOrderIds.add(meta.order_id);
          });
        });
      }
    });

    return { matchedTypeNumbers, matchedBodyNumbers, matchedOrderIds };
  }, [q, ordersById, hardStageMarketItems]);

  /** Returns true if a string directly contains the raw query OR is in the expanded set. */
  const matchesTN  = (pn: string)      => pn.toLowerCase().includes(q) || (searchContext?.matchedTypeNumbers.has(pn) ?? false);
  const matchesBN  = (bn: string)      => bn.toLowerCase().includes(q) || (searchContext?.matchedBodyNumbers.has(bn) ?? false);
  const matchesOID = (oid: string)     => oid.toLowerCase().includes(q) || (searchContext?.matchedOrderIds.has(oid) ?? false);

  const filteredEdmMachines = useMemo(() => {
    if (!q) return edmMachines;
    return edmMachines.filter((m) => {
      const s = m.state as any;
      return (
        m.machine_id.toLowerCase().includes(q) ||
        m.line_id.toLowerCase().includes(q) ||
        matchesTN(s.part_number_id ?? '') ||
        (s.serial_number ?? '').toLowerCase().includes(q)
      );
    });
  }, [edmMachines, q, searchContext]);

  const filteredLaserSummary = useMemo(() => {
    if (!q) return laserSummary;
    return laserSummary.filter((i) => matchesTN(i.part_number_id));
  }, [laserSummary, q, searchContext]);

  const filteredFinishedGoods = useMemo(() => {
    if (!q) return finishedGoods;
    return finishedGoods.filter((i) => {
      const item = i as any;
      return matchesTN(item.part_number_id ?? '') || (item.serial_number ?? '').toLowerCase().includes(q);
    });
  }, [finishedGoods, q, searchContext]);

  const filteredTodayOrderIds = useMemo(() => {
    if (!q) return todayOrderIds;
    return todayOrderIds.filter((id) => {
      const meta = ordersById[id];
      return matchesOID(id) || matchesTN(meta?.part_number_id ?? '');
    });
  }, [todayOrderIds, ordersById, q, searchContext]);

  const filteredBacklogNonToday = useMemo(() => {
    if (!q) return backlogNonTodayByDueDate;
    return backlogNonTodayByDueDate
      .map((g) => ({
        ...g,
        orderIds: g.orderIds.filter((id) => {
          const meta = ordersById[id];
          return matchesOID(id) || matchesTN(meta?.part_number_id ?? '');
        }),
      }))
      .filter((g) => g.orderIds.length > 0);
  }, [backlogNonTodayByDueDate, ordersById, q, searchContext]);

  const filteredHardStageMarket = useMemo(() => {
    if (!q) return hardStageMarketItems;
    return hardStageMarketItems.filter(
      (i) =>
        matchesBN(i.body_partnumber) ||
        (Array.isArray(i.nozzles) && i.nozzles.some((n) => matchesTN(n)))
    );
  }, [hardStageMarketItems, q, searchContext]);

  const filteredHeMarket = useMemo(() => {
    if (!q) return heMarket;
    return heMarket.filter((i) => matchesTN(i.part_number_id));
  }, [heMarket, q, searchContext]);

  const filteredPackagingMarket = useMemo(() => {
    if (!q) return packagingMarket;
    return packagingMarket.filter((i) => matchesTN(i.part_number_id));
  }, [packagingMarket, q, searchContext]);

  // Total matched items across all sections (for badge)
  const searchMatchCount = useMemo(() => {
    if (!q) return null;
    const bufferMatches = (Array.isArray(snapshot?.stock_levels) ? snapshot!.stock_levels : [])
      .flatMap((s) => Array.isArray(s.tray_details) ? s.tray_details.map((t: any) => t.part_number_id as string) : [])
      .filter((pn) => matchesTN(pn ?? ''))
      .length;
    return (
      filteredEdmMachines.length +
      filteredLaserSummary.length +
      filteredFinishedGoods.length +
      filteredTodayOrderIds.length +
      filteredBacklogNonToday.reduce((s, g) => s + g.orderIds.length, 0) +
      filteredHardStageMarket.length +
      filteredHeMarket.length +
      filteredPackagingMarket.length +
      bufferMatches
    );
  }, [q, searchContext, filteredEdmMachines, filteredLaserSummary, filteredFinishedGoods, filteredTodayOrderIds, filteredBacklogNonToday, filteredHardStageMarket, filteredHeMarket, filteredPackagingMarket, snapshot]);

  const filteredGroupedEdm = useMemo(() => {
    return filteredEdmMachines.reduce(
      (acc, m) => {
        if (!acc[m.line_id]) acc[m.line_id] = [];
        acc[m.line_id].push(m);
        return acc;
      },
      {} as Record<string, typeof filteredEdmMachines>
    );
  }, [filteredEdmMachines]);

  const currentTimeSec = snapshot?.time_sec ?? 0;

  // Live ticking clock in the facility timezone (Curitiba)
  const facilityTz = data?.metadata.facility_timezone ?? 'America/Sao_Paulo';
  const [facilityClock, setFacilityClock] = useState<string>(() =>
    new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false, timeZone: facilityTz })
  );
  useEffect(() => {
    const tick = () =>
      setFacilityClock(
        new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false, timeZone: facilityTz })
      );
    tick();
    const id = window.setInterval(tick, 1000);
    return () => window.clearInterval(id);
  }, [facilityTz]);

  const timeLabel = facilityClock;

  // --- Render states ---

  if (error && !data) {
    return (
      <Card title="Data Error">
        <p className="text-sm text-[#525F6B]">{error}</p>
        <button
          onClick={loadData}
          className="mt-4 px-4 py-2 bg-[#007BC0] text-white rounded text-sm hover:bg-[#00629A] transition-colors"
        >
          Retry
        </button>
      </Card>
    );
  }

  if (!data || !snapshot) {
    return (
      <Card title="Loading">
        <p className="text-sm text-[#525F6B]">Loading live data...</p>
      </Card>
    );
  }

  return (
    <div className="space-y-6 h-[calc(100vh-140px)] flex flex-col">
      {/* Live Status Bar */}
      <Card className="flex-shrink-0 sticky top-24 z-10">
        <div className="flex flex-col md:flex-row items-center justify-between gap-4">
          {/* Left: Live indicator + time */}
          <div className="flex items-center gap-4">
            {/* Live / Paused toggle */}
            <button
              onClick={() => setIsLive((v) => !v)}
              className={clsx(
                'flex items-center gap-2 px-4 py-2 rounded-full text-sm font-bold transition-colors shadow-sm',
                isLive
                  ? 'bg-[#00884A] text-white hover:bg-[#006638]'
                  : 'bg-[#EFF1F2] text-[#525F6B] hover:bg-[#BFC0C2]'
              )}
            >
              {isLive ? (
                <>
                  <Wifi size={16} />
                  <span className="flex items-center gap-1">
                    LIVE
                    <span className="inline-block w-2 h-2 rounded-full bg-white animate-pulse ml-1" />
                  </span>
                </>
              ) : (
                <>
                  <WifiOff size={16} />
                  PAUSED
                </>
              )}
            </button>

            {/* Manual refresh */}
            <button
              onClick={loadData}
              disabled={isRefreshing}
              className="p-2 rounded hover:bg-[#EFF1F2] transition-colors text-[#525F6B] disabled:opacity-50"
              title="Refresh now"
            >
              <RefreshCw size={18} className={clsx(isRefreshing && 'animate-spin')} />
            </button>

            {/* Current time */}
            <div className="flex items-center gap-2 text-[#007BC0] font-bold text-lg">
              <Clock size={18} />
              {timeLabel}
            </div>
          </div>

          {/* Center: data freshness timestamp */}
          <div className="text-xs text-[#525F6B] text-center">
            <span className="font-medium">Data updated:</span>{' '}
            {data.metadata.data_last_updated
              ? new Date(data.metadata.data_last_updated).toLocaleString([], { timeZone: data.metadata.facility_timezone ?? 'America/Sao_Paulo' })
              : data.metadata.generated_at
                ? new Date(data.metadata.generated_at).toLocaleString([], { timeZone: data.metadata.facility_timezone ?? 'America/Sao_Paulo' })
                : '—'}
          </div>

          {/* Search bar */}
          <div className="flex items-center gap-2 flex-1 max-w-xs">
            <div className="relative w-full">
              <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-[#BFC0C2] pointer-events-none" />
              <input
                type="text"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder="PN, Serial, Agie…"
                className="w-full pl-8 pr-8 py-1.5 text-xs border border-[#BFC0C2] rounded-full bg-white focus:outline-none focus:border-[#007BC0] focus:ring-1 focus:ring-[#007BC0] transition-colors placeholder:text-[#BFC0C2]"
              />
              {searchQuery && (
                <button
                  onClick={() => setSearchQuery('')}
                  className="absolute right-2.5 top-1/2 -translate-y-1/2 text-[#BFC0C2] hover:text-[#525F6B]"
                >
                  <X size={12} />
                </button>
              )}
            </div>
            {searchMatchCount !== null && (
              <span className={clsx(
                'shrink-0 text-[10px] font-bold px-2 py-0.5 rounded-full',
                searchMatchCount > 0 ? 'bg-[#007BC0] text-white' : 'bg-[#EFF1F2] text-[#BFC0C2]'
              )}>
                {searchMatchCount}
              </span>
            )}
          </div>

          {/* Right: settings + poll interval info */}
          <div className="flex items-center gap-3">
            <div className="text-xs text-[#BFC0C2]">
              Auto-refresh every half an hour
            </div>
            <button
              onClick={() => setShowSettings(true)}
              className="p-2 rounded hover:bg-[#EFF1F2] transition-colors text-[#525F6B] hover:text-[#007BC0]"
              title="Configurações"
            >
              <Settings size={18} />
            </button>
          </div>
        </div>

        {/* Stale data warning */}
        {error && (
          <div className="mt-3 px-3 py-2 bg-[#FFE8E8] border border-[#FF3732] rounded text-xs text-[#FF3732] flex items-center gap-2">
            <WifiOff size={14} />
            Last refresh failed: {error}. Showing cached data.
          </div>
        )}
      </Card>

      {/* Plant Layout Visualization — identical to RealtimePlayback */}
      <div className="flex-grow min-h-0 overflow-x-auto overflow-y-hidden pb-4">
        <div className="min-w-[1400px] h-full grid grid-cols-14 gap-4">

          {/* Col 1-2: Orders */}
          <div className="col-span-2 flex flex-col gap-2 min-h-0">
            <div className="text-xs uppercase font-bold text-[#525F6B] tracking-wide border-b pb-1">1. Orders</div>

            {/* Active Orders */}
            <div className="h-1/2 flex flex-col min-h-0">
              <div className="text-[10px] font-bold text-[#007BC0] mb-1">Active Orders</div>
              <div className="bg-[#EFF1F2] flex-grow min-h-0 rounded-sm p-2 overflow-y-auto space-y-2 border border-[#BFC0C2] border-dashed">
                <AnimatePresence>
                  {filteredTodayOrderIds.map((orderId) => {
                    const meta = ordersById[orderId];
                    const hsItem = hardStageMarketItems.find(h =>
                      Array.isArray(h.nozzles) && h.nozzles.includes(meta?.part_number_id ?? '')
                    );
                    const bodyQty = hsItem?.body_qtty ?? null;
                    const orderQty = meta?.quantity ?? null;
                    const isShort = !hsItem || (bodyQty !== null && orderQty !== null && bodyQty < orderQty);
                    return (
                      <motion.div
                        key={orderId}
                        layout
                        initial={{ opacity: 0, y: -10 }}
                        animate={{ opacity: 1, y: 0 }}
                        exit={{ opacity: 0, scale: 0.5 }}
                        className={clsx(
                          'bg-white p-2 shadow-sm border-l-4 text-sm',
                          isShort ? 'border-l-[#FF3732]' : 'border-l-[#007BC0]'
                        )}
                      >
                        {/* Primary: PN + qty */}
                        <div className="flex items-center justify-between gap-1">
                          <div className="font-bold text-xs text-[#007BC0] truncate">{meta?.part_number_id ?? '—'}</div>
                          <div className="text-[10px] font-mono font-bold text-[#000000] shrink-0">{orderQty ?? '—'} pcs</div>
                        </div>
                        {/* Secondary: corpo + qty corpo */}
                        <div className={clsx('flex items-center justify-between gap-1 mt-0.5', isShort ? 'text-[#FF3732]' : 'text-[#525F6B]')}>
                          <div className="text-[9px] truncate">corpo: {hsItem?.body_partnumber ?? '—'}</div>
                          <div className="text-[9px] font-mono shrink-0">{bodyQty !== null ? `${bodyQty} pcs` : '—'}</div>
                        </div>
                        {/* Order ID + date */}
                        <div className="flex items-center justify-between gap-1 mt-1 pt-1 border-t border-[#EFF1F2]">
                          <div className="text-[9px] text-[#525F6B] truncate">{orderId}</div>
                          <div className="text-[9px] font-mono text-[#525F6B] shrink-0">{meta?.due_date ?? '—'}</div>
                        </div>
                      </motion.div>
                    );
                  })}
                  {filteredTodayOrderIds.length === 0 && (
                    <div className="text-center text-[#BFC0C2] text-xs mt-10">Empty</div>
                  )}
                </AnimatePresence>
              </div>
            </div>

            {/* Backlog Orders */}
            <div className="h-1/2 flex flex-col min-h-0">
              <div className="text-[10px] font-bold text-[#FF3732]">Backlog Orders</div>
              <div className="bg-[#EFF1F2] flex-grow min-h-0 rounded-sm p-2 overflow-y-auto space-y-2 border border-[#BFC0C2] border-dashed">
                <AnimatePresence>
                  {filteredBacklogNonToday.map((group) => (
                    <motion.div
                      key={group.due_date}
                      layout
                      initial={{ opacity: 0, y: -6 }}
                      animate={{ opacity: 1, y: 0 }}
                      exit={{ opacity: 0, scale: 0.98 }}
                      className="space-y-2"
                    >
                      <div className="text-[10px] font-bold text-[#525F6B] flex items-center justify-between">
                        <span>{group.due_date}</span>
                        <span className="font-mono text-[9px]">{group.orderIds.length}</span>
                      </div>
                      {group.orderIds.map((orderId) => {
                        const meta = ordersById[orderId];
                        const hsItem = hardStageMarketItems.find(h =>
                          Array.isArray(h.nozzles) && h.nozzles.includes(meta?.part_number_id ?? '')
                        );
                        const bodyQty = hsItem?.body_qtty ?? null;
                        const orderQty = meta?.quantity ?? null;
                        const isShort = !hsItem || (bodyQty !== null && orderQty !== null && bodyQty < orderQty);
                        return (
                          <motion.div
                            key={orderId}
                            layout
                            initial={{ opacity: 0, y: -6 }}
                            animate={{ opacity: 1, y: 0 }}
                            exit={{ opacity: 0, scale: 0.98 }}
                            className={clsx(
                              'bg-white p-2 shadow-sm border-l-4 text-sm',
                              isShort ? 'border-l-[#FF3732]' : 'border-l-[#E8A500]'
                            )}
                          >
                            {/* Primary: PN + qty */}
                            <div className="flex items-center justify-between gap-1">
                              <div className="font-bold text-xs text-[#007BC0] truncate">{meta?.part_number_id ?? '—'}</div>
                              <div className="text-[10px] font-mono font-bold text-[#000000] shrink-0">{orderQty ?? '—'} pcs</div>
                            </div>
                            {/* Secondary: corpo + qty corpo */}
                            <div className={clsx('flex items-center justify-between gap-1 mt-0.5', isShort ? 'text-[#FF3732]' : 'text-[#525F6B]')}>
                              <div className="text-[9px] truncate">corpo: {hsItem?.body_partnumber ?? '—'}</div>
                              <div className="text-[9px] font-mono shrink-0">{bodyQty !== null ? `${bodyQty} pcs` : '—'}</div>
                            </div>
                            {/* Order ID + due date */}
                            <div className="flex items-center justify-between gap-1 mt-1 pt-1 border-t border-[#EFF1F2]">
                              <div className="text-[9px] text-[#525F6B] truncate">{orderId}</div>
                              <div className="text-[9px] font-mono text-[#525F6B] shrink-0">{meta?.due_date ?? '—'}</div>
                            </div>
                          </motion.div>
                        );
                      })}
                    </motion.div>
                  ))}
                  {filteredBacklogNonToday.length === 0 && (
                    <div className="text-center text-[#BFC0C2] text-xs mt-10">Empty</div>
                  )}
                </AnimatePresence>
              </div>
            </div>
          </div>

          {/* Col 3-4: Hard Stage Market */}
          <div className="col-span-2 flex flex-col gap-2 min-h-0">
            <div className="text-xs uppercase font-bold text-[#525F6B] tracking-wide border-b pb-1">
              2. HARD STAGE MARKET
            </div>
            <div className="flex-grow min-h-0 bg-[#EFF1F2] p-2 rounded-sm">
              {hardStageMarketItems.length === 0 ? (
                <div className="text-center text-[#BFC0C2] text-xs mt-10">Empty</div>
              ) : (
                <VirtualList
                  items={filteredHardStageMarket}
                  itemHeight={120}
                  gap={8}
                  className="h-full"
                  renderItem={(item) => (
                    <div className="bg-white p-2 shadow-sm border border-transparent overflow-hidden" style={{ height: 112 }}>
                        {/* Header: PN + qty */}
                        <div className="flex items-center justify-between gap-2 mb-1.5">
                          <div className="text-xs font-bold text-[#000000] break-all leading-tight">{item.body_partnumber}</div>
                          <div className="text-[10px] font-mono font-bold bg-[#EFF1F2] px-2 py-0.5 rounded text-[#007BC0] shrink-0">
                            {item.body_qtty} pcs
                          </div>
                        </div>
                        {/* Nozzle list inline */}
                        {Array.isArray(item.nozzles) && item.nozzles.length > 0 ? (
                          <div className="pt-1.5 border-t border-[#EFF1F2]">
                            <div className="text-[9px] uppercase font-bold text-[#BFC0C2] tracking-wide mb-1">Nozzles</div>
                            <div className="overflow-y-auto space-y-1" style={{ maxHeight: 52 }}>
                              {item.nozzles.map((nozzlePn) => (
                                <div key={nozzlePn} className="flex items-center gap-1">
                                  <div className="w-1 h-1 rounded-full bg-[#007BC0] shrink-0" />
                                  <div className="font-mono text-[9px] text-[#525F6B] break-all">{nozzlePn}</div>
                                </div>
                              ))}
                            </div>
                          </div>
                        ) : (
                          <div className="text-[9px] text-[#BFC0C2] pt-1 border-t border-[#EFF1F2]">No nozzles</div>
                        )}
                    </div>
                  )}
                />
              )}
            </div>
          </div>

          {/* Col 5-6: Laser Marking — waiting / done summary */}
          <div className="col-span-2 flex flex-col gap-2 min-h-0">
            <div className="text-xs uppercase font-bold text-[#525F6B] tracking-wide border-b pb-1">
              3. Laser Marking
            </div>
            <LaserZone items={filteredLaserSummary} />
          </div>

          {/* Col 7-8: Buffer Stock */}
          <div className="col-span-2 flex flex-col gap-2 min-h-0">
            <div className="text-xs uppercase font-bold text-[#525F6B] tracking-wide border-b pb-1">
              4. AGIE ENTRANCE
            </div>
            <div className="flex-grow min-h-0 bg-[#EFF1F2] p-2 rounded-sm space-y-2 overflow-y-auto">
              {(Array.isArray(snapshot.stock_levels) ? snapshot.stock_levels : [])
                .filter((stock) => selectedEdmLine === null || stock.line_id === selectedEdmLine)
                .map((stock) => {
                const displayLineId = stock.line_id.startsWith('Agie')
                  ? `Corredor ${stock.line_id.replace('Agie', '').trim()}`
                  : stock.line_id;
                // Aggregate PN quantities from tray_details using real pieces field
                const pnMap: Record<string, number> = {};
                if (Array.isArray(stock.tray_details)) {
                  for (const t of stock.tray_details) {
                    if (t.part_number_id) {
                      pnMap[t.part_number_id] = (pnMap[t.part_number_id] ?? 0) + (t.pieces ?? 0);
                    }
                  }
                }
                // Apply global search filter to PN entries
                const allPnEntries = Object.entries(pnMap);
                const pnEntries = q
                  ? allPnEntries.filter(([pn]) => matchesTN(pn))
                  : allPnEntries;
                // If searching and no PNs match this corridor, hide the card
                if (q && pnEntries.length === 0) return null;
                const isExpanded = selectedEdmLine !== null;
                return (
                  <div
                    key={stock.line_id}
                    className="bg-white p-2 shadow-sm border border-transparent cursor-pointer hover:border-[#007BC0] transition-colors"
                    onClick={() => setSelectedStock(stock)}
                  >
                    {/* Header: line + qty */}
                    <div className="flex items-center justify-between mb-1.5">
                      <div className="text-xs font-bold text-[#525F6B]">{displayLineId}</div>
                      <div className="flex items-center gap-1.5 shrink-0">
                        <Layers size={10} className="text-[#007BC0]" />
                        <span className="text-[10px] font-mono font-bold text-[#007BC0]">
                          {typeof stock.parts === 'number' ? stock.parts : stock.trays} pcs
                        </span>
                      </div>
                    </div>
                    {/* PN list */}
                    {pnEntries.length > 0 ? (
                      <div className="pt-1.5 border-t border-[#EFF1F2]">
                        <div className={isExpanded ? 'space-y-1' : 'max-h-[80px] overflow-y-auto space-y-1'}>
                          {pnEntries.map(([pn, count]) => (
                            <div key={pn} className="flex items-center justify-between gap-1">
                              <div className="font-mono text-[9px] text-[#525F6B] truncate">{pn}</div>
                              <div className="text-[9px] font-mono font-bold text-[#000000] shrink-0">{count} pcs</div>
                            </div>
                          ))}
                        </div>
                      </div>
                    ) : (
                      <div className="text-[9px] text-[#BFC0C2] pt-1 border-t border-[#EFF1F2] text-center">Empty</div>
                    )}
                  </div>
                );
              })}
            </div>
          </div>

          {/* Col 9-12: EDM Lines */}
          <div className="col-span-4 flex flex-col gap-2 min-h-0">
            <div className="relative border-b pb-1">
              <div className="text-xs uppercase font-bold text-[#525F6B] tracking-wide">5. EDM Lines</div>
              {/* Corridor selector — absolute so it doesn't affect header height */}
              <div className="absolute right-0" style={{ bottom: '2px' }}>
                <select
                  value={selectedEdmLine ?? ''}
                  onChange={(e) => setSelectedEdmLine(e.target.value === '' ? null : e.target.value)}
                  className="text-[10px] border border-[#BFC0C2] rounded px-1.5 py-0 leading-none h-[16px] bg-white text-[#525F6B] pr-5 appearance-none cursor-pointer focus:outline-none focus:border-[#007BC0]"
                >
                  <option value="">All corridors</option>
                  {Object.keys(groupedEdm).map((lineId) => {
                    const label = lineId.startsWith('Agie')
                      ? `Corredor ${lineId.replace('Agie', '').trim()}`
                      : lineId;
                    return <option key={lineId} value={lineId}>{label}</option>;
                  })}
                </select>
                <ChevronDown size={9} className="absolute right-1 top-1/2 -translate-y-1/2 text-[#525F6B] pointer-events-none" />
              </div>
            </div>

            {selectedEdmLine === null ? (
              /* All corridors: summary cards */
              <div className="flex-grow overflow-y-auto bg-[#f8f9fa] p-2 rounded border border-[#EFF1F2]" style={{ minHeight: 0 }}>
                <div className="grid grid-cols-1 gap-4">
                  {Object.entries(filteredGroupedEdm).map(([lineId, machines]) => (
                    <ZoneSummaryCard
                      key={lineId}
                      lineId={lineId}
                      machines={machines}
                      onClick={() => setSelectedZone(lineId)}
                    />
                  ))}
                </div>
              </div>
            ) : (
              /* Single corridor: machine list */
              <div className="flex-grow min-h-0 overflow-y-auto bg-[#f8f9fa] p-2 rounded border border-[#EFF1F2]">
                <div className="grid grid-cols-2 xl:grid-cols-3 gap-3">
                  {(groupedEdm[selectedEdmLine] ?? []).map((m) => (
                    <MachineCard key={m.machine_id} machine={m} />
                  ))}
                </div>
              </div>
            )}
          </div>

          {/* Col 13-14: Final Markets — HE (top) + Packaging (bottom) */}
          <div className="col-span-2 flex flex-col gap-2 min-h-0">
            <div className="text-xs uppercase font-bold text-[#525F6B] tracking-wide border-b pb-1">6. Final Markets</div>

            {/* HE Market */}
            <div className="h-1/2 flex flex-col min-h-0">
              <div className="text-[10px] font-bold mb-1" style={{ color: BOSCH_COLORS.turquoise }}>HE Market</div>
              <div className="flex-grow min-h-0 bg-[#EFF1F2] rounded-sm border border-[#BFC0C2] border-dashed overflow-hidden">
                <FinalMarketColumn items={filteredHeMarket} accentColor={BOSCH_COLORS.turquoise} />
              </div>
            </div>

            {/* Packaging Market */}
            <div className="h-1/2 flex flex-col min-h-0">
              <div className="text-[10px] font-bold mb-1" style={{ color: BOSCH_COLORS.purple }}>Packaging Market</div>
              <div className="flex-grow min-h-0 bg-[#EFF1F2] rounded-sm border border-[#BFC0C2] border-dashed overflow-hidden">
                <FinalMarketColumn items={filteredPackagingMarket} accentColor={BOSCH_COLORS.purple} />
              </div>
            </div>
          </div>

        </div>
      </div>

      {/* Modals */}
      <AnimatePresence>
        {selectedZone && (
          <ZoneDetailModal
            lineId={selectedZone}
            machines={groupedEdm[selectedZone] || []}
            currentTimeSec={currentTimeSec}
            onClose={() => setSelectedZone(null)}
          />
        )}
        {selectedStock && (
          <StockDetailModal
            stock={selectedStock}
            onClose={() => setSelectedStock(null)}
          />
        )}
        {showSettings && (
          <SettingsModal
            settings={settings}
            allMachineIds={frameMachines.filter((m) => m.process_type === 'EDM').map((m) => m.machine_id)}
            onSave={(newSettings) => {
              setSettings(newSettings);
              saveSettings(newSettings);
              // Reload data immediately with new params
              setTimeout(() => loadData(), 0);
            }}
            onClose={() => setShowSettings(false)}
          />
        )}
      </AnimatePresence>
    </div>
  );
};
