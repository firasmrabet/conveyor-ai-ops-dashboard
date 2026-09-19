'use client';

/**
 * Grille d'injection de défauts : 7 défauts machine + 2 défauts capteur/mécanique.
 * Chaque bouton applique immédiatement le défaut au moteur sélectionné avec
 * l'intensité et la vitesse de dégradation courantes.
 */
import {
  Activity, Bug, CircleDot, GitCompare, OctagonAlert, Scale, Thermometer, Unplug, Waves, Weight, Zap,
} from 'lucide-react';
import * as React from 'react';
import { motion } from 'framer-motion';
import { Button, Panel, PanelHeader } from '@/components/ui/primitives';

export interface SimulatorFault {
  id: string;
  label: string;
  icon?: string;
  description: string;
}

const ICONS: Record<string, React.ReactNode> = {
  BEARING_FAULT: <CircleDot size={14} />,
  UNBALANCE: <Scale size={14} />,
  MISALIGNMENT: <GitCompare size={14} />,
  BELT_SLIP: <Waves size={14} />,
  OVERLOAD: <Weight size={14} />,
  OVERHEATING: <Thermometer size={14} />,
  SENSOR_FAULT: <Unplug size={14} />,
  MOTOR_STALL: <OctagonAlert size={14} />,
};

interface Props {
  faults: SimulatorFault[];
  intensity: number;
  degradation: number;
  onIntensityChange: (value: number) => void;
  onDegradationChange: (value: number) => void;
  onInject: (fault: SimulatorFault) => void;
  onClear: () => void;
  activeFault?: string;
  motorLabel: string;
  pending?: boolean;
}

export function FaultGrid({
  faults, intensity, degradation, onIntensityChange, onDegradationChange,
  onInject, onClear, activeFault, motorLabel, pending,
}: Props) {
  return (
    <Panel>
      <PanelHeader
        title="Injection de défauts"
        subtitle={`Moteur cible ${motorLabel} · intensité ${intensity} % · dégradation ×${degradation}`}
        icon={<Bug size={14} />}
        right={<Button size="sm" variant="success" onClick={onClear} disabled={pending}><Activity size={12} /> Remettre à zéro</Button>}
      />

      <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
        {faults.map((fault) => {
          const active = activeFault === fault.id;
          return (
            <motion.button
              key={fault.id}
              type="button"
              whileTap={{ scale: 0.97 }}
              onClick={() => onInject(fault)}
              title={fault.description}
              className={`flex flex-col gap-1.5 rounded-xl border p-2.5 text-left transition ${
                active
                  ? 'border-[rgba(255,77,79,0.6)] bg-[rgba(255,77,79,0.1)]'
                  : 'border-white/[0.08] bg-white/[0.02] hover:border-[rgba(255,77,79,0.45)] hover:bg-[rgba(255,77,79,0.07)]'
              }`}
            >
              <span className="flex items-center gap-1.5 text-[11.5px] font-medium text-[#FF9294]">
                {ICONS[fault.id] ?? <Zap size={14} />} {fault.label}
                {active && <span className="ml-auto h-1.5 w-1.5 animate-pulse rounded-full bg-[#FF4D4F]" />}
              </span>
              <span className="text-[10px] leading-snug text-[rgb(var(--muted))]">{fault.description}</span>
            </motion.button>
          );
        })}
      </div>

      <div className="mt-3.5 grid grid-cols-1 gap-3.5 md:grid-cols-2">
        <div>
          <div className="mb-1 flex items-center justify-between">
            <span className="label">Intensité du défaut</span>
            <span className="num text-[#FFB020]">{intensity} %</span>
          </div>
          <input
            type="range" min={0} max={100} step={5} value={intensity}
            onChange={(event) => onIntensityChange(Number(event.target.value))}
            className="h-1.5 w-full cursor-pointer appearance-none rounded-full bg-white/10 accent-[#FFB020]"
            aria-label="Intensité du défaut"
          />
          <p className="mt-0.5 text-[10px] text-[rgb(var(--muted))]">
            0 % → défaut imperceptible · 100 % → dégradation critique immédiate
          </p>
        </div>
        <div>
          <div className="mb-1 flex items-center justify-between">
            <span className="label">Vitesse de dégradation</span>
            <span className="num text-[#FFB020]">×{degradation.toFixed(1)}</span>
          </div>
          <input
            type="range" min={0} max={10} step={0.5} value={degradation}
            onChange={(event) => onDegradationChange(Number(event.target.value))}
            className="h-1.5 w-full cursor-pointer appearance-none rounded-full bg-white/10 accent-[#FFB020]"
            aria-label="Vitesse de dégradation"
          />
          <p className="mt-0.5 text-[10px] text-[rgb(var(--muted))]">
            Accélère la montée en intensité (×1 = réel, ×10 = vieillissement accéléré)
          </p>
        </div>
      </div>
    </Panel>
  );
}
