'use client';

/** Alertes, règles de seuil, notifications et maintenance prescriptive. */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  BellRing, Check, CheckCheck, ChevronLeft, ChevronRight, Filter, Mail, MessageSquare,
  Plus, Search, Send, Sliders, Trash2, Volume2, Webhook, Wrench,
} from 'lucide-react';
import * as React from 'react';
import { AlertFeed } from '@/components/motors/AlertFeed';
import { Badge, Button, EmptyState, Panel, PanelHeader, Segmented, Skeleton } from '@/components/ui/primitives';
import { api } from '@/lib/api';
import { FAULT_LABELS, SEVERITY_COLORS, dateTimeFr, fmt } from '@/lib/format';

type Tab = 'alerts' | 'rules' | 'maintenance';

export default function AlertsPage() {
  const [tab, setTab] = React.useState<Tab>('alerts');
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h1 className="font-display text-[17px] font-semibold">Alertes &amp; maintenance</h1>
          <p className="text-[11.5px] text-[rgb(var(--muted))]">Seuils configurables, notifications multi-canaux et maintenance prescriptive</p>
        </div>
        <Segmented
          ariaLabel="Sections alertes" value={tab} onChange={setTab}
          options={[
            { value: 'alerts', label: 'Alertes', icon: <BellRing size={12} /> },
            { value: 'rules', label: 'Règles de seuil', icon: <Sliders size={12} /> },
            { value: 'maintenance', label: 'Maintenance prescriptive', icon: <Wrench size={12} /> },
          ]}
        />
      </div>
      {tab === 'alerts' && <AlertsTable />}
      {tab === 'rules' && <RulesManager />}
      {tab === 'maintenance' && <MaintenanceBoard />}
    </div>
  );
}

