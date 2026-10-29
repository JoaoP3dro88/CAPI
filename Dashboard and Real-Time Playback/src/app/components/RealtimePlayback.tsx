import React, { useState, useEffect, useMemo } from 'react';
import { BOSCH_COLORS, HardStageMarketItem, KPI_THRESHOLDS, getTrafficColor, OrderMeta, RealtimeData, RealtimeMachine, StockInfo, fetchRealtimeData } from '../data';
import { Card } from './ui/Card';
import { Play, Pause, FastForward, Rewind, SkipBack, SkipForward, Clock, Maximize2, X, Grid, Activity, Layers, ChevronDown } from 'lucide-react';
import { motion, AnimatePresence } from 'motion/react';
import clsx from 'clsx';
import { createPortal } from 'react-dom';

const formatTime = (seconds: number) => {
  if (seconds < 3600) {
    const m = Math.max(1, Math.ceil(seconds / 60));
    return `${m} min`;
  }
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  return m > 0 ? `${h} h ${m} min` : `${h} h`;
};

const getCompletionColor = (pct: number) => {
  if (pct >= 0.8) return BOSCH_COLORS.traffic.green;
  if (pct >= 0.5) return BOSCH_COLORS.traffic.yellow;
  return BOSCH_COLORS.traffic.red;
};

// --- Sub-components ---

