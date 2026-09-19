'use client';

/**
 * État global temps réel (Zustand) alimenté par le WebSocket.
 *
 * Anti-fuite mémoire : tous les buffers sont des tableaux circulaires bornés.
 * Throttling : les mises à jour sont regroupées à 10 Hz maximum (requestAnimationFrame
 * + fenêtre de 100 ms) — indispensable pour rester fluide avec 2 moteurs à 8-10 Hz
 * et un re-render React parcimonieux.
 */

import { create } from 'zustand';
import { getSocket, type SocketStatus } from './socket';
import type { AlertItem, Inference, MotorId, Telemetry } from './types';

const TELEMETRY_BUFFER = 300;   // 30 s à 10 Hz
const INFERENCE_BUFFER = 600;
const ALERT_BUFFER = 120;
const TOPIC_BUFFER = 240;       // spectrogramme

export interface ScenarioState {
  name: string;
  label: string;
  current_step: number;
  steps: number;
  elapsed_s: number;
  duration_s: number;
  done?: boolean;
}

interface LiveState {
  // Données
  latest: Record<MotorId, Partial<Telemetry>>;
  inference: Record<MotorId, Partial<Inference>>;
  edge: Record<MotorId, Record<string, any>>;
  telemetry: Record<MotorId, Telemetry[]>;
  inferenceHistory: Record<MotorId, Partial<Inference>[]>;
  features: Record<MotorId, { ts: number; features: Record<string, number> }[]>;
  alerts: AlertItem[];
  status: Record<string, any>;
  rates: Record<string, number>;
  summary: Record<string, any>;
  scenario: ScenarioState | null;
  edgeComparison: Record<string, any>;

  // Connexion / contrôle
  connection: SocketStatus;
  latencyMs: number;
  attempts: number;
  paused: boolean;
  reduceMotion: boolean;
  cursorTs: number;              // position du curseur temporel (graphes figés)

  // Actions
  init: () => void;
  setPaused: (paused: boolean) => void;
  setReduceMotion: (value: boolean) => void;
  pushAlert: (alert: AlertItem) => void;
  hydrate: (payload: Record<string, any>) => void;
}

const emptyMotor = <T,>(value: T): Record<MotorId, T> => ({ M1: value, M2: value });

/** Réducteur de message WebSocket (pur, testable). */
function applyMessage(state: LiveState, message: { type: string; motor_id?: MotorId; data?: any; comparison?: any }): Partial<LiveState> {
  const motor = message.motor_id;
  switch (message.type) {
    case 'telemetry': {
      if (!motor) return {};
      const list = [...(state.telemetry[motor] || []), message.data].slice(-TELEMETRY_BUFFER);
      return { telemetry: { ...state.telemetry, [motor]: list }, latest: { ...state.latest, [motor]: message.data } };
    }
    case 'inference': {
      if (!motor) return {};
      const history = [...(state.inferenceHistory[motor] || []), message.data].slice(-INFERENCE_BUFFER);
      return { inferenceHistory: { ...state.inferenceHistory, [motor]: history }, inference: { ...state.inference, [motor]: message.data } };
    }
    case 'features': {
      if (!motor) return {};
      const list = [...(state.features[motor] || []), message.data].slice(-TOPIC_BUFFER);
      return { features: { ...state.features, [motor]: list } };
    }
    case 'edge_inference': {
      if (!motor) return {};
      return {
        edge: { ...state.edge, [motor]: message.data },
        edgeComparison: message.comparison ? { ...state.edgeComparison, [motor]: message.comparison } : state.edgeComparison,
      };
    }
    case 'alert': {
      const alert = message.data as AlertItem;
      return { alerts: [alert, ...state.alerts.filter((a) => a.id !== alert.id)].slice(0, ALERT_BUFFER) };
    }
    case 'alert_update': {
      const updated = message.data as AlertItem;
      return { alerts: state.alerts.map((a) => (a.id === updated.id ? { ...a, ...updated } : a)) };
    }
    case 'status':
      return { status: { ...state.status, ...message.data } };
    case 'rates':
      return { rates: { ...state.rates, ...message.data } };
    case 'summary':
      return { summary: message.data, status: message.data.status ?? state.status, rates: message.data.rates ?? state.rates };
    case 'scenario':
      return { scenario: message.data };
    case 'snapshot': {
      const data = message.data || {};
      const telemetry: Record<MotorId, Telemetry[]> = { M1: [], M2: [] };
      const inferenceHistory: Record<MotorId, Partial<Inference>[]> = { M1: [], M2: [] };
      (['M1', 'M2'] as MotorId[]).forEach((id) => {
        telemetry[id] = (data.motors?.[id]?.telemetry || []).slice(-TELEMETRY_BUFFER);
        inferenceHistory[id] = (data.motors?.[id]?.inference || []).slice(-INFERENCE_BUFFER);
      });
      return {
        telemetry,
        inferenceHistory,
        latest: { M1: telemetry.M1.at(-1) ?? {}, M2: telemetry.M2.at(-1) ?? {} },
        inference: {
          M1: inferenceHistory.M1.at(-1) ?? {},
          M2: inferenceHistory.M2.at(-1) ?? {},
        },
        alerts: (data.alerts || []).slice(0, ALERT_BUFFER),
        status: data.status || state.status,
        rates: data.rates || state.rates,
        edgeComparison: data.edge_comparison || {},
      };
    }
    default:
      return {};
  }
}

