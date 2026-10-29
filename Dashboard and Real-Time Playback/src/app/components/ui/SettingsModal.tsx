import React, { useState, useEffect } from 'react';
import { createPortal } from 'react-dom';
import { motion, AnimatePresence } from 'motion/react';
import { X, Settings, RotateCcw } from 'lucide-react';
import clsx from 'clsx';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface LiveSettings {
  oeeHours: number;               // Window for OEE calculation (hours)
  offHours: number;               // Inactivity threshold for OFF status (hours)
  marketTimeoutHours: number;     // Hours before a TypeNumber with no orders leaves the final market
  manualOff: string[];            // Machine IDs manually forced to OFF
  defaultTestMin: number;         // Default test (release) time in minutes for pairs not in CSV
}

export const DEFAULT_SETTINGS: LiveSettings = {
  oeeHours: 168,
  offHours: 168,
  marketTimeoutHours: 24,
  manualOff: [],
  defaultTestMin: 60,
};

const STORAGE_KEY = 'live_settings_v1';

export function loadSettings(): LiveSettings {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return { ...DEFAULT_SETTINGS };
    const parsed = JSON.parse(raw);
    return {
      oeeHours: typeof parsed.oeeHours === 'number' ? parsed.oeeHours : DEFAULT_SETTINGS.oeeHours,
      offHours: typeof parsed.offHours === 'number' ? parsed.offHours : DEFAULT_SETTINGS.offHours,
      marketTimeoutHours: typeof parsed.marketTimeoutHours === 'number' ? parsed.marketTimeoutHours : DEFAULT_SETTINGS.marketTimeoutHours,
      manualOff: Array.isArray(parsed.manualOff) ? parsed.manualOff : [],
      defaultTestMin: typeof parsed.defaultTestMin === 'number' ? parsed.defaultTestMin : DEFAULT_SETTINGS.defaultTestMin,
    };
  } catch {
    return { ...DEFAULT_SETTINGS };
  }
}

export function saveSettings(s: LiveSettings): void {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(s));
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

const NumberField = ({
  label,
  hint,
  value,
  min = 1,
  max = 8760,
  onChange,
}: {
  label: string;
  hint: string;
  value: number;
  min?: number;
  max?: number;
  onChange: (v: number) => void;
}) => (
  <div className="flex flex-col gap-1">
    <label className="text-xs font-bold text-[#333]">{label}</label>
    <p className="text-[10px] text-[#525F6B] leading-snug">{hint}</p>
    <div className="flex items-center gap-2 mt-1">
      <input
        type="number"
        min={min}
        max={max}
        value={value}
        onChange={(e) => {
          const v = parseInt(e.target.value, 10);
          if (!isNaN(v) && v >= min && v <= max) onChange(v);
        }}
        className="w-24 border border-[#BFC0C2] rounded px-2 py-1 text-sm font-mono text-[#007BC0] focus:outline-none focus:border-[#007BC0]"
      />
      <span className="text-xs text-[#525F6B]">horas</span>
    </div>
  </div>
);

const MachineToggle = ({
  machineId,
  isOff,
  onChange,
}: {
  machineId: string;
  isOff: boolean;
  onChange: (id: string, off: boolean) => void;
}) => {
  const label = machineId.startsWith('AGIE ') ? `Agie ${machineId.replace('AGIE ', '').padStart(2, '0')}` : machineId;
  return (
    <div className="flex items-center justify-between gap-2 px-2 py-1 rounded hover:bg-[#EFF1F2]">
      <span className={clsx('text-[11px] font-mono', isOff ? 'text-[#FF3732] font-bold' : 'text-[#525F6B]')}>
        {label}
      </span>
      {/* Toggle switch */}
      <button
        onClick={() => onChange(machineId, !isOff)}
        className={clsx(
          'relative inline-flex h-5 w-9 items-center rounded-full transition-colors focus:outline-none shrink-0',
          isOff ? 'bg-[#FF3732]' : 'bg-[#BFC0C2]'
        )}
        title={isOff ? 'Forçar OFF: ativo' : 'Forçar OFF: inativo'}
      >
        <span
          className={clsx(
            'inline-block h-3.5 w-3.5 transform rounded-full bg-white shadow transition-transform',
            isOff ? 'translate-x-4' : 'translate-x-1'
          )}
        />
      </button>
    </div>
  );
};

