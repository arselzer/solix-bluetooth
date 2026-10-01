import { SB3_PARAMS, type ParamDef } from './constants';
import { readUint16LE, readUint32LE, toHex } from './utils';
import type { TelemetryData } from './types';

// C1000 Gen 2 packs several readings into each TLV value. These offsets are
// from SolixBLE's C1000G2 decoder; the first byte is a type marker.
export function parseC1000Gen2Telemetry(payload: Uint8Array, model: 'c1000' | 'c2000' = 'c1000'): { data: TelemetryData; tlvEntries: TlvEntry[] } {
  const data: TelemetryData = {};
  const tlvEntries: TlvEntry[] = [];
  const values = new Map<number, Uint8Array>();
  let offset = payload[0] === 0 ? 1 : 0;
  while (offset + 2 <= payload.length) {
    const entryOffset = offset;
    const id = payload[offset++];
    const length = payload[offset++];
    if (offset + length > payload.length) break;
    const value = payload.slice(offset, offset + length);
    offset += length;
    values.set(id, value);
    tlvEntries.push({
      offset: entryOffset, paramId: id, paramIdHex: id.toString(16).padStart(2, '0'),
      length, rawHex: toHex(value), name: null, decoded: null,
    });
  }

  const number = (id: number, start: number, end: number, signed = false): number | undefined => {
    const value = values.get(id);
    if (!value || end > value.length || end <= start) return undefined;
    const bytes = value.slice(start, end);
    let result = 0;
    for (let i = 0; i < bytes.length; i++) result += bytes[i] * 2 ** (8 * i);
    if (signed && bytes[bytes.length - 1] & 0x80) result -= 2 ** (8 * bytes.length);
    return result;
  };
  const put = (name: string, id: number, start: number, end: number, signed = false) => {
    const value = number(id, start, end, signed);
    if (value !== undefined) data[name] = value;
  };
  put('temperature', 0xa5, 1, 2, true);
  put('battery_percentage', 0xa5, 3, 4);
  // A1763/main 1.1.4.9 returns literal 100 here, not measured state of health.
  put('battery_health_raw', 0xa5, 4, 5);
  put('total_output_power', 0xa6, 1, 3);
  put('ac_power_in', 0xa6, 3, 5);
  put('ac_charging_power_limit_w', 0xa4, 5, 7);
  if (model === 'c1000') {
    const settings = values.get(0xa4);
    if (settings?.length === 34 && settings[0] === 4) {
      // A1763 saved configuration; neither frequency measurement nor output state.
      data.ac_output_frequency_setting_hz = [50, 60].includes(settings[7]) ? settings[7] : 'unknown';
      data.ac_power_saving_mode_enabled = settings[8] <= 1 ? settings[8] : 'unknown';
      data.dc_power_saving_mode_enabled = settings[13] <= 1 ? settings[13] : 'unknown';
    }
    const pv = values.get(0xa8);
    if (pv?.length === 4 && pv[0] === 4 && (pv[1] === 0 || pv[1] === 1)) {
      data.dc_input_active = pv[1];
    }
    const power = values.get(0xa6);
    if (power?.length === 10 && power[0] === 4) {
      // A8 can retain old power during incremental updates; prefer full A6.
      put('dc_input_power_raw', 0xa6, 5, 7);
    }
    const state = values.get(0xa3);
    if (state?.length === 14 && state[0] === 4) {
      data.controller_error_code = state[2];
    }
  }
  if (model === 'c2000') {
    const settings = values.get(0xa4);
    if (settings?.length === 34 && settings[0] === 4) data.ac_frequency_raw = settings[7];
    const versions = values.get(0xf9);
    if (versions) {
      for (const [name, slot] of [
        ['software_version', 0], ['software_version_controller', 1],
        ['software_version_inverter', 3], ['software_version_bms', 4],
        ['software_version_module', 6],
      ] as const) {
        const start = slot * 4;
        if (versions.length >= start + 4) {
          data[name] = Array.from(versions.slice(start, start + 4)).reverse().join('.');
        }
      }
    }
    const expansion = values.get(0xc0);
    if (expansion && expansion.length > 1 + expansion[0]) {
      const tail = expansion.slice(1 + expansion[0]);
      if (tail.length >= 15) data.expansion_battery_count = tail[12] === 1 ? 1 : 0;
    }
  }
  put('ac_switch', 0xa7, 1, 2);
  put('ac_power_out', 0xa7, 2, 4);
  put('ac_input_connected', 0xa7, 4, 5);
  const workStatus = number(0xa3, 1, 2);
  if (workStatus !== undefined) {
    data.battery_status = ({ 0: 'idle', 1: 'discharging', 2: 'charging' } as Record<number, string>)[workStatus] ?? 'unknown';
    data.battery_discharging = workStatus === 1 ? 1 : 0;
    const remainingTenthsHours = number(0xa6, 7, 9);
    if (remainingTenthsHours !== undefined) {
      data.time_remaining_minutes = workStatus === 1 || workStatus === 2 ? remainingTenthsHours * 6 : 0;
    }
  }
  put('dc_switch', 0xb2, 1, 2);
  put('dc_power_out', 0xb2, 2, 4);
  put('max_charge_soc', 0xd9, 4, 5);
  put('min_soc_pct', 0xd9, 5, 6);

  const identity = values.get(0xa2);
  if (identity && identity.length >= 27) {
    data.serial_number = new TextDecoder().decode(identity.slice(3, 20)).replace(/\0+$/, '');
    data.model_name = new TextDecoder().decode(identity.slice(22, 27)).replace(/\0+$/, '');
  }
  return { data, tlvEntries };
}

