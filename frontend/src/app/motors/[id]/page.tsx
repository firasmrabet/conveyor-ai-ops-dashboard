'use client';

/** Fiche détaillée d'un moteur : prédiction IA, FFT, XAI, historique, interventions. */
import { useQuery } from '@tanstack/react-query';
import {
  Activity, AlertTriangle, Brain, Download, FileText, Gauge, LineChart as LineChartIcon,
  Radar as RadarIcon, Table, TrendingDown, Wrench,
} from 'lucide-react';
import { useParams } from 'next/navigation';
import * as React from 'react';
import {
  Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, Legend, Line, LineChart, PolarAngleAxis,
  PolarGrid, PolarRadiusAxis, Radar, RadarChart, ReferenceLine, ResponsiveContainer, Scatter,
  ScatterChart, Tooltip, XAxis, YAxis, ZAxis,
} from 'recharts';
import { FftSpectrum, Spectrogram } from '@/components/charts/FftSpectrum';
import { ContributionBars, Donut, RadialGauge, RulBar } from '@/components/charts/gauges';
import { RealtimeChart } from '@/components/charts/RealtimeChart';
import { Badge, Button, EmptyState, Panel, PanelHeader, ProgressBar, Segmented, Skeleton } from '@/components/ui/primitives';
import { api } from '@/lib/api';
import { FAULT_COLORS, FAULT_LABELS, dateTimeFr, fmt, healthColor, hoursHuman, timeFr } from '@/lib/format';
import { useLive } from '@/lib/store';
import type { MotorId } from '@/lib/types';

type Tab = 'overview' | 'prediction' | 'spectral' | 'xai' | 'history';

