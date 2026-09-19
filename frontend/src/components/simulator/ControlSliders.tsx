'use client';

/**
 * Panneau de contrôle du banc : vitesse de rotation, charge transportée et débit de colis.
 * Les valeurs sont appliquées au backend au relâchement du curseur (évite de spammer l'API).
 */
import { Gauge, Pause, Play, RotateCw } from 'lucide-react';
import * as React from 'react';
import { Button, Panel, PanelHeader } from '@/components/ui/primitives';
import { fmt } from '@/lib/format';

export interface BenchControls {
  speed_rpm: number;
  load_kg: number;
  parcels_per_min: number;
}

interface Props {
  value: BenchControls;
  onChange: (next: BenchControls) => void;
  onCommit: (next: Partial<BenchControls> & { ramp_s?: number }) => void;
  running: boolean;
  state: Record<string, any> | undefined;
}

export function ControlSliders({ value, onChange, onCommit, running, state }: Props) {
  const set = (patch: Partial<BenchControls>) => onChange({ ...value, ...patch });

  return (
    <Panel>
      <PanelHeader
        title="Contrôle du banc"
        subtitle="Vitesse, charge et débit de colis — appliqués au relâchement"
        icon={<Gauge size={14} />}
        right={
          <Button size="sm" variant={running ? 'ghost' : 'success'} onClick={() => onCommit({ ramp_s: 2 })}>
            {running ? <><Pause size={12} /> En marche</> : <><Play size={12} /> À l'arrêt</>}
          </Button>
        }
      />

      <div className="space-y-3.5 text-[11.5px]">
        <div>
          <div className="mb-1 flex items-center justify-between">
            <span className="label">Vitesse de rotation</span>
            <span className="num text-[#00E5FF]">{value.speed_rpm} rpm</span>
          </div>
          <input
            type="range" min={0} max={300} step={5} value={value.speed_rpm}
            onChange={(event) => set({ speed_rpm: Number(event.target.value) })}
            onMouseUp={() => onCommit({ speed_rpm: value.speed_rpm, ramp_s: 2 })}
            onTouchEnd={() => onCommit({ speed_rpm: value.speed_rpm, ramp_s: 2 })}
            className="h-1.5 w-full cursor-pointer appearance-none rounded-full bg-white/10 accent-[#00E5FF]"
            aria-label="Vitesse de rotation"
          />
          <p className="mt-0.5 text-[10px] text-[rgb(var(--muted))]">
            Vitesse bande résultante : <span className="num">{fmt(state?.belt_mm_s, 1)} mm/s</span>
          </p>
        </div>

        <div>
          <div className="mb-1 flex items-center justify-between">
            <span className="label">Charge transportée</span>
            <span className="num text-[#00E5FF]">{value.load_kg.toFixed(1)} kg</span>
          </div>
          <input
            type="range" min={0} max={3} step={0.1} value={value.load_kg}
            onChange={(event) => set({ load_kg: Number(event.target.value) })}
            onMouseUp={() => onCommit({ load_kg: value.load_kg })}
            onTouchEnd={() => onCommit({ load_kg: value.load_kg })}
            className="h-1.5 w-full cursor-pointer appearance-none rounded-full bg-white/10 accent-[#00E5FF]"
            aria-label="Charge transportée"
          />
        </div>

        <div>
          <div className="mb-1 flex items-center justify-between">
            <span className="label">Débit de colis</span>
            <span className="num text-[#00E5FF]">{value.parcels_per_min} colis/min</span>
          </div>
          <input
            type="range" min={0} max={60} step={1} value={value.parcels_per_min}
            onChange={(event) => set({ parcels_per_min: Number(event.target.value) })}
            onMouseUp={() => onCommit({ parcels_per_min: value.parcels_per_min })}
            onTouchEnd={() => onCommit({ parcels_per_min: value.parcels_per_min })}
            className="h-1.5 w-full cursor-pointer appearance-none rounded-full bg-white/10 accent-[#00E5FF]"
            aria-label="Débit de colis"
          />
        </div>

        <div className="grid grid-cols-2 gap-2 border-t border-white/[0.06] pt-2.5 text-[10.5px]">
          <Kv label="Ticks simulés" value={String(state?.ticks ?? 0)} />
          <Kv label="Uptime simulateur" value={`${fmt((state?.uptime_s ?? 0) / 60, 1)} min`} />
          <Kv label="Fréquence de tick" value={`${fmt(state?.tick_hz, 0)} Hz`} />
          <Kv label="Pont MQTT" value={String(state?.mqtt?.status ?? '—')} />
        </div>
      </div>
    </Panel>
  );
}

function Kv({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-white/[0.06] bg-white/[0.02] p-2">
      <p className="label">{label}</p>
      <p className="num mt-0.5 text-[11.5px]">{value}</p>
    </div>
  );
}
