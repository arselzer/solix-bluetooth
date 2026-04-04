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

// ECDH Private Key (hardcoded in SolixBLE - extracted from Anker app)
export const ECDH_PRIVATE_KEY_HEX = '7dfbea61cd95cee49c458ad7419e817f1ade9a66136de3c7d5787af1458e39f4';

// AES-GCM auth data for Prime devices
export const GCM_AUTH_DATA = new Uint8Array([
  0x33, 0x22, 0x11, 0x00, 0x77, 0x66, 0x55, 0x44,
  0xbb, 0xaa, 0x99, 0x88, 0xff, 0xee, 0xdd, 0xcc
]);

// Exact negotiation command packets from SolixBLE const.py
// These are complete packets including header, length, pattern, command, payload, and checksum
export const NEGOTIATION_COMMAND_0 = 'ff0936000300010001a10442ad8c69a22462326463306231372d623735642d346162662d626136652d656337633939376332336537b9';
export const NEGOTIATION_COMMAND_1 = 'ff093d000300010003a10442ad8c69a22462326463306231372d623735642d346162662d626136652d656337633939376332336537a30120a40200f064';
export const NEGOTIATION_COMMAND_2 = 'ff0936000300010029a10442ad8c69a22462326463306231372d623735642d346162662d626136652d65633763393937633233653791';
export const NEGOTIATION_COMMAND_3 = 'ff0940000300010005a10443ad8c69a22462326463306231372d623735642d346162662d626136652d656337633939376332336537a30120a40200f0a50140fa';
// Commands 4 and 5 contain the ECDH public key and are built dynamically
export const NEGOTIATION_COMMAND_4_PREFIX = 'ff094c000300010021a140';
export const NEGOTIATION_COMMAND_5_PREFIX = 'ff095a000300014022';

// Base timestamp for anti-replay (little-endian: 42ad8c69 = 0x698cad42 = 1771040066)
export const BASE_TIMESTAMP_HEX = '42ad8c69';
export const BASE_TIMESTAMP = 0x698cad42; // little-endian interpretation

// Solarbank 3 Pro (A17C5) telemetry parameter IDs
// IMPORTANT: Integer values skip the first byte of param data (begin=1, it's a type byte)
export const SB3_PARAMS: Record<number, { name: string; type: 'string' | 'int' | 'signed_int' | 'float'; divisor?: number; skipFirst?: boolean }> = {
  0xa2: { name: 'serial_number', type: 'string', skipFirst: true },
  0xa5: { name: 'battery_percentage_aggregate', type: 'float', divisor: 10, skipFirst: true },
  0xa6: { name: 'battery_health', type: 'float', divisor: 10, skipFirst: true },
  0xa7: { name: 'battery_percentage', type: 'int', skipFirst: true },
  0xab: { name: 'solar_power_in', type: 'int', skipFirst: true },
  0xac: { name: 'pv_yield', type: 'int', skipFirst: true },
  0xb1: { name: 'house_demand', type: 'int', skipFirst: true },
  0xb2: { name: 'house_consumption', type: 'int', skipFirst: true },
  0xb6: { name: 'battery_power', type: 'signed_int', skipFirst: true },
  0xb7: { name: 'charged_energy', type: 'int', skipFirst: true },
  0xb8: { name: 'discharged_energy', type: 'int', skipFirst: true },
  0xbd: { name: 'grid_power', type: 'signed_int', skipFirst: true },
  0xbe: { name: 'grid_import_energy', type: 'int', skipFirst: true },
  0xbf: { name: 'grid_export_energy', type: 'int', skipFirst: true },
  0xc7: { name: 'solar_pv1_power', type: 'int', skipFirst: true },
  0xc8: { name: 'solar_pv2_power', type: 'int', skipFirst: true },
  0xc9: { name: 'solar_pv3_power', type: 'int', skipFirst: true },
  0xca: { name: 'solar_pv4_power', type: 'int', skipFirst: true },
  0xcc: { name: 'temperature', type: 'signed_int', skipFirst: true },
  0xd3: { name: 'power_out', type: 'int', skipFirst: true },
  0xd5: { name: 'grid_to_home_power', type: 'int', skipFirst: true },
};

// Telemetry identification
export const TELEMETRY_MARKER_BYTE_8 = 0x05;
export const TELEMETRY_MARKER_BYTE_9 = 0x13;
export const EXPECTED_TELEMETRY_LENGTH = 253;
