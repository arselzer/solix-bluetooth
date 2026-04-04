import { SB3_PARAMS } from './constants';
import { readUint16LE, readInt16LE, readUint32LE, readInt32LE, toHex } from './utils';
import type { TelemetryData } from './types';

export function parseTelemetry(decryptedPayload: Uint8Array): TelemetryData {
  const result: TelemetryData = {};
  let offset = 0;

  // Strip leading 0x00 byte if present (as done in SolixBLE)
  if (decryptedPayload.length > 0 && decryptedPayload[0] === 0x00) {
    offset = 1;
  }

  while (offset < decryptedPayload.length) {
    const paramId = decryptedPayload[offset];
    offset++;

    if (offset >= decryptedPayload.length) break;

    const paramLength = decryptedPayload[offset];
    offset++;

    if (paramLength === 0) continue;

    if (offset + paramLength > decryptedPayload.length) {
      console.warn(`[Telemetry] Param 0x${paramId.toString(16)}: need ${paramLength} bytes but only ${decryptedPayload.length - offset} left`);
      break;
    }

    const paramData = decryptedPayload.slice(offset, offset + paramLength);
    offset += paramLength;

    const paramDef = SB3_PARAMS[paramId];
    if (paramDef) {
      try {
        // SolixBLE skips the first byte of param data (type byte) for value parsing
        const valueData = paramDef.skipFirst && paramData.length > 1
          ? paramData.slice(1)
          : paramData;
        result[paramDef.name] = decodeParam(paramDef, valueData);
      } catch (e) {
        console.warn(`[Telemetry] Failed to decode param 0x${paramId.toString(16)} (${paramDef.name}):`, e);
        result[`raw_${paramId.toString(16)}`] = toHex(paramData);
      }
    } else {
      result[`unknown_${paramId.toString(16)}`] = toHex(paramData);
    }
  }

  return result;
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
  serial_number: 'Serial Number',
  battery_percentage_aggregate: 'Battery %',
  battery_health: 'Battery Health %',
  battery_percentage: 'Battery Level',
  solar_power_in: 'Solar Input (W)',
  pv_yield: 'PV Yield',
  house_demand: 'House Demand (W)',
  house_consumption: 'Consumption (W)',
  battery_power: 'Battery Power (W)',
  charged_energy: 'Charged Energy',
  discharged_energy: 'Discharged Energy',
  grid_power: 'Grid Power (W)',
  grid_import_energy: 'Grid Import',
  grid_export_energy: 'Grid Export',
  solar_pv1_power: 'PV1 Power (W)',
  solar_pv2_power: 'PV2 Power (W)',
  solar_pv3_power: 'PV3 Power (W)',
  solar_pv4_power: 'PV4 Power (W)',
  temperature: 'Temperature (C)',
  power_out: 'Power Out (W)',
  grid_to_home_power: 'Grid to Home (W)',
};
