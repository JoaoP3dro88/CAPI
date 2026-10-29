import React, { useMemo, useState, useEffect, useRef } from 'react';
import { BOSCH_COLORS, KPI_THRESHOLDS, getTrafficColor, StockInfo, DashboardData, fetchDashboardData, fetchHistoryData, TimelineEvent } from '../data';
import { Card } from './ui/Card';
import { DataTable } from './ui/DataTable';
import { StockDetailModal } from './ui/StockDetailModal';
import { 
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer
} from 'recharts';
import { Clock, Activity, Layers, CheckCircle, TrendingDown, Hourglass } from 'lucide-react';
import { AnimatePresence } from 'motion/react';
import { createPortal } from 'react-dom';
import clsx from 'clsx';

const formatTime = (seconds: number) => {
  if (seconds < 3600) {
    const m = Math.max(1, Math.ceil(seconds / 60));
    return `${m} min`;
  }
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  return m > 0 ? `${h} h ${m} min` : `${h} h`;
};

const FACILITY_TZ = 'America/Sao_Paulo';
const SHIFT_BOUNDARY_HOURS = [6, 15, 0];
const TIME_TICK_HOURS = 3;

const parseSimulationStart = (simulationStartedAt?: string | null): Date | null => {
  if (!simulationStartedAt) return null;
  const parsed = new Date(simulationStartedAt);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
};

/** Return the hour (0-23) of a Date in the facility timezone. */
const getFacilityHour = (d: Date): number => {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: FACILITY_TZ,
    hour: 'numeric',
    hour12: false,
  }).formatToParts(d);
  const hourPart = parts.find((p) => p.type === 'hour');
  return Number(hourPart?.value ?? 0) % 24;
};

