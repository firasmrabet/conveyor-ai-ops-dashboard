/** Utilitaires de formatage (FR) — valeurs numériques, durées, unités. */
export const fmt = (value: number | null | undefined, digits = 1): string =>
  value === null || value === undefined || Number.isNaN(value) ? '—' : value.toFixed(digits);

export const fmtInt = (value: number | null | undefined): string =>
  value === null || value === undefined ? '—' : Math.round(value).toLocaleString('fr-FR');

export const pct = (value: number | null | undefined, digits = 0): string => `${fmt(value, digits)} %`;

export function hoursHuman(hours: number | null | undefined): string {
  if (hours === null || hours === undefined) return '—';
  if (hours < 1) return `${Math.round(hours * 60)} min`;
  if (hours < 48) return `${hours.toFixed(1)} h`;
  const days = hours / 24;
  if (days < 90) return `${days.toFixed(0)} j (${Math.round(hours)} h)`;
  return `${(days / 30.4).toFixed(1)} mois`;
}

export const timeFr = (ts: number | string | Date | null | undefined): string =>
  !ts ? '—' : new Date(ts).toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit', second: '2-digit' });

export const dateTimeFr = (ts: number | string | Date | null | undefined): string =>
  !ts ? '—' : new Date(ts).toLocaleString('fr-FR', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });

export const ago = (ts: number | null | undefined): string => {
  if (!ts) return '—';
  const seconds = Math.max(0, (Date.now() - ts) / 1000);
  if (seconds < 60) return `il y a ${Math.round(seconds)} s`;
  if (seconds < 3600) return `il y a ${Math.round(seconds / 60)} min`;
  if (seconds < 86400) return `il y a ${Math.round(seconds / 3600)} h`;
  return `il y a ${Math.round(seconds / 86400)} j`;
};

export const FAULT_LABELS: Record<string, string> = {
  HEALTHY: 'Sain',
  BEARING_FAULT: 'Roulement usé',
  UNBALANCE: 'Déséquilibre',
  MISALIGNMENT: 'Désalignement',
  BELT_SLIP: 'Glissement bande',
  OVERLOAD: 'Surcharge',
  OVERHEATING: 'Surchauffe',
};

export const SEVERITY_COLORS: Record<string, string> = {
  INFO: 'rgb(0,229,255)',
  WARNING: 'rgb(255,176,32)',
  CRITICAL: 'rgb(255,77,79)',
};

export const FAULT_COLORS: Record<string, string> = {
  HEALTHY: 'rgb(0,227,150)',
  BEARING_FAULT: 'rgb(255,77,79)',
  UNBALANCE: 'rgb(255,176,32)',
  MISALIGNMENT: 'rgb(255,159,64)',
  BELT_SLIP: 'rgb(0,229,255)',
  OVERLOAD: 'rgb(124,92,255)',
  OVERHEATING: 'rgb(255,94,94)',
};

/** Couleur d'un indice de santé (0–100) : vert → ambre → rouge. */
export const healthColor = (health: number | null | undefined): string => {
  const value = health ?? 100;
  if (value >= 80) return 'rgb(0,227,150)';
  if (value >= 60) return 'rgb(255,176,32)';
  return 'rgb(255,77,79)';
};

export const cn = (...classes: (string | false | null | undefined)[]): string =>
  classes.filter(Boolean).join(' ');
