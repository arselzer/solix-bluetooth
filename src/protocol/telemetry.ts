import { SB3_PARAMS, type ParamDef } from './constants';
import { readUint16LE, readInt16LE, readUint32LE, readInt32LE, toHex } from './utils';
import type { TelemetryData } from './types';

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
  hw_version: 'HW Version',

  // C1000/C300X Power Station
  ac_power_in: 'AC Input (W)',
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
  'Solar': ['solar_power_total', 'solar_input_1', 'solar_input_2', 'solar_pv1_power', 'solar_pv2_power', 'solar_pv3_power', 'solar_pv4_power', 'total_pv_power', 'third_party_pv_power', 'pv_yield_total', 'pv_yield', 'inverter_power'],
  'Battery': ['battery_percentage', 'battery_percentage_aggregate', 'battery_health', 'charge_power', 'battery_charge_current', 'battery_power', 'battery_capacity', 'battery_voltage', 'battery_cycles', 'battery_resistance', 'battery_temperature', 'charging_state', 'battery_soc_raw'],
  'Output': ['output_power', 'house_demand', 'house_consumption', 'power_out', 'power_out_status', 'total_output_power', 'ac_power_in', 'ac_power_out', 'dc_power_out', 'type_c_power_out', 'usb_power_out'],
  'Grid': ['grid_power', 'grid_power_limit', 'grid_import_power', 'grid_import_limit', 'grid_export_current', 'grid_export_power', 'grid_to_home_power', 'grid_connection', 'grid_status', 'feed_in_limit'],
  'Settings': ['output_limit_setting', 'home_load_setting', 'max_output_power', 'max_charge_power', 'capacity_wh', 'max_ac_input_w', 'min_soc_pct', 'max_charge_soc', 'max_discharge_soc', 'charge_speed', 'display_timeout_s', 'idle_timeout_min', 'ups_mode', 'ups_reserve_pct', 'light_mode', 'led_mode', 'system_mode'],
  'Counters': ['cumulative_discharge_kwh', 'cumulative_demand_kwh', 'cumulative_consumption_kwh', 'cumulative_grid_kwh', 'energy_today', 'daily_pv_counter', 'charged_energy', 'discharged_energy', 'charge_sessions', 'discharge_sessions'],
  'Switches': ['ac_switch', 'dc_switch', 'ac_enabled', 'dc_enabled', 'ac_output_active', 'dc_output_active'],
  'Device': ['serial_number', 'model_name', 'device_type', 'firmware_version', 'firmware_info', 'hw_version', 'temperature', 'temperature_2', 'wifi_signal', 'wifi_rssi', 'bt_signal', 'online_status', 'current_hour', 'error_code', 'anti_replay_timestamp'],
};