const MachineCard = ({
  machine,
  dense = false,
  currentTimeSec,
}: {
  machine: RealtimeMachine,
  dense?: boolean,
  currentTimeSec: number,
}) => {
  const { state } = machine;
  const progressPct = (() => {
    if (
      state.start_sec == null ||
      state.end_sec == null ||
      state.end_sec <= state.start_sec ||
      state.activity === 'idle' ||
      state.activity === 'inactive'
    ) {
      return 0;
    }
    const clamped = Math.min(Math.max(currentTimeSec, state.start_sec), state.end_sec);
    return ((clamped - state.start_sec) / (state.end_sec - state.start_sec)) * 100;
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

  if (state.activity === 'process') {
    statusColor = BOSCH_COLORS.chart.process;
    statusText = 'RUNNING';
  } else if (state.activity === 'changeover') {
    statusColor = BOSCH_COLORS.chart.changeover;
    statusText = 'SETUP';
  } else if (state.activity === 'waiting') {
    statusColor = BOSCH_COLORS.chart.waiting;
    statusText = 'WAIT';
  } else if (state.activity === 'inactive') {
    statusColor = BOSCH_COLORS.chart.inactive;
    statusText = 'OFF';
  } else if (state.activity === 'test') {
    statusColor = BOSCH_COLORS.chart.test;
    statusText = 'TEST';
  }

  return (
    <motion.div
      layout
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      className={clsx(
        "bg-white border shadow-sm relative overflow-hidden transition-all hover:shadow-md cursor-default",
        dense ? "p-2" : "p-3"
      )}
      style={{ borderLeft: `4px solid ${statusColor}` }}
    >
      <div className="flex justify-between items-start mb-1">
        <span className={clsx("font-bold text-[#000000]", dense ? "text-xs" : "text-sm")}>{displayMachineId(machine.machine_id)}</span>
        {!dense && (
          <div className="flex items-center gap-2">
            <span
              className="text-[10px] font-mono px-1 rounded"
              style={{
                color: getTrafficColor(state.current_oee ?? 0, KPI_THRESHOLDS.oee, { higherIsBetter: true }),
                backgroundColor: BOSCH_COLORS.lightGray,
              }}
            >
              OEE: {((state.current_oee ?? 0) * 100).toFixed(0)}%
            </span>
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
        {state.activity !== 'idle' ? (
          <>
            <div className={clsx("font-medium text-[#007BC0] truncate", dense ? "text-[10px]" : "text-xs")}>
              {state.part_number_id || state.order_id}
            </div>
            {!dense && state.order_id && (
              <div className="text-[10px] text-[#525F6B] truncate">{state.order_id}</div>
            )}
            <div className="w-full bg-[#EFF1F2] h-1.5 mt-1 rounded-full overflow-hidden">
              <motion.div
                className="h-full"
                style={{ backgroundColor: statusColor }}
                initial={false}
                animate={{
                  width: `${Math.min(100, Math.max(0, progressPct))}%`
                }}
              />
            </div>
          </>
        ) : (
          <div className={clsx("flex items-center text-[#BFC0C2] italic", dense ? "h-4 text-[9px]" : "h-8 text-[10px] justify-center")}>
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
  onClick
}: {
  lineId: string,
  machines: RealtimeMachine[],
  onClick: () => void
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
  // Calculate summary stats
  const total = machines.length;
  const running = machines.filter(m => m.state.activity === 'process').length;
  const setup = machines.filter(m => m.state.activity === 'changeover').length;
  const waiting = machines.filter(m => m.state.activity === 'waiting').length;
  const idle = machines.filter(m => m.state.activity === 'idle').length;
  const inactive = machines.filter(m => m.state.activity === 'inactive').length;
  const utilization = total > 0 ? Math.round(((total - idle) / total) * 100) : 0;

  // Calculate Avg OEE for this line
  const avgOee = total > 0
    ? machines.reduce((sum, m) => sum + (m.state.current_oee ?? 0), 0) / total
    : 0;

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

      {/* Mini Heatmap Visualization */}
      <div className="grid grid-cols-10 gap-1 mb-4">
        {machines.map((m, i) => {
          let color = BOSCH_COLORS.lightGray;
          if (m.state.activity === 'process') color = BOSCH_COLORS.chart.process;
          if (m.state.activity === 'changeover') color = BOSCH_COLORS.chart.changeover;
          if (m.state.activity === 'waiting') color = BOSCH_COLORS.chart.waiting;
          if (m.state.activity === 'inactive') color = BOSCH_COLORS.chart.inactive;
          if (m.state.activity === 'test') color = BOSCH_COLORS.chart.test;

          return (
            <div
              key={m.machine_id}
              className="h-2 w-full rounded-sm"
              style={{ backgroundColor: color }}
              title={`${displayMachineId(m.machine_id)}: ${m.state.activity} (OEE: ${((m.state.current_oee ?? 0) * 100).toFixed(0)}%)`}
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
          <span className="font-bold text-[#007BC0]">{running}</span>
          <span>Run</span>
        </div>
        <div className="flex flex-col items-center">
          <span className="font-bold text-[#E8A500]">{setup}</span>
          <span>Setup</span>
        </div>
        <div className="flex flex-col items-center">
          <span className="font-bold" style={{ color: BOSCH_COLORS.chart.waiting }}>{waiting}</span>
          <span>Wait</span>
        </div>
        <div className="flex flex-col items-center">
          <span className="font-bold" style={{ color: BOSCH_COLORS.chart.inactive }}>{inactive}</span>
          <span>Off</span>
        </div>
        <div className="flex flex-col items-center">
          <span className="font-bold text-[#BFC0C2]">{idle}</span>
          <span>Idle</span>
        </div>
      </div>
    </div>
  );
};

const ZoneDetailModal = ({
  lineId,
  machines,
  currentTimeSec,
  onClose
}: {
  lineId: string,
  machines: RealtimeMachine[],
  currentTimeSec: number,
  onClose: () => void
}) => {
  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-8">
      <motion.div
        initial={{ opacity: 0, scale: 0.95 }}
        animate={{ opacity: 1, scale: 1 }}
        exit={{ opacity: 0, scale: 0.95 }}
        className="bg-[#EFF1F2] w-full h-full max-w-[1800px] max-h-[95vh] rounded-lg shadow-2xl flex flex-col overflow-hidden"
      >
        {/* Modal Header */}
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
          <button
            onClick={onClose}
            className="p-2 hover:bg-[#EFF1F2] rounded-full transition-colors"
          >
            <X size={24} className="text-[#525F6B]" />
          </button>
        </div>

        {/* Modal Body - Grid of Machines */}
        <div className="flex-grow p-6 overflow-y-auto">
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
            {machines.map(m => (
              <MachineCard key={m.machine_id} machine={m} currentTimeSec={currentTimeSec} />
            ))}
          </div>
        </div>
      </motion.div>
    </div>,
    document.body
  );
};

// (HardStageMarketDetailModal removed — nozzles shown inline in the card)

// --- Main Component ---

export const RealtimePlayback: React.FC<{ simRefDt?: string | null }> = ({ simRefDt }) => {
  const [data, setData] = useState<RealtimeData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isPlaying, setIsPlaying] = useState(false);
  const [frameIndex, setFrameIndex] = useState(0);
  const [playbackSpeed, setPlaybackSpeed] = useState(1000);
  const [stepMode, setStepMode] = useState<'minutes' | 'percent'>('minutes');
  const [minuteStep, setMinuteStep] = useState(5);
  const [percentStep, setPercentStep] = useState(2);
  const [selectedZone, setSelectedZone] = useState<string | null>(null);
  const [selectedStock, setSelectedStock] = useState<StockInfo | null>(null);
  const [selectedHardStageMarket, setSelectedHardStageMarket] = useState<HardStageMarketItem | null>(null);
  // EDM line dropdown: null = "All corridors" (summary cards), string = specific line (machine list)
  const [selectedEdmLine, setSelectedEdmLine] = useState<string | null>(null);

  /**
   * When backend adds it, we expect realtime.json to contain a lookup table like:
   * data.orders_by_id[order_id] = { order_id, due_date: 'YYYY-MM-DD', title?, ... }
   *
   * Until then, this safely falls back to an empty record.
   * 
   * Example:
   "orders_by_id": {
    "0632980875": {
      "order_id": "0632980875",
      "due_date": "2026-02-23",
      "title": "Order 0632980875",
      "part_number_id": "0433172203",
      "quantity": 450
    },
    "0632326261": {
      "order_id": "0632326261",
      "due_date": "2026-02-25",
      "title": "Order 0632326261",
      "part_number_id": "0433172204",
      "quantity": 120
    },
    "0635706470": {
      "order_id": "0635706470",
      "due_date": "2026-02-20",
      "title": "Order 0635706470",
      "part_number_id": "0433172205",
      "quantity": 80
    }
  },
  "hard_stage_market": [
    {
      "body_partnumber": "0433172203",
      "body_qtty": 120,
      "nozzles": [
        "0200100001",
        "0200100002",
        "0200100003"
      ]
    },
    {
      "body_partnumber": "0433172204",
      "body_qtty": 35,
      "nozzles": [
        "0200100002",
        "0200100004"
      ]
    },
    {
      "body_partnumber": "0433172205",
      "body_qtty": 0,
      "nozzles": []
    }
  ] 
   *
   *
   *
   *
   *
   */
  const ordersById: Record<string, OrderMeta> = useMemo(() => {
    return data?.orders_by_id ?? {};
  }, [data]);





  useEffect(() => {
    let isMounted = true;
    fetchRealtimeData()
      .then(payload => {
        if (isMounted) setData(payload);
      })
      .catch(err => {
        if (isMounted) setError(err.message ?? 'Failed to load realtime data');
      });
    return () => {
      isMounted = false;
    };
  }, []);

  // Jump to the frame closest to "now" when data first loads, using
  // sim_ref_dt (the wall-clock time at which the simulation was anchored).
  useEffect(() => {
    if (!data || !simRefDt) return;
    const frames = data.frames;
    if (!frames || frames.length === 0) return;
    const refMs = new Date(simRefDt).getTime();
    const nowOffsetSec = (Date.now() - refMs) / 1000;
    const best = frames.findIndex(f => f.time_sec >= nowOffsetSec);
    setFrameIndex(best >= 0 ? best : frames.length - 1);
  // Run once when data arrives; intentionally not re-running on simRefDt changes
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data]);

  const totalFrames = Array.isArray(data?.frames) ? data!.frames.length : 0;
  const safeIndex = totalFrames > 0 ? Math.min(frameIndex, totalFrames - 1) : 0;
  const currentFrame = totalFrames > 0 ? data!.frames[safeIndex] : null;
  const stepFrames = totalFrames > 0
    ? (stepMode === 'percent'
      ? Math.max(1, Math.round((percentStep / 100) * totalFrames))
      : Math.max(1, Math.round((minuteStep * 60) / data!.metadata.step_sec)))
    : 1;

  // Timers
  useEffect(() => {
    let interval: ReturnType<typeof setInterval> | undefined;
    if (isPlaying && totalFrames > 0) {
      interval = setInterval(() => {
        setFrameIndex(prev => {
          if (prev >= totalFrames - 1) {
            setIsPlaying(false);
            return prev;
          }
          const next = prev + stepFrames;
          if (next >= totalFrames - 1) {
            setIsPlaying(false);
            return totalFrames - 1;
          }
          return next;
        });
      }, playbackSpeed);
    }
    return () => {
      if (interval) clearInterval(interval);
    };
  }, [isPlaying, totalFrames, playbackSpeed, stepFrames]);

  // Derived data
  const frameMachines = Array.isArray(currentFrame?.machines) ? currentFrame!.machines : [];
  const laserMachines = frameMachines.filter(m => m.process_type === 'LASER');
  const edmMachines = frameMachines.filter(m => m.process_type === 'EDM');

  const groupedEdm = useMemo(() => {
    return edmMachines.reduce((acc, m) => {
      if (!acc[m.line_id]) acc[m.line_id] = [];
      acc[m.line_id].push(m);
      return acc;
    }, {} as Record<string, typeof edmMachines>);
  }, [edmMachines]);

  // Output Split
  const producedOrders = Array.isArray(currentFrame?.produced_orders) ? currentFrame!.produced_orders : [];
  const producedHEByType = producedOrders.filter(o => o.product_type === 'CRIN');
  const producedAssemblyByType = producedOrders.filter(o => o.product_type === 'PLV');
  const useFallbackSplit = producedOrders.length > 0 && producedHEByType.length === 0 && producedAssemblyByType.length === 0;
  const producedHE = useFallbackSplit ? producedOrders : producedHEByType;
  const producedAssembly = useFallbackSplit ? [] : producedAssemblyByType;



  // Backlog grouped by due date (requires orders_by_id in realtime.json)
  const backlogByDueDate = useMemo(() => {
    const backlogIds = Array.isArray(currentFrame?.backlog_orders) ? currentFrame!.backlog_orders : [];
    const groups = backlogIds.reduce((acc, orderId) => {
      const due = ordersById[orderId]?.due_date ?? 'Unknown date';
      if (!acc[due]) acc[due] = [];
      acc[due].push(orderId);
      return acc;
    }, {} as Record<string, string[]>);

    const keys = Object.keys(groups).sort((a, b) => {
      if (a === 'Unknown date') return 1;
      if (b === 'Unknown date') return -1;
      return a.localeCompare(b);
    });

    return keys.map(k => ({ due_date: k, orderIds: groups[k] }));
  }, [currentFrame, ordersById]);

  const todayIso = useMemo(() => {
    return new Date().toISOString().slice(0, 10);
  }, []);

  const todayOrderIds = useMemo(() => {
    const ids = Array.isArray(currentFrame?.backlog_orders) ? currentFrame!.backlog_orders : [];
    // Today bucket: due_date >= today (YYYY-MM-DD string compare works), plus missing/unknown dates.
    return ids.filter(orderId => {
      const due = ordersById[orderId]?.due_date;
      if (!due) return true;
      return due >= todayIso;
    });
  }, [currentFrame, ordersById, todayIso]);

  const backlogNonTodayByDueDate = useMemo(() => {
    return backlogByDueDate
      .map(g => ({ ...g, orderIds: g.orderIds.filter(id => !todayOrderIds.includes(id)) }))
      .filter(g => g.orderIds.length > 0);
  }, [backlogByDueDate, todayOrderIds]);

  const hardStageMarketItems = useMemo(() => {
    const items = Array.isArray(data?.hard_stage_market) ? data!.hard_stage_market : [];
    // Stable ordering for UI.
    return [...items].sort((a, b) => a.body_partnumber.localeCompare(b.body_partnumber));
  }, [data]);



  if (error) {
    return (
      <Card title="Data Error">
        <p className="text-sm text-[#525F6B]">{error}</p>
      </Card>
    );
  }

  if (!data) {
    return (
      <Card title="Loading">
        <p className="text-sm text-[#525F6B]">Loading realtime data...</p>
      </Card>
    );
  }

  if (totalFrames === 0 || !currentFrame) {
    return (
      <Card title="No Data">
        <p className="text-sm text-[#525F6B]">Realtime frames are empty.</p>
      </Card>
    );
  }

  return (
    <div className="space-y-6 h-[calc(100vh-140px)] flex flex-col">
      {/* 3.1 Playback Controls */}
      <Card className="flex-shrink-0 sticky top-24 z-10">
        <div className="flex flex-col md:flex-row items-center justify-between gap-6">
          <div className="flex items-center gap-4">
            <button
              onClick={() => setIsPlaying(!isPlaying)}
              className="w-12 h-12 flex items-center justify-center rounded-full bg-[#007BC0] text-white hover:bg-[#00629A] transition-colors shadow-md"
            >
              {isPlaying ? <Pause fill="currentColor" /> : <Play fill="currentColor" className="ml-1" />}
            </button>

            <div className="flex gap-2 text-[#525F6B]">
              <button onClick={() => setFrameIndex(0)} className="p-2 hover:bg-[#EFF1F2] rounded"><SkipBack size={20} /></button>
              <button onClick={() => setFrameIndex(Math.max(0, frameIndex - stepFrames))} className="p-2 hover:bg-[#EFF1F2] rounded"><Rewind size={20} /></button>
              <button onClick={() => setFrameIndex(Math.min(totalFrames - 1, frameIndex + stepFrames))} className="p-2 hover:bg-[#EFF1F2] rounded"><FastForward size={20} /></button>
              <button onClick={() => setFrameIndex(totalFrames - 1)} className="p-2 hover:bg-[#EFF1F2] rounded"><SkipForward size={20} /></button>
            </div>
          </div>

          <div className="flex-grow w-full md:w-auto px-4">
            <div className="flex justify-between text-xs text-[#525F6B] mb-2 font-medium">
              <span>Start</span>
              <span className="text-[#007BC0] font-bold text-lg flex items-center gap-2">
                <Clock size={16} />
                {formatTime(currentFrame.time_sec)}
              </span>
              <span>End</span>
            </div>
            <input
              type="range"
              min={0}
              max={totalFrames - 1}
              step={stepFrames}
              value={frameIndex}
              onChange={(e) => setFrameIndex(parseInt(e.target.value))}
              className="w-full h-2 bg-[#EFF1F2] rounded-lg appearance-none cursor-pointer accent-[#007BC0]"
            />
          </div>

          <div className="flex items-center gap-3 text-sm text-[#525F6B]">
            <span>Speed:</span>
            <select
              value={playbackSpeed}
              onChange={(e) => setPlaybackSpeed(parseInt(e.target.value))}
              className="border border-[#BFC0C2] rounded px-2 py-1 bg-white"
            >
              <option value={2000}>0.5x</option>
              <option value={1000}>1x</option>
              <option value={500}>2x</option>
              <option value={100}>10x</option>
            </select>
            <span>Step:</span>
            <select
              value={stepMode}
              onChange={(e) => setStepMode(e.target.value as 'minutes' | 'percent')}
              className="border border-[#BFC0C2] rounded px-2 py-1 bg-white"
            >
              <option value="minutes">Minutes</option>
              <option value="percent">Percent</option>
            </select>
            {stepMode === 'minutes' ? (
              <select
                value={minuteStep}
                onChange={(e) => setMinuteStep(parseInt(e.target.value))}
                className="border border-[#BFC0C2] rounded px-2 py-1 bg-white"
              >
                <option value={1}>1 min</option>
                <option value={2}>2 min</option>
                <option value={5}>5 min</option>
                <option value={10}>10 min</option>
                <option value={15}>15 min</option>
              </select>
            ) : (
              <select
                value={percentStep}
                onChange={(e) => setPercentStep(parseInt(e.target.value))}
                className="border border-[#BFC0C2] rounded px-2 py-1 bg-white"
              >
                <option value={1}>1%</option>
                <option value={2}>2%</option>
                <option value={5}>5%</option>
                <option value={10}>10%</option>
              </select>
            )}
          </div>
        </div>
      </Card>

      {/* 3.2 Plant Layout Visualization */}
      <div className="flex-grow min-h-0 overflow-x-auto overflow-y-hidden pb-4">
        <div className="min-w-[1400px] h-full grid grid-cols-14 gap-4">

          {/* 3.3 Backlog (Col 1-2) */}
          <div className="col-span-2 flex flex-col gap-2 min-h-0">
            <div className="text-xs uppercase font-bold text-[#525F6B] tracking-wide border-b pb-1">1. Orders</div>
            <div className="h-1/2 flex flex-col min-h-0">
              <div className="text-[10px] font-bold text-[#007BC0] mb-1">Active Orders</div>
              <div className="bg-[#EFF1F2] flex-grow min-h-0 rounded-sm p-2 overflow-y-auto space-y-2 border border-[#BFC0C2] border-dashed">
                <AnimatePresence>
                  {todayOrderIds.map(orderId => {
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
                        <div className="flex items-center justify-between gap-1">
                          <div className="font-bold text-xs text-[#007BC0] truncate">{meta?.part_number_id ?? '—'}</div>
                          <div className="text-[10px] font-mono font-bold text-[#000000] shrink-0">{orderQty ?? '—'} pcs</div>
                        </div>
                        <div className={clsx('flex items-center justify-between gap-1 mt-0.5', isShort ? 'text-[#FF3732]' : 'text-[#525F6B]')}>
                          <div className="text-[9px] truncate">corpo: {hsItem?.body_partnumber ?? '—'}</div>
                          <div className="text-[9px] font-mono shrink-0">{bodyQty !== null ? `${bodyQty} pcs` : '—'}</div>
                        </div>
                        <div className="flex items-center justify-between gap-1 mt-1 pt-1 border-t border-[#EFF1F2]">
                          <div className="text-[9px] text-[#525F6B] truncate">{orderId}</div>
                          <div className="text-[9px] font-mono text-[#525F6B] shrink-0">{meta?.due_date ?? '—'}</div>
                        </div>
                      </motion.div>
                    );
                  })}
                  {todayOrderIds.length === 0 && (
                    <div className="text-center text-[#BFC0C2] text-xs mt-10">Empty</div>
                  )}
                </AnimatePresence>
              </div>
            </div>
            <div className="h-1/2 flex flex-col min-h-0">
              <div className="text-[10px] font-bold text-[#FF3732]">Backlog Orders</div>
              <div className="bg-[#EFF1F2] flex-grow min-h-0 rounded-sm p-2 overflow-y-auto space-y-2 border border-[#BFC0C2] border-dashed">
                <AnimatePresence>
                  {backlogNonTodayByDueDate.map(group => (
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
                      {group.orderIds.map(orderId => {
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
                            <div className="flex items-center justify-between gap-1">
                              <div className="font-bold text-xs text-[#007BC0] truncate">{meta?.part_number_id ?? '—'}</div>
                              <div className="text-[10px] font-mono font-bold text-[#000000] shrink-0">{orderQty ?? '—'} pcs</div>
                            </div>
                            <div className={clsx('flex items-center justify-between gap-1 mt-0.5', isShort ? 'text-[#FF3732]' : 'text-[#525F6B]')}>
                              <div className="text-[9px] truncate">corpo: {hsItem?.body_partnumber ?? '—'}</div>
                              <div className="text-[9px] font-mono shrink-0">{bodyQty !== null ? `${bodyQty} pcs` : '—'}</div>
                            </div>
                            <div className="flex items-center justify-between gap-1 mt-1 pt-1 border-t border-[#EFF1F2]">
                              <div className="text-[9px] text-[#525F6B] truncate">{orderId}</div>
                              <div className="text-[9px] font-mono text-[#525F6B] shrink-0">{meta?.due_date ?? '—'}</div>
                            </div>
                          </motion.div>
                        );
                      })}
                    </motion.div>
                  ))}
                  {backlogNonTodayByDueDate.length === 0 && (
                    <div className="text-center text-[#BFC0C2] text-xs mt-10">Empty</div>
                  )}
                </AnimatePresence>
              </div>
            </div>
          </div>

          {/* 3.4 Hard Stage Market (Col 3-4) */}
          <div className="col-span-2 flex flex-col gap-2 min-h-0">
            <div className="text-xs uppercase font-bold text-[#525F6B] tracking-wide border-b pb-1">2. HARD STAGE MARKET</div>
            <div className="flex-grow min-h-0 bg-[#EFF1F2] p-2 rounded-sm space-y-2 overflow-y-auto">
              {hardStageMarketItems.map(item => (
                <motion.div
                  key={item.body_partnumber}
                  layout
                  initial={{ opacity: 0, y: -6 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, scale: 0.98 }}
                  className="bg-white p-2 shadow-sm border border-transparent"
                >
                  <div className="flex items-center justify-between gap-2 mb-1.5">
                    <div className="text-xs font-bold text-[#000000] break-all">{item.body_partnumber}</div>
                    <div className="text-[10px] font-mono font-bold bg-[#EFF1F2] px-2 py-0.5 rounded text-[#007BC0] shrink-0">
                      {item.body_qtty} pcs
                    </div>
                  </div>
                  {Array.isArray(item.nozzles) && item.nozzles.length > 0 ? (
                    <div className="pt-1.5 border-t border-[#EFF1F2]">
                      <div className="text-[9px] uppercase font-bold text-[#BFC0C2] tracking-wide mb-1">Nozzles</div>
                      <div className="max-h-[80px] overflow-y-auto space-y-1">
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
                </motion.div>
              ))}
              {hardStageMarketItems.length === 0 && (
                <div className="text-center text-[#BFC0C2] text-xs mt-10">Empty</div>
              )}
            </div>
          </div>

          {/* 3.5 Laser Marking (Col 5-6) */}
          <div className="col-span-2 flex flex-col gap-2 min-h-0">
            <div className="text-xs uppercase font-bold text-[#525F6B] tracking-wide border-b pb-1">3. Laser Marking</div>
            <div className="flex-grow min-h-0 space-y-3 bg-[#f8f9fa] p-2 rounded border border-transparent overflow-y-auto">
              {laserMachines.map(m => (
                <MachineCard key={m.machine_id} machine={m} currentTimeSec={currentFrame.time_sec} />
              ))}
            </div>
          </div>

          {/* 3.6 Stock (Col 7-8) */}
          <div className="col-span-2 flex flex-col gap-2 min-h-0">
            <div className="text-xs uppercase font-bold text-[#525F6B] tracking-wide border-b pb-1">4. Buffer Stock</div>
            <div className="flex-grow min-h-0 bg-[#EFF1F2] p-2 rounded-sm space-y-2 overflow-y-auto">
              {(Array.isArray(currentFrame.stock_levels) ? currentFrame.stock_levels : [])
                .filter(stock => selectedEdmLine === null || stock.line_id === selectedEdmLine)
                .map(stock => {
                const displayLineId = stock.line_id.startsWith('Agie')
                  ? `Corredor ${stock.line_id.replace('Agie', '').trim()}`
                  : stock.line_id;
                const pnMap: Record<string, number> = {};
                if (Array.isArray(stock.tray_details)) {
                  for (const t of stock.tray_details) {
                    if (t.part_number_id) {
                      pnMap[t.part_number_id] = (pnMap[t.part_number_id] ?? 0) + 1;
                    }
                  }
                }
                const pnEntries = Object.entries(pnMap);
                const isExpanded = selectedEdmLine !== null;
                return (
                  <div key={stock.line_id} className="bg-white p-2 shadow-sm border border-transparent">
                    <div className="flex items-center justify-between mb-1.5">
                      <div className="text-xs font-bold text-[#525F6B]">{displayLineId}</div>
                      <div className="flex items-center gap-1.5 shrink-0">
                        <Layers size={10} className="text-[#007BC0]" />
                        <span className="text-[10px] font-mono font-bold text-[#007BC0]">
                          {typeof stock.parts === 'number' ? stock.parts : stock.trays} pcs
                        </span>
                      </div>
                    </div>
                    {pnEntries.length > 0 ? (
                      <div className="pt-1.5 border-t border-[#EFF1F2]">
                        <div className={isExpanded ? 'space-y-1' : 'max-h-[80px] overflow-y-auto space-y-1'}>
                          {pnEntries.map(([pn, count]) => (
                            <div key={pn} className="flex items-center justify-between gap-1">
                              <div className="font-mono text-[9px] text-[#525F6B] truncate">{pn}</div>
                              <div className="text-[9px] font-mono font-bold text-[#000000] shrink-0">{count * 20} pcs</div>
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

          {/* 3.7 EDM Machines (Col 9-12) */}
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
              <div className="flex-grow overflow-y-auto bg-[#f8f9fa] p-2 rounded border border-[#EFF1F2]" style={{ minHeight: 0 }}>
                <div className="grid grid-cols-1 gap-4">
                  {Object.entries(groupedEdm).map(([lineId, machines]) => (
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
              <div className="flex-grow min-h-0 overflow-y-auto bg-[#f8f9fa] p-2 rounded border border-[#EFF1F2]">
                <div className="grid grid-cols-2 xl:grid-cols-3 gap-3">
                  {(groupedEdm[selectedEdmLine] ?? []).map((m) => (
                    <MachineCard key={m.machine_id} machine={m} currentTimeSec={currentFrame.time_sec} />
                  ))}
                </div>
              </div>
            )}
          </div>

          {/* 3.8 Produced (Col 13-14) - SPLIT VIEW */}
          <div className="col-span-2 flex flex-col gap-2 min-h-0">
            <div className="text-xs uppercase font-bold text-[#525F6B] tracking-wide border-b pb-1">6. Finished Goods</div>

            {/* HE Split */}
            <div className="h-1/2 flex flex-col min-h-0">
              <div className="text-[10px] font-bold text-[#007BC0] mb-1">To HE (CRIN)</div>
              <div className="bg-[#EFF1F2] flex-grow min-h-0 rounded-sm p-2 overflow-y-auto space-y-2 border border-[#BFC0C2] border-dashed">
                {producedHE.map(order => {
                  const pct = typeof order.completion_pct === 'number' ? order.completion_pct : 1;
                  const pctLabel = `${Math.round(pct * 100)}%`;
                  const progressColor = getCompletionColor(pct);
                  return (
                    <motion.div key={order.order_id} className="bg-white p-2 shadow-sm border-l-2 border-l-[#007BC0] text-xs">
                      <div className="flex items-center justify-between">
                        <div className="font-bold">{order.order_id}</div>
                        <div className="text-[10px] font-bold" style={{ color: progressColor }}>{pctLabel}</div>
                      </div>
                      <div className="text-[9px] truncate text-[#525F6B]">{order.part_number_id}</div>
                      <div className="mt-1 h-1.5 w-full bg-[#EFF1F2] rounded-full overflow-hidden">
                        <div className="h-full" style={{ width: `${Math.min(100, Math.round(pct * 100))}%`, backgroundColor: progressColor }} />
                      </div>
                      <div className="text-[9px] text-[#525F6B] mt-1">
                        {typeof order.produced_pieces === 'number' ? order.produced_pieces : 0}/{order.quantity} pcs
                      </div>
                    </motion.div>
                  );
                })}
                {producedHE.length === 0 && <div className="text-center text-[#BFC0C2] text-[10px] mt-4">Empty</div>}
              </div>
            </div>

            {/* Assembly Split */}
            <div className="h-1/2 flex flex-col min-h-0">
              <div className="text-[10px] font-bold text-[#963886] mb-1">To Assembly (PLV)</div>
              <div className="bg-[#EFF1F2] flex-grow min-h-0 rounded-sm p-2 overflow-y-auto space-y-2 border border-[#BFC0C2] border-dashed">
                {producedAssembly.map(order => {
                  const pct = typeof order.completion_pct === 'number' ? order.completion_pct : 1;
                  const pctLabel = `${Math.round(pct * 100)}%`;
                  const progressColor = getCompletionColor(pct);
                  return (
                    <motion.div key={order.order_id} className="bg-white p-2 shadow-sm border-l-2 border-l-[#963886] text-xs">
                      <div className="flex items-center justify-between">
                        <div className="font-bold">{order.order_id}</div>
                        <div className="text-[10px] font-bold" style={{ color: progressColor }}>{pctLabel}</div>
                      </div>
                      <div className="text-[9px] truncate text-[#525F6B]">{order.part_number_id}</div>
                      <div className="mt-1 h-1.5 w-full bg-[#EFF1F2] rounded-full overflow-hidden">
                        <div className="h-full" style={{ width: `${Math.min(100, Math.round(pct * 100))}%`, backgroundColor: progressColor }} />
                      </div>
                      <div className="text-[9px] text-[#525F6B] mt-1">
                        {typeof order.produced_pieces === 'number' ? order.produced_pieces : 0}/{order.quantity} pcs
                      </div>
                    </motion.div>
                  );
                })}
                {producedAssembly.length === 0 && <div className="text-center text-[#BFC0C2] text-[10px] mt-4">Empty</div>}
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
            currentTimeSec={currentFrame.time_sec}
            onClose={() => setSelectedZone(null)}
          />
        )}
      </AnimatePresence>

    </div>
  );
};
