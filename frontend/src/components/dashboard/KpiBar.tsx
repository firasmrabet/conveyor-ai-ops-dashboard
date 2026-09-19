'use client';

/** Bandeau de KPI animés de la salle de contrôle. */
import { Activity, AlertTriangle, Bolt, Boxes, Gauge, HeartPulse, Timer, Wrench } from 'lucide-react';
import { fmt, pct } from '@/lib/format';
import { useCountUp } from '@/lib/hooks';
import { Stat } from '@/components/ui/primitives';
import type { Kpis } from '@/lib/types';

export function KpiBar({ kpis, loading }: { kpis?: Kpis; loading: boolean }) {
  const health = useCountUp(kpis?.global_health ?? 0);
  const availability = useCountUp(kpis?.availability_pct ?? 0);
  const power = useCountUp(kpis?.total_power_w ?? 0);
  const defects = useCountUp(kpis?.defects_24h ?? 0);
  const alerts = useCountUp(kpis?.active_alerts ?? 0);
  const parcels = useCountUp(kpis?.parcels_per_min ?? 0);

  if (loading) {
    return (
      <div className="grid grid-cols-2 gap-2.5 md:grid-cols-4 xl:grid-cols-7">
        {Array.from({ length: 7 }).map((_, index) => (
          <div key={index} className="skeleton h-[86px] rounded-2xl" />
        ))}
      </div>
    );
  }

  const running = kpis?.line_state === 'RUNNING';
  const healthTone = (health ?? 100) >= 80 ? 'green' : (health ?? 100) >= 60 ? 'amber' : 'red';

  return (
    <div className="grid grid-cols-2 gap-2.5 md:grid-cols-4 xl:grid-cols-7">
      <Stat
        label="État de la ligne"
        value={running ? 'EN MARCHE' : 'À L’ARRÊT'}
        tone={running ? 'green' : 'red'}
        icon={<Activity size={15} />}
        hint={`${fmt(kpis?.belt_mm_s, 1)} mm/s de bande`}
      />
      <Stat label="Disponibilité" value={availability.toFixed(1)} unit="%" tone="cyan" icon={<Timer size={15} />} hint="30 derniers jours" />
      <Stat
        label="Santé globale"
        value={health.toFixed(0)} unit="/100"
        tone={healthTone as 'green' | 'amber' | 'red'}
        icon={<HeartPulse size={15} />}
        hint={`M1 ${fmt(kpis?.health_m1, 0)} · M2 ${fmt(kpis?.health_m2, 0)}`}
      />
      <Stat
        label="Alertes actives"
        value={alerts.toFixed(0)}
        tone={(alerts ?? 0) > 0 ? 'amber' : 'green'}
        icon={<AlertTriangle size={15} />}
        hint={`${kpis?.open_work_orders ?? 0} ordres de travail ouverts`}
      />
      <Stat label="Défauts détectés 24 h" value={defects.toFixed(0)} tone="violet" icon={<Gauge size={15} />} hint="anomalies + diagnostics IA" />
      <Stat label="Consommation" value={power.toFixed(1)} unit="W" tone="cyan" icon={<Bolt size={15} />} hint="M1 + M2 cumulés" />
      <Stat label="Débit estimé" value={parcels.toFixed(0)} unit="colis/min" tone="neutral" icon={<Boxes size={15} />} hint={`${kpis?.interventions_total ?? 0} interventions enregistrées`} />
    </div>
  );
}