export default function MotorDetailPage() {
  const params = useParams<{ id: string }>();
  const motor = (params?.id ?? 'M1').toUpperCase() as MotorId;
  const [tab, setTab] = React.useState<Tab>('overview');

  const liveInference = useLive((s) => s.inference[motor]);
  const liveTelemetry = useLive((s) => s.latest[motor]);
  const edge = useLive((s) => s.edge[motor]);
  const comparison = useLive((s) => s.edgeComparison[motor]);

  const header = useQuery({ queryKey: ['motor', motor], queryFn: () => api.motor(motor), refetchInterval: 10000 });
  const health = useQuery({ queryKey: ['health', motor], queryFn: () => api.healthHistory(motor, 24), refetchInterval: 30000 });
  const degradation = useQuery({ queryKey: ['degradation', motor], queryFn: () => api.degradation(motor, 168), refetchInterval: 60000 });
  const fft = useQuery({ queryKey: ['fft', motor], queryFn: () => api.fft(motor), refetchInterval: 5000 });
  const spectro = useQuery({ queryKey: ['spectro', motor], queryFn: () => api.spectrogram(motor), refetchInterval: 5000 });
  const xai = useQuery({ queryKey: ['xai', motor], queryFn: () => api.xai(motor), refetchInterval: 5000 });
  const radar = useQuery({ queryKey: ['radar', motor], queryFn: () => api.radar(motor), refetchInterval: 15000 });
  const anomalies = useQuery({ queryKey: ['anomalies', motor], queryFn: () => api.anomalies(motor, 168), refetchInterval: 30000 });
  const interventions = useQuery({ queryKey: ['interventions', motor], queryFn: () => api.interventions(motor) });

  const inference = liveInference?.health_index !== undefined ? liveInference : header.data?.inference;
  const healthValue = Number(inference?.health_index ?? 100);
  const faultClass = String(inference?.fault_class ?? 'HEALTHY');

  const historyData = React.useMemo(
    () => (health.data?.points ?? []).map((point: any) => ({
      ts: point.ts,
      time: timeFr(point.ts),
      health: point.health_index,
      anomaly: point.anomaly_score,
      vibration: point.vibration_rms,
      current: point.current_rms,
      temperature: point.temp_bearing_c,
      rpm: point.rpm,
    })),
    [health.data],
  );

  // Projection : historique + prédiction (pointillés) sur la dégradation de santé
  const projectionData = React.useMemo(() => {
    const history = (degradation.data?.history ?? []).map((point: any) => ({
      ts: point.ts, time: timeFr(point.ts), health: point.health, projected: null as number | null,
    }));
    const lastTs = history.at(-1)?.ts ?? Date.now();
    const projection = (degradation.data?.projection ?? []).map((point: any, index: number) => ({
      ts: index === 0 ? lastTs : point.ts,
      time: timeFr(point.ts),
      health: null as number | null,
      projected: point.health,
      hours: point.hours,
    }));
    if (projection.length && history.length) {
      projection[0].health = history.at(-1)?.health ?? null;
    }
    return [...history, ...projection];
  }, [degradation.data]);

  const faultProbabilities = React.useMemo(() => {
    const probabilities = (inference?.fault_probabilities ?? {}) as Record<string, number>;
    return Object.entries(probabilities)
      .map(([key, value]) => ({ label: FAULT_LABELS[key] ?? key, key, value: Number(value), color: FAULT_COLORS[key] ?? '#94A3B8' }))
      .sort((a, b) => b.value - a.value);
  }, [inference]);

  const last24Delta = (health.data as any)?.delta_24h ?? null;

  return (
    <div className="space-y-3">
      {/* En-tête */}
      <Panel className="relative overflow-hidden">
        <span className="absolute inset-x-0 top-0 h-[2px]" style={{ background: `linear-gradient(90deg, transparent, ${healthColor(healthValue)}, transparent)` }} />
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="flex items-start gap-3">
            <RadialGauge value={healthValue} size={104} thickness={8} label="Santé" sublabel={`Δ 24 h ${last24Delta !== null ? `${last24Delta > 0 ? '+' : ''}${fmt(last24Delta, 1)}` : '—'}`} />
            <div>
              <div className="flex items-center gap-2">
                <h1 className="font-display text-[18px] font-semibold">Moteur {motor}</h1>
                <Badge tone={motor === 'M1' ? 'cyan' : 'violet'}>{motor === 'M1' ? 'GAUCHE' : 'DROIT'}</Badge>
                <Badge tone={faultClass === 'HEALTHY' ? 'green' : healthValue < 55 ? 'red' : 'amber'} dot>
                  {FAULT_LABELS[faultClass] ?? faultClass}
                </Badge>
              </div>
              <p className="mt-1 text-[11.5px] text-[rgb(var(--muted))]">
                {header.data?.motor?.model ?? 'NEMA17 42HD4027'} · état {liveTelemetry?.state ?? '—'} ·{' '}
                {fmt(liveTelemetry?.speed?.rpm, 1)} rpm · {fmt(liveTelemetry?.current?.rms_a, 2)} A ·{' '}
                {fmt(liveTelemetry?.temperature?.bearing_c, 1)} °C roulement
              </p>
              <div className="mt-2 grid grid-cols-2 gap-x-6 gap-y-1 text-[10.5px] text-[rgb(var(--muted))] sm:grid-cols-4">
                <span>Score anomalie <span className="num text-[rgb(var(--text))]">{fmt(Number(inference?.anomaly_score ?? 0), 3)}</span></span>
                <span>RUL <span className="num text-[#B9A6FF]">{hoursHuman(Number(inference?.rul_hours ?? 0))}</span></span>
                <span>Inférence <span className="num text-[rgb(var(--text))]">{fmt(Number(inference?.inference_ms ?? 0), 1)} ms</span></span>
                <span>Dernier entretien <span className="text-[rgb(var(--text))]">{dateTimeFr(header.data?.motor?.last_maintenance)}</span></span>
              </div>
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
            <a href={api.motorReportUrl(motor, 24)} target="_blank" rel="noreferrer">
              <Button size="sm" variant="violet"><FileText size={13} /> Générer le rapport PDF</Button>
            </a>
            <a href={api.motorCsvUrl(motor, 24)} target="_blank" rel="noreferrer">
              <Button size="sm" variant="default"><Download size={13} /> CSV 24 h</Button>
            </a>
          </div>
        </div>
      </Panel>

      {/* Onglets */}
      <Segmented
        ariaLabel="Sections de la fiche moteur"
        value={tab}
        onChange={setTab}
        options={[
          { value: 'overview', label: 'Vue temps réel', icon: <Activity size={12} /> },
          { value: 'prediction', label: 'Prédiction IA', icon: <TrendingDown size={12} /> },
          { value: 'spectral', label: 'Analyse fréquentielle', icon: <LineChartIcon size={12} /> },
          { value: 'xai', label: 'Explicabilité (XAI)', icon: <Brain size={12} /> },
          { value: 'history', label: 'Historique', icon: <Table size={12} /> },
        ]}
      />

      {tab === 'overview' && (
        <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
          {/* Deux fenêtres complémentaires : vibration (détection fine, 60 s) et
              vitesse/charge (tendance, 5 min) — jamais le même tracé deux fois. */}
          <RealtimeChart motor={motor} windowSeconds={60} height={190} defaultPreset="vibration" />
          <RealtimeChart motor={motor} windowSeconds={300} height={190} defaultPreset="speed" />
          <div className="xl:col-span-2 grid grid-cols-2 gap-3 md:grid-cols-4">
            <MiniStat label="Vibration RMS" value={fmt(liveTelemetry?.vibration?.rms, 3)} unit="g" tone="#00E5FF" />
            <MiniStat label="Kurtosis" value={fmt(liveTelemetry?.vibration?.kurtosis, 2)} unit="" tone="#7C5CFF" />
            <MiniStat label="Facteur de crête" value={fmt(liveTelemetry?.vibration?.crest_factor, 2)} unit="" tone="#FFB020" />
            <MiniStat label="Niveau sonore" value={fmt(liveTelemetry?.acoustic?.spl_db, 1)} unit="dB" tone="#00E396" />
            <MiniStat label="Courant RMS" value={fmt(liveTelemetry?.current?.rms_a, 2)} unit="A" tone="#00E5FF" />
            <MiniStat label="Puissance" value={fmt(liveTelemetry?.current?.power_w, 1)} unit="W" tone="#7C5CFF" />
            <MiniStat label="Temp. moteur" value={fmt(liveTelemetry?.temperature?.motor_c, 1)} unit="°C" tone="#FFB020" />
            <MiniStat label="Temp. roulement" value={fmt(liveTelemetry?.temperature?.bearing_c, 1)} unit="°C" tone="#FF4D4F" />
          </div>
          <Panel className="xl:col-span-2">
            <PanelHeader title="Comparaison Edge (TinyML) vs Serveur" subtitle="Accord des diagnostics, latences, écarts de santé" icon={<Gauge size={14} />} />
            <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
              <MiniStat label="Accord des classes" value={comparison ? `${(Number(comparison.agreement) * 100).toFixed(0)}` : '—'} unit="%" tone="#00E396" />
              <MiniStat label="Échantillons comparés" value={String(comparison?.samples ?? 0)} unit="" tone="#00E5FF" />
              <MiniStat label="Latence edge" value={fmt(edge?.inference_ms ?? comparison?.edge_ms, 1)} unit="ms" tone="#B9A6FF" />
              <MiniStat label="Latence serveur" value={fmt(inference?.inference_ms, 1)} unit="ms" tone="#00E5FF" />
            </div>
            <p className="mt-2.5 text-[10.5px] text-[rgb(var(--muted))]">
              Modèle embarqué : <span className="num">{String(edge?.model_version ?? '—')}</span> · modèle serveur :{' '}
              <span className="num">{String(inference?.model_version ?? '—')}</span>
            </p>
          </Panel>
        </div>
      )}

      {tab === 'prediction' && (
        <div className="grid grid-cols-1 gap-3 xl:grid-cols-[minmax(0,1fr)_360px]">
          <Panel>
            <PanelHeader
              title="Courbe de dégradation et projection"
              subtitle={`Historique de l'indice de santé + projection ${degradation.data?.per_day ? `(${fmt(degradation.data.per_day, 2)} pts/jour)` : ''} · seuil critique 30/100`}
              icon={<TrendingDown size={14} />}
            />
            <div className="h-[260px]">
              {degradation.isLoading ? <Skeleton className="h-full w-full" /> : (
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={projectionData} margin={{ top: 8, right: 12, bottom: 0, left: -18 }}>
                    <defs>
                      <linearGradient id="healthFill" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="0%" stopColor="#00E396" stopOpacity={0.35} />
                        <stop offset="100%" stopColor="#00E396" stopOpacity={0} />
                      </linearGradient>
                      <linearGradient id="projFill" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="0%" stopColor="#7C5CFF" stopOpacity={0.3} />
                        <stop offset="100%" stopColor="#7C5CFF" stopOpacity={0} />
                      </linearGradient>
                    </defs>
                    <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" vertical={false} />
                    <XAxis dataKey="time" tick={{ fontSize: 9, fill: '#94A3B8' }} stroke="rgba(255,255,255,0.12)" interval="preserveStartEnd" />
                    <YAxis domain={[0, 100]} tick={{ fontSize: 9, fill: '#94A3B8' }} stroke="rgba(255,255,255,0.12)" />
                    <Tooltip contentStyle={{ background: 'rgba(15,20,32,0.96)', border: '1px solid rgba(255,255,255,0.1)', borderRadius: 12, fontSize: 11 }} />
                    <ReferenceLine y={30} stroke="#FF4D4F" strokeDasharray="5 4" label={{ value: 'Seuil critique', fontSize: 9, fill: '#FF4D4F' }} />
                    <Area type="monotone" dataKey="health" stroke="#00E396" fill="url(#healthFill)" name="Historique" strokeWidth={1.8} connectNulls={false} />
                    <Area type="monotone" dataKey="projected" stroke="#7C5CFF" strokeDasharray="6 4" fill="url(#projFill)" name="Projection IA" strokeWidth={1.8} connectNulls={false} />
                    <Legend wrapperStyle={{ fontSize: 10 }} />
                  </AreaChart>
                </ResponsiveContainer>
              )}
            </div>
          </Panel>

          <div className="space-y-3">
            <Panel>
              <PanelHeader title="Durée de vie restante" subtitle="Estimation RUL + intervalle de confiance" icon={<Gauge size={14} />} />
              <p className="num text-3xl font-semibold text-[#B9A6FF]">{hoursHuman(Number(inference?.rul_hours ?? 0))}</p>
              <p className="mt-1 text-[11px] text-[rgb(var(--muted))]">
                Intervalle : {hoursHuman(Number(inference?.rul_confidence?.low ?? 0))} → {hoursHuman(Number(inference?.rul_confidence?.high ?? 0))}
              </p>
              <div className="mt-3">
                <RulBar
                  rulHours={Number(inference?.rul_hours ?? 0)}
                  low={inference?.rul_confidence?.low}
                  high={inference?.rul_confidence?.high}
                />
              </div>
              <div className="mt-3 space-y-1.5 text-[10.5px] text-[rgb(var(--muted))]">
                <p>Dégradation observée : <span className="num text-[rgb(var(--text))]">{fmt(degradation.data?.per_day, 2)} pts/jour</span></p>
                <p>Confiance : <span className="num text-[rgb(var(--text))]">{(Number(inference?.rul_hours ?? 0) / Math.max(Number(inference?.rul_confidence?.high ?? 1), 1) * 100).toFixed(0)} %</span></p>
                <p>Modèle : <span className="num text-[rgb(var(--text))]">{String(inference?.model_version ?? '—')}</span></p>
              </div>
            </Panel>

            <Panel>
              <PanelHeader title="Probabilités de défaut" subtitle="Classification multi-classes (RandomForest)" icon={<Brain size={14} />} />
              <div className="flex items-center justify-center">
                <Donut
                  slices={faultProbabilities.filter((p) => p.value > 0.005)}
                  size={158}
                  center={
                    <div className="text-center">
                      <p className="num text-[15px] font-semibold" style={{ color: FAULT_COLORS[faultClass] }}>
                        {(Number(inference?.fault_probabilities?.[faultClass] ?? 0) * 100).toFixed(0)} %
                      </p>
                      <p className="text-[9.5px] text-[rgb(var(--muted))]">{FAULT_LABELS[faultClass] ?? faultClass}</p>
                    </div>
                  }
                />
              </div>
              <div className="mt-2 space-y-1.5">
                {faultProbabilities.slice(0, 4).map((probability) => (
                  <div key={probability.key}>
                    <div className="flex items-center justify-between text-[10.5px]">
                      <span className="flex items-center gap-1.5">
                        <span className="h-2 w-2 rounded-full" style={{ background: probability.color }} />
                        {probability.label}
                      </span>
                      <span className="num">{(probability.value * 100).toFixed(1)} %</span>
                    </div>
                    <ProgressBar value={probability.value * 100} tone={probability.key === 'HEALTHY' ? 'green' : 'amber'} height={3} />
                  </div>
                ))}
              </div>
            </Panel>
          </div>
        </div>
      )}

      {tab === 'spectral' && (
        <div className="space-y-3">
          <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
            <FftSpectrum data={fft.data as any} loading={fft.isLoading} />
            <Spectrogram columns={spectro.data?.columns ?? []} />
          </div>
          <Panel>
            <PanelHeader title="Vitesse et synchronisme" subtitle="Rotation moteur, vitesse de bande et glissement estimé" icon={<Activity size={14} />} />
            <RealtimeChart motor={motor} windowSeconds={120} height={200} />
          </Panel>
        </div>
      )}

      {tab === 'xai' && (
        <div className="grid grid-cols-1 gap-3 xl:grid-cols-[380px_minmax(0,1fr)]">
          <Panel>
            <PanelHeader title="Contributions des features" subtitle="Poids relatif de chaque indicateur dans le diagnostic" icon={<Brain size={14} />} />
            <ContributionBars
              items={(xai.data?.contributions ?? []).map((item: any) => ({
                label: item.label ?? FAULT_LABELS[item.feature] ?? item.feature,
                value: Number(item.contribution),
                tone: '#7C5CFF',
              }))}
            />
            <p className="mt-3 rounded-xl border border-[rgba(124,92,255,0.25)] bg-[rgba(124,92,255,0.07)] p-2.5 text-[11px] leading-relaxed text-[#C9BBFF]">
              {xai.data?.narrative ?? 'Analyse en cours…'}
            </p>
          </Panel>

          <div className="space-y-3">
            <Panel>
              <PanelHeader title="Signature vibratoire vs référence saine" subtitle="Radar comparatif (baseline apprise au démarrage)" icon={<RadarIcon size={14} />} />
              <div className="h-[280px]">
                <ResponsiveContainer width="100%" height="100%">
                  <RadarChart
                    data={(radar.data?.current ?? []).map((point: any, index: number) => ({
                      axis: point.axis,
                      actuel: point.value,
                      reference: radar.data?.baseline?.[index]?.value ?? 0,
                    }))}
                    outerRadius="72%"
                  >
                    <PolarGrid stroke="rgba(255,255,255,0.09)" />
                    <PolarAngleAxis dataKey="axis" tick={{ fontSize: 9, fill: '#94A3B8' }} />
                    <PolarRadiusAxis domain={[0, 100]} tick={{ fontSize: 8, fill: '#64748B' }} />
                    <Radar name="Référence saine" dataKey="reference" stroke="#00E396" fill="#00E396" fillOpacity={0.14} />
                    <Radar name="Signature actuelle" dataKey="actuel" stroke="#7C5CFF" fill="#7C5CFF" fillOpacity={0.26} />
                    <Legend wrapperStyle={{ fontSize: 10 }} />
                    <Tooltip contentStyle={{ background: 'rgba(15,20,32,0.96)', border: '1px solid rgba(255,255,255,0.1)', borderRadius: 12, fontSize: 11 }} />
                  </RadarChart>
                </ResponsiveContainer>
              </div>
            </Panel>

            <Panel>
              <PanelHeader title="Corrélation courant / température" subtitle="Nuage de points issu de l'historique (24 h)" />
              <div className="h-[220px]">
                <ResponsiveContainer width="100%" height="100%">
                  <ScatterChart margin={{ top: 8, right: 12, bottom: 4, left: -18 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
                    <XAxis type="number" dataKey="current" name="Courant" unit=" A" tick={{ fontSize: 9, fill: '#94A3B8' }} stroke="rgba(255,255,255,0.12)" />
                    <YAxis type="number" dataKey="temperature" name="Température" unit=" °C" tick={{ fontSize: 9, fill: '#94A3B8' }} stroke="rgba(255,255,255,0.12)" />
                    <ZAxis type="number" dataKey="vibration" range={[12, 120]} name="Vibration" />
                    <Tooltip contentStyle={{ background: 'rgba(15,20,32,0.96)', border: '1px solid rgba(255,255,255,0.1)', borderRadius: 12, fontSize: 11 }} />
                    <Scatter data={historyData} fill="#00E5FF" fillOpacity={0.65} />
                  </ScatterChart>
                </ResponsiveContainer>
              </div>
            </Panel>
          </div>
        </div>
      )}

      {tab === 'history' && (
        <div className="space-y-3">
          <Panel>
            <PanelHeader
              title="Historique des anomalies"
              subtitle={`${anomalies.data?.items?.length ?? 0} diagnostics non sains sur 7 jours`}
              icon={<AlertTriangle size={14} />}
            />
            <div className="scroll-thin max-h-[420px] overflow-y-auto">
              <table className="w-full text-left text-[11px]">
                <thead className="sticky top-0 bg-[rgb(var(--surface))] text-[10px] uppercase tracking-wider text-[rgb(var(--muted))]">
                  <tr>
                    <th className="px-2 py-2">Horodatage</th>
                    <th className="px-2 py-2">Diagnostic</th>
                    <th className="px-2 py-2">Santé</th>
                    <th className="px-2 py-2">Anomalie</th>
                    <th className="px-2 py-2">RUL</th>
                    <th className="px-2 py-2">Explication principale</th>
                  </tr>
                </thead>
                <tbody>
                  {(anomalies.data?.items ?? []).map((item: any) => (
                    <tr key={item.ts} className="border-t border-white/[0.05] hover:bg-white/[0.03]">
                      <td className="num px-2 py-2">{dateTimeFr(item.ts)}</td>
                      <td className="px-2 py-2">
                        <Badge tone={item.fault_class === 'HEALTHY' ? 'green' : 'red'}>{FAULT_LABELS[item.fault_class] ?? item.fault_class}</Badge>
                      </td>
                      <td className="num px-2 py-2">{fmt(item.health_index, 1)}</td>
                      <td className="num px-2 py-2">{fmt(item.anomaly_score, 3)}</td>
                      <td className="num px-2 py-2">{hoursHuman(item.rul_hours)}</td>
                      <td className="px-2 py-2 text-[rgb(var(--muted))]">
                        {(item.explanation ?? []).slice(0, 2).map((e: any) => `${e.label ?? e.feature} ${(Number(e.contribution) * 100).toFixed(0)} %`).join(' · ') || '—'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {!anomalies.isLoading && (anomalies.data?.items?.length ?? 0) === 0 && (
                <EmptyState title="Aucune anomalie enregistrée" description="Le moteur a fonctionné dans sa signature nominale sur la période analysée." />
              )}
            </div>
          </Panel>

          <Panel>
            <PanelHeader title="Journal des interventions" subtitle="Entrées signées (SHA-256) — traçabilité maintenance" icon={<Wrench size={14} />} />
            <div className="space-y-2">
              {(interventions.data ?? []).map((intervention: any) => (
                <div key={intervention.id} className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-2.5">
                  <div className="flex items-center justify-between">
                    <p className="text-[11.5px] font-medium">{intervention.action}</p>
                    <span className="num text-[10px] text-[rgb(var(--muted))]">{dateTimeFr(intervention.ts)}</span>
                  </div>
                  <p className="mt-0.5 text-[10.5px] text-[rgb(var(--muted))]">
                    {intervention.technician} · {fmt(intervention.duration_min, 0)} min · {fmt(intervention.cost_eur, 0)} € ·{' '}
                    signature <span className="num">{String(intervention.signature ?? '').slice(0, 14)}</span>
                  </p>
                </div>
              ))}
              {!interventions.isLoading && (interventions.data ?? []).length === 0 && (
                <p className="text-[11.5px] text-[rgb(var(--muted))]">Aucune intervention enregistrée pour ce moteur.</p>
              )}
            </div>
          </Panel>
        </div>
      )}
    </div>
  );
}

function MiniStat({ label, value, unit, tone }: { label: string; value: string; unit?: string; tone: string }) {
  return (
    <div className="panel px-3 py-2.5">
      <p className="label">{label}</p>
      <p className="num mt-1 text-[16px] font-semibold" style={{ color: tone }}>
        {value} <span className="text-[10px] text-[rgb(var(--muted))]">{unit}</span>
      </p>
    </div>
  );
}