export interface TlvEntry {
  offset: number;
  paramId: number;
  paramIdHex: string;
  length: number;
  rawHex: string;
  name: string | null;
  decoded: string | number | null;
}

export function parseTelemetryDetailed(decryptedPayload: Uint8Array, paramMap?: Record<number, ParamDef>): { data: TelemetryData; tlvEntries: TlvEntry[] } {
  const params = paramMap ?? SB3_PARAMS;
  const result: TelemetryData = {};
  const tlvEntries: TlvEntry[] = [];
  let offset = 0;

  // Strip leading 0x00 byte if present (as done in SolixBLE)
  if (decryptedPayload.length > 0 && decryptedPayload[0] === 0x00) {
    offset = 1;
  }

  while (offset < decryptedPayload.length) {
    const entryOffset = offset;
    const paramId = decryptedPayload[offset];
    offset++;

    if (offset >= decryptedPayload.length) break;

    const paramLength = decryptedPayload[offset];
    offset++;

    if (paramLength === 0) continue;

    if (offset + paramLength > decryptedPayload.length) {
      console.warn(`[TLV] 0x${paramId.toString(16)} @${entryOffset}: need ${paramLength}B but only ${decryptedPayload.length - offset} left`);
      break;
    }

    const paramData = decryptedPayload.slice(offset, offset + paramLength);
    offset += paramLength;

    const paramDef = params[paramId];
    const entry: TlvEntry = {
      offset: entryOffset,
      paramId,
      paramIdHex: paramId.toString(16).padStart(2, '0'),
      length: paramLength,
      rawHex: toHex(paramData),
      name: paramDef?.name ?? null,
      decoded: null,
    };

    try {
      // Use the type byte (first byte) to auto-decode the value
      const decoded = decodeByTypeByte(paramData);
      entry.decoded = decoded;

      if (paramDef) {
        let value = decoded;
        if (paramDef.divisor && typeof value === 'number') {
          value = value / paramDef.divisor;
        }
        result[paramDef.name] = value;
      } else {
        result[`unknown_${entry.paramIdHex}`] = decoded;
      }
    } catch (e) {
      console.warn(`[TLV] 0x${entry.paramIdHex}: decode failed`, e);
      const key = paramDef ? `raw_${entry.paramIdHex}` : `unknown_${entry.paramIdHex}`;
      result[key] = toHex(paramData);
    }

    tlvEntries.push(entry);
  }

  return { data: result, tlvEntries };
}

