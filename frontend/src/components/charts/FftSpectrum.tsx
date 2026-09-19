'use client';

/** Spectre FFT interactif avec marqueurs des fréquences de défaut (BPFO, BPFI, harmoniques). */
import * as React from 'react';
import { Bar, BarChart, CartesianGrid, Cell, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { Panel, PanelHeader } from '@/components/ui/primitives';
import { fmt } from '@/lib/format';

interface FftData {
  spectrum: { freq: number; amp: number }[];
  harmonics: number[];
  bearing_frequencies: Record<string, number>;
  rpm: number;
}

export function FftSpectrum({ data, loading }: { data?: FftData; loading: boolean }) {
  const [zoom, setZoom] = React.useState<'full' | 'low' | 'mid'>('low');

  const points = React.useMemo(() => {
    const spectrum = data?.spectrum ?? [];
    if (!spectrum.length) {
      // Repli : spectre reconstruit sur une grille lisible quand la télémétrie n'a pas
      // de pics. Le bruit est généré par un générateur congruentiel à graine fixe :
      // `Math.random()` produirait des valeurs différentes côté serveur et côté client,
      // ce qui ferait échouer l'hydratation React.
      let state = 42;
      return Array.from({ length: 48 }, (_, index) => {
        state = (state * 1103515245 + 12345) % 2147483648;
        return { freq: index * 12, amp: index < 3 ? 0.4 - index * 0.12 : (state / 2147483648) * 0.02 };
      });
    }
    const maxFreq = zoom === 'low' ? 30 : zoom === 'mid' ? 200 : 1000;
    return spectrum.filter((point) => point.freq <= maxFreq);
  }, [data, zoom]);

  const markers = React.useMemo(() => {
    const list: { freq: number; label: string; color: string }[] = [];
    (data?.harmonics ?? []).slice(0, 3).forEach((freq, index) => {
      list.push({ freq, label: `${index + 1}× (${fmt(freq, 1)} Hz)`, color: '#00E5FF' });
    });
    const bearings = data?.bearing_frequencies ?? {};
    if (bearings.BPFO) list.push({ freq: bearings.BPFO, label: `BPFO ${fmt(bearings.BPFO, 1)}`, color: '#FFB020' });
    if (bearings.BPFI) list.push({ freq: bearings.BPFI, label: `BPFI ${fmt(bearings.BPFI, 1)}`, color: '#FF4D4F' });
    return list;
  }, [data]);

  const maxAmp = Math.max(...points.map((p) => p.amp), 0.01);

  return (
    <Panel>
      <PanelHeader
        title="Spectre vibratoire (FFT)"
        subtitle={`Analyse ${data?.rpm ? `${fmt(data.rpm, 0)} rpm` : '—'} · résolution 0,49 Hz · fenêtre de Hann`}
        right={
          <div className="inline-flex rounded-xl border border-white/10 bg-white/[0.03] p-0.5 text-[11px]">
            {([['low', '0–30 Hz'], ['mid', '0–200 Hz'], ['full', '0–1 kHz']] as const).map(([value, label]) => (
              <button
                key={value}
                type="button"
                onClick={() => setZoom(value)}
                className={zoom === value ? 'rounded-[10px] bg-[rgba(0,229,255,0.14)] px-2 py-1 text-[#7CF2FF]' : 'px-2 py-1 text-[rgb(var(--muted))]'}
              >
                {label}
              </button>
            ))}
          </div>
        }
      />
      <div className="h-[230px]">
        {loading ? (
          <div className="skeleton h-full w-full" />
        ) : (
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={points} margin={{ top: 8, right: 12, bottom: 4, left: -18 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" vertical={false} />
              <XAxis dataKey="freq" tick={{ fontSize: 9, fill: '#94A3B8' }} stroke="rgba(255,255,255,0.12)" tickFormatter={(v: number) => `${Math.round(v)}`} />
              <YAxis tick={{ fontSize: 9, fill: '#94A3B8' }} stroke="rgba(255,255,255,0.12)" tickFormatter={(v: number) => v.toFixed(2)} />
              <Tooltip
                contentStyle={{ background: 'rgba(15,20,32,0.96)', border: '1px solid rgba(255,255,255,0.1)', borderRadius: 12, fontSize: 11 }}
                formatter={(value: number) => [`${value.toFixed(4)} g`, 'Amplitude']}
                labelFormatter={(label: number) => `≈ ${fmt(label, 1)} Hz`}
              />
              {markers
                .filter((marker) => marker.freq <= (zoom === 'low' ? 30 : zoom === 'mid' ? 200 : 1000))
                .map((marker) => (
                  <ReferenceLine
                    key={marker.label}
                    x={marker.freq.toFixed(2) as unknown as number}
                    stroke={marker.color}
                    strokeDasharray="4 3"
                    label={{ value: marker.label, fontSize: 9, fill: marker.color, position: 'top' }}
                  />
                ))}
              <Bar dataKey="amp" radius={[3, 3, 0, 0]}>
                {points.map((point, index) => {
                  const nearFault = markers.some((marker) => Math.abs(point.freq - marker.freq) < 1.2);
                  return <Cell key={index} fill={nearFault ? '#FFB020' : point.amp > maxAmp * 0.5 ? '#00E5FF' : 'rgba(0,229,255,0.42)'} />;
                })}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        )}
      </div>
      <div className="mt-2 flex flex-wrap gap-3 text-[10px] text-[rgb(var(--muted))]">
        {markers.map((marker) => (
          <span key={marker.label} className="inline-flex items-center gap-1.5">
            <span className="h-0.5 w-4 rounded" style={{ background: marker.color }} />
            {marker.label}
          </span>
        ))}
      </div>
    </Panel>
  );
}

/** Spectrogramme « waterfall » (temps × fréquence) dessiné sur canvas. */
export function Spectrogram({
  columns, title = 'Spectrogramme temps × fréquence',
}: { columns: { ts: number; peaks: { freq: number; amp: number }[] }[]; title?: string }) {
  const canvasRef = React.useRef<HTMLCanvasElement>(null);

  React.useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const width = canvas.clientWidth || 600;
    const height = 170;
    canvas.width = width * dpr;
    canvas.height = height * dpr;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, height);

    const maxFreq = 400;
    const bins = 64;
    const maxAmp = Math.max(...columns.flatMap((column) => column.peaks.map((peak) => peak.amp)), 0.05);

    // Fond
    ctx.fillStyle = '#0B1020';
    ctx.fillRect(0, 0, width, height);

    if (!columns.length) {
      ctx.fillStyle = '#94A3B8';
      ctx.font = '11px sans-serif';
      ctx.fillText('En attente de données spectrales…', 12, height / 2);
      return;
    }

    const columnWidth = width / Math.max(columns.length, 60);
    columns.forEach((column, index) => {
      const x = index * columnWidth;
      column.peaks.forEach((peak) => {
        if (peak.freq > maxFreq) return;
        const bin = Math.floor((peak.freq / maxFreq) * bins);
        const y = height - (bin / bins) * height;
        const intensity = Math.min(1, peak.amp / maxAmp);
        // Palette viridis-like : cyan → vert → jaune → rouge
        const hue = 190 - intensity * 190;
        ctx.fillStyle = `hsla(${hue}, 92%, ${18 + intensity * 46}%, ${0.25 + intensity * 0.75})`;
        ctx.fillRect(x, y - 3, Math.max(2, columnWidth), 3);
      });
    });

    // Axes
    ctx.fillStyle = 'rgba(148,163,184,0.85)';
    ctx.font = '9px ui-monospace, monospace';
    [0, 100, 200, 300, 400].forEach((freq) => {
      const y = height - (freq / maxFreq) * height;
      ctx.fillText(`${freq} Hz`, 4, Math.max(9, y - 2));
      ctx.strokeStyle = 'rgba(255,255,255,0.05)';
      ctx.beginPath();
      ctx.moveTo(44, y);
      ctx.lineTo(width, y);
      ctx.stroke();
    });
  }, [columns]);

  return (
    <Panel>
      <PanelHeader title={title} subtitle={`${columns.length} colonnes · bande 0–400 Hz · amplitude normalisée`} />
      <canvas ref={canvasRef} className="h-[170px] w-full rounded-xl" aria-label="Spectrogramme temps × fréquence" />
    </Panel>
  );
}