// ==========================================================================
function AlertsTable() {
  const client = useQueryClient();
  const [severity, setSeverity] = React.useState('');
  const [status, setStatus] = React.useState('');
  const [search, setSearch] = React.useState('');
  const [hours, setHours] = React.useState(168);
  const [page, setPage] = React.useState(0);
  const limit = 25;

  const alerts = useQuery({
    queryKey: ['alerts', severity, status, search, hours, page],
    queryFn: () => api.alerts({ severity, status, search, hours, limit, offset: page * limit }),
    refetchInterval: 15000,
  });

  const update = useMutation({
    mutationFn: ({ id, body }: { id: number; body: Record<string, unknown> }) => api.updateAlert(id, body),
    onSuccess: () => client.invalidateQueries({ queryKey: ['alerts'] }),
  });
  const toWorkOrder = useMutation({
    mutationFn: (id: number) => api.alertToWorkOrder(id),
    onSuccess: () => client.invalidateQueries({ queryKey: ['work-orders'] }),
  });

  const total = alerts.data?.total ?? 0;
  const pages = Math.max(1, Math.ceil(total / limit));

  return (
    <div className="grid grid-cols-1 gap-3 xl:grid-cols-[minmax(0,1fr)_360px]">
      <Panel className="min-w-0">
        <PanelHeader
          title="Historique des alertes"
          subtitle={`${total} alerte(s) · filtres serveur, tri par date, actions acquittement/assignation`}
          icon={<BellRing size={14} />}
          right={
            <div className="flex flex-wrap items-center gap-2">
              <Segmented
                size="sm" ariaLabel="Filtrer par sévérité" value={severity} onChange={(value) => { setSeverity(value); setPage(0); }}
                options={[
                  { value: '', label: 'Toutes' }, { value: 'CRITICAL', label: 'Critiques' },
                  { value: 'WARNING', label: 'Avertissements' }, { value: 'INFO', label: 'Info' },
                ]}
              />
              <Segmented
                size="sm" ariaLabel="Filtrer par statut" value={status} onChange={(value) => { setStatus(value); setPage(0); }}
                options={[
                  { value: '', label: 'Tous' }, { value: 'NEW', label: 'Nouvelles' },
                  { value: 'ACKNOWLEDGED', label: 'Acquittées' }, { value: 'RESOLVED', label: 'Résolues' },
                ]}
              />
            </div>
          }
        />

        <div className="mb-2.5 flex flex-wrap items-center gap-2">
          <div className="relative flex-1 min-w-[200px]">
            <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-[rgb(var(--muted))]" />
            <input
              value={search}
              onChange={(event) => { setSearch(event.target.value); setPage(0); }}
              placeholder="Rechercher dans les messages…"
              className="w-full rounded-xl border border-white/10 bg-white/[0.03] py-1.5 pl-8 pr-3 text-[11.5px] outline-none focus:border-[rgba(0,229,255,0.4)]"
            />
          </div>
          <Segmented
            size="sm" ariaLabel="Fenêtre temporelle" value={String(hours)} onChange={(value) => setHours(Number(value))}
            options={[
              { value: '1', label: '1 h' }, { value: '24', label: '24 h' },
              { value: '168', label: '7 j' }, { value: '720', label: '30 j' },
            ]}
          />
          <Badge tone="cyan"><Filter size={11} /> {alerts.data?.counts?.NEW ?? 0} nouvelles</Badge>
        </div>

        {alerts.isLoading ? (
          <Skeleton className="h-[420px] w-full" />
        ) : (
          <div className="scroll-thin overflow-x-auto">
            <table className="w-full min-w-[860px] text-left text-[11px]">
              <thead className="text-[10px] uppercase tracking-wider text-[rgb(var(--muted))]">
                <tr>
                  <th className="px-2 py-2">Horodatage</th>
                  <th className="px-2 py-2">Moteur</th>
                  <th className="px-2 py-2">Sévérité</th>
                  <th className="px-2 py-2">Type</th>
                  <th className="px-2 py-2">Message</th>
                  <th className="px-2 py-2">Valeur / seuil</th>
                  <th className="px-2 py-2">Statut</th>
                  <th className="px-2 py-2 text-right">Actions</th>
                </tr>
              </thead>
              <tbody>
                {(alerts.data?.items ?? []).map((alert) => (
                  <tr key={alert.id} className="border-t border-white/[0.05] hover:bg-white/[0.03]">
                    <td className="num px-2 py-2 whitespace-nowrap">{dateTimeFr(alert.ts)}</td>
                    <td className="px-2 py-2">{alert.motor_id}</td>
                    <td className="px-2 py-2">
                      <span className="inline-flex items-center gap-1.5" style={{ color: SEVERITY_COLORS[alert.severity] }}>
                        <span className="h-1.5 w-1.5 rounded-full" style={{ background: SEVERITY_COLORS[alert.severity] }} />
                        {alert.severity}
                      </span>
                    </td>
                    <td className="px-2 py-2 text-[rgb(var(--muted))]">{alert.type} · {alert.origin}</td>
                    <td className="px-2 py-2">{alert.message}</td>
                    <td className="num px-2 py-2 whitespace-nowrap">{fmt(alert.value, 3)} / {fmt(alert.threshold, 3)}</td>
                    <td className="px-2 py-2">
                      <Badge tone={alert.status === 'NEW' ? 'red' : alert.status === 'ACKNOWLEDGED' ? 'amber' : 'green'}>{alert.status}</Badge>
                    </td>
                    <td className="px-2 py-2">
                      <div className="flex justify-end gap-1">
                        <Button size="sm" variant="ghost" title="Acquitter" onClick={() => update.mutate({ id: alert.id, body: { status: 'ACKNOWLEDGED' } })}>
                          <Check size={12} />
                        </Button>
                        <Button size="sm" variant="ghost" title="Résoudre" onClick={() => update.mutate({ id: alert.id, body: { status: 'RESOLVED' } })}>
                          <CheckCheck size={12} />
                        </Button>
                        <Button size="sm" variant="ghost" title="Créer un ordre de travail" onClick={() => toWorkOrder.mutate(alert.id)}>
                          <Wrench size={12} />
                        </Button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {(alerts.data?.items ?? []).length === 0 && (
              <EmptyState title="Aucune alerte sur ce filtre" description="Ajustez la sévérité, le statut ou la fenêtre temporelle." />
            )}
          </div>
        )}

        <div className="mt-3 flex items-center justify-between text-[10.5px] text-[rgb(var(--muted))]">
          <span>Page {page + 1} / {pages}</span>
          <div className="flex gap-1.5">
            <Button size="sm" onClick={() => setPage((p) => Math.max(0, p - 1))} disabled={page === 0}>
              <ChevronLeft size={12} /> Précédent
            </Button>
            <Button size="sm" onClick={() => setPage((p) => Math.min(pages - 1, p + 1))} disabled={page >= pages - 1}>
              Suivant <ChevronRight size={12} />
            </Button>
          </div>
        </div>
      </Panel>

      <div className="space-y-3">
        <AlertFeed limit={25} />
        <NotificationsPanel />
      </div>
    </div>
  );
}

// ==========================================================================
function NotificationsPanel() {
  const client = useQueryClient();
  const log = useQuery({ queryKey: ['notifications'], queryFn: api.notificationLog, refetchInterval: 20000 });
  const test = useMutation({
    mutationFn: (channel: string) => api.testNotification(channel),
    onSuccess: () => client.invalidateQueries({ queryKey: ['notifications'] }),
  });

  const channels = log.data?.channels ?? {};

  return (
    <Panel>
      <PanelHeader
        title="Canaux de notification"
        subtitle="In-app + son, e-mail SMTP, webhook (Slack / Discord / Telegram)"
        icon={<Send size={14} />}
      />
      <div className="space-y-2">
        {[
          { id: 'inapp', label: 'In-app + toast', icon: <Volume2 size={13} />, enabled: true, detail: 'temps réel WebSocket' },
          { id: 'email', label: 'E-mail (SMTP)', icon: <Mail size={13} />, enabled: Boolean(channels.email?.enabled), detail: String(channels.email?.smtp ?? 'non configuré') },
          { id: 'webhook', label: 'Webhook', icon: <Webhook size={13} />, enabled: Boolean(channels.webhook?.enabled), detail: String(channels.webhook?.url ?? 'non configuré') },
        ].map((channel) => (
          <div key={channel.id} className="flex items-center justify-between rounded-xl border border-white/[0.06] bg-white/[0.02] p-2.5">
            <div className="flex items-center gap-2">
              <span className="text-[rgb(var(--muted))]">{channel.icon}</span>
              <div>
                <p className="text-[11.5px] font-medium">{channel.label}</p>
                <p className="text-[10px] text-[rgb(var(--muted))]">{channel.detail}</p>
              </div>
            </div>
            <div className="flex items-center gap-1.5">
              <Badge tone={channel.enabled ? 'green' : 'neutral'}>{channel.enabled ? 'actif' : 'inactif'}</Badge>
              <Button size="sm" variant="ghost" onClick={() => test.mutate(channel.id)} title="Test d'envoi">
                <Send size={12} />
              </Button>
            </div>
          </div>
        ))}
      </div>
      <div className="mt-2.5 space-y-1.5">
        {(log.data?.items ?? []).slice(-4).reverse().map((item, index) => (
          <div key={index} className="flex items-center justify-between text-[10.5px] text-[rgb(var(--muted))]">
            <span className="truncate">{dateTimeFr(item.ts)} · {item.channel}</span>
            <Badge tone={item.state === 'delivered' ? 'green' : item.state === 'disabled' ? 'neutral' : 'red'}>{item.state}</Badge>
          </div>
        ))}
      </div>
    </Panel>
  );
}

// ==========================================================================
function RulesManager() {
  const client = useQueryClient();
  const rules = useQuery({ queryKey: ['rules'], queryFn: api.rules });
  const [form, setForm] = React.useState({ name: '', metric: 'vibration_rms', operator: '>', threshold: 0.4, hysteresis: 0.05, duration_s: 10, debounce_s: 60, severity: 'WARNING' });

  const create = useMutation({
    mutationFn: () => api.createRule({ ...form, description: 'Créée depuis la console' }),
    onSuccess: () => { client.invalidateQueries({ queryKey: ['rules'] }); setForm({ ...form, name: '' }); },
  });
  const toggle = useMutation({
    mutationFn: (id: number) => api.toggleRule(id),
    onSuccess: () => client.invalidateQueries({ queryKey: ['rules'] }),
  });
  const remove = useMutation({
    mutationFn: (id: number) => api.deleteRule(id),
    onSuccess: () => client.invalidateQueries({ queryKey: ['rules'] }),
  });

  const METRICS = ['vibration_rms', 'vibration_peak', 'kurtosis', 'current_rms', 'power_w', 'motor_temp', 'bearing_temp', 'health_index', 'anomaly_score', 'rpm', 'spl_db'];

  return (
    <div className="grid grid-cols-1 gap-3 xl:grid-cols-[minmax(0,1fr)_380px]">
      <Panel>
        <PanelHeader title="Règles de seuil actives" subtitle="Hystérésis, durée de dépassement et anti-spam configurables" icon={<Sliders size={14} />} />
        {rules.isLoading ? <Skeleton className="h-[320px] w-full" /> : (
          <div className="scroll-thin overflow-x-auto">
            <table className="w-full min-w-[720px] text-left text-[11px]">
              <thead className="text-[10px] uppercase tracking-wider text-[rgb(var(--muted))]">
                <tr>
                  <th className="px-2 py-2">Règle</th>
                  <th className="px-2 py-2">Moteur</th>
                  <th className="px-2 py-2">Condition</th>
                  <th className="px-2 py-2">Hystérésis</th>
                  <th className="px-2 py-2">Durée / anti-spam</th>
                  <th className="px-2 py-2">Sévérité</th>
                  <th className="px-2 py-2 text-right">État</th>
                </tr>
              </thead>
              <tbody>
                {(rules.data ?? []).map((rule) => (
                  <tr key={rule.id} className="border-t border-white/[0.05] hover:bg-white/[0.03]">
                    <td className="px-2 py-2">
                      <p className="font-medium">{rule.name}</p>
                      <p className="text-[10px] text-[rgb(var(--muted))]">{rule.description}</p>
                    </td>
                    <td className="px-2 py-2">{rule.motor_id}</td>
                    <td className="num px-2 py-2">{rule.metric} {rule.operator} {rule.threshold}</td>
                    <td className="num px-2 py-2">{rule.hysteresis}</td>
                    <td className="num px-2 py-2">{rule.duration_s}s / {rule.debounce_s}s</td>
                    <td className="px-2 py-2">
                      <Badge tone={rule.severity === 'CRITICAL' ? 'red' : rule.severity === 'WARNING' ? 'amber' : 'cyan'}>{rule.severity}</Badge>
                    </td>
                    <td className="px-2 py-2">
                      <div className="flex justify-end gap-1">
                        <Button size="sm" variant={rule.enabled ? 'success' : 'ghost'} onClick={() => toggle.mutate(rule.id)}>
                          {rule.enabled ? 'active' : 'inactive'}
                        </Button>
                        <Button size="sm" variant="ghost" onClick={() => remove.mutate(rule.id)} title="Supprimer">
                          <Trash2 size={12} />
                        </Button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>

      <Panel>
        <PanelHeader title="Nouvelle règle" subtitle="Les règles sont évaluées à chaque trame de télémétrie" icon={<Plus size={14} />} />
        <div className="space-y-2 text-[11.5px]">
          <Field label="Nom">
            <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="Ex : Vibration roulement M2" className="input" />
          </Field>
          <div className="grid grid-cols-2 gap-2">
            <Field label="Métrique">
              <select value={form.metric} onChange={(e) => setForm({ ...form, metric: e.target.value })} className="input">
                {METRICS.map((metric) => <option key={metric} value={metric}>{metric}</option>)}
              </select>
            </Field>
            <Field label="Opérateur">
              <select value={form.operator} onChange={(e) => setForm({ ...form, operator: e.target.value })} className="input">
                {['>', '>=', '<', '<='].map((operator) => <option key={operator} value={operator}>{operator}</option>)}
              </select>
            </Field>
            <Field label="Seuil">
              <input type="number" step="0.01" value={form.threshold} onChange={(e) => setForm({ ...form, threshold: Number(e.target.value) })} className="input num" />
            </Field>
            <Field label="Hystérésis">
              <input type="number" step="0.01" value={form.hysteresis} onChange={(e) => setForm({ ...form, hysteresis: Number(e.target.value) })} className="input num" />
            </Field>
            <Field label="Durée (s)">
              <input type="number" value={form.duration_s} onChange={(e) => setForm({ ...form, duration_s: Number(e.target.value) })} className="input num" />
            </Field>
            <Field label="Anti-spam (s)">
              <input type="number" value={form.debounce_s} onChange={(e) => setForm({ ...form, debounce_s: Number(e.target.value) })} className="input num" />
            </Field>
          </div>
          <Field label="Sévérité">
            <Segmented
              size="sm" ariaLabel="Sévérité" value={form.severity} onChange={(value) => setForm({ ...form, severity: value })}
              options={[{ value: 'INFO', label: 'Info' }, { value: 'WARNING', label: 'Avertissement' }, { value: 'CRITICAL', label: 'Critique' }]}
            />
          </Field>
          <Button variant="primary" onClick={() => create.mutate()} disabled={!form.name || create.isPending}>
            <Plus size={13} /> {create.isPending ? 'Création…' : 'Créer la règle'}
          </Button>
        </div>
      </Panel>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="label mb-1 block">{label}</span>
      {children}
    </label>
  );
}

// ==========================================================================
function MaintenanceBoard() {
  const client = useQueryClient();
  const prescriptions = useQuery({ queryKey: ['prescriptions'], queryFn: api.prescriptions, refetchInterval: 20000 });
  const orders = useQuery({ queryKey: ['work-orders'], queryFn: api.workOrders, refetchInterval: 20000 });
  const stats = useQuery({ queryKey: ['maintenance-stats'], queryFn: api.maintenanceStats, refetchInterval: 30000 });
  const interventions = useQuery({ queryKey: ['interventions-all'], queryFn: api.allInterventions, refetchInterval: 30000 });

  const advance = useMutation({
    mutationFn: ({ id, status }: { id: number; status: string }) => api.updateWorkOrder(id, status),
    onSuccess: () => client.invalidateQueries({ queryKey: ['work-orders'] }),
  });

  const NEXT_STATUS: Record<string, string> = { OPEN: 'PLANNED', PLANNED: 'IN_PROGRESS', IN_PROGRESS: 'DONE', DONE: 'DONE' };

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-1 gap-3 xl:grid-cols-[minmax(0,1fr)_360px]">
        <Panel>
          <PanelHeader
            title="Recommandations prescriptives"
            subtitle="Générées par le pipeline IA à partir du diagnostic et du RUL"
            icon={<Wrench size={14} />}
          />
          <div className="space-y-2">
            {(prescriptions.data ?? []).map((prescription) => (
              <div key={`${prescription.motor_id}-${prescription.title}`} className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-3">
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div>
                    <p className="text-[12.5px] font-medium">{prescription.title}</p>
                    <p className="text-[10.5px] text-[rgb(var(--muted))]">
                      {prescription.motor_id} · {FAULT_LABELS[prescription.fault_class] ?? prescription.fault_class} · RUL {Math.round(prescription.rul_hours)} h
                    </p>
                  </div>
                  <Badge tone={prescription.priority === 'CRITICAL' ? 'red' : prescription.priority === 'HIGH' ? 'amber' : prescription.priority === 'MEDIUM' ? 'violet' : 'green'}>
                    {prescription.priority}
                  </Badge>
                </div>
                <p className="mt-1.5 text-[11px]">{prescription.action}</p>
                <p className="mt-1 text-[10.5px] text-[rgb(var(--muted))]">{prescription.rationale}</p>
                <div className="mt-2 flex flex-wrap gap-3 text-[10px] text-[rgb(var(--muted))]">
                  <span>Délai <span className="num text-[rgb(var(--text))]">{prescription.due_in_days} j</span></span>
                  <span>Durée estimée <span className="num text-[rgb(var(--text))]">{prescription.estimated_hours} h</span></span>
                  <span>Confiance <span className="num text-[rgb(var(--text))]">{(prescription.confidence * 100).toFixed(0)} %</span></span>
                  {prescription.parts.length > 0 && <span>Pièces : {prescription.parts.join(', ')}</span>}
                </div>
              </div>
            ))}
          </div>
        </Panel>

        <div className="space-y-3">
          <Panel>
            <PanelHeader title="Statistiques maintenance" subtitle="Cumul sur l'historique complet" />
            <div className="grid grid-cols-2 gap-2 text-[10.5px]">
              <Kv label="Interventions" value={String(stats.data?.total_interventions ?? 0)} />
              <Kv label="Ordres ouverts" value={String(stats.data?.open_orders ?? 0)} />
              <Kv label="Heures totales" value={`${fmt(stats.data?.total_hours, 1)} h`} />
              <Kv label="Coût cumulé" value={`${fmt(stats.data?.total_cost_eur, 0)} €`} />
            </div>
            {stats.data?.parts_used && Object.keys(stats.data.parts_used).length > 0 && (
              <div className="mt-3">
                <p className="label mb-1.5">Pièces consommées</p>
                {Object.entries(stats.data.parts_used as Record<string, number>).map(([part, count]) => (
                  <div key={part} className="flex items-center justify-between text-[10.5px]">
                    <span>{part}</span><span className="num">{count}×</span>
                  </div>
                ))}
              </div>
            )}
          </Panel>

          <Panel>
            <PanelHeader title="Journal des interventions" subtitle="Signé et horodaté" />
            <div className="space-y-2">
              {(interventions.data ?? []).slice(0, 6).map((intervention: any) => (
                <div key={intervention.id} className="rounded-lg border border-white/[0.06] bg-white/[0.02] p-2">
                  <div className="flex items-center justify-between">
                    <p className="text-[11px] font-medium">{intervention.action}</p>
                    <span className="num text-[9.5px] text-[rgb(var(--muted))]">{dateTimeFr(intervention.ts)}</span>
                  </div>
                  <p className="text-[10px] text-[rgb(var(--muted))]">
                    {intervention.motor_id} · {intervention.technician} · <span className="num">{String(intervention.signature).slice(0, 12)}</span>
                  </p>
                </div>
              ))}
            </div>
          </Panel>
        </div>
      </div>

      <Panel>
        <PanelHeader title="Ordres de travail" subtitle="Suivi du cycle OPEN → PLANNED → IN_PROGRESS → DONE" icon={<MessageSquare size={14} />} />
        <div className="scroll-thin overflow-x-auto">
          <table className="w-full min-w-[760px] text-left text-[11px]">
            <thead className="text-[10px] uppercase tracking-wider text-[rgb(var(--muted))]">
              <tr>
                <th className="px-2 py-2">Créé le</th>
                <th className="px-2 py-2">Moteur</th>
                <th className="px-2 py-2">Titre</th>
                <th className="px-2 py-2">Priorité</th>
                <th className="px-2 py-2">Source</th>
                <th className="px-2 py-2">Délai</th>
                <th className="px-2 py-2">Statut</th>
                <th className="px-2 py-2 text-right">Action</th>
              </tr>
            </thead>
            <tbody>
              {(orders.data ?? []).map((order: any) => (
                <tr key={order.id} className="border-t border-white/[0.05] hover:bg-white/[0.03]">
                  <td className="num px-2 py-2 whitespace-nowrap">{dateTimeFr(order.created_at)}</td>
                  <td className="px-2 py-2">{order.motor_id}</td>
                  <td className="px-2 py-2">{order.title}</td>
                  <td className="px-2 py-2">
                    <Badge tone={order.priority === 'CRITICAL' ? 'red' : order.priority === 'HIGH' ? 'amber' : order.priority === 'MEDIUM' ? 'violet' : 'green'}>{order.priority}</Badge>
                  </td>
                  <td className="px-2 py-2 text-[rgb(var(--muted))]">{order.source}</td>
                  <td className="num px-2 py-2">{order.due_in_days} j</td>
                  <td className="px-2 py-2">
                    <Badge tone={order.status === 'DONE' ? 'green' : order.status === 'IN_PROGRESS' ? 'cyan' : 'amber'}>{order.status}</Badge>
                  </td>
                  <td className="px-2 py-2 text-right">
                    <Button size="sm" variant="ghost" onClick={() => advance.mutate({ id: order.id, status: NEXT_STATUS[order.status] ?? 'DONE' })}>
                      Avancer
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>
    </div>
  );
}

function Kv({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-white/[0.06] bg-white/[0.02] p-2">
      <p className="label">{label}</p>
      <p className="num mt-0.5 text-[12px]">{value}</p>
    </div>
  );
}
