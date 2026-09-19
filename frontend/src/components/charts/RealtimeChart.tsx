'use client';

/** Graphique multi-séries temps réel branché sur le store (fenêtre glissante 60 s). */
import * as React from 'react';
import { Activity, Pause, Play } from 'lucide-react';
import { LiveChart, type Series } from './LiveChart';
import { Button, Panel, PanelHeader, Segmented } from '@/components/ui/primitives';
import { useMotorSeries } from '@/lib/hooks';
import { useLive } from '@/lib/store';
import type { MotorId } from '@/lib/types';

type Preset = 'vibration' | 'current' | 'temperature' | 'speed' | 'acoustic';

const PRESETS: { value: Preset; label: string }[] = [
  { value: 'vibration', label: 'Vibration' },
  { value: 'current', label: 'Courant' },
  { value: 'temperature', label: 'Température' },
  { value: 'speed', label: 'Vitesse' },
  { value: 'acoustic', label: 'Acoustique' },
];

export function RealtimeChart({ motor = 'M1' as MotorId, windowSeconds = 60, height = 200,
  defaultPreset = 'vibration' }: { motor?: MotorId; windowSeconds?: number; height?: number; defaultPreset?: Preset }) {
  const [preset, setPreset] = React.useState<Preset>(defaultPreset);
  const [paused, setPaused] = React.useState(false);
  const pausedGlobal = useLive((s) => s.paused);
  const setPausedGlobal = useLive((s) => s.setPaused);
  const rows = useMotorSeries(motor, windowSeconds);

  const [frozen, setFrozen] = React.useState(rows);
  React.useEffect(() => {
    if (!paused && !pausedGlobal) setFrozen(rows);
  }, [rows, paused, pausedGlobal]);
  const data = paused || pausedGlobal ? frozen : rows;

  const series: Series[] = React.useMemo(() => {
    switch (preset) {
      case 'current':
        return [
          { key: 'current', label: 'Courant RMS', color: '#00E5FF', values: data.map((d) => d.current), unit: 'A' },
          { key: 'power', label: 'Puissance', color: '#7C5CFF', values: data.map((d) => d.power), unit: 'W' },
        ];
      case 'temperature':
        return [
          { key: 'bearing', label: 'Roulement', color: '#FF4D4F', values: data.map((d) => d.bearingTemp), unit: '°C' },
          { key: 'motor', label: 'Moteur', color: '#FFB020', values: data.map((d) => d.motorTemp), unit: '°C' },
          { key: 'ambient', label: 'Ambiante', color: '#4D9FFF', values: data.map((d) => d.ambientTemp), unit: '°C' },
        ];
      case 'speed':
        return [
          { key: 'rpm', label: 'Rotation', color: '#00E396', values: data.map((d) => d.rpm), unit: 'rpm' },
          { key: 'belt', label: 'Bande', color: '#00E5FF', values: data.map((d) => d.belt), unit: 'mm/s' },
        ];
      case 'acoustic':
        return [{ key: 'spl', label: 'Niveau sonore', color: '#B9A6FF', values: data.map((d) => d.spl), unit: 'dB', fill: true }];
      default:
        return [
          { key: 'rms', label: 'RMS', color: '#00E5FF', values: data.map((d) => d.vibRms), unit: 'g', fill: true },
          { key: 'ax', label: 'Axe X', color: '#00E396', values: data.map((d) => d.vibAx), unit: 'g' },
          { key: 'ay', label: 'Axe Y', color: '#FFB020', values: data.map((d) => d.vibAy), unit: 'g' },
          { key: 'az', label: 'Axe Z', color: '#7C5CFF', values: data.map((d) => d.vibAz), unit: 'g' },
        ];
    }
  }, [preset, data]);

  const thresholds = React.useMemo(() => {
    if (preset === 'vibration') {
      return [
        { value: 0.35, color: 'rgba(255,176,32,0.75)', label: 'seuil avertissement' },
        { value: 0.55, color: 'rgba(255,77,79,0.75)', label: 'seuil critique' },
      ];
    }
    if (preset === 'temperature') {
      return [
        { value: 65, color: 'rgba(255,176,32,0.7)', label: '65 °C' },
        { value: 75, color: 'rgba(255,77,79,0.7)', label: '75 °C' },
      ];
    }
    return [];
  }, [preset]);

  return (
    <Panel className="min-w-0">
      <PanelHeader
        title={`Flux temps réel — ${motor}`}
        subtitle={`Fenêtre glissante ${windowSeconds} s · ${data.length} points · rendu canvas 10 Hz`}
        icon={<Activity size={14} />}
        right={
          <div className="flex flex-wrap items-center gap-2">
            <Segmented size="sm" ariaLabel="Choix des séries" options={PRESETS} value={preset} onChange={setPreset} />
            <Button size="sm" variant="ghost" onClick={() => { setPaused(!paused); setPausedGlobal(!pausedGlobal); }} title={paused ? 'Reprendre le flux' : 'Figer le flux'}>
              {paused || pausedGlobal ? <Play size={13} /> : <Pause size={13} />}
              {paused || pausedGlobal ? 'Reprendre' : 'Pause'}
            </Button>
          </div>
        }
      />
      <LiveChart series={series} times={data.map((d) => d.ts)} height={height} thresholds={thresholds} windowSeconds={windowSeconds} />
    </Panel>
  );
}
