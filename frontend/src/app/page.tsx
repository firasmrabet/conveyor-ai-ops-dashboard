'use client';

/** Vue d'ensemble « Control Room » : KPI, jumeau numérique 3D, moteurs, alertes, flux. */
import { useQuery } from '@tanstack/react-query';
import { Boxes, Cpu, Download, Radio, Sparkles, Wrench } from 'lucide-react';
import Link from 'next/link';
import * as React from 'react';
import { KpiBar } from '@/components/dashboard/KpiBar';
import { RealtimeChart } from '@/components/charts/RealtimeChart';
import { AlertFeed } from '@/components/motors/AlertFeed';
import { MotorCard } from '@/components/motors/MotorCard';
import { ConveyorScene } from '@/components/three/ConveyorScene';
import { Badge, Button, Panel, PanelHeader, ProgressBar, Skeleton } from '@/components/ui/primitives';
import { api } from '@/lib/api';
import { fmt, hoursHuman, pct } from '@/lib/format';
import { useLive } from '@/lib/store';
import type { MotorId } from '@/lib/types';

export default function ControlRoomPage() {
  const overview = useQuery({ queryKey: ['overview'], queryFn: api.overview, refetchInterval: 5000 });
  const status = useLive((s) => s.status);
  const inference = useLive((s) => s.inference);
  const latest = useLive((s) => s.latest);
  const prescriptions = overview.data?.prescriptions ?? [];
  const kpis = overview.data?.kpis;

  // Les valeurs temps réel priment sur le REST dès que le WebSocket est connecté
  const beltSpeed = Number(status.belt_mm_s ?? overview.data?.status?.belt_mm_s ?? 0);
  const running = Boolean(status.running ?? overview.data?.status?.running ?? true);
  const loadKg = Number(status.load_kg ?? overview.data?.status?.load_kg ?? 0.5);

  const motorVisuals = React.useMemo(
    () =>
      (['M1', 'M2'] as MotorId[]).map((id) => ({
        id,
        health: Number(inference[id]?.health_index ?? overview.data?.motors?.[id]?.inference?.health_index ?? 100),
        faultClass: String(inference[id]?.fault_class ?? 'HEALTHY'),
        anomaly: Number(inference[id]?.anomaly_score ?? 0),
        rpm: Number(latest[id]?.speed?.rpm ?? 0),
        running,
        label: id === 'M1' ? 'GAUCHE' : 'DROIT',
      })),
    [inference, latest, overview.data, running],
  );

  const liveKpis = React.useMemo(() => {
    if (!kpis) return undefined;
    const h1 = Number(inference.M1?.health_index ?? kpis.health_m1);
    const h2 = Number(inference.M2?.health_index ?? kpis.health_m2);
    return { ...kpis, health_m1: h1, health_m2: h2, global_health: (h1 + h2) / 2, line_state: running ? 'RUNNING' : 'STOPPED' };
  }, [kpis, inference, running]);

  return (
    <div className="space-y-3">
      {/* En-tête de page */}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h1 className="font-display text-[17px] font-semibold">Salle de contrôle</h1>
          <p className="text-[11.5px] text-[rgb(var(--muted))]">
            Convoyeur à bande bi-moteur · M1 GAUCHE / M2 DROIT · {overview.data?.model?.version ? `modèle ${String(overview.data.model.version)}` : 'modèle en chargement'}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone={running ? 'green' : 'red'} dot>{running ? 'Ligne en marche' : 'Ligne à l’arrêt'}</Badge>
          <Badge tone="cyan"><Radio size={11} /> {String(overview.data?.mqtt?.status ?? status.mode ?? 'SIMULATION')}</Badge>
          <a href={api.analyticsReportUrl(24)} target="_blank" rel="noreferrer">
            <Button size="sm" variant="violet"><Download size={13} /> Rapport PDF</Button>
          </a>
          <Link href="/simulator">
            <Button size="sm" variant="primary"><Sparkles size={13} /> Console de démo</Button>
          </Link>
        </div>
      </div>

      <KpiBar kpis={liveKpis} loading={overview.isLoading} />

      {/* Jumeau numérique + alertes */}
      <div className="grid grid-cols-1 gap-3 xl:grid-cols-[minmax(0,1fr)_360px]">
        <Panel padded={false} className="overflow-hidden">
          <div className="flex items-center justify-between border-b border-white/[0.06] px-4 py-2.5">
            <PanelHeader
              className="mb-0"
              title="Jumeau numérique 3D"
              subtitle="Rotation à la vitesse réelle · santé des moteurs en couleur · clic sur un moteur pour sa fiche"
              icon={<Boxes size={14} />}
            />
          </div>
          <div className="p-3">
            <ConveyorScene
              motors={motorVisuals}
              beltMmPerSecond={beltSpeed}
              running={running}
              loadKg={loadKg}
              height={330}
            />
          </div>
        </Panel>

        <div className="min-h-[520px]">
          <AlertFeed />
        </div>
      </div>

      {/* Cartes moteurs */}
      <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
        <MotorCard motor="M1" label="GAUCHE" side="Moteur pas à pas gauche" />
        <MotorCard motor="M2" label="DROIT" side="Moteur pas à pas droit" />
      </div>

      {/* Flux temps réel + maintenance prescriptive */}
      <div className="grid grid-cols-1 gap-3 xl:grid-cols-[minmax(0,1fr)_360px]">
        <div className="space-y-3">
          <RealtimeChart motor="M1" windowSeconds={60} height={205} />
          <RealtimeChart motor="M2" windowSeconds={60} height={205} />
        </div>

        <div className="space-y-3">
          <Panel>
            <PanelHeader
              title="Maintenance prescriptive"
              subtitle="Recommandations générées par le pipeline IA"
              icon={<Wrench size={14} />}
              right={<Link href="/alerts"><Button size="sm" variant="ghost">Détails</Button></Link>}
            />
            <div className="space-y-2">
              {overview.isLoading && <Skeleton className="h-20 w-full" />}
              {prescriptions.map((prescription) => (
                <div key={`${prescription.motor_id}-${prescription.title}`} className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-2.5">
                  <div className="flex items-start justify-between gap-2">
                    <div>
                      <p className="text-[12px] font-medium">{prescription.title}</p>
                      <p className="text-[10px] text-[rgb(var(--muted))]">{prescription.motor_id} · {prescription.action}</p>
                    </div>
                    <Badge tone={prescription.priority === 'CRITICAL' ? 'red' : prescription.priority === 'HIGH' ? 'amber' : prescription.priority === 'MEDIUM' ? 'violet' : 'green'}>
                      {prescription.priority}
                    </Badge>
                  </div>
                  <p className="mt-1.5 text-[10.5px] text-[rgb(var(--muted))]">{prescription.rationale}</p>
                  <div className="mt-2 grid grid-cols-3 gap-2 text-[10px]">
                    <span>Délai <span className="num text-[rgb(var(--text))]">{prescription.due_in_days} j</span></span>
                    <span>Durée <span className="num text-[rgb(var(--text))]">{prescription.estimated_hours} h</span></span>
                    <span>Confiance <span className="num text-[rgb(var(--text))]">{(prescription.confidence * 100).toFixed(0)} %</span></span>
                  </div>
                  {prescription.parts.length > 0 && (
                    <p className="mt-1.5 text-[10px] text-[rgb(var(--muted))]">Pièces : {prescription.parts.join(', ')}</p>
                  )}
                </div>
              ))}
              {!overview.isLoading && prescriptions.length === 0 && (
                <p className="text-[11.5px] text-[rgb(var(--muted))]">Aucune action requise pour le moment.</p>
              )}
            </div>
          </Panel>

          <Panel>
            <PanelHeader title="Santé de la ligne" subtitle="Comparaison M1 / M2 et état des équipements" icon={<Cpu size={14} />} />
            {(['M1', 'M2'] as MotorId[]).map((id) => {
              const health = Number(inference[id]?.health_index ?? overview.data?.motors?.[id]?.inference?.health_index ?? 0);
              return (
                <div key={id} className="mb-2.5">
                  <div className="mb-1 flex items-center justify-between text-[11px]">
                    <span>{id} · {String(inference[id]?.fault_class ?? 'HEALTHY')}</span>
                    <span className="num">{fmt(health, 0)}/100</span>
                  </div>
                  <ProgressBar value={health} tone={health >= 80 ? 'green' : health >= 60 ? 'amber' : 'red'} label={`Santé ${id}`} />
                </div>
              );
            })}
            <div className="mt-3 grid grid-cols-2 gap-2 text-[10.5px]">
              <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-2">
                <p className="label">RUL M1</p>
                <p className="num text-[12px] text-[#B9A6FF]">{hoursHuman(Number(inference.M1?.rul_hours ?? 0))}</p>
              </div>
              <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-2">
                <p className="label">RUL M2</p>
                <p className="num text-[12px] text-[#B9A6FF]">{hoursHuman(Number(inference.M2?.rul_hours ?? 0))}</p>
              </div>
              <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-2">
                <p className="label">Accord edge/serveur</p>
                <p className="num text-[12px] text-[#00E396]">
                  {pct(Number(overview.data?.edge_comparison?.M1?.agreement ?? 1) * 100, 0)}
                </p>
              </div>
              <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-2">
                <p className="label">Heures de service</p>
                <p className="num text-[12px]">{fmt(kpis?.running_hours_total, 0)} h</p>
              </div>
            </div>
          </Panel>
        </div>
      </div>
    </div>
  );
}
