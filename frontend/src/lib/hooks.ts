'use client';

import * as React from 'react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { useLive } from './store';
import type { MotorId } from './types';

/** Valeur animée (compteurs KPI). Respecte la préférence « réduire les animations ». */
export function useCountUp(target: number, durationMs = 800): number {
  const [value, setValue] = useState(target);
  const previous = useRef(target);
  const reduceMotion = useLive((s) => s.reduceMotion);

  useEffect(() => {
    if (reduceMotion || !Number.isFinite(target)) {
      setValue(target);
      previous.current = target;
      return;
    }
    const from = previous.current;
    const delta = target - from;
    if (Math.abs(delta) < 1e-9) return;
    let frame = 0;
    const startedAt = performance.now();
    const tick = (now: number) => {
      const progress = Math.min(1, (now - startedAt) / durationMs);
      const eased = 1 - Math.pow(1 - progress, 3);
      setValue(from + delta * eased);
      if (progress < 1) frame = requestAnimationFrame(tick);
      else previous.current = target;
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [target, durationMs, reduceMotion]);

  return value;
}

/**
 * Horloge temps réel (mise à jour chaque seconde).
 *
 * Renvoie `null` tant que le composant n'est pas monté dans le navigateur : la page
 * étant pré-rendue au build, l'heure du serveur ne peut pas correspondre à celle du
 * client. Rendre une valeur nulle d'abord garantit que le HTML statique et le premier
 * rendu client sont identiques — sinon React échoue à l'hydratation et toute
 * l'application reste inerte (aucune connexion WebSocket, aucun graphique).
 * Les appelants affichent un gabarit (--:--:--) en attendant la première seconde.
 */
export function useClock(): Date | null {
  const [now, setNow] = useState<Date | null>(null);
  useEffect(() => {
    setNow(new Date());
    const id = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(id);
  }, []);
  return now;
}

/** Vrai une fois le composant monté côté navigateur (garde anti-hydratation). */
export function useMounted(): boolean {
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);
  return mounted;
}

/** Télémétrie d'un moteur sous forme de séries prêtes pour les graphes. */
export function useMotorSeries(motor: MotorId, windowSeconds = 60) {
  const telemetry = useLive((s) => s.telemetry[motor]);
  return useMemo(() => {
    const cutoff = Date.now() - windowSeconds * 1000;
    const points = telemetry.filter((t) => t.ts >= cutoff);
    return points.map((t) => ({
      ts: t.ts,
      time: new Date(t.ts).toLocaleTimeString('fr-FR', { hour12: false }),
      vibRms: t.vibration?.rms ?? 0,
      vibAx: t.vibration?.ax ?? 0,
      vibAy: t.vibration?.ay ?? 0,
      vibAz: t.vibration?.az ?? 0,
      kurtosis: t.vibration?.kurtosis ?? 0,
      crest: t.vibration?.crest_factor ?? 0,
      current: t.current?.rms_a ?? 0,
      power: t.current?.power_w ?? 0,
      motorTemp: t.temperature?.motor_c ?? 0,
      bearingTemp: t.temperature?.bearing_c ?? 0,
      ambientTemp: t.temperature?.ambient_c ?? 0,
      rpm: t.speed?.rpm ?? 0,
      belt: t.speed?.belt_mm_s ?? 0,
      spl: t.acoustic?.spl_db ?? 0,
      state: t.state,
    }));
  }, [telemetry, windowSeconds]);
}

/** Points d'alerte critique pour déclencher une micro-interaction (shake). */
export function useCriticalPulse(ms = 1200): boolean {
  const alerts = useLive((s) => s.alerts);
  const [pulse, setPulse] = useState(false);
  const lastId = useRef<number>(0);

  useEffect(() => {
    const latest = alerts[0];
    if (latest && latest.severity === 'CRITICAL' && latest.id !== lastId.current) {
      lastId.current = latest.id;
      setPulse(true);
      const id = setTimeout(() => setPulse(false), ms);
      return () => clearTimeout(id);
    }
  }, [alerts, ms]);

  return pulse;
}

/**
 * Respecte le réglage utilisateur « réduire les animations » tout en le
 * persistant côté navigateur (clé `conveyor-reduced-motion`) et en l'appliquant
 * à la racine du document via l'attribut `data-reduced-motion`.
 */
export function useReducedMotion(): [boolean, (next: boolean) => void] {
  const [reduced, setReduced] = React.useState(false);

  React.useEffect(() => {
    const stored = window.localStorage.getItem('conveyor-reduced-motion');
    const initial = stored === null
      ? window.matchMedia('(prefers-reduced-motion: reduce)').matches
      : stored === 'true';
    setReduced(initial);
    document.documentElement.dataset.reducedMotion = initial ? 'true' : 'false';
  }, []);

  const update = React.useCallback((next: boolean) => {
    setReduced(next);
    window.localStorage.setItem('conveyor-reduced-motion', String(next));
    document.documentElement.dataset.reducedMotion = next ? 'true' : 'false';
  }, []);

  return [reduced, update];
}
