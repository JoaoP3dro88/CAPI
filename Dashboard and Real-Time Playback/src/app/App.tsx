import React, { useCallback, useEffect, useState } from 'react';
import { Dashboard } from './components/Dashboard';
import { RealtimePlayback } from './components/RealtimePlayback';
import { LiveVisualization } from './components/LiveVisualization';
import { LayoutDashboard, PlayCircle, Radio } from 'lucide-react';
import clsx from 'clsx';
import { ErrorBoundary } from './components/ErrorBoundary';
import kapiIcon from '../resources/Kapi_Icon.png';
import { Card } from './components/ui/Card';
import { fetchSimulationJob, fetchSimulationStatus, runSimulation } from './data';
import { loadSettings } from './components/ui/SettingsModal';

const SESSION_STORAGE_KEYS = {
  simulationJobId: 'simulationJobId',
  simulationStartedAt: 'simulationStartedAt',
  simulationReady: 'simulationReady',
} as const;

function readSessionStorage(key: string): string | null {
  if (typeof window === 'undefined') {
    return null;
  }
  return window.sessionStorage.getItem(key);
}

function writeSessionStorage(key: string, value: string | null) {
  if (typeof window === 'undefined') {
    return;
  }
  if (value == null) {
    window.sessionStorage.removeItem(key);
    return;
  }
  window.sessionStorage.setItem(key, value);
}

