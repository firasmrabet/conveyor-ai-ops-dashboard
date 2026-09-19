/** Client REST typé — passe par le proxy Next (/api → backend FastAPI). */

import type { AlertItem, Inference, MotorId, MotorSummary, Overview, Prescription, Telemetry } from './types';

const json = async <T>(response: Response): Promise<T> => {
  if (!response.ok) {
    const detail = await response.text().catch(() => '');
    throw new Error(`${response.status} ${response.statusText} — ${detail.slice(0, 200)}`);
  }
  return (await response.json()) as T;
};

const get = <T>(path: string) => fetch(path, { cache: 'no-store' }).then(json<T>);
const post = <T>(path: string, body?: unknown) =>
  fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: body ? JSON.stringify(body) : undefined }).then(json<T>);
const patch = <T>(path: string, body?: unknown) =>
  fetch(path, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: body ? JSON.stringify(body) : undefined }).then(json<T>);
const put = <T>(path: string, body?: unknown) =>
  fetch(path, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: body ? JSON.stringify(body) : undefined }).then(json<T>);
const del = <T>(path: string) => fetch(path, { method: 'DELETE' }).then(json<T>);

export const api = {
  // --- Vue d'ensemble / système ---
  health: () => get<Record<string, unknown>>('/api/health'),
  overview: () => get<Overview>('/api/overview'),
  systemInfo: () => get<Record<string, any>>('/api/system/info'),
  settings: () => get<{ values: Record<string, any>; environment: Record<string, any> }>('/api/settings'),
  updateSettings: (values: Record<string, unknown>) => put<Record<string, any>>('/api/settings', { values }),
  saveSettings: (values: Record<string, unknown>) => put<Record<string, any>>('/api/settings', { values }),
  audit: () => get<{ security_events: Record<string, any>[]; interventions: Record<string, any>[] }>('/api/audit'),
  generateReport: (motorId: MotorId | null = null, hours = 24) =>
    post<Record<string, any>>(`/api/reports/generate?hours=${hours}${motorId ? `&motor_id=${motorId}` : ''}`),
  fullCsvUrl: () => '/api/export/full.csv',

  // --- Utilisateurs ---
  users: () => get<Record<string, any>[]>('/api/auth/users'),
  createUser: (body: Record<string, unknown>) => post<Record<string, any>>('/api/auth/users', body),
  deactivateUser: (id: number) => del<Record<string, any>>(`/api/auth/users/${id}`),

  // --- Moteurs ---
  motors: () => get<MotorSummary[]>('/api/motors'),
  motor: (id: MotorId) => get<Record<string, any>>(`/api/motors/${id}`),
  telemetry: (id: MotorId, hours = 1) =>
    get<{ points: Telemetry[]; source: string }>(`/api/motors/${id}/telemetry?from=${Date.now() - hours * 3600_000}&to=${Date.now()}&limit=6000`),
  features: (id: MotorId, limit = 600) => get<{ points: { ts: number; features: Record<string, number> }[] }>(`/api/motors/${id}/features?limit=${limit}`),
  healthHistory: (id: MotorId, hours = 24) => get<Record<string, any>>(`/api/motors/${id}/health?hours=${hours}`),
  prediction: (id: MotorId) => get<Record<string, any>>(`/api/motors/${id}/prediction`),
  degradation: (id: MotorId, hours = 168) => get<Record<string, any>>(`/api/motors/${id}/degradation?hours=${hours}`),
  radar: (id: MotorId) => get<Record<string, any>>(`/api/motors/${id}/radar`),
  fft: (id: MotorId) => get<Record<string, any>>(`/api/motors/${id}/fft`),
  spectrogram: (id: MotorId) => get<Record<string, any>>(`/api/motors/${id}/spectrogram`),
  anomalies: (id: MotorId, hours = 168) => get<{ items: Inference[] }>(`/api/motors/${id}/anomalies?hours=${hours}`),
  xai: (id: MotorId) => get<Record<string, any>>(`/api/motors/${id}/xai`),
  interventions: (id: MotorId) => get<Record<string, any>[]>(`/api/motors/${id}/interventions`),
  addIntervention: (id: MotorId, body: Record<string, unknown>) => post<Record<string, any>>(`/api/motors/${id}/interventions`, body),
  motorReportUrl: (id: MotorId, hours = 24) => `/api/motors/${id}/report?hours=${hours}`,
  motorCsvUrl: (id: MotorId, hours = 24) => `/api/motors/${id}/export.csv?hours=${hours}`,

  // --- Analytique ---
  analyticsSummary: (range: string) => get<Record<string, any>>(`/api/analytics/summary?range=${range}`),
  heatmap: (range: string, motorId: MotorId) => get<{ matrix: (number | null)[][]; days: string[] }>(`/api/analytics/heatmap?range=${range}&motor_id=${motorId}`),
  comparison: (range: string, metric: string) => get<Record<string, any>>(`/api/analytics/comparison?range=${range}&metric=${metric}`),
  correlation: (range: string, motorId: MotorId) => get<Record<string, any>>(`/api/analytics/correlation?range=${range}&motor_id=${motorId}`),
  faultDistribution: (range: string) => get<Record<string, any>>(`/api/analytics/fault-distribution?range=${range}`),
  reliability: (range: string) => get<Record<string, any>>(`/api/analytics/reliability?range=${range}`),
  hourly: (days: number, motorId: MotorId) => get<Record<string, any>>(`/api/analytics/hourly?days=${days}&motor_id=${motorId}`),
  analyticsCsvUrl: (range: string) => `/api/analytics/export.csv?range=${range}`,
  analyticsReportUrl: (hours = 24) => `/api/analytics/report?hours=${hours}`,

  // --- Alertes / maintenance ---
  alerts: (params: Record<string, string | number | undefined> = {}) => {
    const query = new URLSearchParams(
      Object.entries(params).filter(([, v]) => v !== undefined && v !== '') as [string, string][],
    ).toString();
    return get<{ total: number; items: AlertItem[]; counts: Record<string, number>; severity_counts: Record<string, number> }>(`/api/alerts?${query}`);
  },
  updateAlert: (id: number, body: Record<string, unknown>) => patch<AlertItem>(`/api/alerts/${id}`, body),
  alertToWorkOrder: (id: number) => post<Record<string, any>>(`/api/alerts/${id}/work-order`),
  rules: () => get<Record<string, any>[]>('/api/rules'),
  createRule: (body: Record<string, unknown>) => post<Record<string, any>>('/api/rules', body),
  updateRule: (id: number, body: Record<string, unknown>) => put<Record<string, any>>(`/api/rules/${id}`, body),
  toggleRule: (id: number) => patch<Record<string, any>>(`/api/rules/${id}/toggle`),
  deleteRule: (id: number) => del<Record<string, any>>(`/api/rules/${id}`),
  notificationLog: () => get<{ items: Record<string, any>[]; channels: Record<string, any> }>('/api/notifications/log'),
  testNotification: (channel: string) => post<Record<string, any>>(`/api/notifications/test/${channel}`),
  prescriptions: () => get<Prescription[]>('/api/maintenance/prescriptions'),
  workOrders: () => get<Record<string, any>[]>('/api/maintenance/work-orders'),
  updateWorkOrder: (id: number, status: string) => patch<Record<string, any>>(`/api/maintenance/work-orders/${id}?status=${status}`),
  maintenanceStats: () => get<Record<string, any>>('/api/maintenance/stats'),
  allInterventions: () => get<Record<string, any>[]>('/api/maintenance/interventions'),

  // --- MLOps ---
  models: () => get<Record<string, any>>('/api/models'),
  modelMetrics: () => get<Record<string, any>>('/api/models/metrics'),
  modelHealth: () => get<Record<string, any>>('/api/models/health'),
  edgeComparison: () => get<Record<string, any>>('/api/models/edge-comparison'),
  rollbackCandidates: () => get<Record<string, any>[]>('/api/models/rollback-candidates'),
  rollback: (bundle?: string) => post<Record<string, any>>(`/api/models/rollback${bundle ? `?bundle=${bundle}` : ''}`),
  syncModel: () => post<Record<string, any>>('/api/models/sync'),
  reloadModel: () => post<Record<string, any>>('/api/models/reload'),

  // --- Devices ---
  devices: () => get<Record<string, any>>('/api/devices'),
  deviceNetwork: (minutes = 15) => get<Record<string, any>>(`/api/devices/network?minutes=${minutes}`),
  deviceSecurity: () => get<Record<string, any>>('/api/devices/security'),
  deviceLogs: (deviceId: string, limit = 60) => get<Record<string, any>[]>(`/api/devices/${deviceId}/logs?limit=${limit}`),
  network: () => get<Record<string, any>>('/api/devices/network'),
  security: () => get<Record<string, any>>('/api/devices/security'),
  deviceCommand: (deviceId: string, action: string, payload: Record<string, unknown> = {}) =>
    post<Record<string, any>>(`/api/devices/${deviceId}/command`, { action, payload }),
  simulateNetworkEvent: (deviceId: string, event: string) => post<Record<string, any>>(`/api/devices/${deviceId}/simulate-network-event?event=${event}`),
  resetTamper: (deviceId: string) => post<Record<string, any>>(`/api/devices/${deviceId}/reset-tamper`),

  // --- Simulateur ---
  simulatorState: () => get<Record<string, any>>('/api/simulator/state'),
  simulatorFaults: () => get<Record<string, any>[]>('/api/simulator/faults'),
  simulatorControl: (body: Record<string, unknown>) => post<Record<string, any>>('/api/simulator/control', body),
  injectFault: (body: Record<string, unknown>) => post<Record<string, any>>('/api/simulator/inject-fault', body),
  clearFaults: () => post<Record<string, any>>('/api/simulator/clear-faults'),
  runScenario: (name = 'demo-3min') => post<Record<string, any>>('/api/simulator/scenario', { name, duration_s: 174 }),
  stopScenario: () => post<Record<string, any>>('/api/simulator/stop-scenario'),
  resetDemoData: () => post<Record<string, any>>('/api/simulator/reset-data'),

  // --- Auth ---
  login: (email: string, password: string) => post<Record<string, any>>('/api/auth/login', { email, password }),
  me: () => get<Record<string, any>>('/api/auth/me'),
};

export type Api = typeof api;