const formatTimelineClockTime = (seconds: number, simulationStart: Date | null) => {
  if (!simulationStart) {
    const totalSec = seconds % (24 * 3600);
    const h = Math.floor(totalSec / 3600);
    const m = Math.floor((totalSec % 3600) / 60);
    return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`;
  }

  const timestamp = new Date(simulationStart.getTime() + (seconds * 1000));
  return timestamp.toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
    timeZone: FACILITY_TZ,
  });
};

const getShiftBoundaryOffsets = (simulationStart: Date | null, horizonSec: number) => {
  if (!simulationStart) return [] as number[];

  const startMs = simulationStart.getTime();

  // Compute the start of the facility-local day by scanning back from the
  // simulation start until the facility hour is 0.
  let anchorMs = startMs;
  // Step back up to 24 h in 1-hour increments to find midnight in facility TZ
  for (let i = 0; i < 48; i++) {
    const candidate = new Date(startMs - i * 3600_000);
    if (getFacilityHour(candidate) === 0) {
      anchorMs = candidate.getTime();
      // Snap to the exact minute boundary
      anchorMs -= anchorMs % 3600_000;
      break;
    }
  }

  const offsets = new Set<number>();
  for (let dayOffset = -1; dayOffset <= 2; dayOffset += 1) {
    SHIFT_BOUNDARY_HOURS.forEach((hour) => {
      const boundaryMs = anchorMs + (dayOffset * 24 + hour) * 3600_000;
      const offsetSec = (boundaryMs - startMs) / 1000;
      if (offsetSec > 0 && offsetSec < horizonSec) {
        offsets.add(offsetSec);
      }
    });
  }

  return Array.from(offsets).sort((left, right) => left - right);
};

const KPICard = ({
  label,
  value,
  subtext,
  icon: Icon,
  highlightColor,
  onClick,
  className,
}: {
  label: string;
  value: string | number | React.ReactNode;
  subtext?: string;
  icon?: any;
  highlightColor?: string;
  onClick?: () => void;
  className?: string;
}) => (
  <Card
    className={clsx(
      "!border-l-4",
      onClick && "cursor-pointer hover:shadow-md transition-shadow",
      className
    )}
    style={{ borderLeftColor: highlightColor || BOSCH_COLORS.blue }}
    onClick={onClick}
  >
    <div className="flex items-start justify-between">
      <div>
        <p className="text-xs text-[#525F6B] uppercase font-semibold mb-1">{label}</p>
        <div className="text-2xl font-bold text-[#000000]">{value}</div>
        {subtext && <p className="text-xs text-[#525F6B] mt-1">{subtext}</p>}
      </div>
      {Icon && <Icon style={{ color: highlightColor || BOSCH_COLORS.blue }} className="opacity-80" size={20} />}
    </div>
  </Card>
);

const displayLineId = (lineId: string) => {
  if (lineId === 'LaserM') {
    return 'Gravadoras';
  }
  if (lineId.startsWith('Agie')) {
    const number = lineId.replace('Agie', '').trim();
    return `Corredor ${number}`;
  }
  return lineId;
};

const displayMachineId = (machineId: string) => {
  if (machineId.startsWith('MACHINE_')) {
    const number = machineId.replace('MACHINE_', '').trim();
    return `Gravadora ${number}`;
  }
  if (machineId.startsWith('EDM')) {
    const number = machineId.replace('EDM', '').trim();
    return `Agie ${number}`;
  }
  return machineId;
};

const ChangeoverDetailModal = ({
  open,
  onClose,
  stats,
}: {
  open: boolean;
  onClose: () => void;
  stats: Array<{
    line_id: string;
    total_changeovers: number;
    changeover_loss_percentage: number;
    total_changeover_sec: number;
    total_waiting_sec: number;
    changeovers_per_machine: number;
  }>;
}) => {
  if (!open) return null;
  return createPortal(
    <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/60 backdrop-blur-sm p-8">
      <div className="bg-white w-full max-w-4xl max-h-[80vh] rounded-lg shadow-2xl flex flex-col overflow-hidden">
        <div className="bg-[#EFF1F2] px-6 py-4 border-b border-[#BFC0C2] flex justify-between items-center">
          <h2 className="text-xl font-bold text-[#000000]">Changeover by Line</h2>
          <button
            onClick={onClose}
            className="p-2 hover:bg-[#BFC0C2] rounded-full transition-colors"
          >
            ✕
          </button>
        </div>
        <div className="p-6 overflow-y-auto grid grid-cols-1 md:grid-cols-2 gap-4">
          {stats.map((line) => (
            <div key={line.line_id} className="border border-[#BFC0C2] rounded p-4">
              <div className="font-bold text-[#000000] mb-2">{displayLineId(line.line_id)}</div>
              <div className="space-y-3 text-xs text-[#525F6B]">
                <div className="flex items-center justify-between">
                  <span className="font-normal">CO total</span>
                  <span className="font-bold text-[#000000] ml-3">{line.total_changeovers}</span>
                </div>
                <div className="flex items-center justify-between">
                  <span className="font-normal">Avg loss</span>
                  <span
                    className="font-bold ml-3"
                    style={{
                      color: getTrafficColor(
                        line.changeover_loss_percentage,
                        KPI_THRESHOLDS.changeover_loss_percentage,
                        { higherIsBetter: false }
                      ),
                    }}
                  >
                    {line.changeover_loss_percentage}%
                  </span>
                </div>
                <div className="flex items-center justify-between">
                  <span className="font-normal">Total time</span>
                  <span className="font-bold text-[#000000] ml-3">{formatTime(line.total_changeover_sec)}</span>
                </div>
                <div className="flex items-center justify-between">
                  <span className="font-normal">Waiting time</span>
                  <span className="font-bold text-[#000000] ml-3">{formatTime(line.total_waiting_sec)}</span>
                </div>
                <div className="flex items-center justify-between">
                  <span className="font-normal">CO/máq</span>
                  <span className="font-bold text-[#000000] ml-3">{line.changeovers_per_machine}</span>
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>,
    document.body
  );
};

const StockTooltip = ({ active, payload }: any) => {
  if (!active || !payload || !payload.length) return null;
  const data = payload[0]?.payload;
  return (
    <div className="bg-white border border-[#BFC0C2] shadow-sm px-3 py-2 text-xs">
      <div className="font-semibold text-[#000000]">{data?.line_id}</div>
      <div className="text-[#525F6B]">Trays: {data?.trays ?? 0}</div>
      {typeof data?.parts === 'number' && (
        <div className="text-[#525F6B]">Parts: {data.parts}</div>
      )}
    </div>
  );
};

const TimelineChart = ({
  data,
  selectedPartNumberId,
  onSelectPartNumber,
  filterPartNumberId,
  showNowLine,
  showRealPlaceholder,
  lineChangeoverStats,
  simulationStartedAt,
  historyTimeline,
}: {
  data: DashboardData;
  selectedPartNumberId: string | null;
  onSelectPartNumber: (partNumberId: string) => void;
  filterPartNumberId?: string | null;
  showNowLine: boolean;
  showRealPlaceholder: boolean;
  lineChangeoverStats: Array<{
    line_id: string;
    total_changeovers: number;
    changeover_loss_percentage: number;
    total_changeover_sec: number;
    total_waiting_sec: number;
    changeovers_per_machine: number;
  }>;
  simulationStartedAt?: string | null;
  historyTimeline?: TimelineEvent[];
}) => {
  const { timeline, machines, kpis } = data;
  const totalTime = kpis.total_time_sec;
  const daySec = 24 * 3600;
  const timeScaleSec = daySec; // Full timeline is 24h (for calculations)
  const visibleTimeScaleSec = 12 * 3600; // But show only 12h visible (with scroll for rest)
  const simulationStart = useMemo(() => parseSimulationStart(simulationStartedAt), [simulationStartedAt]);

  // Tick every 60 s so nowOffsetSec — and therefore the "Real" bars — advance in real time.
  const [, setTick] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setTick(t => t + 1), 60_000);
    return () => clearInterval(id);
  }, []);

  const nowOffsetSec = useMemo(() => {
    if (!simulationStart) return null;
    return Math.max(0, Math.min(timeScaleSec, (Date.now() - simulationStart.getTime()) / 1000));
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [simulationStart, timeScaleSec, Math.floor(Date.now() / 60_000)]);
  
  const machineEvents = useMemo(() => {
    const grouped: Record<string, typeof timeline> = {};
    machines.forEach(m => grouped[m.machine_id] = []);
    timeline.forEach(e => {
      if (grouped[e.machine_id]) grouped[e.machine_id].push(e);
    });
    const activityPriority: Record<string, number> = {
      inactive: 0,
      waiting: 1,
      changeover: 2,
      test: 3,
      process: 4,
    };
    Object.values(grouped).forEach((events) =>
      events.sort((a, b) => {
        if (a.start_sec !== b.start_sec) return a.start_sec - b.start_sec;
        if (a.end_sec !== b.end_sec) return a.end_sec - b.end_sec;
        return (activityPriority[a.activity] ?? 5) - (activityPriority[b.activity] ?? 5);
      })
    );
    return grouped;
  }, [timeline, machines]);

  const historyEventsByMachine = useMemo(() => {
    const grouped: Record<string, TimelineEvent[]> = {};
    if (!historyTimeline) return grouped;
    historyTimeline.forEach(e => {
      if (!grouped[e.machine_id]) grouped[e.machine_id] = [];
      grouped[e.machine_id].push(e);
    });
    Object.values(grouped).forEach(events =>
      events.sort((a, b) => a.start_sec - b.start_sec || a.end_sec - b.end_sec)
    );
    return grouped;
  }, [historyTimeline]);

  const ticksSec = useMemo(() => {
    const tickStepSec = TIME_TICK_HOURS * 3600;
    const values: number[] = [];
    for (let sec = 0; sec <= timeScaleSec; sec += tickStepSec) {
      values.push(sec);
    }
    if (values[values.length - 1] !== timeScaleSec) {
      values.push(timeScaleSec);
    }
    return values;
  }, [timeScaleSec]);

  const shiftBoundariesSec = useMemo(
    () => getShiftBoundaryOffsets(simulationStart, timeScaleSec),
    [simulationStart, timeScaleSec]
  );

  const oeeNow = useMemo(() => {
    if (machines.length === 0 || totalTime <= 0 || nowOffsetSec == null) return 0;
    let running = 0;
    machines.forEach(machine => {
      const events = machineEvents[machine.machine_id] || [];
      const current = events.find(e => nowOffsetSec >= e.start_sec && nowOffsetSec <= e.end_sec);
      if (current?.activity === 'process') running += 1;
    });
    return running / machines.length;
  }, [machines, machineEvents, nowOffsetSec, totalTime]);

  const filteredMachines = useMemo(() => {
    if (!filterPartNumberId) return machines;
    return machines.filter(machine =>
      (machineEvents[machine.machine_id] || []).some(
        event => event.part_number_id === filterPartNumberId
      )
    );
  }, [machines, machineEvents, filterPartNumberId]);

  const machinesByLine = useMemo(() => {
    const grouped: Record<string, typeof machines> = {};
    filteredMachines.forEach(m => {
      if (!grouped[m.line_id]) grouped[m.line_id] = [];
      grouped[m.line_id].push(m);
    });
    
    // Sort lines: LaserM first, then Agie1-5
    const sortedEntries = Object.entries(grouped).sort(([aKey], [bKey]) => {
      // LaserM comes first
      if (aKey === 'LaserM') return -1;
      if (bKey === 'LaserM') return 1;
      
      // Then sort Agie lines numerically
      const aMatch = aKey.match(/Agie(\d+)/);
      const bMatch = bKey.match(/Agie(\d+)/);
      if (aMatch && bMatch) {
        return parseInt(aMatch[1]) - parseInt(bMatch[1]);
      }
      
      // Fallback alphabetical
      return aKey.localeCompare(bKey);
    });
    
    return Object.fromEntries(sortedEntries);
  }, [filteredMachines]);

  const [collapsedLines, setCollapsedLines] = React.useState<Record<string, boolean>>({});
  const timelineBodyRef = useRef<HTMLDivElement | null>(null);
  const [laneOffsetPx, setLaneOffsetPx] = useState<number>(128);
  const [laneWidthPx, setLaneWidthPx] = useState<number | null>(null);

  useEffect(() => {
    const root = timelineBodyRef.current;
    if (!root) return;

    const updateLaneOffset = () => {
      const lane = root.querySelector<HTMLElement>('[data-timeline-lane="true"]');
      if (!lane) return;
      const rootRect = root.getBoundingClientRect();
      const laneRect = lane.getBoundingClientRect();
      const nextOffset = Math.max(0, laneRect.left - rootRect.left);
      const nextWidth = Math.max(0, laneRect.width);
      setLaneOffsetPx(nextOffset);
      if (nextWidth > 0) {
        setLaneWidthPx(nextWidth);
      }
    };

    updateLaneOffset();
    window.addEventListener('resize', updateLaneOffset);

    let resizeObserver: ResizeObserver | null = null;
    if (typeof ResizeObserver !== 'undefined') {
      resizeObserver = new ResizeObserver(updateLaneOffset);
      resizeObserver.observe(root);
    }

    return () => {
      window.removeEventListener('resize', updateLaneOffset);
      if (resizeObserver) {
        resizeObserver.disconnect();
      }
    };
  }, [machinesByLine, collapsedLines, filterPartNumberId]);

  const toggleLine = (lineId: string) => {
    setCollapsedLines(prev => ({ ...prev, [lineId]: !prev[lineId] }));
  };

  const toPercent = (seconds: number): number => {
    if (timeScaleSec <= 0) return 0;
    const clamped = Math.max(0, Math.min(timeScaleSec, seconds));
    return (clamped / timeScaleSec) * 100;
  };

  // Calculate width multiplier: if we want to show 12h but have 24h of data,
  // the timeline needs to be 200% wide to enable horizontal scroll
  const widthMultiplier = timeScaleSec / visibleTimeScaleSec;

  return (
    <div className="max-h-[520px] overflow-y-auto overflow-x-auto">
      <div 
        className="min-w-[800px] relative" 
        ref={timelineBodyRef}
        style={{ width: `${widthMultiplier * 100}%` }}
      >
        <div className="sticky top-0 bg-white z-50 border-b border-[#BFC0C2] mb-2 pt-6 pb-2">
          <div className="relative h-10">
            <div
              className="absolute inset-y-0 text-xs text-[#525F6B]"
              style={{
                left: `${laneOffsetPx}px`,
                width: laneWidthPx != null ? `${laneWidthPx}px` : undefined,
                right: laneWidthPx == null ? 0 : undefined,
              }}
            >
              {ticksSec.map((tickSec) => {
                const isStart = tickSec === 0;
                const isEnd = tickSec === timeScaleSec;
                const transform = isStart ? 'translateX(0)' : isEnd ? 'translateX(-100%)' : 'translateX(-50%)';
                return (
                  <div
                    key={`tick-${tickSec}`}
                    className="absolute bottom-0 whitespace-nowrap"
                    style={{
                      left: `${toPercent(tickSec)}%`,
                      transform,
                    }}
                  >
                    {formatTimelineClockTime(tickSec, simulationStart)}
                  </div>
                );
              })}
              {shiftBoundariesSec.map((shiftSec) => {
                return (
                  <div
                    key={`shift-label-${shiftSec}`}
                    className="absolute top-0 text-[10px] text-[#525F6B] bg-white px-1 leading-none"
                    style={{
                      left: `${toPercent(shiftSec)}%`,
                      transform: 'translateX(-50%)',
                    }}
                  >
                    <span className="block text-center">Shift</span>
                    <span className="block text-center">change</span>
                  </div>
                );
              })}
            </div>
          </div>
        </div>

        <div className="space-y-4 relative">
          {timeScaleSec > 0 && (
            <div
              className="absolute top-0 bottom-0 pointer-events-none"
              style={{
                left: `${laneOffsetPx}px`,
                width: laneWidthPx != null ? `${laneWidthPx}px` : undefined,
                right: laneWidthPx == null ? 0 : undefined,
              }}
            >
              {showNowLine && nowOffsetSec != null && (
                <div
                  className="absolute top-0 bottom-0 w-[3px] z-20"
                  style={{
                    left: `${toPercent(nowOffsetSec)}%`,
                    backgroundColor: getTrafficColor(oeeNow, KPI_THRESHOLDS.oee, { higherIsBetter: true }),
                  }}
                  title={`Actual time: ${formatTimelineClockTime(nowOffsetSec, simulationStart)}`}
                >
                  <div
                    className="absolute top-1 left-1/2 -translate-x-1/2 w-3 h-3 rounded-full border border-white shadow"
                    style={{
                      backgroundColor: getTrafficColor(oeeNow, KPI_THRESHOLDS.oee, { higherIsBetter: true }),
                    }}
                  />
                  <div className="absolute top-5 left-1/2 -translate-x-1/2 text-[10px] font-semibold text-[#000000] bg-white/90 px-1 rounded">
                    {formatTimelineClockTime(nowOffsetSec, simulationStart)}
                  </div>
                </div>
              )}
              {shiftBoundariesSec.map((shiftSec, idx) => {
                return (
                  <div
                    key={`shift-line-${shiftSec}-${idx}`}
                    className="absolute top-0 bottom-0 w-0 z-10 border-l border-dashed border-[#BFC0C2]"
                    style={{
                      left: `${toPercent(shiftSec)}%`,
                      transform: 'translateX(-0.5px)',
                    }}
                    title={`Shift boundary: ${formatTimelineClockTime(shiftSec, simulationStart)}`}
                  />
                );
              })}
            </div>
          )}
          {Object.entries(machinesByLine).map(([lineId, lineMachines]) => {
             const isCollapsed = collapsedLines[lineId];
             const lineStats = lineChangeoverStats.find(s => s.line_id === lineId);
             return (
              <div key={lineId} className="border border-[#EFF1F2] rounded-md overflow-hidden">
                <div 
                  className="bg-[#EFF1F2] px-4 py-2 flex items-center gap-2 cursor-pointer hover:bg-[#E0E2E5] transition-colors"
                  onClick={() => toggleLine(lineId)}
                >
                  <div className={`transition-transform ${isCollapsed ? '-rotate-90' : 'rotate-0'}`}>
                    ▼
                  </div>
                  <span className="font-bold text-sm text-[#000000]">{displayLineId(lineId)}</span>
                  <span className="text-xs text-[#525F6B] bg-white px-2 py-0.5 rounded-full ml-2">
                    {lineMachines.length} Machines
                  </span>
                  {lineStats && (
                    <>
                      <span className="text-xs text-[#525F6B] bg-white px-2 py-0.5 rounded-full">
                        CO: {lineStats.total_changeovers}
                      </span>
                      <span
                        className="text-xs font-semibold bg-white px-2 py-0.5 rounded-full"
                        style={{
                          color: getTrafficColor(
                            lineStats.changeover_loss_percentage,
                            KPI_THRESHOLDS.changeover_loss_percentage,
                            { higherIsBetter: false }
                          ),
                        }}
                      >
                        Loss: {lineStats.changeover_loss_percentage}%
                      </span>
                      <span className="text-xs text-[#525F6B] bg-white px-2 py-0.5 rounded-full">
                        Total time: {formatTime(lineStats.total_changeover_sec)}
                      </span>
                      <span className="text-xs text-[#525F6B] bg-white px-2 py-0.5 rounded-full">
                        Wait: {formatTime(lineStats.total_waiting_sec)}
                      </span>
                      <span className="text-xs text-[#525F6B] bg-white px-2 py-0.5 rounded-full">
                        CO/máq: {lineStats.changeovers_per_machine}
                      </span>
                    </>
                  )}
                </div>

                {!isCollapsed && (
                  <div className="p-2 space-y-3 bg-white">
                    {lineMachines.map(machine => (
                      <div key={machine.machine_id} className="flex items-start group">
                        {/* Machine label */}
                        <div className="w-32 flex-shrink-0 pr-4 text-right pt-1">
                          <div className="text-sm font-bold truncate">{displayMachineId(machine.machine_id)}</div>
                          <div
                            className="text-[10px] font-semibold"
                            style={{
                              color: getTrafficColor(machine.static_oee, KPI_THRESHOLDS.oee, { higherIsBetter: true }),
                            }}
                          >
                            OEE {(machine.static_oee * 100).toFixed(1)}%
                          </div>
                        </div>

                        {/* Timeline lanes */}
                        <div className="flex-grow flex flex-col gap-[3px]">
                          <div
                            className="h-5 bg-[#EFF1F2] relative rounded-sm overflow-hidden"
                            data-timeline-lane="true"
                            title="Plan"
                          >
                            {machineEvents[machine.machine_id]?.map((event, idx) => {
                              if (
                                filterPartNumberId &&
                                event.activity !== 'inactive' &&
                                event.part_number_id !== filterPartNumberId
                              ) return null;
                              const startSec = Math.max(0, Math.min(timeScaleSec, event.start_sec));
                              const endSec = Math.max(0, Math.min(timeScaleSec, event.end_sec));
                              if (endSec <= startSec) return null;
                              const left = toPercent(startSec);
                              const width = toPercent(endSec) - toPercent(startSec);
                              let color = BOSCH_COLORS.chart.idle;
                              if (event.activity === 'process') color = BOSCH_COLORS.chart.process;
                              if (event.activity === 'changeover') color = BOSCH_COLORS.chart.changeover;
                              if (event.activity === 'waiting') color = BOSCH_COLORS.chart.waiting;
                              if (event.activity === 'inactive') color = BOSCH_COLORS.chart.inactive;
                              if (event.activity === 'test') color = BOSCH_COLORS.chart.test;
                              return (
                                <div
                                  key={idx}
                                  className="absolute h-full top-0 border-r border-white/20 flex items-center justify-center overflow-hidden hover:opacity-90 transition-opacity cursor-pointer"
                                  style={{
                                    left: `${left}%`,
                                    width: `${width}%`,
                                    backgroundColor: color,
                                    opacity:
                                      selectedPartNumberId && event.part_number_id && event.part_number_id !== selectedPartNumberId
                                        ? 0.2
                                        : 1,
                                    outline:
                                      selectedPartNumberId && event.part_number_id === selectedPartNumberId
                                        ? `2px solid ${BOSCH_COLORS.red}`
                                        : undefined,
                                  }}
                                    title={`${event.activity.toUpperCase()} — ${displayMachineId(machine.machine_id)} — ${event.part_number_id || event.order_id || ''} (${formatTime(event.end_sec - event.start_sec)})`}
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    if (!event.part_number_id) return;
                                    onSelectPartNumber(event.part_number_id);
                                  }}
                                >
                                  {width > 5 && (
                                    <span className="text-[9px] text-white font-medium truncate px-1">
                                      {event.activity === 'process'
                                        ? (event.part_number_id || event.order_id)
                                        : event.activity[0].toUpperCase()}
                                    </span>
                                  )}
                                </div>
                              );
                            })}
                          </div>

                          {showRealPlaceholder && (
                            <div
                              className="h-5 bg-[#EFF1F2] relative rounded-sm overflow-hidden border border-dashed border-[#BFC0C2]"
                              title="Real timeline"
                            >
                              {(historyEventsByMachine[machine.machine_id] ?? []).length === 0 && (
                                <>
                                  <div className="absolute inset-0 bg-[repeating-linear-gradient(45deg,_rgba(82,95,107,0.08)_0,_rgba(82,95,107,0.08)_8px,_transparent_8px,_transparent_16px)]" />
                                  <div className="absolute inset-0 flex items-center justify-center">
                                    <span className="text-[9px] font-medium text-[#525F6B] tracking-wide uppercase">
                                      No real data
                                    </span>
                                  </div>
                                </>
                              )}
                              {(historyEventsByMachine[machine.machine_id] ?? []).map((event, idx, arr) => {
                                // Clamp real events to nowOffsetSec — production cannot happen in the future.
                                // For the LAST event, extend end_sec to nowOffsetSec so the bar tracks
                                // the now-line in real time between 30-min snapshot refreshes.
                                const realCap = nowOffsetSec != null ? nowOffsetSec : timeScaleSec;
                                const isLast = idx === arr.length - 1;
                                const rawEnd = isLast
                                  ? Math.max(event.end_sec, nowOffsetSec ?? event.end_sec)
                                  : event.end_sec;
                                const startSec = Math.max(0, Math.min(realCap, event.start_sec));
                                const endSec = Math.max(0, Math.min(realCap, rawEnd));
                                if (endSec <= startSec) return null;
                                const left = toPercent(startSec);
                                const width = toPercent(endSec) - toPercent(startSec);
                                let color = BOSCH_COLORS.chart.idle;
                                if (event.activity === 'process') color = BOSCH_COLORS.chart.process;
                                if (event.activity === 'changeover') color = BOSCH_COLORS.chart.changeover;
                                if (event.activity === 'waiting') color = BOSCH_COLORS.chart.waiting;
                                if (event.activity === 'inactive') color = BOSCH_COLORS.chart.inactive;
                                if (event.activity === 'test') color = BOSCH_COLORS.chart.test;
                                return (
                                  <div
                                    key={`real-${idx}`}
                                    className="absolute h-full top-0 border-r border-white/20 flex items-center justify-center overflow-hidden opacity-80 hover:opacity-100 transition-opacity cursor-pointer"
                                    style={{
                                      left: `${left}%`,
                                      width: `${width}%`,
                                      backgroundColor: color,
                                    }}
                                    title={`REAL ${event.activity.toUpperCase()} — ${displayMachineId(machine.machine_id)} — ${event.part_number_id || ''} (${formatTime(event.end_sec - event.start_sec)})`}
                                    onClick={(e) => {
                                      e.stopPropagation();
                                      if (!event.part_number_id) return;
                                      onSelectPartNumber(event.part_number_id);
                                    }}
                                  >
                                    {width > 5 && (
                                      <span className="text-[9px] text-white font-medium truncate px-1">
                                        {event.activity === 'process'
                                          ? (event.part_number_id || '')
                                          : event.activity[0].toUpperCase()}
                                      </span>
                                    )}
                                  </div>
                                );
                              })}
                            </div>
                          )}
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
             );
          })}
        </div>
      </div>
    </div>
  );
};

export const Dashboard: React.FC<{ simulationStartedAt?: string | null }> = ({ simulationStartedAt = null }) => {
  const [data, setData] = useState<DashboardData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selectedStock, setSelectedStock] = useState<StockInfo | null>(null);
  const [selectedPartNumberId, setSelectedPartNumberId] = useState<string | null>(null);
  const [filteredPartNumberId, setFilteredPartNumberId] = useState<string | null>(null);
  const [showChangeoverModal, setShowChangeoverModal] = useState(false);
  const [showNowLine, setShowNowLine] = useState(true);
  const [showRealPlaceholder, setShowRealPlaceholder] = useState(true);
  const [historyTimeline, setHistoryTimeline] = useState<TimelineEvent[]>([]);

  useEffect(() => {
    let isMounted = true;

    const loadHistory = () => {
      fetchHistoryData()
        .then(payload => { if (isMounted) setHistoryTimeline(payload.timeline ?? []); })
        .catch(() => { /* history data is optional */ });
    };

    fetchDashboardData()
      .then(payload => { if (isMounted) setData(payload); })
      .catch(err => { if (isMounted) setError(err.message ?? 'Failed to load dashboard data'); });

    // Load history immediately, then refresh every 5 minutes so the "Real" row
    // stays in sync with the 30-min snapshot cycle on the backend.
    loadHistory();
    const historyInterval = setInterval(loadHistory, 5 * 60 * 1000);

    return () => {
      isMounted = false;
      clearInterval(historyInterval);
    };
  }, []);

  // Handle stock click
  const handleStockClick = (data: any) => {
    if (data && data.activePayload && data.activePayload[0]) {
      const payload = data.activePayload[0].payload as StockInfo;
      setSelectedStock(payload);
    }
  };

  const lineChangeoverStats = useMemo(() => {
    if (!data) {
      return [] as Array<{
        line_id: string;
        total_changeovers: number;
        changeover_loss_percentage: number;
        total_changeover_sec: number;
        total_waiting_sec: number;
        changeovers_per_machine: number;
      }>;
    }
    const totalTime = data.kpis.total_time_sec || 0;
    const machinesByLine = data.machines.reduce((acc, machine) => {
      if (!acc[machine.line_id]) acc[machine.line_id] = [];
      acc[machine.line_id].push(machine.machine_id);
      return acc;
    }, {} as Record<string, string[]>);

    const changeoverByLine = data.timeline.reduce((acc, event) => {
      if (event.activity !== 'changeover' && event.activity !== 'waiting') return acc;
      const line = event.line_id;
      if (!acc[line]) {
        acc[line] = { total_changeovers: 0, total_changeover_sec: 0, total_waiting_sec: 0 };
      }
      if (event.activity === 'changeover') {
        acc[line].total_changeovers += 1;
        acc[line].total_changeover_sec += Math.max(0, event.end_sec - event.start_sec);
      }
      if (event.activity === 'waiting') {
        acc[line].total_waiting_sec += Math.max(0, event.end_sec - event.start_sec);
      }
      return acc;
    }, {} as Record<string, { total_changeovers: number; total_changeover_sec: number; total_waiting_sec: number }>);

    return Object.keys(machinesByLine)
      .sort()
      .map((line_id) => {
        const machinesCount = machinesByLine[line_id]?.length || 0;
        const stats = changeoverByLine[line_id] || { total_changeovers: 0, total_changeover_sec: 0, total_waiting_sec: 0 };
        const avgLoss =
          totalTime > 0 && machinesCount > 0
            ? (stats.total_changeover_sec / (totalTime * machinesCount)) * 100
            : 0;
        const coPerMachine = machinesCount > 0 ? stats.total_changeovers / machinesCount : 0;
        return {
          line_id,
          total_changeovers: stats.total_changeovers,
          changeover_loss_percentage: Number(avgLoss.toFixed(2)),
          total_changeover_sec: stats.total_changeover_sec,
          total_waiting_sec: stats.total_waiting_sec,
          changeovers_per_machine: Number(coPerMachine.toFixed(2)),
        };
      });
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
        <p className="text-sm text-[#525F6B]">Loading dashboard data...</p>
      </Card>
    );
  }

  const { kpis, stock_final, orders } = data;
  const activePartNumberId = filteredPartNumberId || selectedPartNumberId;
  const scrollTargetOrderId = activePartNumberId
    ? (orders.find((order) => order.part_number_id === activePartNumberId)?.order_id ?? null)
    : null;
  const stockFinalDisplay = stock_final.map((stock) => ({
    ...stock,
    line_id_display: displayLineId(stock.line_id),
  }));
  const filteredStockFinal = activePartNumberId
    ? stockFinalDisplay.filter((stock) =>
        Array.isArray(stock.tray_details)
          ? stock.tray_details.some((tray) => tray.part_number_id === activePartNumberId)
          : false
      )
    : stockFinalDisplay;

  return (
    <div
      className="space-y-6 animate-in fade-in duration-500"
      onClick={() => setSelectedPartNumberId(null)}
    >
      {/* 2.1 KPI Summary */}
      <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-5 gap-4">
        <KPICard label="Total Time" value={formatTime(kpis.total_time_sec)} icon={Clock} />
        <KPICard
          label="Processed Parts"
          value={kpis.total_parts}
          subtext={`Parts/hr ${kpis.parts_per_hour} · Trays ${kpis.total_trays}`}
          icon={Layers}
        />
        <KPICard 
          label="Global OEE" 
          value={`${(kpis.global_oee * 100).toFixed(1)}%`} 
          subtext="OEE Proxy"
          icon={Activity} 
          highlightColor={getTrafficColor(kpis.global_oee, KPI_THRESHOLDS.oee, { higherIsBetter: true })}
        />
        <KPICard 
          label="Changeover Loss" 
          value={
            <div className="space-y-3 text-xs text-[#525F6B]">
              <div className="flex items-center justify-between">
                <span className="font-normal">CO total</span>
                <span className="font-bold text-[#000000] ml-3">{kpis.total_changeovers}</span>
              </div>
              <div className="flex items-center justify-between">
                <span className="font-normal">Avg loss</span>
                <span
                  className="font-bold ml-3"
                  style={{
                    color: getTrafficColor(
                      kpis.changeover_loss_percentage,
                      KPI_THRESHOLDS.changeover_loss_percentage,
                      { higherIsBetter: false }
                    ),
                  }}
                >
                  {kpis.changeover_loss_percentage}%
                </span>
              </div>
              <div className="flex items-center justify-between">
                <span className="font-normal">Total time</span>
                <span className="font-bold text-[#000000] ml-3">{formatTime(kpis.total_changeover_sec)}</span>
              </div>
              <div className="flex items-center justify-between">
                <span className="font-normal">Waiting time</span>
                <span className="font-bold text-[#000000] ml-3">{formatTime(kpis.total_waiting_sec)}</span>
              </div>
              <div className="flex items-center justify-between">
                <span className="font-normal">CO/máq</span>
                <span className="font-bold text-[#000000] ml-3">{kpis.changeovers_per_machine}</span>
              </div>
            </div>
          }
          icon={TrendingDown} 
          highlightColor={getTrafficColor(
            kpis.changeover_loss_percentage,
            KPI_THRESHOLDS.changeover_loss_percentage,
            { higherIsBetter: false }
          )}
          onClick={() => setShowChangeoverModal(true)}
        />
        <div className="hidden lg:block bg-[#EFF1F2] p-4 rounded text-xs text-[#525F6B] flex flex-col justify-center">
            <div className="flex justify-between mb-1"><span>Machines</span> <strong>{kpis.machines_count}</strong></div>
            <div className="flex justify-between mb-1"><span>Lines</span> <strong>{kpis.lines_count}</strong></div>
            <div className="flex justify-between"><span>Test Time</span> <strong>{formatTime(kpis.total_test_sec)}</strong></div>
        </div>
      </div>

      {/* 2.2 Timeline / Gantt */}
      <Card
        title="Machine Activity Timeline"
        className="min-h-[300px]"
        action={
          <div className="flex items-center gap-4 text-xs flex-wrap">
            <div className="flex items-center gap-1.5 border-r border-[#BFC0C2] pr-4 mr-1">
              <div className="flex flex-col gap-[3px]">
                <div className="w-6 h-2 rounded-sm bg-[#007BC0]" />
                <div className="w-6 h-2 rounded-sm border border-dashed border-[#525F6B] bg-[#EFF1F2]" />
              </div>
              <div className="flex flex-col leading-tight">
                <span className="text-[10px] text-[#525F6B]">Plan</span>
                <span className="text-[10px] text-[#525F6B]">Real</span>
              </div>
            </div>
            <div className="flex items-center gap-1.5"><div className="w-3 h-3 rounded-sm" style={{ background: BOSCH_COLORS.chart.process }}></div> Process</div>
            <div className="flex items-center gap-1.5"><div className="w-3 h-3 rounded-sm" style={{ background: BOSCH_COLORS.chart.changeover }}></div> Changeover</div>
            <div className="flex items-center gap-1.5"><div className="w-3 h-3 rounded-sm" style={{ background: BOSCH_COLORS.chart.waiting }}></div> Waiting</div>
            <div className="flex items-center gap-1.5"><div className="w-3 h-3 rounded-sm" style={{ background: BOSCH_COLORS.chart.inactive }}></div> Inactive</div>
            <div className="flex items-center gap-1.5"><div className="w-3 h-3 rounded-sm" style={{ background: BOSCH_COLORS.chart.test }}></div> Test</div>
            <div className="flex items-center gap-1.5"><div className="w-3 h-3 rounded-sm" style={{ background: BOSCH_COLORS.chart.idle }}></div> Idle</div>
            <button
              className="ml-2 text-xs text-[#007BC0] hover:underline"
              onClick={(e) => {
                e.stopPropagation();
                setShowRealPlaceholder((prev) => !prev);
              }}
            >
              {showRealPlaceholder ? 'Hide real line' : 'Show real line'}
            </button>
            <button
              className="ml-2 text-xs text-[#007BC0] hover:underline"
              onClick={(e) => {
                e.stopPropagation();
                setShowNowLine((prev) => !prev);
              }}
            >
              {showNowLine ? 'Hide now line' : 'Show now line'}
            </button>
          </div>
        }
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex justify-end mb-2">
          {selectedPartNumberId && (
            <button
              className="text-xs text-[#007BC0] hover:underline"
              onClick={(e) => {
                e.stopPropagation();
                setSelectedPartNumberId(null);
              }}
            >
              Clear selection: {selectedPartNumberId}
            </button>
          )}
        </div>
        <TimelineChart
          data={data}
          selectedPartNumberId={activePartNumberId}
          onSelectPartNumber={(partNumberId) =>
            setSelectedPartNumberId((prev) => (prev === partNumberId ? null : partNumberId))
          }
          filterPartNumberId={activePartNumberId}
          showNowLine={showNowLine}
          showRealPlaceholder={showRealPlaceholder}
          lineChangeoverStats={lineChangeoverStats}
          simulationStartedAt={simulationStartedAt}
          historyTimeline={historyTimeline}
        />
      </Card>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* 2.3 Stock State */}
        <Card title="End-of-Shift Stock Levels" subtitle="Click bars for details" className="lg:col-span-1">
          <div className="h-[300px] w-full">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart 
                data={filteredStockFinal} 
                layout="vertical" 
                margin={{ top: 5, right: 30, left: 20, bottom: 5 }}
                onClick={handleStockClick}
                style={{ cursor: 'pointer' }}
              >
                <CartesianGrid strokeDasharray="3 3" horizontal={false} />
                <XAxis type="number" hide />
                <YAxis dataKey="line_id_display" type="category" width={90} tick={{ fontSize: 12 }} />
                <Tooltip cursor={{ fill: 'transparent' }} content={<StockTooltip />} />
                <Bar dataKey="trays" fill={BOSCH_COLORS.blue} barSize={20} radius={[0, 4, 4, 0]} name="Trays in Stock" />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </Card>

        {/* 2.4 Orders Produced Table */}
          <Card title="Production Report" className="lg:col-span-2">
             <DataTable 
             data={orders}
               rowKey={(item) => item.order_id}
               scrollToKey={scrollTargetOrderId}
                maxHeightClass="max-h-[360px]"
              columns={[
            { key: 'order_id', label: 'Order ID', sortable: true, render: val => <span className="font-medium text-[#007BC0]">{val}</span> },
               { key: 'part_number_id', label: 'Part Number', sortable: true, filterable: true },
               { key: 'product_type', label: 'Type', sortable: true, filterable: true, render: val => <span className="px-2 py-0.5 bg-[#EFF1F2] rounded text-xs border border-[#BFC0C2]">{val}</span> },
               { key: 'quantity', label: 'Target Qty', sortable: true },
               { key: 'produced_pieces', label: 'Produced', sortable: true, render: (val, item) => (
                  <div className="flex flex-col">
                    <span>{val}</span>
                    <span className="text-[10px] text-[#525F6B]">{Math.round((val / item.quantity) * 100)}%</span>
                  </div>
               )},
               { key: 'completion_pct', label: 'Status', render: (val, item) => {
                  const percent = Math.min(100, Math.round((item.produced_pieces / item.quantity) * 100));
                  if (percent === 100) return <CheckCircle size={16} className="text-[#00884A]" />;
                  if (percent === 0) return <Hourglass size={16} className="text-[#BFC0C2]" />;
                  return (
                    <div className="w-16 h-1.5 bg-[#EFF1F2] rounded-full overflow-hidden">
                       <div className="h-full bg-[#007BC0]" style={{ width: `${percent}%` }}></div>
                    </div>
                  );
               }}
             ]}
             onRowClick={(item, event) => {
               event.stopPropagation();
               setSelectedPartNumberId((prev) => (prev === item.part_number_id ? null : item.part_number_id));
             }}
             getRowClassName={(item) =>
               activePartNumberId && item.part_number_id === activePartNumberId
                 ? "bg-[#E8F4FB]"
                 : ""
             }
             onFiltersChange={(filters) => {
               const partFilter = filters["part_number_id"] || null;
               setFilteredPartNumberId(partFilter);
               if (partFilter) {
                 setSelectedPartNumberId(partFilter);
               }
             }}
          />
        </Card>
      </div>

      <AnimatePresence>
        <ChangeoverDetailModal
          open={showChangeoverModal}
          onClose={() => setShowChangeoverModal(false)}
          stats={lineChangeoverStats}
        />
        {selectedStock && (
          <StockDetailModal
            stock={selectedStock}
            onClose={() => setSelectedStock(null)}
          />
        )}
      </AnimatePresence>
    </div>
  );
};
