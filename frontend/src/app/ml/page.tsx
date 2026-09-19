'use client';

/** Supervision MLOps : versions, métriques, drift, comparaison edge/serveur. */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Activity, BrainCircuit, Cpu, GitBranch, Layers, RefreshCw, TrendingUp, Upload } from 'lucide-react';
import * as React from 'react';
import {
  Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, Legend, Line, LineChart, ResponsiveContainer,
  Scatter, ScatterChart, Tooltip, XAxis, YAxis, ZAxis, ReferenceLine,
} from 'recharts';
import { Badge, Button, Panel, PanelHeader, ProgressBar, Skeleton, Stat } from '@/components/ui/primitives';
import { api } from '@/lib/api';
import { fmt, dateTimeFr } from '@/lib/format';

export default function MlOpsPage() {
  const client = useQueryClient();
  const models = useQuery({ queryKey: ['models'], queryFn: api.models });
  const metrics = useQuery({ queryKey: ['model-metrics'], queryFn: api.modelMetrics, retry: 0 });
  const modelHealth = useQuery({ queryKey: ['model-health'], queryFn: api.modelHealth, refetchInterval: 10000 });
  const edge = useQuery({ queryKey: ['edge-comparison'], queryFn: () => api.edgeComparison(), refetchInterval: 10000 });
  const candidates = useQuery({ queryKey: ['rollback-candidates'], queryFn: api.rollbackCandidates });

  const [confusionHover, setConfusionHover] = React.useState<string | null>(null);

  const sync = useMutation({
    mutationFn: () => api.syncModel(),
    onSuccess: () => { client.invalidateQueries({ queryKey: ['models'] }); client.invalidateQueries({ queryKey: ['model-metrics'] }); },
  });
  const reload = useMutation({
    mutationFn: () => api.reloadModel(),
    onSuccess: () => { client.invalidateQueries({ queryKey: ['models'] }); client.invalidateQueries({ queryKey: ['model-health'] }); },
  });
  const rollback = useMutation({
    mutationFn: () => api.rollback(),
    onSuccess: () => { client.invalidateQueries({ queryKey: ['models'] }); client.invalidateQueries({ queryKey: ['model-health'] }); },
  });

  const data = metrics.data;
  const classes: string[] = data?.classes ?? [];
  const matrix: number[][] = data?.confusion_matrix ?? [];
  const maxCell = Math.max(...matrix.flat().map(Number), 1);

  const active = models.data?.active ?? {};
  const fileMetrics = models.data?.metrics_file ?? {};

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h1 className="font-display text-[17px] font-semibold">Supervision des modèles (MLOps)</h1>
          <p className="text-[11.5px] text-[rgb(var(--muted))]">
            Modèle actif : <span className="num text-[rgb(var(--text))]">{String(active.type ?? '—')}</span> {String(active.version ?? '')} ·{' '}
            artefacts dans <span className="num">{String(models.data?.artifacts_dir ?? '—')}</span>
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button size="sm" onClick={() => reload.mutate()}><RefreshCw size={13} /> Recharger (hot-swap)</Button>
          <Button size="sm" variant="primary" onClick={() => sync.mutate()}><Upload size={13} /> Synchroniser les métriques</Button>
          <Button size="sm" variant="violet" onClick={() => rollback.mutate()} disabled={!candidates.data?.length}>
            <GitBranch size={13} /> Rollback artefact
          </Button>
        </div>
      </div>

      {!models.data?.artifact_present && (
        <Panel className="border-[rgba(255,176,32,0.35)] bg-[rgba(255,176,32,0.06)]">
          <p className="text-[11.5px] text-[#FFB020]">
            Aucun artefact entraîné détecté : le modèle physique informé (heuristique) est actif. Lancez
            <span className="num"> python -m ml.train</span> côté backend puis cliquez « Synchroniser les métriques ».
          </p>
        </Panel>
      )}

      {/* Cartes de métriques */}
      <div className="grid grid-cols-2 gap-2.5 md:grid-cols-4 xl:grid-cols-6">
        <Stat label="Accuracy" value={fmt(fileMetrics.accuracy ?? 0, 3)} tone="green" icon={<TrendingUp size={14} />} hint={`${fileMetrics.dataset_size ?? 0} échantillons`} />
        <Stat label="F1 macro" value={fmt(fileMetrics.f1_macro ?? 0, 3)} tone="cyan" icon={<Layers size={14} />} hint={`${classes.length} classes`} />
        <Stat label="ROC AUC" value={fmt(fileMetrics.roc_auc ?? 0, 3)} tone="violet" hint="sain vs défaut" />
        <Stat label="MAE du RUL" value={fmt(fileMetrics.rul_mae_hours ?? 0, 1)} unit="h" tone="amber" hint="erreur moyenne absolue" />
        <Stat label="Latence inférence" value={fmt(modelHealth.data?.avg_inference_ms, 1)} unit="ms" tone="cyan" hint={`p95 ${fmt(modelHealth.data?.p95_inference_ms, 1)} ms`} />
        <Stat label="Taille artefact" value={fmt(fileMetrics.artifact_kb ?? 0, 0)} unit="ko" tone="neutral" hint={`entraîné le ${dateTimeFr(fileMetrics.trained_at)}`} />
      </div>

      <div className="grid grid-cols-1 gap-3 xl:grid-cols-3">
        {/* Matrice de confusion */}
        <Panel>
          <PanelHeader title="Matrice de confusion" subtitle="Sur le jeu de test (25 % des données synthétiques)" icon={<BrainCircuit size={14} />} />
          {metrics.isLoading ? <Skeleton className="h-[280px] w-full" /> : (
            <>
              <div className="overflow-x-auto">
                <table className="border-separate border-spacing-1 text-[9.5px]">
                  <thead>
                    <tr>
                      <th />
                      {classes.map((cls) => (
                        <th key={cls} className="max-w-[54px] px-1 pb-1 text-left align-bottom text-[8.5px] text-[rgb(var(--muted))]">
                          {cls.replace('_', ' ')}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {matrix.map((row, rowIndex) => (
                      <tr key={rowIndex}>
                        <td className="pr-1 text-right text-[8.5px] text-[rgb(var(--muted))]">{classes[rowIndex]?.replace('_', ' ')}</td>
                        {row.map((value, columnIndex) => {
                          const intensity = Number(value) / maxCell;
                          const correct = rowIndex === columnIndex;
                          const key = `${rowIndex}-${columnIndex}`;
                          return (
                            <td
                              key={columnIndex}
                              onMouseEnter={() => setConfusionHover(key)}
                              onMouseLeave={() => setConfusionHover(null)}
                              className="h-8 w-8 cursor-default rounded-md text-center transition-transform hover:scale-105"
                              style={{
                                background: correct ? `rgba(0,227,150,${0.14 + intensity * 0.66})` : `rgba(255,77,79,${0.06 + intensity * 0.62})`,
                                color: intensity > 0.5 ? '#0A0E1A' : 'rgb(var(--text))',
                              }}
                              title={`Réel : ${classes[rowIndex]} → prédit : ${classes[columnIndex]} = ${value}`}
                            >
                              <span className="num text-[10px]">{value}</span>
                            </td>
                          );
                        })}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="mt-2 text-[10px] text-[rgb(var(--muted))]">
                {confusionHover ? 'Cellule survolée : ligne = classe réelle, colonne = classe prédite.' : 'Lignes : classe réelle · colonnes : classe prédite.'}
              </p>
            </>
          )}
        </Panel>

        {/* ROC / PR */}
        <Panel>
          <PanelHeader title="Courbes ROC & Precision-Recall" subtitle="Détection sain vs défaut" />
          <div className="h-[280px]">
            {metrics.isLoading ? <Skeleton className="h-full w-full" /> : (
              <ResponsiveContainer width="100%" height="100%">
                <LineChart margin={{ top: 8, right: 12, bottom: 0, left: -18 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
                  <XAxis dataKey="fpr" type="number" domain={[0, 1]} tick={{ fontSize: 9, fill: '#94A3B8' }} stroke="rgba(255,255,255,0.12)" />
                  <YAxis domain={[0, 1]} tick={{ fontSize: 9, fill: '#94A3B8' }} stroke="rgba(255,255,255,0.12)" />
                  <Tooltip contentStyle={{ background: 'rgba(15,20,32,0.96)', border: '1px solid rgba(255,255,255,0.1)', borderRadius: 12, fontSize: 11 }} />
                  <ReferenceLine x={0} y={0} stroke="rgba(255,255,255,0.2)" />
                  <Line data={data?.roc_curve ?? []} dataKey="tpr" stroke="#00E5FF" dot={false} strokeWidth={2} name="ROC (TPR vs FPR)" />
                  <Line
                    data={data?.pr_curve ?? []}
                    dataKey="recall"
                    stroke="#7C5CFF"
                    dot={false}
                    strokeWidth={2}
                    name="Recall (PR)"
                  />
                  <Legend wrapperStyle={{ fontSize: 10 }} />
                </LineChart>
              </ResponsiveContainer>
            )}
          </div>
        </Panel>

        {/* Importances */}
        <Panel>
          <PanelHeader title="Importance des features" subtitle="RandomForest — poids dans la classification" />
          <div className="h-[280px]">
            {metrics.isLoading ? <Skeleton className="h-full w-full" /> : (
              <ResponsiveContainer width="100%" height="100%">
                <BarChart
                  layout="vertical"
                  data={Object.entries(fileMetrics.feature_importances ?? {}).slice(0, 10).map(([feature, importance]) => ({ feature, importance }))}
                  margin={{ top: 4, right: 16, bottom: 0, left: 30 }}
                >
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" horizontal={false} />
                  <XAxis type="number" tick={{ fontSize: 9, fill: '#94A3B8' }} stroke="rgba(255,255,255,0.12)" />
                  <YAxis type="category" dataKey="feature" width={96} tick={{ fontSize: 8.5, fill: '#94A3B8' }} stroke="rgba(255,255,255,0.12)" />
                  <Tooltip contentStyle={{ background: 'rgba(15,20,32,0.96)', border: '1px solid rgba(255,255,255,0.1)', borderRadius: 12, fontSize: 11 }} />
                  <Bar dataKey="importance" fill="#7C5CFF" radius={[0, 4, 4, 0]} />
                </BarChart>
              </ResponsiveContainer>
            )}
          </div>
        </Panel>
      </div>

      {/* Drift */}
      <Panel>
        <PanelHeader
          title="Détection de dérive (data drift)"
          subtitle={`Entraînement (synthétique) vs production · verdict : ${data?.drift?.verdict ?? '—'}`}
          icon={<Activity size={14} />}
          right={<Badge tone={(data?.drift?.drifted_count ?? 0) === 0 ? 'green' : (data?.drift?.drifted_count ?? 0) <= 2 ? 'amber' : 'red'}>
            {data?.drift?.drifted_count ?? 0} feature(s) en dérive
          </Badge>}
        />
        {metrics.isLoading ? <Skeleton className="h-[180px] w-full" /> : (
          <div className="scroll-thin overflow-x-auto">
            <table className="w-full min-w-[720px] text-left text-[11px]">
              <thead className="text-[10px] uppercase tracking-wider text-[rgb(var(--muted))]">
                <tr>
                  <th className="px-2 py-2">Feature</th>
                  <th className="px-2 py-2">Moyenne entraînement</th>
                  <th className="px-2 py-2">Moyenne production</th>
                  <th className="px-2 py-2">Écart relatif</th>
                  <th className="px-2 py-2">Échantillons</th>
                  <th className="px-2 py-2">État</th>
                </tr>
              </thead>
              <tbody>
                {(data?.drift?.features ?? []).map((row: any) => (
                  <tr key={row.feature} className="border-t border-white/[0.05]">
                    <td className="num px-2 py-1.5">{row.feature}</td>
                    <td className="num px-2 py-1.5">{fmt(row.train_mean, 4)}</td>
                    <td className="num px-2 py-1.5">{fmt(row.prod_mean, 4)}</td>
                    <td className="num px-2 py-1.5">{fmt(row.drift, 3)}</td>
                    <td className="num px-2 py-1.5">{row.samples}</td>
                    <td className="px-2 py-1.5">
                      <Badge tone={row.status === 'ok' ? 'green' : row.status === 'watch' ? 'amber' : 'red'}>{row.status}</Badge>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>

      {/* Edge vs serveur + versions */}
      <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
        <Panel>
          <PanelHeader title="TinyML embarqué vs modèle serveur" subtitle="Accord des diagnostics, latences et écarts de santé" icon={<Cpu size={14} />} />
          <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
            {(['M1', 'M2'] as const).map((motor) => {
              const item = edge.data?.motors?.[motor] ?? {};
              return (
                <div key={motor} className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-2.5">
                  <div className="mb-1.5 flex items-center justify-between">
                    <p className="text-[12px] font-medium">{motor}</p>
                    <Badge tone={(item.agreement ?? 1) > 0.9 ? 'green' : 'amber'}>
                      accord {pctSafe(item.agreement)}
                    </Badge>
                  </div>
                  <div className="space-y-1 text-[10.5px] text-[rgb(var(--muted))]">
                    <p>Échantillons comparés : <span className="num text-[rgb(var(--text))]">{item.samples ?? 0}</span></p>
                    <p>Écart de santé moyen : <span className="num text-[rgb(var(--text))]">{fmt(item.mean_health_delta, 2)}</span></p>
                    <p>Latence edge / serveur : <span className="num text-[rgb(var(--text))]">{fmt(item.edge_latency_ms, 1)} / {fmt(item.server_latency_ms, 1)} ms</span></p>
                    <p>Anomalies edge / serveur : <span className="num text-[rgb(var(--text))]">{item.edge_only_anomalies ?? 0} / {item.server_only_anomalies ?? 0}</span></p>
                  </div>
                  <div className="mt-1.5 space-y-1">
                    <ProgressBar value={(item.agreement ?? 1) * 100} tone="green" height={4} label="Accord edge/serveur" />
                  </div>
                </div>
              );
            })}
          </div>
          <p className="mt-2.5 text-[10.5px] text-[rgb(var(--muted))]">
            Le TinyML int8 embarqué sert de détection de secours hors ligne ; le modèle serveur apporte la granularité
            (7 classes + RUL). L'accord des deux vaut validation croisée en continu.
          </p>
        </Panel>

        <Panel>
          <PanelHeader title="Registre des versions" subtitle="Historique, métriques et activation" icon={<GitBranch size={14} />} />
          <div className="space-y-2">
            {(models.data?.versions ?? []).map((version: any) => (
              <div key={version.id} className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-2.5">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div>
                    <p className="text-[12px] font-medium">
                      {version.name} <span className="num text-[#B9A6FF]">{version.version}</span>
                      {version.active && <Badge tone="green" className="ml-2">actif</Badge>}
                    </p>
                    <p className="text-[10px] text-[rgb(var(--muted))]">
                      {version.kind === 'edge' ? 'Embarqué (ESP32)' : 'Serveur'} · entraîné le {dateTimeFr(version.trained_at)} · {fmt(version.size_kb, 0)} ko
                    </p>
                  </div>
                  <div className="grid grid-cols-3 gap-3 text-[10px]">
                    <span>Acc. <span className="num text-[rgb(var(--text))]">{fmt(version.accuracy, 3)}</span></span>
                    <span>F1 <span className="num text-[rgb(var(--text))]">{fmt(version.f1, 3)}</span></span>
                    <span>RUL MAE <span className="num text-[rgb(var(--text))]">{fmt(version.rul_mae, 1)} h</span></span>
                  </div>
                </div>
                <p className="mt-1 text-[10px] text-[rgb(var(--muted))]">{version.notes}</p>
              </div>
            ))}
            {(models.data?.versions ?? []).length === 0 && <p className="text-[11.5px] text-[rgb(var(--muted))]">Aucune version enregistrée.</p>}
          </div>
          <div className="mt-3">
            <p className="label mb-1.5">Artefacts disponibles</p>
            <div className="space-y-1">
              {(candidates.data ?? []).map((candidate: any) => (
                <div key={candidate.file} className="flex items-center justify-between text-[10.5px]">
                  <span className="num">{candidate.file}</span>
                  <span className="text-[rgb(var(--muted))]">
                    {fmt(candidate.size_kb, 0)} ko · {dateTimeFr(candidate.modified)} {candidate.active && <Badge tone="green">actif</Badge>}
                  </span>
                </div>
              ))}
            </div>
          </div>
        </Panel>
      </div>
    </div>
  );
}

function pctSafe(value: number | undefined): string {
  if (value === undefined || value === null) return '—';
  return `${(value * 100).toFixed(0)} %`;
}