export default function App() {
  const [currentView, setCurrentView] = useState<'dashboard' | 'realtime' | 'live'>('live');
  const [simulationReady, setSimulationReady] = useState(() => readSessionStorage(SESSION_STORAGE_KEYS.simulationReady) === 'true');
  const [simulationRunning, setSimulationRunning] = useState(false);
  const [simulationJobId, setSimulationJobId] = useState<string | null>(() => readSessionStorage(SESSION_STORAGE_KEYS.simulationJobId));
  const [simulationStartedAt, setSimulationStartedAt] = useState<string | null>(() => readSessionStorage(SESSION_STORAGE_KEYS.simulationStartedAt));
  const [simulationMessage, setSimulationMessage] = useState<string | null>(null);
  const [simulationError, setSimulationError] = useState<string | null>(null);
  const [simulationProgress, setSimulationProgress] = useState<number | null>(null);
  /** ISO string of when the simulation was run — used to position the playback slider at "now". */
  const [simRefDt, setSimRefDt] = useState<string | null>(null);

  useEffect(() => {
    writeSessionStorage(SESSION_STORAGE_KEYS.simulationJobId, simulationJobId);
  }, [simulationJobId]);

  useEffect(() => {
    writeSessionStorage(SESSION_STORAGE_KEYS.simulationStartedAt, simulationStartedAt);
  }, [simulationStartedAt]);

  useEffect(() => {
    writeSessionStorage(SESSION_STORAGE_KEYS.simulationReady, simulationReady ? 'true' : null);
  }, [simulationReady]);

  const refreshSimulationStatus = useCallback(async (jobIdOverride?: string | null) => {
    const trackedJobId = jobIdOverride ?? simulationJobId;
    try {
      if (trackedJobId) {
        const job = await fetchSimulationJob(trackedJobId);
        setSimulationJobId(job.job_id);

        if (job.status === 'queued' || job.status === 'running') {
          setSimulationRunning(true);
          setSimulationReady(false);
          setSimulationMessage(job.message ?? 'Simulation running');
          setSimulationProgress(job.progress ?? null);
          setSimulationError(null);
          return;
        }

        if (job.status === 'succeeded' && job.has_results) {
          // Fetch status to pick up sim_ref_dt written after job completion
          try {
            const latestStatus = await fetchSimulationStatus();
            if (latestStatus.sim_ref_dt) setSimRefDt(latestStatus.sim_ref_dt);
          } catch { /* non-fatal */ }
          setSimulationRunning(false);
          setSimulationReady(true);
          setSimulationMessage(job.message ?? 'Simulation results are ready for this session.');
          setSimulationProgress(null);
          setSimulationError(null);
          return;
        }

        if (job.status === 'failed') {
          setSimulationRunning(false);
          setSimulationReady(false);
          setSimulationMessage(job.message ?? 'Simulation failed.');
          setSimulationProgress(null);
          setSimulationError(job.error ?? 'Simulation failed.');
          return;
        }
      }

      const status = await fetchSimulationStatus();
      const active = status.active_job;

      if (active && (active.status === 'queued' || active.status === 'running')) {
        setSimulationRunning(true);
        setSimulationReady(false);
        setSimulationProgress(active.progress ?? null);
        setSimulationMessage(active.message ?? 'Simulation running. Run it from this session to unlock the planning views.');
        setSimulationError(null);
      } else if (status.has_results) {
        // Results available on disk — unlock for everyone, including users who
        // didn't trigger the simulation themselves.
        setSimulationRunning(false);
        setSimulationReady(true);
        setSimulationProgress(null);
        if (status.sim_ref_dt) setSimRefDt(status.sim_ref_dt);
        setSimulationMessage('Simulation results available. Run again to refresh.');
        setSimulationError(null);
      } else {
        setSimulationRunning(false);
        setSimulationReady(false);
        setSimulationProgress(null);
        setSimulationMessage('Run simulation to unlock Dashboard and Real-time Playback.');
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setSimulationError(message);
      setSimulationRunning(false);
      setSimulationReady(false);
      setSimulationProgress(null);
      setSimulationMessage('Simulation API unavailable. Start backend API and try again.');
    }
  }, [simulationJobId]);

  useEffect(() => {
    void refreshSimulationStatus();
  }, [refreshSimulationStatus]);

  // Single unified poll — fast when running (2s), slow at idle (10s).
  // Uses a ref so the interval is never re-created mid-run when the callback
  // identity changes (e.g. after simulationJobId updates).
  const refreshRef = React.useRef(refreshSimulationStatus);
  useEffect(() => { refreshRef.current = refreshSimulationStatus; }, [refreshSimulationStatus]);

  const simulationRunningRef = React.useRef(simulationRunning);
  useEffect(() => { simulationRunningRef.current = simulationRunning; }, [simulationRunning]);

  useEffect(() => {
    let timer: number;
    function schedule() {
      const delay = simulationRunningRef.current ? 2000 : 10000;
      timer = window.setTimeout(() => {
        void refreshRef.current();
        schedule(); // reschedule after each call so rate adapts immediately
      }, delay);
    }
    schedule();
    return () => window.clearTimeout(timer);
  }, []); // runs once — refs keep it up to date

  const handleRunSimulation = async () => {
    setSimulationError(null);
    setSimulationReady(false);
    setSimulationProgress(null);
    setSimulationMessage('Starting simulation...');
    try {
      const startedAt = new Date().toISOString();
      // Read settings at click-time so manualOff is always the latest saved value.
      const currentSettings = loadSettings();
      const response = await runSimulation({
        data_source: 'db',
        max_sim_hours: 24,
        use_live_initial_state: true,
        excluded_machines: currentSettings.manualOff,
      });
      setSimulationJobId(response.job_id);
      setSimulationStartedAt(startedAt);
      setSimulationRunning(true);
      setSimulationMessage('Simulation queued. Preparing 24-hour plan...');
      await refreshSimulationStatus(response.job_id);
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setSimulationError(message);
      setSimulationRunning(false);
      setSimulationProgress(null);
      setSimulationJobId(null);
      setSimulationStartedAt(null);
      setSimulationMessage('Could not start simulation.');
    }
  };

  const dashboardDisabled = !simulationReady;
  const realtimeDisabled = !simulationReady;

  useEffect(() => {
    if ((currentView === 'dashboard' || currentView === 'realtime') && !simulationReady) {
      setCurrentView('live');
    }
  }, [currentView, simulationReady]);

  const LockedPanel = ({ viewLabel }: { viewLabel: string }) => (
    <Card title={`${viewLabel} locked`} className="max-w-2xl mx-auto mt-8">
      <p className="text-sm text-[#525F6B]">
        Dashboard and Real-time Playback are enabled after a 24-hour simulation run.
      </p>
      <div className="mt-4 flex flex-col gap-2">
        <button
          onClick={handleRunSimulation}
          disabled={simulationRunning}
          className={clsx(
            'relative px-5 py-2.5 text-sm font-semibold rounded transition-colors w-fit overflow-hidden min-w-[240px] text-center',
            simulationRunning
              ? 'bg-[#BFC0C2] text-white cursor-not-allowed'
              : 'bg-[#007BC0] text-white hover:bg-[#00629A]'
          )}
        >
          {simulationRunning && (
            <span
              className="absolute top-0 left-0 h-full bg-[#007BC0] transition-all duration-700 ease-out"
              style={{ width: `${Math.min(100, Math.max(0, simulationProgress ?? 0))}%` }}
            />
          )}
          <span className="relative">
            {simulationRunning
              ? simulationMessage || `Simulating… ${Math.round(simulationProgress ?? 0)}%`
              : 'Run 24h Simulation'}
          </span>
        </button>
        {simulationMessage && <p className="text-xs text-[#525F6B]">{simulationMessage}</p>}
        {simulationJobId && <p className="text-xs text-[#525F6B]">Job ID: {simulationJobId}</p>}
        {simulationError && <p className="text-xs text-[#FF3732]">{simulationError}</p>}
      </div>
    </Card>
  );

  return (
    <div className="min-h-screen bg-[#EFF1F2] font-sans text-[#000000] flex flex-col">
      {/* Header */}
      <header className="bg-white border-b border-[#BFC0C2] h-16 flex items-center px-8 shadow-sm fixed top-0 left-0 right-0 z-20">
        <div className="flex items-center gap-4">
          <img
            src={kapiIcon}
            alt="Kapi Icon"
            className="w-8 h-8 object-contain"
          />
          <h1 className="text-xl font-bold tracking-tight text-[#000000]">Production Simulation</h1>
        </div>
        
        <div className="ml-auto flex items-center gap-3">
            <button
              onClick={handleRunSimulation}
              disabled={simulationRunning}
              className={clsx(
                'relative px-4 py-1.5 text-xs font-semibold rounded transition-colors overflow-hidden min-w-[180px] text-center',
                simulationRunning
                  ? 'bg-[#BFC0C2] text-white cursor-not-allowed'
                  : 'bg-[#007BC0] text-white hover:bg-[#00629A]'
              )}
              title="Generate 24-hour production sequence"
            >
              {simulationRunning && (
                <span
                  className="absolute top-0 left-0 h-full bg-[#007BC0] transition-all duration-700 ease-out"
                  style={{ width: `${Math.min(100, Math.max(0, simulationProgress ?? 0))}%` }}
                />
              )}
              <span className="relative">
                {simulationRunning
                  ? simulationMessage || `Simulating… ${Math.round(simulationProgress ?? 0)}%`
                  : 'Run 24h Simulation'}
              </span>
            </button>
            <button 
              onClick={() => setCurrentView('dashboard')}
              disabled={dashboardDisabled}
              className={clsx(
                "flex items-center gap-2 px-4 py-2 text-sm font-medium transition-colors border-b-2",
                currentView === 'dashboard' 
                  ? "border-[#007BC0] text-[#007BC0]" 
                  : "border-transparent text-[#525F6B] hover:text-[#000000]",
                dashboardDisabled && "opacity-50 cursor-not-allowed hover:text-[#525F6B]"
              )}
            >
              <LayoutDashboard size={18} />
              Dashboard
            </button>
            <button 
              onClick={() => setCurrentView('realtime')}
              disabled={realtimeDisabled}
              className={clsx(
                "flex items-center gap-2 px-4 py-2 text-sm font-medium transition-colors border-b-2",
                currentView === 'realtime' 
                  ? "border-[#007BC0] text-[#007BC0]" 
                  : "border-transparent text-[#525F6B] hover:text-[#000000]",
                realtimeDisabled && "opacity-50 cursor-not-allowed hover:text-[#525F6B]"
              )}
            >
              <PlayCircle size={18} />
              Real-time Playback
            </button>
            <button 
              onClick={() => setCurrentView('live')}
              className={clsx(
                "flex items-center gap-2 px-4 py-2 text-sm font-medium transition-colors border-b-2",
                currentView === 'live' 
                  ? "border-[#00884A] text-[#00884A]" 
                  : "border-transparent text-[#525F6B] hover:text-[#000000]"
              )}
            >
              <Radio size={18} />
              Live View
            </button>
        </div>
      </header>

      {/* Main Content */}
      <main className="flex-1 p-8 max-w-[1920px] mx-auto pt-24 w-full">
        {!simulationReady && (
          <div className="mb-4 text-xs text-[#525F6B]">
            {simulationError ? (
              <span className="text-[#FF3732]">{simulationError}</span>
            ) : (
              <span>{simulationMessage ?? 'Run simulation to unlock Dashboard and Real-time Playback.'}</span>
            )}
          </div>
        )}
        <ErrorBoundary>
          {currentView === 'dashboard'
            ? (simulationReady ? <Dashboard simulationStartedAt={simulationStartedAt ?? simRefDt} /> : <LockedPanel viewLabel="Dashboard" />)
            : currentView === 'realtime'
              ? (simulationReady ? <RealtimePlayback simRefDt={simRefDt} /> : <LockedPanel viewLabel="Real-time Playback" />)
              : <LiveVisualization />}
        </ErrorBoundary>
      </main>
      
      {/* Footer / Copyright */}
      <footer className="mt-auto py-6 text-center text-xs text-[#525F6B] border-t border-[#BFC0C2]">
        &copy; 2026 Bosch Manufacturing Simulation. All rights reserved.
      </footer>
    </div>
  );
}