const MachineOverrideField = ({
  machineId,
  value,
  onChange,
}: {
  machineId: string;
  value: number;
  onChange: (id: string, minutes: number) => void;
}) => {
  const label = machineId.startsWith('AGIE ') ? `Agie ${machineId.replace('AGIE ', '').padStart(2, '0')}` : machineId;
  return (
    <div className="flex items-center justify-between gap-2 px-2 py-1 rounded hover:bg-[#EFF1F2]">
      <span className={clsx('text-[11px] font-mono shrink-0', value > 0 ? 'text-[#007BC0] font-bold' : 'text-[#525F6B]')}>
        {label}
      </span>
      <div className="flex items-center gap-1">
        <input
          type="number"
          min={0}
          max={480}
          value={value}
          onChange={(e) => {
            const v = parseInt(e.target.value, 10);
            onChange(machineId, isNaN(v) || v < 0 ? 0 : v);
          }}
          className="w-16 border border-[#BFC0C2] rounded px-1.5 py-0.5 text-xs font-mono text-[#007BC0] focus:outline-none focus:border-[#007BC0] text-right"
          placeholder="0"
        />
        <span className="text-[10px] text-[#525F6B] shrink-0">min</span>
      </div>
    </div>
  );
};

// ---------------------------------------------------------------------------
// Main modal
// ---------------------------------------------------------------------------

interface SettingsModalProps {
  settings: LiveSettings;
  allMachineIds: string[];    // full list of 50 machine IDs from snapshot
  onSave: (s: LiveSettings) => void;
  onClose: () => void;
}

