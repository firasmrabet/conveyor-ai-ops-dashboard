'use client';

/** Analyse historique : heatmap, comparateur M1/M2, corrélations, fiabilité, exports. */
import { useQuery } from '@tanstack/react-query';
import { BarChart3, Download, GitCompare, Grid3x3, LineChart, ScatterChart as ScatterIcon, Sigma } from 'lucide-react';
import * as React from 'react';
import {
  Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, Legend, Line, LineChart as RLineChart, Pie, PieChart,
  ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis, ZAxis, ComposedChart, ReferenceLine,
} from 'recharts';
import { Badge, Button, Panel, PanelHeader, ProgressBar, Segmented, Skeleton, Stat } from '@/components/ui/primitives';
import { api } from '@/lib/api';
import { FAULT_COLORS, FAULT_LABELS, fmt, hoursHuman, pct, timeFr } from '@/lib/format';
import type { MotorId } from '@/lib/types';

const RANGES = [
  { value: '1h', label: '1 h' }, { value: '6h', label: '6 h' }, { value: '24h', label: '24 h' },
  { value: '7d', label: '7 j' }, { value: '30d', label: '30 j' },
];

export default function AnalyticsPage() {
  const [range, setRange] = React.useState('24h');
  const [motor, setMotor] = React.useState<MotorId>('M1');
  const [metric, setMetric] = React.useState('health_index');

  const summary = useQuery({ queryKey: ['analytics-summary', range], queryFn: () => api.analyticsSummary(range) });
  const heatmap = useQuery({ queryKey: ['heatmap', range, motor], queryFn: () => api.heatmap(range, motor) });
  const comparison = useQuery({ queryKey: ['comparison', range, metric], queryFn: () => api.comparison(range, metric), refetchInterval: 30000 });
  const correlation = useQuery({ queryKey: ['correlation', range, motor], queryFn: () => api.correlation(range, motor) });
  const faults = useQuery({ queryKey: ['faults', range], queryFn: () => api.faultDistribution(range) });
  const reliability = useQuery({ queryKey: ['reliability', range], queryFn: () => api.reliability(range) });

  const comparisonSeries = React.useMemo(() => {
    const m1 = comparison.data?.series?.M1 ?? [];
    const m2 = comparison.data?.series?.M2 ?? [];
    const length = Math.max(m1.length, m2.length);
    return Array.from({ length }, (_, index) => ({
      ts: m1[index]?.ts ?? m2[index]?.ts,
      time: timeFr(m1[index]?.ts ?? m2[index]?.ts),
      M1: m1[index]?.value,
      M2: m2[index]?.value,
      delta: m1[index]?.value !== undefined && m2[index]?.value !== undefined ? Number(m1[index].value) - Number(m2[index].value) : null,
    }));
  }, [comparison.data]);

  const cells = heatmap.data?.matrix ?? [];

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h1 className="font-display text-[17px] font-semibold">Analyse historique</h1>
          <p className="text-[11.5px] text-[rgb(var(--muted))]">Tendances, comparateur M1/M2, corrélations et indicateurs de fiabilité</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Segmented ariaLabel="Plage d'analyse" options={RANGES} value={range} onChange={setRange} size="sm" />
          <Segmented
            ariaLabel="Moteur analysé" size="sm" value={motor} onChange={setMotor}
            options={[{ value: 'M1' as MotorId, label: 'M1' }, { value: 'M2' as MotorId, label: 'M2' }]}
          />
          <a href={api.analyticsCsvUrl(range)} target="_blank" rel="noreferrer">
            <Button size="sm"><Download size={13} /> CSV</Button>
          </a>
          <a href={api.analyticsReportUrl(24)} target="_blank" rel="noreferrer">
            <Button size="sm" variant="violet"><Download size={13} /> PDF</Button>
          </a>
        </div>
      </div>

      {/* Statistiques synthétiques */}
      <div className="grid grid-cols-2 gap-2.5 md:grid-cols-4 xl:grid-cols-6">
        {(['M1', 'M2'] as MotorId[]).map((id) => {
          const data = summary.data?.motors?.[id];
          return (
            <React.Fragment key={id}>
              <Stat label={`Santé moy. ${id}`} value={fmt(data?.avg_health, 1)} unit="/100" tone="green" icon={<Sigma size={14} />} hint={`min ${fmt(data?.min_health, 0)} · ${data?.samples ?? 0} éch.`} />
              <Stat label={`Vibration max ${id}`} value={fmt(data?.max_vibration, 3)} unit="g" tone="cyan" hint={`moy ${fmt(data?.avg_vibration, 3)} g`} />
              <Stat label={`Anomalies ${id}`} value={String(data?.anomalies ?? 0)} tone="violet" hint={`${data?.critical_alerts ?? 0} critiques`} />
            </React.Fragment>
          );
        })}
      </div>

      <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
        {/* Heatmap calendaire */}
        <Panel>
          <PanelHeader
            title="Heatmap de santé (jour × heure)"
            subtitle={`Moteur ${motor} · couleur = indice de santé moyen`}
            icon={<Grid3x3 size={14} />}
            right={<Badge tone="cyan">{range}</Badge>}
          />
          {heatmap.isLoading ? <Skeleton className="h-[220px] w-full" /> : (
            <div className="overflow-x-auto">
              <div className="min-w-[560px]">
                <div className="mb-1 flex pl-10 text-[9px] text-[rgb(var(--muted))]">
                  {Array.from({ length: 24 }).map((_, hour) => (
                    <span key={hour} className="num flex-1 text-center">{hour % 3 === 0 ? hour : ''}</span>
                  ))}
                </div>
                {(heatmap.data?.days ?? []).map((day, dayIndex) => (
                  <div key={day} className="mb-0.5 flex items-center">
                    <span className="w-10 shrink-0 text-[9.5px] text-[rgb(var(--muted))]">{day.slice(0, 3)}</span>
                    {Array.from({ length: 24 }).map((_, hour) => {
                      const value = cells[dayIndex]?.[hour];
                      const opacity = value === null || value === undefined ? 0.05 : Math.max(0.14, Math.min(1, value / 100));
                      const color = value === null || value === undefined ? '#334155' : value >= 80 ? '#00E396' : value >= 60 ? '#FFB020' : '#FF4D4F';
                      return (
                        <div
                          key={hour}
                          title={`${day} ${hour}:00 — ${value === null || value === undefined ? 'aucune donnée' : `${fmt(value, 1)}/100`}`}
                          className="mx-[1px] h-6 flex-1 rounded-[3px] transition-transform hover:scale-110"
                          style={{ background: color, opacity }}
                        />
                      );
                    })}
                  </div>
                ))}
                <div className="mt-2 flex items-center gap-2 text-[10px] text-[rgb(var(--muted))]">
                  <span>Faible</span>
                  {['#FF4D4F', '#FFB020', '#00E396'].map((color) => (
                    <span key={color} className="h-2.5 w-8 rounded" style={{ background: color }} />
                  ))}
                  <span>Élevée</span>
                </div>
              </div>
            </div>
          )}
        </Panel>

        {/* Comparateur M1 vs M2 */}
        <Panel>
          <PanelHeader
            title="Comparateur M1 vs M2"
            subtitle="Écart de synchronisation entre les deux moteurs du convoyeur"
            icon={<GitCompare size={14} />}
            right={
              <Segmented
                size="sm" ariaLabel="Métrique comparée" value={metric} onChange={setMetric}
                options={[
                  { value: 'health_index', label: 'Santé' },
                  { value: 'rpm', label: 'Vitesse' },
                  { value: 'vibration_rms', label: 'Vibration' },
                  { value: 'temp_bearing_c', label: 'Temp.' },
                ]}
              />
            }
          />
          <div className="h-[215px]">
            {comparison.isLoading ? <Skeleton className="h-full w-full" /> : (
              <ResponsiveContainer width="100%" height="100%">
                <ComposedChart data={comparisonSeries} margin={{ top: 8, right: 12, bottom: 0, left: -18 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" vertical={false} />
                  <XAxis dataKey="time" tick={{ fontSize: 9, fill: '#94A3B8' }} stroke="rgba(255,255,255,0.12)" interval="preserveStartEnd" />
                  <YAxis tick={{ fontSize: 9, fill: '#94A3B8' }} stroke="rgba(255,255,255,0.12)" />
                  <Tooltip contentStyle={{ background: 'rgba(15,20,32,0.96)', border: '1px solid rgba(255,255,255,0.1)', borderRadius: 12, fontSize: 11 }} />
                  <Legend wrapperStyle={{ fontSize: 10 }} />
                  <Line type="monotone" dataKey="M1" stroke="#00E5FF" dot={false} strokeWidth={1.8} />
                  <Line type="monotone" dataKey="M2" stroke="#7C5CFF" dot={false} strokeWidth={1.8} />
                  <Area type="monotone" dataKey="delta" stroke="#FFB020" fill="rgba(255,176,32,0.12)" name="Écart M1−M2" />
                </ComposedChart>
              </ResponsiveContainer>
            )}
          </div>
          <div className="mt-2 grid grid-cols-2 gap-2 text-[10.5px] md:grid-cols-4">
            <span>Écart moyen <span className="num text-[rgb(var(--text))]">{fmt(comparison.data?.sync?.delta_mean, 3)}</span></span>
            <span>Écart max <span className="num text-[rgb(var(--text))]">{fmt(comparison.data?.sync?.max_gap, 2)}</span></span>
            <span>Corrélation <span className="num text-[rgb(var(--text))]">{fmt(comparison.data?.sync?.correlation, 3)}</span></span>
            <span>
              Synchronisme{' '}
              <Badge tone={comparison.data?.sync?.aligned ? 'green' : 'amber'}>
                {comparison.data?.sync?.aligned ? 'aligné' : 'dérive'}
              </Badge>
            </span>
          </div>
        </Panel>

        {/* Corrélations */}
        <Panel>
          <PanelHeader
            title="Corrélations capteurs"
            subtitle={`Moteur ${motor} · nuage courant / température, taille = vibration`}
            icon={<ScatterIcon size={14} />}
          />
          <div className="h-[230px]">
            {correlation.isLoading ? <Skeleton className="h-full w-full" /> : (
              <ResponsiveContainer width="100%" height="100%">
                <ScatterChart margin={{ top: 8, right: 12, bottom: 0, left: -18 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
                  <XAxis type="number" dataKey="current" name="Courant" unit=" A" tick={{ fontSize: 9, fill: '#94A3B8' }} stroke="rgba(255,255,255,0.12)" />
                  <YAxis type="number" dataKey="temperature" name="Température" unit=" °C" tick={{ fontSize: 9, fill: '#94A3B8' }} stroke="rgba(255,255,255,0.12)" />
                  <ZAxis type="number" dataKey="vibration" range={[10, 130]} />
                  <Tooltip contentStyle={{ background: 'rgba(15,20,32,0.96)', border: '1px solid rgba(255,255,255,0.1)', borderRadius: 12, fontSize: 11 }} />
                  <Scatter data={correlation.data?.points ?? []} fill="#00E5FF" fillOpacity={0.55} />
                </ScatterChart>
              </ResponsiveContainer>
            )}
          </div>
          <div className="mt-2 grid grid-cols-3 gap-2 text-[10.5px]">
            {Object.entries(correlation.data?.pairs ?? {}).map(([key, value]: [string, any]) => (
              <div key={key} className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-2">
                <p className="label">{key.replace(/_/g, ' ')}</p>
                <p className="num mt-0.5 text-[12px]">r = {fmt(value.r, 3)}</p>
                <p className="text-[9.5px] text-[rgb(var(--muted))]">R² = {fmt(value.r2, 3)}</p>
              </div>
            ))}
          </div>
        </Panel>

        {/* Distribution des défauts */}
        <Panel>
          <PanelHeader
            title="Distribution des défauts & Pareto"
            subtitle="Types de défauts détectés par l'IA sur la période"
            icon={<BarChart3 size={14} />}
            right={<Badge tone="violet">{faults.data?.total_anomalies ?? 0} anomalies</Badge>}
          />
          <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
            <div className="h-[195px]">
              <ResponsiveContainer width="100%" height="100%">
                <PieChart>
                  <Pie
                    data={(faults.data?.distribution ?? []).map((item: any) => ({
                      name: FAULT_LABELS[item.fault_class] ?? item.fault_class,
                      value: item.count,
                      color: FAULT_COLORS[item.fault_class] ?? '#94A3B8',
                    }))}
                    dataKey="value" nameKey="name" innerRadius={42} outerRadius={70} paddingAngle={2}
                  >
                    {(faults.data?.distribution ?? []).map((item: any) => (
                      <Cell key={item.fault_class} fill={FAULT_COLORS[item.fault_class] ?? '#94A3B8'} />
                    ))}
                  </Pie>
                  <Tooltip contentStyle={{ background: 'rgba(15,20,32,0.96)', border: '1px solid rgba(255,255,255,0.1)', borderRadius: 12, fontSize: 11 }} />
                </PieChart>
              </ResponsiveContainer>
            </div>
            <div className="h-[195px]">
              <ResponsiveContainer width="100%" height="100%">
                <ComposedChart data={faults.data?.pareto ?? []} margin={{ top: 8, right: 8, bottom: 0, left: -20 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" vertical={false} />
                  <XAxis dataKey="fault_class" tick={{ fontSize: 8, fill: '#94A3B8' }} stroke="rgba(255,255,255,0.12)" tickFormatter={(v: string) => FAULT_LABELS[v] ?? v} />
                  <YAxis yAxisId="left" tick={{ fontSize: 9, fill: '#94A3B8' }} stroke="rgba(255,255,255,0.12)" />
                  <YAxis yAxisId="right" orientation="right" domain={[0, 100]} tick={{ fontSize: 9, fill: '#94A3B8' }} stroke="rgba(255,255,255,0.12)" />
                  <Tooltip contentStyle={{ background: 'rgba(15,20,32,0.96)', border: '1px solid rgba(255,255,255,0.1)', borderRadius: 12, fontSize: 11 }} />
                  <Bar yAxisId="left" dataKey="count" fill="#7C5CFF" radius={[4, 4, 0, 0]} name="Occurrences" />
                  <Line yAxisId="right" type="monotone" dataKey="cumulative_pct" stroke="#FFB020" strokeWidth={1.8} dot={false} name="Cumul %" />
                </ComposedChart>
              </ResponsiveContainer>
            </div>
          </div>
        </Panel>
      </div>

      {/* Fiabilité */}
      <Panel>
        <PanelHeader title="Indicateurs de fiabilité" subtitle="MTBF, MTTR, arrêts, faux positifs et coûts de maintenance" icon={<LineChart size={14} />} />
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
          {(['M1', 'M2'] as MotorId[]).map((id) => {
            const data = reliability.data?.motors?.[id] ?? {};
            return (
              <div key={id} className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-3">
                <div className="mb-2 flex items-center justify-between">
                  <p className="text-[12.5px] font-medium">Moteur {id}</p>
                  <Badge tone={(data.availability_pct ?? 100) > 95 ? 'green' : 'amber'}>
                    {pct(data.availability_pct, 1)} dispo
                  </Badge>
                </div>
                <div className="grid grid-cols-2 gap-2 text-[10.5px] sm:grid-cols-3">
                  <Metric label="MTBF" value={hoursHuman(data.mtbf_h)} />
                  <Metric label="MTTR" value={hoursHuman(data.mttr_h)} />
                  <Metric label="Arrêts" value={String(data.stops ?? 0)} />
                  <Metric label="Pannes critiques" value={String(data.failures ?? 0)} />
                  <Metric label="Taux de faux positifs" value={pct((data.false_positive_rate ?? 0) * 100, 1)} />
                  <Metric label="Coût maintenance" value={`${fmt(data.maintenance_cost_eur, 0)} €`} />
                </div>
              </div>
            );
          })}
        </div>
      </Panel>
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="label">{label}</p>
      <p className="num mt-0.5 text-[12px] text-[rgb(var(--text))]">{value}</p>
    </div>
  );
}
