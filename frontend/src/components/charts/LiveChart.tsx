'use client';

/**
 * Graphe temps réel haute fréquence (rendu Canvas).
 *
 * Les bibliothèques SVG (Recharts) ne tiennent pas 10 Hz × plusieurs séries sans
 * saccade ni consommation GPU excessive : ce composant dessine directement sur un
 * <canvas> (même approche que uPlot), avec axe temporel, seuils, curseur et légende.
 * Les graphes historiques (Recharts) restent utilisés ailleurs.
 */
import * as React from 'react';
import { cn } from '@/lib/format';

export interface Series {
  key: string;
  label: string;
  color: string;
  values: number[];
  unit?: string;
  dashed?: boolean;
  fill?: boolean;
}

interface LiveChartProps {
  series: Series[];
  times: number[];
  height?: number;
  thresholds?: { value: number; color: string; label?: string }[];
  windowSeconds?: number;
  yDomain?: [number, number];
  className?: string;
  title?: string;
}

export function LiveChart({
  series, times, height = 180, thresholds = [], windowSeconds = 60, yDomain, className, title,
}: LiveChartProps) {
  const canvasRef = React.useRef<HTMLCanvasElement>(null);
  const wrapperRef = React.useRef<HTMLDivElement>(null);
  const [size, setSize] = React.useState({ width: 600, height });
  const [hover, setHover] = React.useState<{ x: number; index: number } | null>(null);

  // Redimensionnement observé (responsive jusqu'au 4K)
  React.useEffect(() => {
    const element = wrapperRef.current;
    if (!element) return;
    const observer = new ResizeObserver(([entry]) => {
      setSize({ width: Math.max(240, entry.contentRect.width), height });
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, [height]);

  const domain = React.useMemo<[number, number]>(() => {
    if (yDomain) return yDomain;
    const all = series.flatMap((s) => s.values).filter((v) => Number.isFinite(v));
    const extra = thresholds.map((t) => t.value);
    if (!all.length) return [0, 1];
    const min = Math.min(...all, ...extra);
    const max = Math.max(...all, ...extra);
    const padding = (max - min) * 0.15 || Math.abs(max) * 0.2 || 1;
    return [Math.max(0, min - padding), max + padding];
  }, [series, thresholds, yDomain]);

  // ------------------------------------------------------------- rendu canvas
  React.useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const { width, height: h } = size;
    canvas.width = width * dpr;
    canvas.height = h * dpr;
    canvas.style.width = `${width}px`;
    canvas.style.height = `${h}px`;

    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, h);

    const padding = { left: 44, right: 10, top: 12, bottom: 20 };
    const plotW = width - padding.left - padding.right;
    const plotH = h - padding.top - padding.bottom;
    const [yMin, yMax] = domain;
    const yToPx = (value: number) => padding.top + plotH - ((value - yMin) / (yMax - yMin || 1)) * plotH;
    const xToPx = (index: number, count: number) => padding.left + (index / Math.max(1, count - 1)) * plotW;

    // Grille + graduations Y
    ctx.strokeStyle = 'rgba(255,255,255,0.05)';
    ctx.fillStyle = 'rgba(148,163,184,0.85)';
    ctx.font = '9px ui-monospace, monospace';
    ctx.lineWidth = 1;
    for (let i = 0; i <= 4; i += 1) {
      const y = padding.top + (plotH / 4) * i;
      ctx.beginPath();
      ctx.moveTo(padding.left, y);
      ctx.lineTo(width - padding.right, y);
      ctx.stroke();
      const value = yMax - ((yMax - yMin) / 4) * i;
      ctx.fillText(value.toFixed(Math.abs(value) < 10 ? 2 : 0), 6, y + 3);
    }

    // Seuils horizontaux
    thresholds.forEach((threshold) => {
      const y = yToPx(threshold.value);
      if (y < padding.top || y > padding.top + plotH) return;
      ctx.save();
      ctx.setLineDash([5, 4]);
      ctx.strokeStyle = threshold.color;
      ctx.globalAlpha = 0.7;
      ctx.beginPath();
      ctx.moveTo(padding.left, y);
      ctx.lineTo(width - padding.right, y);
      ctx.stroke();
      ctx.restore();
      if (threshold.label) {
        ctx.fillStyle = threshold.color;
        ctx.fillText(threshold.label, width - padding.right - ctx.measureText(threshold.label).width - 2, y - 3);
      }
    });

    // Séries
    series.forEach((serie) => {
      if (serie.values.length < 2) return;
      ctx.save();
      ctx.beginPath();
      serie.values.forEach((value, index) => {
        const x = xToPx(index, serie.values.length);
        const y = yToPx(value);
        if (index === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      });
      ctx.strokeStyle = serie.color;
      ctx.lineWidth = 1.5;
      ctx.lineJoin = 'round';
      ctx.shadowColor = `${serie.color}77`;
      ctx.shadowBlur = 7;
      if (serie.dashed) ctx.setLineDash([4, 3]);
      ctx.stroke();
      ctx.restore();

      if (serie.fill) {
        const gradient = ctx.createLinearGradient(0, padding.top, 0, padding.top + plotH);
        gradient.addColorStop(0, `${serie.color}44`);
        gradient.addColorStop(1, `${serie.color}00`);
        ctx.beginPath();
        serie.values.forEach((value, index) => {
          const x = xToPx(index, serie.values.length);
          const y = yToPx(value);
          if (index === 0) ctx.moveTo(x, y);
          else ctx.lineTo(x, y);
        });
        ctx.lineTo(padding.left + plotW, padding.top + plotH);
        ctx.lineTo(padding.left, padding.top + plotH);
        ctx.closePath();
        ctx.fillStyle = gradient;
        ctx.fill();
      }

      // Point terminal « live »
      const lastIndex = serie.values.length - 1;
      const lastValue = serie.values[lastIndex];
      if (Number.isFinite(lastValue)) {
        const x = xToPx(lastIndex, serie.values.length);
        const y = yToPx(lastValue);
        ctx.beginPath();
        ctx.arc(x, y, 2.6, 0, Math.PI * 2);
        ctx.fillStyle = serie.color;
        ctx.shadowColor = serie.color;
        ctx.shadowBlur = 10;
        ctx.fill();
        ctx.shadowBlur = 0;
      }
    });

    // Axe temporel
    if (times.length > 1) {
      ctx.fillStyle = 'rgba(148,163,184,0.75)';
      ctx.font = '9px ui-monospace, monospace';
      const labelCount = Math.min(5, Math.max(2, Math.floor(plotW / 110)));
      for (let i = 0; i <= labelCount; i += 1) {
        const index = Math.round((times.length - 1) * (i / labelCount));
        const label = new Date(times[index] ?? Date.now()).toLocaleTimeString('fr-FR', { hour12: false, minute: '2-digit', second: '2-digit' });
        const x = xToPx(index, times.length);
        if (i === labelCount) {
          ctx.fillText(label, Math.min(width - padding.right - 34, x - 30), h - 5);
        } else {
          ctx.fillText(label, x - 4, h - 5);
        }
        // Graduations verticales
        ctx.strokeStyle = 'rgba(255,255,255,0.04)';
        ctx.beginPath();
        ctx.moveTo(x, padding.top);
        ctx.lineTo(x, padding.top + plotH);
        ctx.stroke();
      }
    }

    // Curseur
    if (hover) {
      ctx.strokeStyle = 'rgba(0,229,255,0.55)';
      ctx.setLineDash([3, 3]);
      ctx.beginPath();
      ctx.moveTo(hover.x, padding.top);
      ctx.lineTo(hover.x, padding.top + plotH);
      ctx.stroke();
      ctx.setLineDash([]);
    }
  }, [series, times, size, domain, thresholds, hover]);

  const hoveredValues = hover
    ? series.map((serie) => ({ serie, value: serie.values[hover.index] })).filter((entry) => Number.isFinite(entry.value))
    : [];

  const yToPxLocal = (value: number) => {
    const padding = { top: 12, bottom: 20 };
    const plotH = size.height - padding.top - padding.bottom;
    return padding.top + plotH - ((value - domain[0]) / (domain[1] - domain[0] || 1)) * plotH;
  };

  return (
    <div ref={wrapperRef} className={cn('relative w-full select-none', className)}>
      {title && <div className="label mb-1.5">{title}</div>}
      <canvas
        ref={canvasRef}
        role="img"
        aria-label={`Graphe temps réel — fenêtre ${windowSeconds} s — ${series.map((s) => s.label).join(', ')}`}
        onMouseMove={(event) => {
          const rect = event.currentTarget.getBoundingClientRect();
          const x = event.clientX - rect.left;
          const plotLeft = 44;
          const plotW = size.width - 54;
          const index = Math.max(0, Math.min((times.length || 1) - 1, Math.round(((x - plotLeft) / plotW) * ((times.length || 1) - 1))));
          setHover({ x, index });
        }}
        onMouseLeave={() => setHover(null)}
        className="cursor-crosshair"
      />
      {hover && hoveredValues.length > 0 && (
        <div
          className="panel pointer-events-none absolute top-2 z-20 min-w-[150px] px-2.5 py-2 text-[11px]"
          style={{ left: Math.min(size.width - 160, Math.max(4, hover.x + 12)) }}
        >
          <div className="num mb-1 text-[10px] text-[rgb(var(--muted))]">
            {new Date(times[hover.index] ?? Date.now()).toLocaleTimeString('fr-FR', { hour12: false })}
          </div>
          {hoveredValues.map(({ serie, value }) => (
            <div key={serie.key} className="flex items-center justify-between gap-3">
              <span className="flex items-center gap-1.5">
                <span className="h-2 w-2 rounded-full" style={{ background: serie.color }} />
                {serie.label}
              </span>
              <span className="num">{Number(value).toFixed(3)} {serie.unit ?? ''}</span>
            </div>
          ))}
        </div>
      )}
      {series.length > 1 && (
        <div className="mt-1.5 flex flex-wrap gap-x-3 gap-y-1">
          {series.map((serie) => {
            const last = serie.values.at(-1);
            return (
              <span key={serie.key} className="inline-flex items-center gap-1.5 text-[10.5px] text-[rgb(var(--muted))]">
                <span className="h-1.5 w-4 rounded-full" style={{ background: serie.color }} />
                {serie.label}
                {Number.isFinite(last) && <span className="num text-[rgb(var(--text))]">{Number(last).toFixed(2)}</span>}
                {serie.unit && <span>{serie.unit}</span>}
              </span>
            );
          })}
        </div>
      )}
    </div>
  );
}