export function parseTelemetry(decryptedPayload: Uint8Array, paramMap?: Record<number, ParamDef>): TelemetryData {
  return parseTelemetryDetailed(decryptedPayload, paramMap).data;
}

// The first byte of each TLV param data is a type indicator:
// 0x00 = raw string/bytes (skip this byte, rest is ASCII)
// 0x01 = uint8 (1 byte value follows)
// 0x02 = uint16 LE (2 byte value follows)
// 0x03 = uint32 LE (4 byte value follows)
// 0x04 = bytes/string (variable length follows)
// 0x05 = float32 LE (4 byte IEEE 754 follows)
function decodeByTypeByte(data: Uint8Array): string | number {
  if (data.length === 0) return 0;
  if (data.length === 1) return data[0]; // No type byte, just a raw value

  const typeByte = data[0];
  const valueData = data.slice(1);

  switch (typeByte) {
    case 0x00: // ASCII string
      return new TextDecoder().decode(valueData);

    case 0x01: // uint8
      return valueData.length >= 1 ? valueData[0] : 0;

    case 0x02: // uint16 LE
      return valueData.length >= 2 ? readUint16LE(valueData, 0) : (valueData.length >= 1 ? valueData[0] : 0);

    case 0x03: // uint32 LE
      return valueData.length >= 4 ? readUint32LE(valueData, 0) : (valueData.length >= 2 ? readUint16LE(valueData, 0) : 0);

    case 0x04: { // bytes/string
      // Try ASCII if printable, otherwise hex
      const ascii = new TextDecoder().decode(valueData);
      if (/^[\x20-\x7e]*$/.test(ascii) && ascii.length > 0) return ascii;
      return toHex(valueData);
    }

    case 0x05: { // float32 LE (IEEE 754)
      if (valueData.length >= 4) {
        const buf = new ArrayBuffer(4);
        const view = new DataView(buf);
        view.setUint8(0, valueData[0]);
        view.setUint8(1, valueData[1]);
        view.setUint8(2, valueData[2]);
        view.setUint8(3, valueData[3]);
        return Math.round(view.getFloat32(0, true) * 100) / 100;
      }
      return 0;
    }

    default:
      // Unknown type byte — treat as raw int with skipFirst
      if (valueData.length === 1) return valueData[0];
      if (valueData.length === 2) return readUint16LE(valueData, 0);
      if (valueData.length === 4) return readUint32LE(valueData, 0);
      return toHex(data);
  }
}

