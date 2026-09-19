'use client';

/**
 * Client WebSocket temps réel.
 *
 * Choix technique documenté : le backend FastAPI expose un WebSocket natif
 * (`/ws/telemetry`) — socket.io apporterait une couche protocolaire inutile ici.
 * Le client implémente ce que socket.io apporte habituellement :
 *  - reconnexion automatique avec backoff exponentiel + jitter (max 15 s),
 *  - heartbeat applicatif (ping/pong) pour détecter les connexions mortes,
 *  - file d'attente des messages sortants pendant la déconnexion,
 *  - état de connexion observable (latence, statut, tentatives).
 */

export type SocketStatus = 'connecting' | 'live' | 'reconnecting' | 'offline';
export type SocketHandler = (message: { type: string; motor_id?: 'M1' | 'M2'; data?: any; comparison?: any }) => void;
export type StatusHandler = (status: SocketStatus, meta: { latencyMs: number; attempts: number }) => void;

export class RealtimeSocket {
  private socket: WebSocket | null = null;
  private handlers = new Set<SocketHandler>();
  private statusHandlers = new Set<StatusHandler>();
  private attempts = 0;
  private queue: string[] = [];
  private heartbeat: ReturnType<typeof setInterval> | null = null;
  private lastPingAt = 0;
  private closedByUser = false;
  status: SocketStatus = 'connecting';
  latencyMs = 0;

  constructor(private readonly url: string) {}

  connect(): void {
    this.closedByUser = false;
    this.setStatus(this.attempts === 0 ? 'connecting' : 'reconnecting');
    try {
      this.socket = new WebSocket(this.url);
    } catch {
      this.scheduleReconnect();
      return;
    }

    this.socket.onopen = () => {
      this.attempts = 0;
      this.setStatus('live');
      // Vidage de la file : un socket remplacé par une reconnexion peut encore être
      // en CONNECTING, ce qui ferait lever une exception au premier envoi.
      this.queue.splice(0).forEach((message) => this.rawSend(message));
      this.startHeartbeat();
    };

    this.socket.onmessage = (event) => {
      let message: any;
      try {
        message = JSON.parse(event.data);
      } catch {
        return;
      }
      if (message.type === 'pong') {
        this.latencyMs = Math.max(1, Math.round(performance.now() - this.lastPingAt));
        this.pushStatus();
        return;
      }
      if (message.type === 'ping') {
        this.send({ type: 'pong' });
        return;
      }
      this.handlers.forEach((handler) => {
        try {
          handler(message);
        } catch (error) {
          console.error('[ws] handler error', error);
        }
      });
    };

    this.socket.onclose = () => {
      this.stopHeartbeat();
      if (!this.closedByUser) this.scheduleReconnect();
    };

    this.socket.onerror = () => {
      this.socket?.close();
    };
  }

  private scheduleReconnect(): void {
    this.attempts += 1;
    this.setStatus('reconnecting');
    // Backoff exponentiel plafonné à 15 s, avec jitter pour éviter les tempêtes de reconnexion
    const delay = Math.min(15000, 500 * 2 ** Math.min(this.attempts, 5)) * (0.75 + Math.random() * 0.5);
    window.setTimeout(() => this.connect(), delay);
  }

  private startHeartbeat(): void {
    this.stopHeartbeat();
    this.heartbeat = setInterval(() => {
      this.lastPingAt = performance.now();
      this.send({ type: 'ping' });
    }, 5000);
  }

  private stopHeartbeat(): void {
    if (this.heartbeat) clearInterval(this.heartbeat);
    this.heartbeat = null;
  }

  /** Envoi brut protégé : utilisé pour les messages issus de la file d'attente. */
  private rawSend(message: string): void {
    try {
      if (this.socket?.readyState === WebSocket.OPEN) this.socket.send(message);
    } catch {
      // Le socket a été fermé entre le test et l'envoi : la file reprendra la main.
    }
  }

  send(payload: Record<string, unknown>): void {
    const message = JSON.stringify(payload);
    if (this.socket?.readyState === WebSocket.OPEN) {
      this.rawSend(message);
    } else if (this.queue.length < 50) {
      this.queue.push(message);
    }
  }

  subscribeTo(motors: ('M1' | 'M2')[]): void {
    this.send({ type: 'subscribe', motors });
  }

  requestSnapshot(): void {
    this.send({ type: 'snapshot' });
  }

  onMessage(handler: SocketHandler): () => void {
    this.handlers.add(handler);
    return () => this.handlers.delete(handler);
  }

  onStatus(handler: StatusHandler): () => void {
    this.statusHandlers.add(handler);
    handler(this.status, { latencyMs: this.latencyMs, attempts: this.attempts });
    return () => this.statusHandlers.delete(handler);
  }

  private setStatus(status: SocketStatus): void {
    this.status = status;
    this.pushStatus();
  }

  private pushStatus(): void {
    this.statusHandlers.forEach((handler) => handler(this.status, { latencyMs: this.latencyMs, attempts: this.attempts }));
  }

  close(): void {
    this.closedByUser = true;
    this.stopHeartbeat();
    this.socket?.close();
    this.setStatus('offline');
  }
}

let instance: RealtimeSocket | null = null;

/** Singleton : une seule connexion WebSocket pour toute l'application. */
export function getSocket(): RealtimeSocket {
  if (typeof window === 'undefined') throw new Error('Socket navigateur uniquement');
  if (!instance) {
    // Par défaut : même origine (le serveur Next proxifie /ws → backend FastAPI).
    // NEXT_PUBLIC_BACKEND_WS permet de viser directement le backend si le
    // déploiement n'assure pas le passage des connexions WebSocket par le proxy.
    const explicit = process.env.NEXT_PUBLIC_BACKEND_WS;
    if (explicit) {
      instance = new RealtimeSocket(`${explicit.replace(/\/$/, '')}/ws/telemetry`);
      return instance;
    }
    const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws';
    instance = new RealtimeSocket(`${protocol}://${window.location.host}/ws/telemetry`);
  }
  return instance;
}