export const SettingsModal: React.FC<SettingsModalProps> = ({
  settings,
  allMachineIds,
  onSave,
  onClose,
}) => {
  const [draft, setDraft] = useState<LiveSettings>({
    ...settings,
    manualOff: [...settings.manualOff],
  });
  const [search, setSearch] = useState('');

  // Sort machines numerically
  const sortedMachines = [...allMachineIds].sort((a, b) => {
    const na = parseInt(a.replace(/\D/g, ''), 10) || 0;
    const nb = parseInt(b.replace(/\D/g, ''), 10) || 0;
    return na - nb;
  });

  const filtered = sortedMachines.filter((id) =>
    id.toLowerCase().includes(search.toLowerCase())
  );

  const toggleMachine = (id: string, off: boolean) => {
    setDraft((prev) => ({
      ...prev,
      manualOff: off
        ? [...prev.manualOff, id]
        : prev.manualOff.filter((m) => m !== id),
    }));
  };

  const handleSave = () => {
    onSave(draft);
    onClose();
  };

  const handleReset = () => {
    setDraft({ ...DEFAULT_SETTINGS });
  };

  const manualOffCount = draft.manualOff.length;

  return createPortal(
    <AnimatePresence>
      <motion.div
        className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm"
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        exit={{ opacity: 0 }}
        onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
      >
        <motion.div
          className="bg-white rounded-sm shadow-xl w-[520px] max-h-[90vh] flex flex-col"
          initial={{ scale: 0.95, opacity: 0 }}
          animate={{ scale: 1, opacity: 1 }}
          exit={{ scale: 0.95, opacity: 0 }}
          transition={{ duration: 0.15 }}
        >
          {/* Header */}
          <div className="flex items-center justify-between px-5 py-3 border-b border-[#EFF1F2]">
            <div className="flex items-center gap-2">
              <Settings size={16} className="text-[#007BC0]" />
              <span className="font-bold text-sm text-[#333]">Configurações do Live View</span>
            </div>
            <button onClick={onClose} className="p-1 rounded hover:bg-[#EFF1F2] text-[#525F6B]">
              <X size={16} />
            </button>
          </div>

          {/* Body */}
          <div className="flex-grow overflow-y-auto px-5 py-4 space-y-6">

            {/* ── Section 1: Time thresholds ── */}
            <div>
              <div className="text-[11px] uppercase font-bold text-[#525F6B] tracking-wider mb-3 border-b pb-1">
                Limiares de Tempo
              </div>
              <div className="grid grid-cols-2 gap-4">
                <NumberField
                  label="Janela OEE"
                  hint="Execuções completas nas últimas N horas contam para o cálculo do OEE."
                  value={draft.oeeHours}
                  onChange={(v) => setDraft((p) => ({ ...p, oeeHours: v }))}
                />
                <NumberField
                  label="Limiar de OFF"
                  hint="Máquinas sem execução completa nas últimas N horas aparecem como OFF."
                  value={draft.offHours}
                  onChange={(v) => setDraft((p) => ({ ...p, offHours: v }))}
                />
              </div>
              <div className="mt-4">
                <NumberField
                  label="Tempo de permanência mercado final"
                  hint="TypeNumbers sem ordem aberta são ocultados do mercado final após N horas desde a saída da Agie."
                  value={draft.marketTimeoutHours}
                  min={1}
                  max={8760}
                  onChange={(v) => setDraft((p) => ({ ...p, marketTimeoutHours: v }))}
                />
              </div>
            </div>

            {/* ── Section 2: Manual OFF overrides ── */}
            <div>
              <div className="flex items-center justify-between border-b pb-1 mb-2">
                <div className="text-[11px] uppercase font-bold text-[#525F6B] tracking-wider">
                  Forçar OFF Manual
                </div>
                {manualOffCount > 0 && (
                  <span className="text-[10px] font-bold text-[#FF3732] bg-[#FFE8E8] px-1.5 py-0.5 rounded-full">
                    {manualOffCount} máquina{manualOffCount > 1 ? 's' : ''} forçada{manualOffCount > 1 ? 's' : ''}
                  </span>
                )}
              </div>
              <p className="text-[10px] text-[#525F6B] mb-2">
                Activa o toggle para forçar uma máquina a aparecer como OFF independentemente dos dados do banco.
              </p>
              {/* Search */}
              <input
                type="text"
                placeholder="Filtrar máquinas..."
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                className="w-full border border-[#BFC0C2] rounded px-2 py-1 text-xs mb-2 focus:outline-none focus:border-[#007BC0]"
              />
              {/* Machine list */}
              <div className="grid grid-cols-2 gap-x-2 max-h-[280px] overflow-y-auto border border-[#EFF1F2] rounded p-1">
                {filtered.length > 0 ? (
                  filtered.map((id) => (
                    <MachineToggle
                      key={id}
                      machineId={id}
                      isOff={draft.manualOff.includes(id)}
                      onChange={toggleMachine}
                    />
                  ))
                ) : (
                  <div className="col-span-2 text-center text-[10px] text-[#BFC0C2] py-4">
                    Nenhuma máquina encontrada
                  </div>
                )}
              </div>
            </div>

            {/* ── Section 3: Default test time ── */}
            <div>
              <div className="text-[11px] uppercase font-bold text-[#525F6B] tracking-wider mb-3 border-b pb-1">
                Tempo de Teste Padrão
              </div>
              <div className="flex flex-col gap-1">
                <label className="text-xs font-bold text-[#333]">Duração padrão do Teste</label>
                <p className="text-[10px] text-[#525F6B] leading-snug">
                  Tempo de teste (release time) em minutos aplicado a pares de TypeNumber que não existem no CSV.
                  O tempo de setup padrão (para pares sem CSV) é fixo em <strong>20 min</strong>.
                </p>
                <div className="flex items-center gap-2 mt-1">
                  <input
                    type="number"
                    min={0}
                    max={480}
                    value={draft.defaultTestMin}
                    onChange={(e) => {
                      const v = parseInt(e.target.value, 10);
                      if (!isNaN(v) && v >= 0 && v <= 480) setDraft((p) => ({ ...p, defaultTestMin: v }));
                    }}
                    className="w-24 border border-[#BFC0C2] rounded px-2 py-1 text-sm font-mono text-[#007BC0] focus:outline-none focus:border-[#007BC0]"
                  />
                  <span className="text-xs text-[#525F6B]">minutos</span>
                </div>
              </div>
            </div>
          </div>

          {/* Footer */}
          <div className="flex items-center justify-between px-5 py-3 border-t border-[#EFF1F2] bg-[#FAFAFA]">
            <button
              onClick={handleReset}
              className="flex items-center gap-1.5 text-xs text-[#525F6B] hover:text-[#FF3732] transition-colors"
            >
              <RotateCcw size={13} />
              Restaurar padrões
            </button>
            <div className="flex gap-2">
              <button
                onClick={onClose}
                className="px-4 py-1.5 text-xs border border-[#BFC0C2] rounded text-[#525F6B] hover:bg-[#EFF1F2] transition-colors"
              >
                Cancelar
              </button>
              <button
                onClick={handleSave}
                className="px-4 py-1.5 text-xs bg-[#007BC0] text-white rounded hover:bg-[#00629A] transition-colors font-bold"
              >
                Aplicar
              </button>
            </div>
          </div>
        </motion.div>
      </motion.div>
    </AnimatePresence>,
    document.body
  );
};
