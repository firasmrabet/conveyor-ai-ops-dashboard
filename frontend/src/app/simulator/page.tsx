'use client';

/**
 * Console de démonstration : contrôle du banc, injection de défauts instrumentés
 * et scénario de soutenance scripté. Tous les effets sont visibles en quelques
 * secondes sur le Control Room, la fiche moteur, les alertes et la maintenance.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Gauge, RotateCcw, Thermometer } from 'lucide-react';
import * as React from 'react';
import { ControlSliders, type BenchControls } from '@/components/simulator/ControlSliders';
import { FaultGrid, type SimulatorFault } from '@/components/simulator/FaultGrid';
import { ScenarioTimeline } from '@/components/simulator/ScenarioTimeline';
import { Sparkline } from '@/components/charts/gauges';
import { Badge, Button, Panel, PanelHeader, Segmented, Skeleton } from '@/components/ui/primitives';
import { api } from '@/lib/api';
import { FAULT_LABELS, fmt } from '@/lib/format';
import { useMotorSeries } from '@/lib/hooks';
import { useLive } from '@/lib/store';
import type { MotorId } from '@/lib/types';

export default function SimulatorPage() {
  const client = useQueryClient();
  const state = useQuery({ queryKey: ['sim-state'], queryFn: api.simulatorState, refetchInterval: 2000 });
  const scenarioLive = useLive((s) => s.scenario);

  const [motor, setMotor] = React.useState<MotorId>('M1');
  const [intensity, setIntensity] = React.useState(70);
  const [degradation, setDegradation] = React.useState(1.5);
  const [bench, setBench] = React.useState<BenchControls>({ speed_rpm: 120, load_kg: 0.6, parcels_per_min: 12 });

  const refresh = () => client.invalidateQueries({ queryKey: ['sim-state'] });

  const control = useMutation({ mutationFn: (body: Record<string, unknown>) => api.simulatorControl(body), onSuccess: refresh });
  const inject = useMutation({ mutationFn: (body: Record<string, unknown>) => api.injectFault(body), onSuccess: refresh });
  const clear = useMutation({ mutationFn: () => api.clearFaults(), onSuccess: refresh });
  const runScenario = useMutation({ mutationFn: () => api.runScenario(), onSuccess: refresh });
  const stopScenario = useMutation({ mutationFn: () => api.stopScenario(), onSuccess: refresh });
  const resetData = useMutation({ mutationFn: () => api.resetDemoData(), onSuccess: refresh });

  const data = state.data;
  const running = Boolean(data?.running);
  const faults: SimulatorFault[] = data?.fault_catalog ?? [];
  // Le scénario courant vient du WebSocket (temps réel) et retombe sur le REST si absent
  const scenario = scenarioLive ?? data?.scenario ?? null;
  const motorState = data?.motors?.[motor];
  const activeFault = motorState?.fault && motorState.fault !== 'NONE' ? motorState.fault : undefined;

  const series1 = useMotorSeries('M1', 60);
  const series2 = useMotorSeries('M2', 60);

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h1 className="font-display text-[17px] font-semibold">Console de démonstration</h1>
          <p className="text-[11.5px] text-[rgb(var(--muted))]">
            Mode <span className="num">{String(data?.mode ?? 'SIMULATION')}</span> · tick{' '}
            <span className="num">{data?.tick_hz ?? 8} Hz</span> · simulateur{' '}
            <span className={running ? 'text-[#00E396]' : 'text-[#FFB020]'}>{running ? 'en marche' : 'à l\'arrêt'}</span>
            {data?.available === false && ' · passez MODE=SIMULATION dans .env pour activer la console'}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button variant={running ? 'danger' : 'success'} onClick={() => control.mutate({ running: !running })}>
            {running ? 'Arrêter la ligne' : 'Démarrer la ligne'}
          </Button>
          <Button variant="ghost" onClick={() => resetData.mutate()} disabled={resetData.isPending}
                  title="Vide télémétrie, alertes, interventions et ordres de travail de démonstration">
            <RotateCcw size={13} /> {resetData.isPending ? 'Réinitialisation…' : 'Réinitialiser les données de démo'}
          </Button>
        </div>
      </div>

      <ScenarioTimeline
        scenario={scenario}
        pending={runScenario.isPending || stopScenario.isPending}
        onStart={() => runScenario.mutate()}
        onStop={() => stopScenario.mutate()}
      />

      <div className="grid grid-cols-1 gap-3 xl:grid-cols-[340px_minmax(0,1fr)]">
        <div className="space-y-3">
          <ControlSliders
            value={bench}
            onChange={setBench}
            running={running}
            state={data}
            onCommit={(patch) => {
              if ('ramp_s' in patch && Object.keys(patch).length === 1) {
                control.mutate({ running: !running });
                return;
              }
              control.mutate({ ...patch });
            }}
          />

          <Panel>
            <PanelHeader title="Physique interne des moteurs" subtitle="Grandeurs calculées par le modèle de simulation" icon={<Gauge size={14} />} />
            <Segmented
              ariaLabel="Moteur inspecté" size="sm" value={motor} onChange={setMotor}
              options={[{ value: 'M1' as MotorId, label: 'M1 — GAUCHE' }, { value: 'M2' as MotorId, label: 'M2 — DROIT' }]}
            />
            {motorState ? (
              <div className="mt-2.5 grid grid-cols-2 gap-2 text-[10.5px]">
                <Kv label="Défaut actif" value={motorState.fault === 'NONE' ? 'aucun' : FAULT_LABELS[motorState.fault] ?? motorState.fault} />
                <Kv label="Intensité effective" value={`${fmt(motorState.effective_intensity, 1)} %`} />
                <Kv label="Vitesse" value={`${fmt(motorState.rpm, 1)} rpm`} />
                <Kv label="Usure cumulée" value={`${fmt((motorState.wear ?? 0) * 100, 1)} %`} />
                <Kv label="Temp. moteur" value={`${fmt(motorState.temp_motor, 1)} °C`} />
                <Kv label="Temp. roulement" value={`${fmt(motorState.temp_bearing, 1)} °C`} />
              </div>
            ) : <Skeleton className="mt-2.5 h-[96px] w-full" />}
            <div className="mt-3 grid grid-cols-2 gap-3">
              <div>
                <p className="label mb-1">M1 — vibration (60 s)</p>
                <Sparkline data={series1.map((point) => point.vibRms)} width={135} height={34} color="#00E5FF" />
              </div>
              <div>
                <p className="label mb-1">M2 — vibration (60 s)</p>
                <Sparkline data={series2.map((point) => point.vibRms)} width={135} height={34} color="#7C5CFF" />
              </div>
            </div>
          </Panel>
        </div>

        <div className="space-y-3">
          <FaultGrid
            faults={faults}
            intensity={intensity}
            degradation={degradation}
            activeFault={activeFault}
            motorLabel={motor}
            pending={inject.isPending || clear.isPending}
            onIntensityChange={setIntensity}
            onDegradationChange={setDegradation}
            onInject={(fault) => inject.mutate({ motor_id: motor, fault: fault.id, intensity, degradation_rate: degradation })}
            onClear={() => clear.mutate()}
          />

          <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
            <Panel>
              <PanelHeader title="Effets attendus par défaut" subtitle="Signature physique que la démo doit montrer" icon={<Thermometer size={14} />} />
              <ul className="space-y-1.5 text-[11px] text-[rgb(var(--muted))]">
                <li>• <span className="text-[rgb(var(--text))]">Roulement usé</span> → kurtosis et haute fréquence en hausse, chocs BPFO, +5 à 10 °C sur le roulement</li>
                <li>• <span className="text-[rgb(var(--text))]">Déséquilibre</span> → harmonique 1× dominante, vibration globale en hausse</li>
                <li>• <span className="text-[rgb(var(--text))]">Désalignement</span> → harmonique 2× marquée + courant plus élevé</li>
                <li>• <span className="text-[rgb(var(--text))]">Glissement de bande</span> → vitesse qui décroche, écart vitesse moteur / vitesse bande</li>
                <li>• <span className="text-[rgb(var(--text))]">Surcharge</span> → courant et puissance élevés, échauffement corps moteur</li>
                <li>• <span className="text-[rgb(var(--text))]">Surchauffe</span> → dérive thermique roulement puis moteur</li>
                <li>• <span className="text-[rgb(var(--text))]">Perte de capteur</span> → signal figé, incohérence de variance détectée</li>
                <li>• <span className="text-[rgb(var(--text))]">Blocage moteur</span> → vitesse nulle avec courant de blocage, arrêt d'urgence</li>
              </ul>
            </Panel>

            <Panel>
              <PanelHeader
                title="Journal d'injection"
                subtitle="10 derniers ordres appliqués au banc"
                right={<Badge tone="amber">{data?.fault_log?.length ?? 0} action(s)</Badge>}
              />
              <div className="space-y-1.5">
                {(data?.fault_log ?? []).slice().reverse().map((entry: any, index: number) => (
                  <div key={`${entry.ts}-${index}`} className="flex items-center justify-between text-[10.5px]">
                    <span className="text-[rgb(var(--muted))]">
                      {new Date(entry.ts).toLocaleTimeString('fr-FR')} · {entry.motor_id ?? '—'}
                    </span>
                    <Badge tone={entry.action === 'clear' ? 'green' : 'amber'}>
                      {entry.action === 'clear' ? 'remise à zéro' : `${FAULT_LABELS[entry.fault] ?? entry.fault} · ${entry.intensity ?? '—'} %`}
                    </Badge>
                  </div>
                ))}
                {(data?.fault_log ?? []).length === 0 && (
                  <p className="text-[11px] text-[rgb(var(--muted))]">
                    Aucune injection pour l'instant — cliquez sur un défaut ci-dessus ou lancez la démo 3 minutes.
                  </p>
                )}
              </div>
            </Panel>
          </div>
        </div>
      </div>
    </div>
  );
}

function Kv({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-white/[0.06] bg-white/[0.02] p-2">
      <p className="label">{label}</p>
      <p className="num mt-0.5 text-[11.5px]">{value}</p>
    </div>
  );
}
