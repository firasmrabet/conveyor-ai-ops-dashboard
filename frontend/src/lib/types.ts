/** Types partagés avec l'API FastAPI (contrat de données exact du projet). */
export type MotorId = 'M1' | 'M2';
export type Severity = 'INFO' | 'WARNING' | 'CRITICAL';
export type AlertStatus = 'NEW' | 'ACKNOWLEDGED' | 'RESOLVED';
export type FaultClass =
  | 'HEALTHY' | 'BEARING_FAULT' | 'UNBALANCE' | 'MISALIGNMENT'
  | 'BELT_SLIP' | 'OVERLOAD' | 'OVERHEATING';

export interface Telemetry {
  device_id: string;
  motor_id: MotorId;
  ts: number;
  vibration: { ax: number; ay: number; az: number; rms: number; peak: number; kurtosis: number; crest_factor: number };
  fft_peaks: { freq: number; amp: number }[];
  current: { rms_a: number; peak_a: number; power_w: number };
  temperature: { motor_c: number; bearing_c: number; ambient_c: number };
  acoustic: { spl_db: number; band_energy: number[] };
  speed: { rpm: number; steps_per_s: number; belt_mm_s: number };
  state: string;
  target_speed_rpm?: number;
}

export interface Inference {
  motor_id: MotorId;
  ts: number;
  health_index: number;
  anomaly_score: number;
  is_anomaly: boolean;
  fault_class: FaultClass;
  fault_probabilities: Record<string, number>;
  rul_hours: number;
  rul_confidence: { low: number; high: number };
  explanation: { feature: string; label?: string; contribution: number; deviation?: number }[];
  model_version: string;
  inference_ms: number;
}

export interface AlertItem {
  id: number;
  ts: number;
  motor_id: string;
  severity: Severity;
  type: string;
  origin: string;
  metric: string;
  value: number;
  threshold: number;
  message: string;
  status: AlertStatus;
  assigned_to?: string;
  comment?: string;
  context?: Record<string, unknown>;
}

export interface Kpis {
  line_state: string;
  availability_pct: number;
  global_health: number;
  health_m1: number;
  health_m2: number;
  active_alerts: number;
  defects_24h: number;
  total_power_w: number;
  parcels_per_min: number;
  belt_mm_s: number;
  running_hours_total: number;
  open_work_orders: number;
  interventions_total: number;
}

export interface MotorSummary {
  id: MotorId;
  label: string;
  side: string;
  model?: string;
  state: string;
  health_index: number;
  anomaly_score: number;
  fault_class: FaultClass;
  rul_hours: number;
  rpm: number;
  power_w: number;
  temp_bearing_c: number;
  vibration_rms: number;
  edge_health_index?: number;
  model_version?: string;
  running_hours?: number;
  last_maintenance?: string;
}

export interface Prescription {
  motor_id: string;
  fault_class: string;
  title: string;
  action: string;
  priority: string;
  estimated_hours: number;
  due_in_days: number;
  parts: string[];
  health_index: number;
  rul_hours: number;
  confidence: number;
  rationale: string;
}

export interface Overview {
  generated_at: number;
  kpis: Kpis;
  motors: Record<MotorId, { telemetry: Partial<Telemetry>; inference: Partial<Inference>; edge: Record<string, unknown>; sparkline: { ts: number; vibration: number; current: number; temperature: number; rpm: number }[] }>;
  status: Record<string, number | string | boolean>;
  alerts: AlertItem[];
  prescriptions: Prescription[];
  rates: Record<string, number>;
  mqtt: Record<string, unknown>;
  model: Record<string, unknown>;
  edge_comparison: Record<string, { agreement: number; samples: number; edge_ms: number; server_ms: number; mismatch: number }>;
  simulator: Record<string, unknown> | null;
}

export interface WsMessage {
  type: 'snapshot' | 'telemetry' | 'inference' | 'features' | 'alert' | 'alert_update' | 'status' | 'rates' | 'summary' | 'heartbeat' | 'edge_inference' | 'scenario' | 'fault_injected' | 'device_command' | 'ping' | 'pong';
  motor_id?: MotorId;
  data?: any;
}
