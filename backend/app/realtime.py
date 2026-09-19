"""Hub temps réel : buffers circulaires + diffusion WebSocket.

- RingBuffer : dernières N valeurs par moteur (pas de fuite mémoire).
- RealtimeHub : abonnements clients, broadcast ciblé, métriques de flux.
- Redis optionnel : si activé, les dernières valeurs sont aussi exposées via
  pub/sub (utile pour un déploiement multi-instances).
"""
from __future__ import annotations

import asyncio
import json
import time
from collections import deque
from typing import Any, Deque, Dict, List, Optional, Set

from fastapi import WebSocket

from .config import settings


class RingBuffer:
    """Buffer circulaire typé, thread-safe côté asyncio (usage mono-loop)."""

    def __init__(self, size: int, key: str = "ts") -> None:
        self.size = size
        self.key = key
        self._data: Deque[Dict[str, Any]] = deque(maxlen=size)
        self._lock = asyncio.Lock()

    async def push(self, item: Dict[str, Any]) -> None:
        async with self._lock:
            self._data.append(item)

    def push_nowait(self, item: Dict[str, Any]) -> None:
        self._data.append(item)

    def snapshot(self, limit: Optional[int] = None, since_ts: Optional[int] = None) -> List[Dict[str, Any]]:
        data = list(self._data)
        if since_ts is not None:
            data = [d for d in data if d.get(self.key, 0) >= since_ts]
        if limit is not None:
            data = data[-limit:]
        return data

    def last(self) -> Optional[Dict[str, Any]]:
        return self._data[-1] if self._data else None

    def window(self, window_s: float) -> List[Dict[str, Any]]:
        cutoff = int((time.time() - window_s) * 1000)
        return [d for d in self._data if d.get(self.key, 0) >= cutoff]

    def __len__(self) -> int:  # pragma: no cover
        return len(self._data)


class RealtimeHub:
    """Gestion des connexions WebSocket + buffers."""

    def __init__(self) -> None:
        self.clients: Set[WebSocket] = set()
        self.client_meta: Dict[WebSocket, Dict[str, Any]] = {}
        self.started_at = time.time()
        # Dernières valeurs : télémétrie (10 s à 10 Hz), features (1 h à 1 Hz), inférences (1 h)
        self.telemetry: Dict[str, RingBuffer] = {m: RingBuffer(600) for m in ("M1", "M2")}
        self.features: Dict[str, RingBuffer] = {m: RingBuffer(7200) for m in ("M1", "M2")}
        self.inference: Dict[str, RingBuffer] = {m: RingBuffer(7200) for m in ("M1", "M2")}
        self.edge_inference: Dict[str, RingBuffer] = {m: RingBuffer(3600) for m in ("M1", "M2")}
        self.alerts: RingBuffer = RingBuffer(500)
        self.status: Dict[str, Any] = {
            "running": True,
            "speed_rpm": 120.0,
            "load_kg": 0.5,
            "parcels_per_min": 12.0,
            "mode": settings.mode,
            "belt_mm_s": 85.0,
        }
        self.devices: Dict[str, Any] = {}
        # Compteurs de flux (messages/s calculés par le scheduler)
        self.counters: Dict[str, int] = {"telemetry": 0, "inference": 0, "alerts": 0, "bytes": 0}
        self.rates: Dict[str, float] = {"telemetry_per_s": 0.0, "inference_per_s": 0.0, "throughput_kb_s": 0.0}
        self._rate_ts = time.time()
        self.edge_comparison: Dict[str, Dict[str, Any]] = {
            m: {"agreement": 1.0, "samples": 0, "edge_ms": 3.2, "server_ms": 8.1, "mismatch": 0}
            for m in ("M1", "M2")
        }

    # ---------------- Connexions ----------------
    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self.clients.add(ws)
        self.client_meta[ws] = {"connected_at": time.time(), "motors": ["M1", "M2"], "alive": True}
        # Snapshot initial : le client n'attend pas la première trame pour peindre l'UI
        await self._send(ws, {"type": "snapshot", "data": self.snapshot()})

    def disconnect(self, ws: WebSocket) -> None:
        self.clients.discard(ws)
        self.client_meta.pop(ws, None)

    async def _send(self, ws: WebSocket, message: Dict[str, Any]) -> None:
        try:
            await ws.send_text(json.dumps(message, default=str))
        except Exception:
            self.disconnect(ws)

    async def broadcast(self, message: Dict[str, Any], motors: Optional[List[str]] = None) -> None:
        if not self.clients:
            return
        payload = json.dumps(message, default=str)
        dead: List[WebSocket] = []
        for ws in list(self.clients):
            meta = self.client_meta.get(ws, {})
            if motors and meta.get("motors") and not set(motors) & set(meta["motors"]):
                continue
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)

    def snapshot(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "motors": {
                m: {
                    "telemetry": self.telemetry[m].snapshot(limit=120),
                    "features": self.features[m].snapshot(limit=120),
                    "inference": self.inference[m].snapshot(limit=60),
                    "edge_inference": self.edge_inference[m].snapshot(limit=30),
                }
                for m in ("M1", "M2")
            },
            "alerts": self.alerts.snapshot(limit=30),
            "devices": self.devices,
            "rates": self.rates,
            "edge_comparison": self.edge_comparison,
            "server_time": int(time.time() * 1000),
        }

    def update_subscription(self, ws: WebSocket, motors: List[str]) -> None:
        if ws in self.client_meta:
            self.client_meta[ws]["motors"] = [m for m in motors if m in ("M1", "M2")] or ["M1", "M2"]

    def tick_counters(self, n_telemetry: int = 0, n_inference: int = 0, n_alerts: int = 0, n_bytes: int = 0) -> None:
        self.counters["telemetry"] += n_telemetry
        self.counters["inference"] += n_inference
        self.counters["alerts"] += n_alerts
        self.counters["bytes"] += n_bytes

    def compute_rates(self) -> None:
        now = time.time()
        dt = max(now - self._rate_ts, 1e-6)
        self.rates["telemetry_per_s"] = round(self.counters["telemetry"] / dt, 2)
        self.rates["inference_per_s"] = round(self.counters["inference"] / dt, 2)
        self.rates["throughput_kb_s"] = round(self.counters["bytes"] / dt / 1024, 2)
        self.counters.update({"telemetry": 0, "inference": 0, "alerts": 0, "bytes": 0})
        self._rate_ts = now


hub = RealtimeHub()


# --------------------------------------------------------------------------
#  Redis optionnel (cache/pub-sub)
# --------------------------------------------------------------------------
class OptionalRedis:
    """Wrapper minimal : no-op si Redis désactivé ou indisponible."""

    def __init__(self) -> None:
        self.client = None
        self.available = False

    async def connect(self) -> None:
        if not settings.redis_enabled:
            return
        try:
            import redis.asyncio as aioredis  # type: ignore

            self.client = aioredis.from_url(settings.redis_url, encoding="utf-8", decode_responses=True)
            await self.client.ping()
            self.available = True
        except Exception:
            self.available = False

    async def close(self) -> None:
        if self.client:
            try:
                await self.client.close()
            except Exception:
                pass

    async def cache_set(self, key: str, value: Any, ttl: int = 120) -> None:
        if not self.available:
            return
        try:
            await self.client.set(key, json.dumps(value, default=str), ex=ttl)
        except Exception:
            self.available = False

    async def cache_get(self, key: str) -> Optional[Any]:
        if not self.available:
            return None
        try:
            raw = await self.client.get(key)
            return json.loads(raw) if raw else None
        except Exception:
            return None


redis_client = OptionalRedis()
