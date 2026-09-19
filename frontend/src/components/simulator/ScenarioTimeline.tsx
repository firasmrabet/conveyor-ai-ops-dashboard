'use client';

/**
 * Timeline du scénario de soutenance « Démo 3 minutes ».
 * Les jalons reprennent exactement le script exécuté par le backend
 * (`app/services/simulator.py::_demo_script`) : 12 étapes sur 174 s.
 */
import { CheckCircle2, Circle, Flag, Loader2, Square, Timer } from 'lucide-react';
import * as React from 'react';
import { motion } from 'framer-motion';
import { Badge, Button, Panel, ProgressBar } from '@/components/ui/primitives';

interface Step {
  t: number;
  label: string;
  kind: 'nominal' | 'fault' | 'stop' | 'recover';
}

export const DEMO_STEPS: Step[] = [
  { t: 0, label: 'Remise à zéro : ligne nominale', kind: 'nominal' },
  { t: 8, label: 'Montée en cadence (150 rpm)', kind: 'nominal' },
  { t: 22, label: 'M1 : déséquilibre léger (45 %)', kind: 'fault' },
  { t: 40, label: 'M1 : déséquilibre aggravé (85 %)', kind: 'fault' },
  { t: 60, label: 'M2 : défaut de roulement (55 %)', kind: 'fault' },
  { t: 85, label: 'Charge lourde 1,6 kg — surcharge', kind: 'fault' },
  { t: 105, label: 'M2 : surchauffe roulement (70 %)', kind: 'fault' },
  { t: 125, label: 'M1 : glissement de bande (60 %)', kind: 'fault' },
  { t: 145, label: "M2 : blocage moteur — arrêt d'urgence", kind: 'stop' },
  { t: 158, label: 'Arrêt de ligne (sécurité)', kind: 'stop' },
  { t: 165, label: 'Maintenance : remise en état nominale', kind: 'recover' },
  { t: 172, label: 'Redémarrage — retour à la normale', kind: 'recover' },
];

const KIND_COLORS: Record<Step['kind'], string> = {
  nominal: '#00E396', fault: '#FFB020', stop: '#FF4D4F', recover: '#7C5CFF',
};

interface Props {
  scenario: { name: string; current_step: number; steps: number; elapsed_s: number; duration_s: number; label: string; done?: boolean } | null;
  onStart: () => void;
  onStop: () => void;
  pending?: boolean;
}

export function ScenarioTimeline({ scenario, onStart, onStop, pending }: Props) {
  const elapsed = scenario?.elapsed_s ?? 0;
  const duration = scenario?.duration_s ?? 174;
  const running = Boolean(scenario && !scenario.done);

  return (
    <Panel className={running ? 'border-[rgba(124,92,255,0.35)] bg-[rgba(124,92,255,0.05)]' : undefined}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="flex items-center gap-2 font-display text-[13.5px] font-semibold">
            <Flag size={14} className="text-[#B9A6FF]" /> Scénario de soutenance « Démo 3 minutes »
          </h3>
          <p className="mt-0.5 text-[11px] text-[rgb(var(--muted))]">
            12 jalons scriptés côté serveur : défauts progressifs, arrêt d'urgence, maintenance puis retour au nominal.
            Idéal pour dérouler toute la chaîne (capteurs → IA → alertes → maintenance) devant un jury.
          </p>
        </div>
        <div className="flex items-center gap-2">
          {running ? (
            <Button size="sm" variant="danger" onClick={onStop} disabled={pending}><Square size={12} /> Stopper</Button>
          ) : (
            <Button size="sm" variant="violet" onClick={onStart} disabled={pending}>
              {pending ? <><Loader2 size={12} className="animate-spin" /> Démarrage…</> : <><Timer size={12} /> Lancer la démo</>}
            </Button>
          )}
        </div>
      </div>

      {scenario && (
        <>
          <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
            <p className="text-[11.5px]">
              <span className="text-[#C9BBFF]">Étape {Math.min(scenario.current_step + 1, scenario.steps)}/{scenario.steps}</span>
              <span className="text-[rgb(var(--muted))]"> · {scenario.label}</span>
            </p>
            <div className="flex items-center gap-2">
              <Badge tone={scenario.done ? 'green' : 'violet'}>{scenario.done ? 'terminé' : 'en cours'}</Badge>
              <span className="num text-[10.5px] text-[rgb(var(--muted))]">{Math.round(elapsed)} s / {duration} s</span>
            </div>
          </div>
          <div className="mt-2">
            <ProgressBar value={(elapsed / (duration || 1)) * 100} tone="violet" height={5} label="Avancement du scénario" />
          </div>
        </>
      )}

      {/* Frise des jalons */}
      <div className="scroll-thin mt-3 overflow-x-auto pb-1">
        <div className="flex min-w-[720px] items-stretch gap-1.5">
          {DEMO_STEPS.map((step, index) => {
            const done = elapsed >= step.t;
            const current = scenario?.current_step === index;
            return (
              <motion.div
                key={`${step.t}-${index}`}
                animate={{ scale: current ? 1.02 : 1 }}
                className={`w-[92px] shrink-0 rounded-lg border p-1.5 text-[9.5px] leading-tight transition ${
                  current
                    ? 'border-[rgba(0,229,255,0.55)] bg-[rgba(0,229,255,0.09)]'
                    : done && scenario
                      ? 'border-white/[0.09] bg-white/[0.03]'
                      : 'border-white/[0.06] bg-transparent'
                }`}
              >
                <div className="mb-1 flex items-center justify-between">
                  <span className="num text-[9px]" style={{ color: KIND_COLORS[step.kind] }}>T+{step.t}s</span>
                  {current ? <Loader2 size={9} className="animate-spin text-[#00E5FF]" />
                    : done && scenario ? <CheckCircle2 size={9} className="text-[#00E396]" />
                      : <Circle size={9} className="text-[rgb(var(--muted))]" />}
                </div>
                <p className="text-[rgb(var(--text))]/85">{step.label}</p>
              </motion.div>
            );
          })}
        </div>
      </div>
    </Panel>
  );
}