export const useLive = create<LiveState>((set, get) => ({
  latest: emptyMotor<Partial<Telemetry>>({}),
  inference: emptyMotor<Partial<Inference>>({}),
  edge: emptyMotor<Record<string, any>>({}),
  telemetry: emptyMotor<Telemetry[]>([]),
  inferenceHistory: emptyMotor<Partial<Inference>[]>([]),
  features: emptyMotor<{ ts: number; features: Record<string, number> }[]>([]),
  alerts: [],
  status: {},
  rates: {},
  summary: {},
  scenario: null,
  edgeComparison: {},
  connection: 'connecting',
  latencyMs: 0,
  attempts: 0,
  paused: false,
  reduceMotion: false,
  cursorTs: 0,

  init: () => {
    if (typeof window === 'undefined') return;
    const socket = getSocket();
    // Regroupement des messages : au plus 10 rafraîchissements/seconde
    let pending: Partial<LiveState> = {};
    let scheduled = false;
    const flush = () => {
      scheduled = false;
      if (Object.keys(pending).length === 0) return;
      if (!get().paused) set(pending);
      pending = {};
    };
    const schedule = () => {
      if (scheduled) return;
      scheduled = true;
      window.setTimeout(flush, 100);
    };

    socket.onMessage((message) => {
      // Le réducteur travaille sur l'état DÉJÀ accumulé dans la file d'attente
      // (`{ ...get(), ...pending }`) : chaque message empile ses points à la suite.
      // En repartant de `get()` seul, deux trames de moteurs différents arrivées dans
      // la même fenêtre de 100 ms s'écrasaient mutuellement et le graphique temps réel
      // ne conservait qu'environ 2 points/s au lieu des 8 Hz publiés par le banc.
      const accumulated = { ...get(), ...pending } as LiveState;
      pending = { ...pending, ...applyMessage(accumulated, message) };
      schedule();
    });
    socket.onStatus((connection, meta) => set({ connection, latencyMs: meta.latencyMs, attempts: meta.attempts }));
    if (socket.status === 'connecting') socket.connect();
    socket.subscribeTo(['M1', 'M2']);
  },

  setPaused: (paused) => set({ paused, cursorTs: paused ? Date.now() : 0 }),
  setReduceMotion: (reduceMotion) => {
    set({ reduceMotion });
    if (typeof document !== 'undefined') document.documentElement.classList.toggle('reduce-motion', reduceMotion);
  },
  pushAlert: (alert) => set((state) => ({ alerts: [alert, ...state.alerts.filter((a) => a.id !== alert.id)].slice(0, ALERT_BUFFER) })),
  hydrate: (payload) => set(applyMessage(get(), { type: 'snapshot', data: payload })),
}));

/** Sélecteur pratique : dernière trame de télémétrie d'un moteur. */
export const selectLatest = (motor: MotorId) => (state: LiveState): Partial<Telemetry> => state.latest[motor] ?? {};
export const selectInference = (motor: MotorId) => (state: LiveState): Partial<Inference> => state.inference[motor] ?? {};
