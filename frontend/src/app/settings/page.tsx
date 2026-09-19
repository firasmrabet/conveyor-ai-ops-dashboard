'use client';

/** Paramètres : seuils, unités, langue, thème, notifications, utilisateurs et système. */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Bell, Building2, Globe, Info, Languages, Monitor, Moon, Palette, Save, Shield, SlidersHorizontal,
  Sun, Thermometer, UserPlus, Users, Wrench,
} from 'lucide-react';
import * as React from 'react';
import { Badge, Button, Panel, PanelHeader, Segmented, Skeleton, Toggle } from '@/components/ui/primitives';
import { api } from '@/lib/api';
import { dateTimeFr, fmt } from '@/lib/format';
import { useReducedMotion } from '@/lib/hooks';

export default function SettingsPage() {
  const client = useQueryClient();
  const settings = useQuery({ queryKey: ['settings'], queryFn: api.settings });
  const users = useQuery({ queryKey: ['users'], queryFn: api.users, retry: 0 });
  const system = useQuery({ queryKey: ['system-info'], queryFn: api.systemInfo });
  const audit = useQuery({ queryKey: ['audit'], queryFn: api.audit, retry: 0 });

  const [values, setValues] = React.useState<Record<string, any> | null>(null);
  const [newUser, setNewUser] = React.useState({ email: '', full_name: '', password: '', role: 'operator' });
  const [savedAt, setSavedAt] = React.useState<string>('');

  React.useEffect(() => {
    if (settings.data?.values && values === null) setValues(settings.data.values);
  }, [settings.data, values]);

  const save = useMutation({
    mutationFn: (payload: Record<string, unknown>) => api.updateSettings(payload),
    onSuccess: () => {
      setSavedAt(new Date().toLocaleTimeString('fr-FR'));
      client.invalidateQueries({ queryKey: ['settings'] });
    },
  });
  const createUser = useMutation({
    mutationFn: () => api.createUser(newUser),
    onSuccess: () => { client.invalidateQueries({ queryKey: ['users'] }); setNewUser({ email: '', full_name: '', password: '', role: 'operator' }); },
  });
  const deactivate = useMutation({
    mutationFn: (id: number) => api.deactivateUser(id),
    onSuccess: () => client.invalidateQueries({ queryKey: ['users'] }),
  });
  const generate = useMutation({ mutationFn: () => api.generateReport(null, 24) });

  const [reducedMotion, setReducedMotion] = useReducedMotion();

  if (!values) {
    return (
      <div className="space-y-3">
        <Skeleton className="h-[120px] w-full" />
        <Skeleton className="h-[320px] w-full" />
      </div>
    );
  }

  const update = (path: string, value: unknown) => {
    const next = JSON.parse(JSON.stringify(values));
    const parts = path.split('.');
    let cursor = next;
    for (let i = 0; i < parts.length - 1; i += 1) cursor = cursor[parts[i]];
    cursor[parts[parts.length - 1]] = value;
    setValues(next);
  };

  const environment = settings.data?.environment ?? {};
  const thresholds = values.thresholds ?? {};
  const notifications = values.notifications ?? {};
  const units = values.units ?? {};

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h1 className="font-display text-[17px] font-semibold">Paramètres</h1>
          <p className="text-[11.5px] text-[rgb(var(--muted))]">
            Seuils d'alerte, unités, langue, notifications, rôles et informations système
            {savedAt && <span className="text-[#00E396]"> · enregistré à {savedAt}</span>}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button onClick={() => save.mutate({ thresholds: values.thresholds, units: values.units, language: values.language, theme: values.theme, notifications: values.notifications, reduced_motion: values.reduced_motion })} disabled={save.isPending}>
            <Save size={13} /> {save.isPending ? 'Enregistrement…' : 'Enregistrer les paramètres'}
          </Button>
          <Button variant="violet" onClick={() => generate.mutate()}><Wrench size={13} /> Rapport PDF global</Button>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
        {/* Seuils */}
        <Panel>
          <PanelHeader title="Seuils d'alerte" subtitle="Utilisés par les règles et l'évaluation temps réel" icon={<SlidersHorizontal size={14} />} />
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <NumberField label="Santé — avertissement" unit="/100" value={thresholds.health_warning} onChange={(v) => update('thresholds.health_warning', v)} hint="sous cette valeur : alerte jaune" />
            <NumberField label="Santé — critique" unit="/100" value={thresholds.health_critical} onChange={(v) => update('thresholds.health_critical', v)} hint="sous cette valeur : arrêt recommandé" />
            <NumberField label="Vibration — avertissement" unit="g" step={0.01} value={thresholds.vibration_warning} onChange={(v) => update('thresholds.vibration_warning', v)} />
            <NumberField label="Vibration — critique" unit="g" step={0.01} value={thresholds.vibration_critical} onChange={(v) => update('thresholds.vibration_critical', v)} />
            <NumberField label="Température — avertissement" unit="°C" value={thresholds.temp_warning} onChange={(v) => update('thresholds.temp_warning', v)} />
            <NumberField label="Température — critique" unit="°C" value={thresholds.temp_critical} onChange={(v) => update('thresholds.temp_critical', v)} />
          </div>
          <p className="mt-2 text-[10.5px] text-[rgb(var(--muted))]">
            Les règles fines (par métrique, avec hystérésis et anti-spam) se gèrent dans
            <span className="text-[rgb(var(--text))]"> Alertes → Règles de seuil</span>.
          </p>
        </Panel>

        {/* Apparence + langue */}
        <div className="space-y-3">
          <Panel>
            <PanelHeader title="Apparence &amp; langue" subtitle="Thème, densité et accessibilité" icon={<Palette size={14} />} />
            <div className="space-y-3 text-[11.5px]">
              <div>
                <span className="label mb-1.5 block">Thème</span>
                <Segmented
                  ariaLabel="Thème" value={values.theme} onChange={(value) => update('theme', value)}
                  options={[
                    { value: 'dark', label: 'Sombre', icon: <Moon size={12} /> },
                    { value: 'light', label: 'Clair', icon: <Sun size={12} /> },
                    { value: 'system', label: 'Système', icon: <Monitor size={12} /> },
                  ]}
                />
                <p className="mt-1 text-[10px] text-[rgb(var(--muted))]">
                  Le thème se change aussi depuis l'en-tête ; le mode sombre industriel est optimisé salle de contrôle.
                </p>
              </div>
              <div>
                <span className="label mb-1.5 block">Langue de l'interface</span>
                <Segmented
                  ariaLabel="Langue" value={values.language} onChange={(value) => update('language', value)}
                  options={[
                    { value: 'fr', label: 'Français', icon: <Languages size={12} /> },
                    { value: 'en', label: 'English', icon: <Globe size={12} /> },
                  ]}
                />
              </div>
              <Toggle checked={Boolean(reducedMotion)} onChange={(checked) => { setReducedMotion(checked); update('reduced_motion', checked); }} label="Réduire les animations" description="Désactive les pulsations néon, les transitions longues et le défilement de la bande 3D." />
            </div>
          </Panel>

          <Panel>
            <PanelHeader title="Unités" subtitle="Affichage des grandeurs physiques" icon={<Thermometer size={14} />} />
            <div className="grid grid-cols-2 gap-3">
              <SelectField label="Température" value={units.temperature} onChange={(v) => update('units.temperature', v)} options={[['C', '°C'], ['F', '°F']]} />
              <SelectField label="Vibration" value={units.vibration} onChange={(v) => update('units.vibration', v)} options={[['g', 'g (9,81 m/s²)'], ['mms2', 'mm/s²'], ['ips', 'in/s']]} />
              <SelectField label="Vitesse" value={units.speed} onChange={(v) => update('units.speed', v)} options={[['rpm', 'tr/min'], ['hz', 'Hz'], ['mms', 'mm/s (bande)']]} />
              <SelectField label="Puissance" value={units.power} onChange={(v) => update('units.power', v)} options={[['W', 'Watt'], ['kW', 'Kilowatt'], ['hp', 'Cheval']]} />
            </div>
          </Panel>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-3 xl:grid-cols-3">
        {/* Notifications */}
        <Panel>
          <PanelHeader title="Notifications" subtitle="Canaux activés pour les alertes" icon={<Bell size={14} />} />
          <div className="space-y-2">
            <Toggle checked={Boolean(notifications.inapp)} onChange={(checked) => update('notifications.inapp', checked)} label="In-app" description="Bandeau temps réel + flux latéral" />
            <Toggle checked={Boolean(notifications.sound)} onChange={(checked) => update('notifications.sound', checked)} label="Signal sonore" description="Bip court sur alerte critique" />
            <Toggle checked={Boolean(notifications.email)} onChange={(checked) => update('notifications.email', checked)} label="E-mail (SMTP)" description="Variables SMTP_* du .env requises" />
            <Toggle checked={Boolean(notifications.webhook)} onChange={(checked) => update('notifications.webhook', checked)} label="Webhook" description="Slack / Discord / Telegram (WEBHOOK_URL)" />
          </div>
        </Panel>

        {/* Rôles & utilisateurs */}
        <Panel className="xl:col-span-2">
          <PanelHeader title="Utilisateurs &amp; rôles" subtitle="Admin (tout) · Opérateur (acquittement, commandes) · Invité (lecture)" icon={<Users size={14} />} />
          <div className="scroll-thin overflow-x-auto">
            <table className="w-full min-w-[560px] text-left text-[11px]">
              <thead className="text-[10px] uppercase tracking-wider text-[rgb(var(--muted))]">
                <tr>
                  <th className="px-2 py-2">Utilisateur</th>
                  <th className="px-2 py-2">Adresse</th>
                  <th className="px-2 py-2">Rôle</th>
                  <th className="px-2 py-2">Dernière connexion</th>
                  <th className="px-2 py-2">État</th>
                  <th className="px-2 py-2 text-right">Action</th>
                </tr>
              </thead>
              <tbody>
                {(users.data ?? []).map((user: any) => (
                  <tr key={user.id} className="border-t border-white/[0.05]">
                    <td className="px-2 py-2">{user.full_name}</td>
                    <td className="num px-2 py-2">{user.email}</td>
                    <td className="px-2 py-2">
                      <Badge tone={ROLE_TONES[user.role] ?? 'neutral'}>{user.role}</Badge>
                    </td>
                    <td className="num px-2 py-2">{dateTimeFr(user.last_login)}</td>
                    <td className="px-2 py-2">
                      <Badge tone={user.active ? 'green' : 'red'}>{user.active ? 'actif' : 'désactivé'}</Badge>
                    </td>
                    <td className="px-2 py-2 text-right">
                      <Button size="sm" variant="ghost" onClick={() => deactivate.mutate(user.id)} disabled={!user.active}>Désactiver</Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="mt-3 grid grid-cols-1 gap-2 border-t border-white/[0.06] pt-3 sm:grid-cols-4">
            <input className="input" placeholder="Nom complet" value={newUser.full_name} onChange={(e) => setNewUser({ ...newUser, full_name: e.target.value })} />
            <input className="input" placeholder="email@conveyor.local" value={newUser.email} onChange={(e) => setNewUser({ ...newUser, email: e.target.value })} />
            <input className="input" type="password" placeholder="Mot de passe" value={newUser.password} onChange={(e) => setNewUser({ ...newUser, password: e.target.value })} />
            <select className="input" value={newUser.role} onChange={(e) => setNewUser({ ...newUser, role: e.target.value })}>
              <option value="admin">admin</option>
              <option value="operator">operator</option>
              <option value="viewer">viewer</option>
            </select>
          </div>
          <div className="mt-2">
            <Button size="sm" variant="primary" onClick={() => createUser.mutate()} disabled={!newUser.email || !newUser.password || createUser.isPending}>
              <UserPlus size={13} /> Créer l'utilisateur
            </Button>
          </div>
          <p className="mt-2 text-[10.5px] text-[rgb(var(--muted))]">
            Sessions : jetons JWT signés (HS256), durée {fmt(environment.session_expire_minutes ?? 720, 0)} min,
            mots de passe hachés PBKDF2-SHA256 (260 000 itérations) — jamais stockés en clair.
          </p>
        </Panel>
      </div>

      {/* Système */}
      <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
        <Panel>
          <PanelHeader title="Environnement d'exécution" subtitle="Valeurs effectives du backend (lecture seule)" icon={<Info size={14} />} />
          <div className="grid grid-cols-2 gap-2 text-[10.5px] md:grid-cols-3">
            <Kv label="Mode" value={String(environment.mode ?? '—')} />
            <Kv label="MQTT" value={environment.mqtt_enabled ? 'activé' : 'désactivé'} />
            <Kv label="Redis" value={environment.redis_enabled ? 'activé' : 'désactivé (fallback mémoire)'} />
            <Kv label="Bundle ML" value={String(environment.ml_active_bundle ?? 'heuristique')} />
            <Kv label="Version modèle" value={String(environment.ml_model_version ?? '—')} />
            <Kv label="Rétention brute" value={`${environment.raw_retention_days ?? '—'} j`} />
            <Kv label="Rétention features" value={`${environment.feature_retention_days ?? '—'} j`} />
            <Kv label="CORS" value={String(environment.cors_origins ?? '—')} />
            <Kv label="Version API" value={String(system.data?.versions?.api ?? '—')} />
            <Kv label="Python" value={String(system.data?.versions?.python ?? '—')} />
            <Kv label="Modèle actif" value={String(system.data?.ml?.active ?? system.data?.ml?.type ?? '—')} />
            <Kv label="Uptime" value={String(system.data?.uptime_human ?? system.data?.uptime ?? '—')} />
          </div>
        </Panel>

        <Panel>
          <PanelHeader title="Journal d'audit" subtitle="Actions sensibles et interventions signées" icon={<Shield size={14} />} />
          <div className="scroll-thin max-h-[300px] space-y-1.5 overflow-y-auto pr-1">
            {(audit.data?.security_events ?? []).slice(0, 25).map((event: any) => (
              <div key={`s-${event.id}`} className="flex items-center justify-between text-[10.5px]">
                <span>
                  <span className="num">{event.event}</span> · {event.actor} · {event.detail}
                </span>
                <span className="shrink-0 text-[9.5px] text-[rgb(var(--muted))]">{dateTimeFr(event.ts)}</span>
              </div>
            ))}
            {(audit.data?.interventions ?? []).slice(0, 10).map((intervention: any) => (
              <div key={`i-${intervention.id}`} className="flex items-center justify-between text-[10.5px]">
                <span>
                  <Badge tone="cyan">intervention</Badge> {intervention.motor_id} · {intervention.action}
                </span>
                <span className="shrink-0 text-[9.5px] text-[rgb(var(--muted))]">{dateTimeFr(intervention.ts)}</span>
              </div>
            ))}
            {(audit.data?.security_events ?? []).length === 0 && (audit.data?.interventions ?? []).length === 0 && (
              <p className="text-[11px] text-[rgb(var(--muted))]">Aucun événement d'audit pour l'instant.</p>
            )}
          </div>
        </Panel>
      </div>

      <Panel>
        <PanelHeader title="À propos du projet" subtitle="Détection d'anomalies & maintenance prédictive d'un tapis roulant industriel" icon={<Building2 size={14} />} />
        <p className="text-[11.5px] leading-relaxed text-[rgb(var(--muted))]">
          Chaîne complète : ESP32 (accéléromètre 3 axes, capteur de courant, thermistances, micro MEMS, encodeur) →
          MQTT → backend FastAPI (features, Isolation Forest / Autoencoder, RandomForest + XGBoost, régression de RUL,
          fusion pondérée de l'indice de santé) → Next.js (jumeau numérique 3D, spectrogrammes, XAI, rapports PDF).
          Le mode <span className="num">SIMULATION</span> reproduit fidèlement la physique du banc pour la soutenance,
          et <span className="num">MODE=HARDWARE</span> bascule sur le matériel réel sans redémarrage.
        </p>
      </Panel>
    </div>
  );
}

/** Couleur de badge par rôle : table lisible plutôt qu'un ternaire imbriqué. */
const ROLE_TONES: Record<string, 'violet' | 'cyan' | 'neutral'> = {
  admin: 'violet',
  operator: 'cyan',
  viewer: 'neutral',
};

function NumberField({ label, unit, value, onChange, step = 1, hint }: { label: string; unit: string; value: number | undefined; onChange: (v: number) => void; step?: number; hint?: string }) {
  return (
    <label className="block">
      <span className="label mb-1 block">{label}</span>
      <div className="flex items-center gap-2">
        <input
          type="number" step={step} value={value ?? 0}
          onChange={(event) => onChange(Number(event.target.value))}
          className="input num"
        />
        <span className="num shrink-0 text-[10.5px] text-[rgb(var(--muted))]">{unit}</span>
      </div>
      {hint && <span className="mt-0.5 block text-[9.5px] text-[rgb(var(--muted))]">{hint}</span>}
    </label>
  );
}

function SelectField({ label, value, onChange, options }: { label: string; value: string | undefined; onChange: (v: string) => void; options: [string, string][] }) {
  return (
    <label className="block">
      <span className="label mb-1 block">{label}</span>
      <select className="input" value={value ?? options[0][0]} onChange={(event) => onChange(event.target.value)}>
        {options.map(([optionValue, optionLabel]) => <option key={optionValue} value={optionValue}>{optionLabel}</option>)}
      </select>
    </label>
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
