'use client';

/** Passerelles ESP32 : état, diagnostic réseau, sécurité et commandes à distance. */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Activity, Cpu, KeyRound, Lock, RefreshCw, Router, ShieldAlert, ShieldCheck,
  Terminal, Upload, Wifi, Power, Gauge, Network, RadioTower,
} from 'lucide-react';
import * as React from 'react';
import {
  Area, AreaChart, CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';
import { Badge, Button, EmptyState, Panel, PanelHeader, ProgressBar, Skeleton } from '@/components/ui/primitives';
import { api } from '@/lib/api';
import { dateTimeFr, fmt, timeFr } from '@/lib/format';

const COMMANDS = [
  { action: 'reboot', label: 'Redémarrer', icon: <Power size={13} />, tone: 'ghost' as const, help: 'Redémarre la passerelle (heartbeat 5 s après retour)' },
  { action: 'recalibrate', label: 'Recalibrer', icon: <Gauge size={13} />, tone: 'ghost' as const, help: 'Offset accéléromètre + zéro capteur de courant' },
  { action: 'ota_update', label: 'OTA', icon: <Upload size={13} />, tone: 'primary' as const, help: 'Mise à jour firmware par canal OTA (stable/beta)' },
  { action: 'rotate_key', label: 'Rotation clé', icon: <KeyRound size={13} />, tone: 'ghost' as const, help: 'Renouvelle la clé du device et émet un jeton MQTT' },
  { action: 'test_network', label: 'Test réseau', icon: <Wifi size={13} />, tone: 'ghost' as const, help: 'Mesure latence, RSSI et pertes' },
];

export default function DevicesPage() {
  const client = useQueryClient();
  const devices = useQuery({ queryKey: ['devices'], queryFn: api.devices, refetchInterval: 5000 });
  const network = useQuery({ queryKey: ['device-network'], queryFn: () => api.deviceNetwork(15), refetchInterval: 15000 });
  const security = useQuery({ queryKey: ['device-security'], queryFn: api.deviceSecurity, refetchInterval: 15000 });
  const logs = useQuery({ queryKey: ['device-logs'], queryFn: () => api.deviceLogs('esp32-conveyor-01', 60), refetchInterval: 15000 });

  const [hz, setHz] = React.useState(10);
  const [otaVersion, setOtaVersion] = React.useState('1.1.0');
  const [lastResult, setLastResult] = React.useState<string>('');

  const sendCommand = useMutation({
    mutationFn: ({ deviceId, action, payload }: { deviceId: string; action: string; payload?: Record<string, unknown> }) =>
      api.deviceCommand(deviceId, action, payload ?? {}),
    onSuccess: (result: any) => {
      setLastResult(`${new Date().toLocaleTimeString('fr-FR')} — ${result.action} : ${result.message ?? 'commande transmise'}`);
      client.invalidateQueries({ queryKey: ['devices'] });
      client.invalidateQueries({ queryKey: ['device-logs'] });
    },
  });
  const simulate = useMutation({
    mutationFn: ({ deviceId, event }: { deviceId: string; event: string }) => api.simulateNetworkEvent(deviceId, event),
    onSuccess: () => { client.invalidateQueries({ queryKey: ['devices'] }); client.invalidateQueries({ queryKey: ['device-logs'] }); client.invalidateQueries({ queryKey: ['device-security'] }); },
  });
  const resetTamper = useMutation({
    mutationFn: (deviceId: string) => api.resetTamper(deviceId),
    onSuccess: () => { client.invalidateQueries({ queryKey: ['devices'] }); client.invalidateQueries({ queryKey: ['device-security'] }); },
  });

  const list = devices.data?.devices ?? [];
  const observed = devices.data?.observed ?? [];
  const mqtt = devices.data?.mqtt ?? {};

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h1 className="font-display text-[17px] font-semibold">Passerelles &amp; réseau</h1>
          <p className="text-[11.5px] text-[rgb(var(--muted))]">
            {list.length} équipement(s) enregistré(s) · pont MQTT <span className="num">{String(mqtt.status ?? '—')}</span> ·{' '}
            <span className="num">{fmt(mqtt.messages ?? 0, 0)}</span> messages reçus
          </p>
        </div>
        <div className="flex gap-2">
          <Button size="sm" onClick={() => { devices.refetch(); network.refetch(); security.refetch(); }}>
            <RefreshCw size={13} /> Rafraîchir
          </Button>
          <a href={api.fullCsvUrl()} target="_blank" rel="noreferrer">
            <Button size="sm" variant="ghost"><Terminal size={13} /> Export complet</Button>
          </a>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-3 xl:grid-cols-[minmax(0,1fr)_360px]">
        {/* Liste des devices */}
        <div className="space-y-3">
          {devices.isLoading && <Skeleton className="h-[240px] w-full" />}
          {list.map((device: any) => (
            <Panel key={device.device_id}>
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="flex items-start gap-3">
                  <span className={`rounded-xl border p-2 ${device.status === 'ONLINE' ? 'border-[rgba(0,227,150,0.3)] bg-[rgba(0,227,150,0.1)] text-[#00E396]' : device.status === 'DEGRADED' ? 'border-[rgba(255,176,32,0.3)] bg-[rgba(255,176,32,0.1)] text-[#FFB020]' : 'border-[rgba(255,77,79,0.3)] bg-[rgba(255,77,79,0.1)] text-[#FF4D4F]'}`}>
                    <Cpu size={20} />
                  </span>
                  <div>
                    <p className="flex items-center gap-2 text-[13px] font-medium">
                      {device.name || device.device_id}
                      <Badge tone={device.status === 'ONLINE' ? 'green' : device.status === 'DEGRADED' ? 'amber' : 'red'}>{device.status}</Badge>
                      {device.tamper_detected && <Badge tone="red"><ShieldAlert size={10} /> tampering</Badge>}
                    </p>
                    <p className="num mt-0.5 text-[10.5px] text-[rgb(var(--muted))]">
                      {device.device_id} · {device.ip} · {device.mac} · firmware {device.firmware} ({device.ota_channel})
                    </p>
                  </div>
                </div>
                <div className="grid grid-cols-2 gap-x-5 gap-y-1 text-[10.5px] sm:grid-cols-3">
                  <Kv label="RSSI" value={`${device.rssi_dbm} dBm`} />
                  <Kv label="Latence" value={`${fmt(device.latency_ms, 1)} ms`} />
                  <Kv label="Pertes" value={`${fmt(device.packet_loss_pct, 2)} %`} />
                  <Kv label="Échantillonnage" value={`${fmt(device.sampling_hz, 0)} Hz`} />
                  <Kv label="Débit" value={`${fmt(device.msgs_per_s, 1)} msg/s`} />
                  <Kv label="Uptime" value={fmt(device.uptime_s / 3600, 1) + ' h'} />
                  <Kv label="Alimentation" value={device.power_source} />
                  <Kv label="Batterie" value={`${fmt(device.battery_pct, 0)} %`} />
                  <Kv label="Heartbeat" value={dateTimeFr(device.last_heartbeat)} />
                </div>
              </div>

              <div className="mt-3 flex flex-wrap gap-1.5">
                {COMMANDS.map((command) => (
                  <Button
                    key={command.action}
                    size="sm"
                    variant={command.tone}
                    title={command.help}
                    onClick={() => sendCommand.mutate({
                      deviceId: device.device_id,
                      action: command.action,
                      payload: command.action === 'set_sampling' ? { hz } : command.action === 'ota_update' ? { version: otaVersion } : {},
                    })}
                  >
                    {command.icon} {command.label}
                  </Button>
                ))}
              </div>
            </Panel>
          ))}

          {observed.length > 0 && (
            <Panel>
              <PanelHeader title="Passerelles observées sur le bus MQTT" subtitle="Détectées via heartbeat sans être enregistrées en base" icon={<RadioTower size={14} />} />
              <div className="space-y-1.5">
                {observed.map((device: any) => (
                  <div key={device.device_id} className="flex items-center justify-between text-[11px]">
                    <span className="num">{device.device_id}</span>
                    <span className="text-[rgb(var(--muted))]">
                      vu il y a {fmt((Date.now() - device.last_seen) / 1000, 0)} s · {device.messages} msg · {device.firmware}
                    </span>
                  </div>
                ))}
              </div>
            </Panel>
          )}

          {!devices.isLoading && list.length === 0 && (
            <EmptyState title="Aucun équipement enregistré" description="Lancez le seed ou connectez un ESP32 réel sur le broker MQTT." />
          )}

          {/* Diagnostic réseau */}
          <Panel>
            <PanelHeader
              title="Diagnostic réseau"
              subtitle="Latence broker ↔ passerelle, débit et pertes sur 15 minutes"
              icon={<Network size={14} />}
              right={<Badge tone={(network.data?.summary?.avg_loss_pct ?? 0) < 1 ? 'green' : 'amber'}>
                pertes moy. {fmt(network.data?.summary?.avg_loss_pct, 2)} %
              </Badge>}
            />
            <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
              <div className="h-[170px]">
                {network.isLoading ? <Skeleton className="h-full w-full" /> : (
                  <ResponsiveContainer width="100%" height="100%">
                    <AreaChart data={network.data?.samples ?? []} margin={{ top: 6, right: 10, bottom: 0, left: -20 }}>
                      <defs>
                        <linearGradient id="latencyFill" x1="0" y1="0" x2="0" y2="1">
                          <stop offset="0%" stopColor="#00E5FF" stopOpacity={0.45} />
                          <stop offset="100%" stopColor="#00E5FF" stopOpacity={0} />
                        </linearGradient>
                      </defs>
                      <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" vertical={false} />
                      <XAxis dataKey="ts" tick={{ fontSize: 9, fill: '#94A3B8' }} tickFormatter={(value: number) => timeFr(value)} stroke="rgba(255,255,255,0.12)" interval="preserveStartEnd" />
                      <YAxis tick={{ fontSize: 9, fill: '#94A3B8' }} stroke="rgba(255,255,255,0.12)" />
                      <Tooltip contentStyle={{ background: 'rgba(15,20,32,0.96)', border: '1px solid rgba(255,255,255,0.1)', borderRadius: 12, fontSize: 11 }} labelFormatter={(value: number) => dateTimeFr(value)} />
                      <Area type="monotone" dataKey="latency_ms" stroke="#00E5FF" strokeWidth={1.6} fill="url(#latencyFill)" name="Latence (ms)" />
                    </AreaChart>
                  </ResponsiveContainer>
                )}
              </div>
              <div className="h-[170px]">
                {network.isLoading ? <Skeleton className="h-full w-full" /> : (
                  <ResponsiveContainer width="100%" height="100%">
                    <LineChart data={network.data?.samples ?? []} margin={{ top: 6, right: 10, bottom: 0, left: -20 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" vertical={false} />
                      <XAxis dataKey="ts" tick={{ fontSize: 9, fill: '#94A3B8' }} tickFormatter={(value: number) => timeFr(value)} stroke="rgba(255,255,255,0.12)" interval="preserveStartEnd" />
                      <YAxis yAxisId="left" tick={{ fontSize: 9, fill: '#94A3B8' }} stroke="rgba(255,255,255,0.12)" />
                      <YAxis yAxisId="right" orientation="right" domain={[-100, -40]} tick={{ fontSize: 9, fill: '#94A3B8' }} stroke="rgba(255,255,255,0.12)" />
                      <Tooltip contentStyle={{ background: 'rgba(15,20,32,0.96)', border: '1px solid rgba(255,255,255,0.1)', borderRadius: 12, fontSize: 11 }} labelFormatter={(value: number) => dateTimeFr(value)} />
                      <Line yAxisId="left" type="monotone" dataKey="messages_per_s" stroke="#00E396" dot={false} strokeWidth={1.5} name="Messages/s" />
                      <Line yAxisId="right" type="monotone" dataKey="rssi_dbm" stroke="#7C5CFF" dot={false} strokeWidth={1.5} name="RSSI (dBm)" />
                    </LineChart>
                  </ResponsiveContainer>
                )}
              </div>
            </div>
            <div className="mt-2 grid grid-cols-2 gap-2 text-[10.5px] md:grid-cols-4">
              <Kv label="Latence moyenne" value={`${fmt(network.data?.summary?.avg_latency_ms, 1)} ms`} />
              <Kv label="Latence max" value={`${fmt(network.data?.summary?.max_latency_ms, 1)} ms`} />
              <Kv label="Débit télémétrie" value={`${fmt(network.data?.summary?.messages_per_s, 1)} msg/s`} />
              <Kv label="Bande passante" value={`${fmt(network.data?.summary?.throughput_kb_s, 2)} ko/s`} />
            </div>
          </Panel>
        </div>

        {/* Colonne latérale : commandes, sécurité, journal */}
        <div className="space-y-3">
          <Panel>
            <PanelHeader title="Paramètres de commande" subtitle="Appliqués aux boutons OTA / échantillonnage" />
            <div className="space-y-2 text-[11.5px]">
              <div>
                <span className="label mb-1 block">Fréquence d'échantillonnage cible</span>
                <div className="flex items-center gap-2">
                  <input type="range" min={1} max={50} value={hz} onChange={(event) => setHz(Number(event.target.value))} className="h-1.5 flex-1 appearance-none rounded-full bg-white/10 accent-[#00E5FF]" aria-label="Fréquence d'échantillonnage" />
                  <span className="num w-14 text-right">{hz} Hz</span>
                </div>
              </div>
              <label className="block">
                <span className="label mb-1 block">Version firmware OTA</span>
                <input value={otaVersion} onChange={(event) => setOtaVersion(event.target.value)} className="input num" />
              </label>
              <div className="flex flex-wrap gap-1.5">
                <Button size="sm" variant="ghost" onClick={() => sendCommand.mutate({ deviceId: 'esp32-conveyor-01', action: 'set_sampling', payload: { hz } })}>
                  <Activity size={12} /> Appliquer {hz} Hz
                </Button>
                <Button size="sm" variant="primary" onClick={() => sendCommand.mutate({ deviceId: 'esp32-conveyor-01', action: 'ota_update', payload: { version: otaVersion } })}>
                  <Upload size={12} /> Pousser {otaVersion}
                </Button>
              </div>
              <div className="rounded-lg border border-white/[0.06] bg-white/[0.02] p-2 text-[10px] text-[rgb(var(--muted))]">
                <p className="mb-1">Événements de démonstration :</p>
                <div className="flex flex-wrap gap-1.5">
                  <Button size="sm" variant="ghost" onClick={() => simulate.mutate({ deviceId: 'esp32-conveyor-01', event: 'TAMPER' })}>Tamper</Button>
                  <Button size="sm" variant="ghost" onClick={() => simulate.mutate({ deviceId: 'esp32-conveyor-01', event: 'OFFLINE' })}>Hors ligne</Button>
                  <Button size="sm" variant="ghost" onClick={() => simulate.mutate({ deviceId: 'esp32-conveyor-01', event: 'WEAK_SIGNAL' })}>Signal faible</Button>
                  <Button size="sm" variant="ghost" onClick={() => resetTamper.mutate('esp32-conveyor-01')}>Réarmer</Button>
                </div>
              </div>
              {lastResult && <p className="text-[10px] text-[#00E396]">{lastResult}</p>}
            </div>
          </Panel>

          <Panel>
            <PanelHeader title="Sécurité" subtitle="TLS, authentification, rotation de clés et tampering" icon={<Lock size={14} />} />
            {security.isLoading ? <Skeleton className="h-[200px] w-full" /> : (
              <div className="space-y-2.5 text-[11px]">
                <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-2.5">
                  <p className="mb-1 flex items-center gap-1.5 text-[11.5px] font-medium">
                    {security.data?.tls?.enabled ? <ShieldCheck size={13} className="text-[#00E396]" /> : <ShieldAlert size={13} className="text-[#FFB020]" />}
                    Transport MQTT
                  </p>
                  <p className="text-[10.5px] text-[rgb(var(--muted))]">
                    TLS {security.data?.tls?.enabled ? security.data.tls.version : 'désactivé'} · CA {security.data?.tls?.ca_configured ? 'configurée' : 'absente'} · mode {security.data?.tls?.mode}
                  </p>
                </div>
                <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-2.5">
                  <p className="mb-1 text-[11.5px] font-medium">Authentification</p>
                  <p className="text-[10.5px] text-[rgb(var(--muted))]">
                    Device : {security.data?.authentication?.mode} · mot de passe MQTT {security.data?.authentication?.mqtt_user_configured ? 'défini' : 'absent'} · JWT {security.data?.authentication?.jwt_enabled ? 'actif' : 'inactif'}
                  </p>
                  <p className="mt-1 text-[10px] text-[rgb(var(--muted))]">Rôles : {(security.data?.authentication?.roles ?? []).join(' / ')}</p>
                </div>
                <div>
                  <p className="label mb-1">Rotation de clé (tous les {security.data?.key_rotation?.interval_days ?? 30} j)</p>
                  <p className="num text-[10.5px]">Dernière : {dateTimeFr(security.data?.key_rotation?.last_rotation)}</p>
                  <div className="mt-1">
                    <ProgressBar
                      value={Math.max(0, 100 - ((security.data?.key_rotation?.next_rotation_days ?? 30) / (security.data?.key_rotation?.interval_days || 30)) * 100)}
                      tone="cyan" height={4}
                      label={`Prochaine rotation dans ${fmt(Math.max(0, security.data?.key_rotation?.next_rotation_days ?? 30), 1)} j`}
                    />
                  </div>
                </div>
                <div className={`rounded-xl border p-2.5 ${security.data?.tamper_detected ? 'border-[rgba(255,77,79,0.4)] bg-[rgba(255,77,79,0.08)]' : 'border-white/[0.06] bg-white/[0.02]'}`}>
                  <p className="text-[11px] font-medium">
                    Tampering : {security.data?.tamper_detected ? 'DÉTECTÉ' : 'aucun'}
                  </p>
                </div>
                <div>
                  <p className="label mb-1.5">Recommandations de durcissement</p>
                  <ul className="space-y-1 text-[10.5px] text-[rgb(var(--muted))]">
                    {(security.data?.recommendations ?? []).map((recommendation: string) => (
                      <li key={recommendation}>• {recommendation}</li>
                    ))}
                  </ul>
                </div>
              </div>
            )}
          </Panel>

          <Panel>
            <PanelHeader title="Journal du device" subtitle="Événements sécurité & OTA" icon={<Terminal size={14} />} />
            <div className="scroll-thin max-h-[280px] space-y-1.5 overflow-y-auto pr-1">
              {(logs.data ?? []).map((event: any) => (
                <div key={event.id} className="flex items-start justify-between gap-2 text-[10.5px]">
                  <div>
                    <p className="num">{event.event}</p>
                    <p className="text-[10px] text-[rgb(var(--muted))]">{event.detail}</p>
                  </div>
                  <span className="shrink-0 text-[9.5px] text-[rgb(var(--muted))]">{timeFr(event.ts)}</span>
                </div>
              ))}
              {(logs.data ?? []).length === 0 && <p className="text-[11px] text-[rgb(var(--muted))]">Aucun événement.</p>}
            </div>
          </Panel>
        </div>
      </div>
    </div>
  );
}

function Kv({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="label">{label}</p>
      <p className="num mt-0.5 text-[11.5px]">{value}</p>
    </div>
  );
}
