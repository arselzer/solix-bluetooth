// Anker Solix BLE Protocol Constants
// Based on SolixBLE project (https://github.com/flip-dots/SolixBLE)

// BLE UUIDs
export const SERVICE_UUID = '8c850001-0302-41c5-b46e-cf057c562025';
export const UUID_COMMAND = '8c850002-0302-41c5-b46e-cf057c562025';
export const UUID_TELEMETRY = '8c850003-0302-41c5-b46e-cf057c562025';

// UUID used in advertising data for device discovery
export const UUID_IDENTIFIER = '0000ff09-0000-1000-8000-00805f9b34fb';

// Packet framing
export const HEADER = 0xff09;
export const PATTERN_NEGOTIATION = new Uint8Array([0x03, 0x00, 0x01]);
export const PATTERN_ENCRYPTED = new Uint8Array([0x03, 0x01, 0x0f]);
export const PATTERN_ENCRYPTED_ALT = new Uint8Array([0x03, 0x01, 0x11]);

// ECDH Private Key (hardcoded in SolixBLE)
export const ECDH_PRIVATE_KEY_HEX = '7dfbea61cd95cee49c458ad7419e817f1ade9a66136de3c7d5787af1458e39f4';

// AES-GCM auth data for Prime devices
export const GCM_AUTH_DATA = new Uint8Array([
  0x33, 0x22, 0x11, 0x00, 0x77, 0x66, 0x55, 0x44,
  0xbb, 0xaa, 0x99, 0x88, 0xff, 0xee, 0xdd, 0xcc
]);

// Negotiation command bytes
export const NEGOTIATION_COMMANDS: Record<number, Uint8Array> = {
  0: new Uint8Array([0x01, 0x0a]),
  1: new Uint8Array([0x03, 0x0a]),
  2: new Uint8Array([0x29, 0x0a]),
  3: new Uint8Array([0x05, 0x0a]),
  4: new Uint8Array([0x21, 0x0a]),
};

// Negotiation response command identifiers
export const NEGOTIATION_RESPONSE_MAP: Record<number, number> = {
  // response command byte -> next stage to send
  0x01: 1,  // stage 0 response -> send stage 1
  0x03: 2,  // stage 1 response -> send stage 2
  0x29: 3,  // stage 2 response -> send stage 3
  0x05: 4,  // stage 3 response -> send stage 4
  0x21: 5,  // stage 4 response -> derive keys (stage 5)
};

// Solarbank 3 Pro (A17C5) telemetry parameter IDs
export const SB3_PARAMS: Record<number, { name: string; type: 'string' | 'int' | 'signed_int' | 'float'; divisor?: number }> = {
  0xa2: { name: 'serial_number', type: 'string' },
  0xa5: { name: 'battery_percentage_aggregate', type: 'float', divisor: 10 },
  0xa6: { name: 'battery_health', type: 'float', divisor: 10 },
  0xa7: { name: 'battery_percentage', type: 'int' },
  0xab: { name: 'solar_power_in', type: 'int' },
  0xac: { name: 'pv_yield', type: 'int' },
  0xb1: { name: 'house_demand', type: 'int' },
  0xb2: { name: 'house_consumption', type: 'int' },
  0xb6: { name: 'battery_power', type: 'signed_int' },
  0xb7: { name: 'charged_energy', type: 'int' },
  0xb8: { name: 'discharged_energy', type: 'int' },
  0xbd: { name: 'grid_power', type: 'signed_int' },
  0xbe: { name: 'grid_import_energy', type: 'int' },
  0xbf: { name: 'grid_export_energy', type: 'int' },
  0xc7: { name: 'solar_pv1_power', type: 'int' },
  0xc8: { name: 'solar_pv2_power', type: 'int' },
  0xc9: { name: 'solar_pv3_power', type: 'int' },
  0xca: { name: 'solar_pv4_power', type: 'int' },
  0xcc: { name: 'temperature', type: 'signed_int' },
  0xd3: { name: 'power_out', type: 'int' },
  0xd5: { name: 'grid_to_home_power', type: 'int' },
};

// Telemetry identification bytes
export const TELEMETRY_MARKER_BYTE_8 = 0x05;
export const TELEMETRY_MARKER_BYTE_9 = 0x13;

// Anti-replay base timestamp
export const BASE_TIMESTAMP = 1672531200; // 2023-01-01 00:00:00 UTC
