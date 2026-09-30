export type Metric = number | string | boolean | null;

export interface Station {
  name: string;
  model: string;
  protocol?: string;
  connected: boolean;
  available: boolean;
  last_seen_timestamp: number | null;
  power_flow?: string;
  timezone_name?: string | null;
  controls: string[];
  metrics: Record<string, Metric>;
}

export interface Sample {
  time: number;
  input: number | null;
  output: number | null;
  battery: number | null;
}

export type Command = { command: string } & Record<string, unknown>;
export interface Proposal {
  station: string;
  body: Command;
  title: string;
  detail: string;
  summary: string[];
}

export interface DraftPeriod {
  id: number;
  tariff: string;
  start: string;
  end: string;
}

export interface Draft {
  watts: string;
  upper: string;
  lower: string;
  reserve: string;
  seconds: string;
  timeoutMinutes: string;
  fast: string;
  light: string;
  fahrenheit: string;
  alert: string;
  periods: DraftPeriod[];
}

export function numberMetric(station: Station, key: string): number | null {
  const value = station.metrics[key];
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

export function powerMetric(station: Station, direction: 'input' | 'output'): number | null {
  const total = numberMetric(station, `total_${direction}_power_w`) ?? numberMetric(station, `${direction}_power_w`);
  if (total !== null) return total;
  const ac = numberMetric(station, `ac_${direction}_power_w`);
  const dc = numberMetric(station, `dc_${direction}_power_w`)
    ?? (direction === 'input' ? numberMetric(station, 'solar_input_power_w') : null);
  return ac === null && dc === null ? null : (ac ?? 0) + (dc ?? 0);
}

export function telemetryFresh(station: Station, now: number): boolean {
  const latest = station.last_seen_timestamp;
  const age = typeof latest === 'number' ? now / 1000 - latest : NaN;
  return station.available && station.connected && Number.isFinite(age)
    && age >= -5 && age < (station.protocol === 'native_mqtt' ? 30 : 90);
}

export function modelLabel(model: string): string {
  return { c300: 'C300 AC', c1000: 'C1000', c1000_gen2: 'C1000 Gen 2', c2000_gen2: 'C2000 Gen 2' }[model] ?? 'SOLIX station';
}

export function draftFor(station: Station): Draft {
  const current = (key: string, fallback: string) => String(station.metrics[key] ?? fallback);
  return {
    watts: current('ac_charging_power_limit_w', station.model === 'c300' ? '300' : '800'),
    upper: current('max_charge_percentage', '100'),
    lower: current('min_charge_percentage', '1'),
    reserve: current('backup_reserve_percentage', '10'),
    seconds: current('display_timeout_seconds', '30'),
    timeoutMinutes: current('device_timeout_minutes', ''),
    fast: current('ac_fast_charge_enabled', '0'),
    light: current('light_mode', '0'),
    fahrenheit: current('temperature_unit_fahrenheit', '0'),
    alert: current('ac_off_grid_alert_enabled', '0'),
    periods: [],
  };
}
