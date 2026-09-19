'use client';

/** Carte moteur : jauge de santé, sparklines, diagnostic IA, RUL. */
import { ArrowRight, CircleDot, Gauge, Thermometer, Waves, Zap } from 'lucide-react';
import Link from 'next/link';
import * as React from 'react';
import { motion } from 'framer-motion';
import { Badge, Button, Panel, ProgressBar } from '@/components/ui/primitives';
import { RadialGauge, RulBar, Sparkline } from '@/components/charts/gauges';
import { FAULT_LABELS, cn, fmt, healthColor, hoursHuman } from '@/lib/format';
import { useMotorSeries } from '@/lib/hooks';
import { useLive } from '@/lib/store';
import type { MotorId } from '@/lib/types';

interface MotorCardProps {
  motor: MotorId;
  label: string;
  side?: string;
  onClick3D?: () => void;
}

export function MotorCard({ motor, label, side }: MotorCardProps) {
  const series = useMotorSeries(motor, 60);
  const inference = useLive((s) => s.inference[motor]);
  const latest = useLive((s) => s.latest[motor]);
  const edge = useLive((s) => s.edge[motor]);

  const health = Number(inference?.health_index ?? 100);
  const anomaly = Number(inference?.anomaly_score ?? 0);
  const faultClass = String(inference?.fault_class ?? 'HEALTHY');
  const critical = anomaly > 0.6 || health < 55;
  const color = healthColor(health);

  const sparkVibration = React.useMemo(() => series.map((d) => d.vibRms), [series]);
  const sparkCurrent = React.useMemo(() => series.map((d) => d.current), [series]);
  const sparkTemp = React.useMemo(() => series.map((d) => d.bearingTemp), [series]);
  const sparkRpm = React.useMemo(() => series.map((d) => d.rpm), [series]);

  return (
    <Panel hover className={cn('relative overflow-hidden', critical && 'ring-1 ring-[rgba(255,77,79,0.4)]')}>
      {/* Liseré coloré selon la santé */}
      <span className="absolute inset-x-0 top-0 h-[2px]" style={{ background: `linear-gradient(90deg, transparent, ${color}, transparent)` }} />

      <div className="mb-2 flex items-start justify-between">
        <div>
          <div className="flex items-center gap-2">
            <h3 className="font-display text-[14px] font-semibold">{motor}</h3>
            <Badge tone={motor === 'M1' ? 'cyan' : 'violet'}>{label}</Badge>
          </div>
          <p className="mt-0.5 text-[10.5px] text-[rgb(var(--muted))]">
            {side ?? (motor === 'M1' ? 'Côté GAUCHE' : 'Côté DROIT')} · {latest?.state ?? 'INCONNU'} ·{' '}
            {fmt(latest?.speed?.rpm, 1)} rpm
          </p>
        </div>
        <Badge tone={faultClass === 'HEALTHY' ? 'green' : critical ? 'red' : 'amber'} dot>
          {FAULT_LABELS[faultClass] ?? faultClass}
        </Badge>
      </div>

      <div className="flex items-center gap-3">
        <RadialGauge value={health} size={118} thickness={9} label="Santé" />
        <div className="min-w-0 flex-1 space-y-2">
          <MiniMetric
            icon={<Waves size={12} />} label="Vibration RMS"
            value={fmt(latest?.vibration?.rms, 3)} unit="g" color="#00E5FF" data={sparkVibration}
          />
          <MiniMetric
            icon={<Zap size={12} />} label="Courant"
            value={fmt(latest?.current?.rms_a, 2)} unit="A" color="#7C5CFF" data={sparkCurrent}
          />
          <MiniMetric
            icon={<Thermometer size={12} />} label="Roulement"
            value={fmt(latest?.temperature?.bearing_c, 1)} unit="°C" color="#FF4D4F" data={sparkTemp}
          />
        </div>
      </div>

      <div className="mt-3 grid grid-cols-2 gap-2 text-[10.5px]">
        <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-2">
          <p className="label">Score d'anomalie</p>
          <p className="num mt-0.5 text-[13px]" style={{ color: anomaly > 0.6 ? '#FF4D4F' : anomaly > 0.35 ? '#FFB020' : '#00E396' }}>
            {anomaly.toFixed(3)}
          </p>
          <ProgressBar value={anomaly * 100} tone={anomaly > 0.6 ? 'red' : anomaly > 0.35 ? 'amber' : 'green'} height={4} label="Score d'anomalie" />
        </div>
        <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-2">
          <p className="label">Vitesse / charge</p>
          <p className="num mt-0.5 text-[13px] text-[#00E5FF]">{fmt(latest?.speed?.belt_mm_s, 1)} mm/s</p>
          <Sparkline data={sparkRpm.length ? sparkRpm : [0, 0]} width={120} height={18} color="#00E396" fill={false} />
        </div>
      </div>

      <div className="mt-3">
        <div className="mb-1 flex items-center justify-between">
          <span className="label">Durée de vie restante (RUL)</span>
          <span className="num text-[11px]" style={{ color: '#B9A6FF' }}>{hoursHuman(Number(inference?.rul_hours ?? 0))}</span>
        </div>
        <RulBar rulHours={Number(inference?.rul_hours ?? 0)} low={inference?.rul_confidence?.low} high={inference?.rul_confidence?.high} />
      </div>

      <div className="mt-3 flex items-center justify-between gap-2">
        <span className="text-[10px] text-[rgb(var(--muted))]">
          Edge TinyML : <span className="num text-[#B9A6FF]">{edge?.health_index ? `${Number(edge.health_index).toFixed(0)}/100` : '—'}</span> ·{' '}
          modèle <span className="num">{inference?.model_version ?? '—'}</span> ·{' '}
          {fmt(inference?.inference_ms, 1)} ms
        </span>
        <Link href={`/motors/${motor}`}>
          <Button size="sm" variant="primary">
            Détails <ArrowRight size={12} />
          </Button>
        </Link>
      </div>

      {critical && (
        <motion.span
          className="pointer-events-none absolute inset-0 rounded-2xl"
          animate={{ boxShadow: ['0 0 0 0 rgba(255,77,79,0)', '0 0 0 6px rgba(255,77,79,0.12)', '0 0 0 0 rgba(255,77,79,0)'] }}
          transition={{ duration: 2, repeat: Infinity }}
        />
      )}
    </Panel>
  );
}

function MiniMetric({
  icon, label, value, unit, color, data,
}: { icon: React.ReactNode; label: string; value: string; unit: string; color: string; data: number[] }) {
  return (
    <div className="flex items-center gap-2">
      <span style={{ color }}>{icon}</span>
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline justify-between">
          <span className="text-[10px] text-[rgb(var(--muted))]">{label}</span>
          <span className="num text-[11.5px]">
            {value} <span className="text-[9.5px] text-[rgb(var(--muted))]">{unit}</span>
          </span>
        </div>
        <Sparkline data={data.length > 1 ? data : [0, 0]} width={150} height={16} color={color} fill={false} strokeWidth={1.3} />
      </div>
    </div>
  );
}