// Pretty label mapping for display
export const PARAM_LABELS: Record<string, string> = {
  // Common
  device_type: 'Device Type',
  serial_number: 'Serial Number',
  model_name: 'Model',
  wifi_signal: 'WiFi Signal',
  wifi_rssi: 'WiFi RSSI',
  bt_signal: 'BT Signal',
  firmware_version: 'Firmware',
  hw_version: 'HW Version',
  temperature: 'Temperature (C)',
  battery_temperature: 'Battery Temp (C)',
  online_status: 'Online',
  current_hour: 'Clock (hour)',

  // Battery
  battery_percentage: 'Battery %',
  battery_percentage_aggregate: 'Battery % (agg)',
  battery_health: 'Battery Health',
  battery_health_raw: 'Battery Compatibility Byte (raw)',
  dc_input_active: 'DC/PV Input Active',
  dc_input_power_raw: 'DC/PV Input Power (raw)',
  controller_error_code: 'Controller Error Code (raw)',
  battery_capacity: 'Battery Capacity',
  battery_power: 'Battery Power (W)',
  battery_cycles: 'Battery Cycles',
  battery_resistance: 'Battery R (mOhm)',
  battery_voltage: 'Battery Voltage',
  battery_soc_raw: 'Battery SoC Raw',
  battery_info: 'Battery Info',
  firmware_info: 'Firmware Info',
  charge_sessions: 'Charge Sessions',
  discharge_sessions: 'Discharge Sessions',
  charged_energy: 'Charged (kWh)',
  discharged_energy: 'Discharged (kWh)',
  charge_power: 'Charge Power (W)',
  discharge_power: 'Discharge Power (W)',
  charging_state: 'Charging State',
  battery_charge_current: 'Battery Charge (W)',
  battery_status: 'Battery State',
  battery_discharging: 'Battery Discharging',
  time_remaining_minutes: 'Time Remaining (min)',

  // Solar
  solar_power_total: 'Solar Total (W)',
  solar_power_in: 'Solar Input (W)',
  solar_input_1: 'Solar Input 1 (W)',
  solar_input_2: 'Solar Input 2 (W)',
  solar_pv1_power: 'PV1 (W)',
  solar_pv2_power: 'PV2 (W)',
  solar_pv3_power: 'PV3 (W)',
  solar_pv4_power: 'PV4 (W)',
  total_pv_power: 'Total PV (W)',
  third_party_pv_power: 'Third-Party PV (W)',
  pv_yield: 'PV Yield (kWh)',
  pv_yield_total: 'PV Yield Total (W)',
  daily_pv_counter: 'Daily PV Counter',

  // Home / Output
  house_demand: 'House Demand (W)',
  house_consumption: 'Consumption (W)',
  power_out: 'Output (W)',
  output_power: 'Output Power (W)',
  power_out_status: 'Output Status',
  total_output_power: 'Total Output (W)',
  output_limit: 'Output Limit (W)',
  input_limit: 'Input Limit (W)',
  output_limit_setting: 'Output Limit Set (W)',
  home_load_setting: 'Home Load Set',
  feed_in_limit: 'Feed-in Limit (W)',
  max_output_power: 'Max Output (W)',
  max_charge_power: 'Max Charge (W)',

  // Grid
  grid_power: 'Grid Power (W)',
  grid_power_limit: 'Grid Power Limit (W)',
  grid_import_energy: 'Grid Import (kWh)',
  grid_import_limit: 'Grid Import Limit (W)',
  grid_import_power: 'Grid Import (W)',
  grid_export_energy: 'Grid Export (kWh)',
  grid_export_power: 'Grid Export (W)',
  grid_export_current: 'Grid Export Now (W)',
  grid_to_home_power: 'Grid to Home (W)',
  grid_status: 'Grid Status',
  grid_connection: 'Grid Connection',

  // Cumulative Counters
  cumulative_discharge_kwh: 'Total Discharge (kWh)',
  cumulative_demand_kwh: 'Total Demand (kWh)',
  cumulative_consumption_kwh: 'Total Consumption (kWh)',
  cumulative_grid_kwh: 'Total Grid (kWh)',
  energy_today: 'Energy Today',
  inverter_power: 'Inverter (W)',

  // System
  system_mode: 'System Mode',
  system_flags: 'System Flags',
  device_config: 'Device Config',
  anti_replay_timestamp: 'Timestamp',
  temperature_2: 'Temperature 2 (C)',

  // C300X specific
  battery_capacity_wh: 'Battery Capacity (Wh)',
  total_discharged_wh: 'Total Discharged (Wh)',
  battery_soc: 'Battery SoC',
  battery_health_wh: 'Battery Health (Wh)',
  screen_brightness: 'Screen Brightness',
  ac_power_limit: 'AC Power Limit (W)',
  dc_power_limit: 'DC Power Limit (W)',
  max_solar_input_w: 'Max Solar Input (W)',
  system_info: 'System Info',

  // C1000/C300X Power Station
  ac_power_in: 'AC Input (W)',
  ac_input_connected: 'AC Input Connected',
  ac_frequency_raw: 'AC Frequency Byte (Raw)',
  ac_output_frequency_setting_hz: 'AC Output Frequency Setting (Hz)',
  ac_power_saving_mode_enabled: 'AC Smart Mode Enabled',
  dc_power_saving_mode_enabled: 'DC Smart Mode Enabled',
  ac_charging_power_limit_w: 'AC Charging Limit (W)',
  expansion_battery_count: 'Expansion Batteries',
  ac_power_out: 'AC Output (W)',
  dc_power_out: 'DC Output (W)',
  type_c_power_out: 'USB-C Out (W)',
  usb_power_out: 'USB-A Out (W)',
  ac_switch: 'AC Switch',
  dc_switch: 'DC Switch',
  ac_output_active: 'AC Active',
  dc_output_active: 'DC Active',
  ac_enabled: 'AC Enabled',
  dc_enabled: 'DC Enabled',
  error_code: 'Error Code',
  software_version: 'Main Software',
  software_version_controller: 'Controller Software',
  software_version_inverter: 'Inverter Software',
  software_version_bms: 'Battery Software',
  software_version_module: 'Wireless Software',

  // C1000 Settings
  capacity_wh: 'Capacity (Wh)',
  max_ac_input_w: 'Max AC Input (W)',
  display_timeout_s: 'Display Off (s)',
  idle_timeout_min: 'Idle Off (min)',
  ups_mode: 'UPS Mode',
  ups_reserve_pct: 'UPS Reserve %',
  min_soc_pct: 'Min SoC %',
  max_charge_soc: 'Max Charge %',
  max_discharge_soc: 'Max Discharge %',
  charge_speed: 'Charge Speed',
  light_mode: 'Light Mode',
  led_mode: 'LED Mode',
};

