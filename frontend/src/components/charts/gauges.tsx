'use client';

/** Jauges, sparklines et micro-visualisations SVG (sans dépendance externe). */
import { motion } from 'framer-motion';
import * as React from 'react';
import { healthColor } from '@/lib/format';
import { useCountUp } from '@/lib/hooks';

// ------------------------------------------------------------------ Jauge radiale (Health Index)
export function RadialGauge({
  value, size = 132, thickness = 10, label = 'Santé', sublabel, showValue = true,
}: { value: number; size?: number; thickness?: number; label?: string; sublabel?: string; showValue?: boolean }) {
  const animated = useCountUp(value, 900);
  const radius = (size - thickness) / 2;
  const circumference = Math.PI * 1.5 * radius;   // arc de 270°
  const clamped = Math.max(0, Math.min(100, value));
  const color = healthColor(value);

  return (
    <div className="relative inline-flex items-center justify-center" style={{ width: size, height: size }}>
      <svg width={size} height={size} className="-rotate-[135deg]">
        <circle
          cx={size / 2} cy={size / 2} r={radius} fill="none" stroke="rgb(var(--line) / 0.10)"
          strokeWidth={thickness} strokeLinecap="round"
          strokeDasharray={`${circumference} ${circumference * 4}`}
        />
        <motion.circle
          cx={size / 2} cy={size / 2} r={radius} fill="none" stroke={color}
          strokeWidth={thickness} strokeLinecap="round"
          strokeDasharray={`${circumference} ${circumference * 4}`}
          initial={{ strokeDashoffset: circumference }}
          animate={{ strokeDashoffset: circumference * (1 - clamped / 100) }}
          transition={{ type: 'spring', stiffness: 60, damping: 18 }}
          style={{ filter: `drop-shadow(0 0 8px ${color}88)` }}
        />
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        {showValue && (
          <span className="num text-2xl font-semibold" style={{ color }}>
            {animated.toFixed(0)}
          </span>
        )}
        <span className="label mt-0.5">{label}</span>
        {sublabel && <span className="mt-0.5 text-[10px] text-[rgb(var(--muted))]">{sublabel}</span>}
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ Sparkline
export function Sparkline({
  data, width = 120, height = 34, color = '#00E5FF', fill = true, strokeWidth = 1.6, domain,
}: { data: number[]; width?: number; height?: number; color?: string; fill?: boolean; strokeWidth?: number; domain?: [number, number] }) {
  const path = React.useMemo(() => {
    if (!data.length) return { line: '', area: '' };
    const [min, max] = domain ?? [Math.min(...data), Math.max(...data)];
    const span = max - min || 1;
    const stepX = width / Math.max(1, data.length - 1);
    const points = data.map((value, index) => {
      const x = index * stepX;
      const y = height - ((value - min) / span) * (height - 6) - 3;
      return [x, Number.isFinite(y) ? y : height / 2] as const;
    });
    const line = points.map(([x, y], index) => `${index === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`).join(' ');
    const area = `${line} L${width},${height} L0,${height} Z`;
    return { line, area };
  }, [data, width, height, domain]);

  const gradientId = React.useId();
  return (
    <svg width={width} height={height} className="overflow-visible">
      {fill && (
        <>
          <defs>
            <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={color} stopOpacity="0.35" />
              <stop offset="100%" stopColor={color} stopOpacity="0" />
            </linearGradient>
          </defs>
          <path d={path.area} fill={`url(#${gradientId})`} />
        </>
      )}
      <path d={path.line} fill="none" stroke={color} strokeWidth={strokeWidth} strokeLinejoin="round" strokeLinecap="round" style={{ filter: `drop-shadow(0 0 5px ${color}66)` }} />
    </svg>
  );
}

// ------------------------------------------------------------------ Donut (probabilités de défauts)
export function Donut({
  slices, size = 150, thickness = 16, center,
}: { slices: { label: string; value: number; color: string }[]; size?: number; thickness?: number; center?: React.ReactNode }) {
  const total = slices.reduce((sum, slice) => sum + slice.value, 0) || 1;
  const radius = (size - thickness) / 2;
  const circumference = 2 * Math.PI * radius;
  let offset = 0;

  return (
    <div className="relative inline-flex items-center justify-center" style={{ width: size, height: size }}>
      <svg width={size} height={size} className="-rotate-90">
        <circle cx={size / 2} cy={size / 2} r={radius} fill="none" stroke="rgb(var(--line) / 0.08)" strokeWidth={thickness} />
        {slices.map((slice) => {
          const length = (slice.value / total) * circumference;
          const element = (
            <motion.circle
              key={slice.label}
              cx={size / 2} cy={size / 2} r={radius} fill="none" stroke={slice.color} strokeWidth={thickness}
              strokeDasharray={`${length} ${circumference - length}`}
              strokeDashoffset={-offset}
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              transition={{ duration: 0.4 }}
              strokeLinecap="butt"
            />
          );
          offset += length;
          return element;
        })}
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center text-center">{center}</div>
    </div>
  );
}

// ------------------------------------------------------------------ Barres horizontales (XAI)
export function ContributionBars({
  items, max = 1,
}: { items: { label: string; value: number; tone?: string }[]; max?: number }) {
  return (
    <div className="space-y-2">
      {items.map((item) => {
        const ratio = Math.max(0.02, Math.min(1, item.value / (max || 1)));
        return (
          <div key={item.label}>
            <div className="mb-1 flex items-center justify-between text-[11px]">
              <span className="truncate pr-2 text-[rgb(var(--text))]">{item.label}</span>
              <span className="num text-[rgb(var(--muted))]">{(item.value * 100).toFixed(0)} %</span>
            </div>
            <div className="h-1.5 w-full overflow-hidden rounded-full bg-white/[0.06]">
              <motion.div
                className="h-full rounded-full"
                style={{ background: `linear-gradient(90deg, ${item.tone ?? '#7C5CFF'}66, ${item.tone ?? '#7C5CFF'})` }}
                initial={{ width: 0 }}
                animate={{ width: `${ratio * 100}%` }}
                transition={{ type: 'spring', stiffness: 90, damping: 18 }}
              />
            </div>
          </div>
        );
      })}
      {items.length === 0 && <p className="text-[11.5px] text-[rgb(var(--muted))]">Aucune contribution significative.</p>}
    </div>
  );
}

// ------------------------------------------------------------------ Barre RUL
export function RulBar({ rulHours, low, high, critical = 72 }: { rulHours: number; low?: number; high?: number; critical?: number }) {
  const max = Math.max(high ?? rulHours * 1.5, 240);
  const ratio = Math.max(0, Math.min(100, (rulHours / max) * 100));
  const lowRatio = low ? Math.max(0, Math.min(100, (low / max) * 100)) : ratio;
  const highRatio = high ? Math.max(0, Math.min(100, (high / max) * 100)) : ratio;
  const tone = rulHours < critical ? '#FF4D4F' : rulHours < 400 ? '#FFB020' : '#00E396';

  return (
    <div>
      <div className="relative h-2 w-full overflow-hidden rounded-full bg-white/[0.06]">
        <div className="absolute inset-y-0 rounded-full bg-white/10" style={{ left: `${lowRatio}%`, width: `${Math.max(2, highRatio - lowRatio)}%` }} />
        <motion.div
          className="absolute inset-y-0 left-0 rounded-full"
          style={{ background: `linear-gradient(90deg, ${tone}66, ${tone})` }}
          initial={{ width: 0 }} animate={{ width: `${ratio}%` }} transition={{ type: 'spring', stiffness: 90, damping: 20 }}
        />
      </div>
      <div className="mt-1 flex justify-between text-[10px] text-[rgb(var(--muted))]">
        <span>0 h</span><span>{Math.round(max)} h</span>
      </div>
    </div>
  );
}
