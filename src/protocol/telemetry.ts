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

    if (paramDef) {
      try {
        const valueData = paramDef.skipFirst && paramData.length > 1
          ? paramData.slice(1)
          : paramData;
        const decoded = decodeParam(paramDef, valueData);
        result[paramDef.name] = decoded;
        entry.decoded = decoded;
      } catch (e) {
        console.warn(`[TLV] 0x${entry.paramIdHex} (${paramDef.name}): decode failed`, e);
        result[`raw_${entry.paramIdHex}`] = toHex(paramData);
      }
    } else {
      result[`unknown_${entry.paramIdHex}`] = toHex(paramData);
    }

    tlvEntries.push(entry);
  }

  return { data: result, tlvEntries };
}

export function parseTelemetry(decryptedPayload: Uint8Array, paramMap?: Record<number, ParamDef>): TelemetryData {
  return parseTelemetryDetailed(decryptedPayload, paramMap).data;
}

function decodeParam(
  paramDef: { name: string; type: string; divisor?: number },
  data: Uint8Array
): string | number {
  switch (paramDef.type) {
    case 'string':
      return new TextDecoder().decode(data);

    case 'int':
      if (data.length === 1) return data[0];
      if (data.length === 2) return readUint16LE(data, 0);
      if (data.length === 4) return readUint32LE(data, 0);
      return readUint16LE(data, 0);

    case 'signed_int':
      if (data.length === 1) return data[0] > 127 ? data[0] - 256 : data[0];
      if (data.length === 2) return readInt16LE(data, 0);
      if (data.length === 4) return readInt32LE(data, 0);
      return readInt16LE(data, 0);

    case 'float': {
      let raw: number;
      if (data.length === 1) raw = data[0];
      else if (data.length === 2) raw = readUint16LE(data, 0);
      else if (data.length === 4) raw = readUint32LE(data, 0);
      else raw = readUint16LE(data, 0);
      return paramDef.divisor ? raw / paramDef.divisor : raw;
    }

    default:
      return toHex(data);
  }
}

// Pretty label mapping for display
export const PARAM_LABELS: Record<string, string> = {
  // Common
  device_type: 'Device Type',
  serial_number: 'Serial Number',
  wifi_signal: 'WiFi Signal',
  bt_signal: 'BT Signal',
  firmware_version: 'Firmware',
  temperature: 'Temperature (C)',

  // Battery
  battery_percentage_aggregate: 'Battery %',
  battery_health: 'Battery Health %',
  battery_percentage: 'Battery Level',
  battery_capacity: 'Battery Capacity',
  battery_power: 'Battery Power (W)',
  charged_energy: 'Charged (kWh)',
  discharged_energy: 'Discharged (kWh)',
  charge_power: 'Charge Power (W)',
  discharge_power: 'Discharge Power (W)',

  // Solar
  solar_power_in: 'Solar Input (W)',
  solar_pv1_power: 'PV1 (W)',
  solar_pv2_power: 'PV2 (W)',
  solar_pv3_power: 'PV3 (W)',
  solar_pv4_power: 'PV4 (W)',
  pv_yield: 'PV Yield (kWh)',

  // Home / Output
  house_demand: 'House Demand (W)',
  house_consumption: 'Consumption (W)',
  power_out: 'Output (W)',
  output_power: 'Output Power (W)',
  output_limit: 'Output Limit (W)',
  input_limit: 'Input Limit (W)',

  // Grid
  grid_power: 'Grid Power (W)',
  grid_import_energy: 'Grid Import (kWh)',
  grid_export_energy: 'Grid Export (kWh)',
  grid_to_home_power: 'Grid to Home (W)',
  grid_status: 'Grid Status',

  // C1000 specific
  ac_power_in: 'AC Input (W)',
  ac_power_out: 'AC Output (W)',
  dc_power_out: 'DC Output (W)',
  type_c_power_out: 'USB-C Output (W)',
  usb_power_out: 'USB-A Output (W)',
  ac_switch: 'AC Switch',
  dc_switch: 'DC Switch',
  wifi_name: 'WiFi Name',
};

// Display grouping for organized layout
export const PARAM_GROUPS: Record<string, string[]> = {
  'Battery': ['battery_percentage', 'battery_percentage_aggregate', 'battery_health', 'battery_power', 'battery_capacity', 'charge_power', 'discharge_power', 'charged_energy', 'discharged_energy'],
  'Solar': ['solar_power_in', 'solar_pv1_power', 'solar_pv2_power', 'solar_pv3_power', 'solar_pv4_power', 'pv_yield'],
  'Home': ['house_demand', 'house_consumption', 'power_out', 'output_power', 'output_limit', 'input_limit'],
  'Grid': ['grid_power', 'grid_to_home_power', 'grid_import_energy', 'grid_export_energy', 'grid_status'],
  'Power Station': ['ac_power_in', 'ac_power_out', 'dc_power_out', 'type_c_power_out', 'usb_power_out', 'ac_switch', 'dc_switch'],
  'Device': ['serial_number', 'device_type', 'firmware_version', 'temperature', 'wifi_signal', 'bt_signal', 'wifi_name'],
};