// Display grouping for organized layout
export const PARAM_GROUPS: Record<string, string[]> = {
  'Diagnostics': ['battery_health_raw', 'dc_input_active', 'dc_input_power_raw', 'controller_error_code'],
  'Solar': ['solar_power_total', 'solar_input_1', 'solar_input_2', 'solar_pv1_power', 'solar_pv2_power', 'solar_pv3_power', 'solar_pv4_power', 'total_pv_power', 'third_party_pv_power', 'pv_yield_total', 'pv_yield', 'inverter_power'],
  'Battery': ['battery_percentage', 'battery_percentage_aggregate', 'battery_health', 'charge_power', 'battery_charge_current', 'battery_power', 'battery_capacity', 'battery_voltage', 'battery_cycles', 'battery_resistance', 'battery_temperature', 'charging_state', 'battery_soc_raw', 'battery_status', 'battery_discharging', 'time_remaining_minutes'],
  'Output': ['output_power', 'house_demand', 'house_consumption', 'power_out', 'power_out_status', 'total_output_power', 'ac_power_in', 'ac_power_out', 'dc_power_out', 'type_c_power_out', 'usb_power_out'],
  'Grid': ['grid_power', 'grid_power_limit', 'grid_import_power', 'grid_import_limit', 'grid_export_current', 'grid_export_power', 'grid_to_home_power', 'grid_connection', 'grid_status', 'feed_in_limit', 'ac_input_connected', 'ac_frequency_raw'],
  'Settings': ['output_limit_setting', 'home_load_setting', 'max_output_power', 'max_charge_power', 'capacity_wh', 'max_ac_input_w', 'min_soc_pct', 'max_charge_soc', 'max_discharge_soc', 'charge_speed', 'display_timeout_s', 'idle_timeout_min', 'ups_mode', 'ups_reserve_pct', 'light_mode', 'led_mode', 'system_mode', 'ac_charging_power_limit_w'],
  'Counters': ['cumulative_discharge_kwh', 'cumulative_demand_kwh', 'cumulative_consumption_kwh', 'cumulative_grid_kwh', 'energy_today', 'daily_pv_counter', 'charged_energy', 'discharged_energy', 'charge_sessions', 'discharge_sessions'],
  'Switches': ['ac_switch', 'dc_switch', 'ac_enabled', 'dc_enabled', 'ac_output_active', 'dc_output_active'],
  'Device': ['serial_number', 'model_name', 'device_type', 'firmware_version', 'firmware_info', 'hw_version', 'temperature', 'temperature_2', 'wifi_signal', 'wifi_rssi', 'bt_signal', 'online_status', 'current_hour', 'error_code', 'anti_replay_timestamp', 'software_version', 'software_version_controller', 'software_version_inverter', 'software_version_bms', 'software_version_module', 'expansion_battery_count'],
};
