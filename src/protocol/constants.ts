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
  0xa1: { name: 'device_type', type: 'int', skipFirst: false },
  0xa2: { name: 'serial_number', type: 'string', skipFirst: true },
  0xa3: { name: 'wifi_signal', type: 'int', skipFirst: true },
  0xa4: { name: 'bt_signal', type: 'int', skipFirst: true },
  0xa5: { name: 'battery_percentage_aggregate', type: 'float', divisor: 10, skipFirst: true },
  0xa6: { name: 'battery_health', type: 'float', divisor: 10, skipFirst: true },
  0xa7: { name: 'battery_percentage', type: 'int', skipFirst: true },
  0xa8: { name: 'firmware_version', type: 'int', skipFirst: true },
  0xa9: { name: 'battery_capacity', type: 'int', skipFirst: true },
  0xab: { name: 'solar_power_in', type: 'int', skipFirst: true },
  0xac: { name: 'pv_yield', type: 'int', skipFirst: true },
  0xad: { name: 'output_power', type: 'int', skipFirst: true },
  0xae: { name: 'charge_power', type: 'int', skipFirst: true },
  0xb0: { name: 'discharge_power', type: 'int', skipFirst: true },
  0xb1: { name: 'house_demand', type: 'int', skipFirst: true },
  0xb2: { name: 'house_consumption', type: 'int', skipFirst: true },
  0xb3: { name: 'grid_status', type: 'int', skipFirst: true },
  0xb6: { name: 'battery_power', type: 'signed_int', skipFirst: true },
  0xb7: { name: 'charged_energy', type: 'int', skipFirst: true },
  0xb8: { name: 'discharged_energy', type: 'int', skipFirst: true },
  0xbd: { name: 'grid_power', type: 'signed_int', skipFirst: true },
  0xbe: { name: 'grid_import_energy', type: 'int', skipFirst: true },
  0xbf: { name: 'grid_export_energy', type: 'int', skipFirst: true },
  0xc0: { name: 'output_limit', type: 'int', skipFirst: true },
  0xc1: { name: 'input_limit', type: 'int', skipFirst: true },
  0xc7: { name: 'solar_pv1_power', type: 'int', skipFirst: true },
  0xc8: { name: 'solar_pv2_power', type: 'int', skipFirst: true },
  0xc9: { name: 'solar_pv3_power', type: 'int', skipFirst: true },
  0xca: { name: 'solar_pv4_power', type: 'int', skipFirst: true },
  0xcc: { name: 'temperature', type: 'signed_int', skipFirst: true },
  0xd3: { name: 'power_out', type: 'int', skipFirst: true },
  0xd5: { name: 'grid_to_home_power', type: 'int', skipFirst: true },
};

// Solix C1000 (A1761) telemetry parameter IDs
// Reverse-engineered from live device captures
export type ParamDef = { name: string; type: 'string' | 'int' | 'signed_int' | 'float'; divisor?: number; skipFirst?: boolean };
export const C1000_PARAMS: Record<number, ParamDef> = {
  // Power I/O
  0xa4: { name: 'wifi_rssi', type: 'signed_int', skipFirst: true },         // 0xFFFF when not connected
  0xa5: { name: 'ac_power_in', type: 'int', skipFirst: true },
  0xa6: { name: 'ac_power_out', type: 'int', skipFirst: true },
  0xa7: { name: 'dc_power_out', type: 'int', skipFirst: true },
  0xa8: { name: 'type_c_power_out', type: 'int', skipFirst: true },
  0xa9: { name: 'usb_power_out', type: 'int', skipFirst: true },
  0xaa: { name: 'total_output_power', type: 'int', skipFirst: true },
  0xae: { name: 'solar_power_in', type: 'int', skipFirst: true },

  // Battery
  0xab: { name: 'battery_percentage', type: 'int', skipFirst: true },
  0xac: { name: 'battery_power', type: 'signed_int', skipFirst: true },
  0xad: { name: 'battery_voltage', type: 'int', skipFirst: true },
  0xb5: { name: 'battery_cycles', type: 'int', skipFirst: true },
  0xb6: { name: 'battery_health', type: 'int', skipFirst: true },
  0xb8: { name: 'battery_resistance', type: 'int', skipFirst: true },

  // Switch states
  0xaf: { name: 'ac_switch', type: 'int', skipFirst: true },
  0xb0: { name: 'dc_switch', type: 'int', skipFirst: true },
  0xcc: { name: 'dc_output_active', type: 'int', skipFirst: true },         // changes when DC toggled

  // Temperature
  0xb3: { name: 'temperature', type: 'int', divisor: 10, skipFirst: true },  // /10 = Celsius
  0xba: { name: 'battery_temperature', type: 'int', divisor: 10, skipFirst: true },

  // Status
  0xb1: { name: 'charging_state', type: 'int', skipFirst: true },
  0xb2: { name: 'online_status', type: 'int', skipFirst: true },
  0xb4: { name: 'error_code', type: 'int', skipFirst: true },
  0xbb: { name: 'ac_output_active', type: 'int', skipFirst: true },

  // Settings
  0xbc: { name: 'led_mode', type: 'int', skipFirst: true },
  0xbd: { name: 'current_hour', type: 'int', skipFirst: true },             // system clock hour
  0xc0: { name: 'charge_speed', type: 'int', skipFirst: true },
  0xc1: { name: 'max_charge_soc', type: 'int', skipFirst: true },           // 100 = charge to full
  0xc3: { name: 'max_discharge_soc', type: 'int', skipFirst: true },        // 100 = use full capacity

  // Solar
  0xc7: { name: 'solar_pv1_power', type: 'int', skipFirst: true },
  0xc8: { name: 'solar_pv2_power', type: 'int', skipFirst: true },

  // Device info (from status request response)
  0xd0: { name: 'serial_number', type: 'string', skipFirst: true },
  0xd1: { name: 'capacity_wh', type: 'int', skipFirst: true },              // 1000 = 1000Wh
  0xd2: { name: 'max_ac_input_w', type: 'int', skipFirst: true },           // 720W AC charging
  0xd3: { name: 'display_timeout_s', type: 'int', skipFirst: true },        // 30 seconds
  0xd4: { name: 'idle_timeout_min', type: 'int', skipFirst: true },         // 60 minutes
  0xd5: { name: 'ups_mode', type: 'int', skipFirst: true },
  0xd6: { name: 'ups_reserve_pct', type: 'int', skipFirst: true },
  0xd7: { name: 'ac_enabled', type: 'int', skipFirst: true },               // 1 = AC output on
  0xd8: { name: 'dc_enabled', type: 'int', skipFirst: true },               // 1 = DC output on
  0xd9: { name: 'light_mode', type: 'int', skipFirst: true },               // 0=off 1=on 2=auto
  0xda: { name: 'min_soc_pct', type: 'int', skipFirst: true },              // 50 = reserve 50%
  0xfd: { name: 'model_name', type: 'string', skipFirst: true },            // "A1761_30Ah"
  0xf8: { name: 'hw_version', type: 'int', skipFirst: true },
};

// Combined param map: try all known device params
// Since param IDs can overlap between devices with different meanings,
// we use the SB3 map as default and merge unique C1000 params
export function getParamMap(deviceName?: string): Record<number, ParamDef> {
  if (deviceName?.includes('C1000') || deviceName?.includes('A17X')) {
    return C1000_PARAMS;
  }
  // Default to Solarbank 3 Pro
  return SB3_PARAMS;
}

// Telemetry identification
export const TELEMETRY_MARKER_BYTE_8 = 0x05;
export const TELEMETRY_MARKER_BYTE_9 = 0x13;
export const EXPECTED_TELEMETRY_LENGTH = 253;

// ============================================================
// Known BLE command codes (first byte is typically 0x40)
// ============================================================
// Confirmed via live device testing on C1000 (A1761)
export const KNOWN_COMMANDS: Record<string, { name: string; description: string; payloads?: Record<string, string> }> = {
  '4040': {
    name: 'STATUS_REQUEST',
    description: 'Full status + settings. Returns large telemetry via c840 fragments.',
    payloads: { 'query': 'a10121' },
  },
  '4041': {
    name: 'PARTIAL_STATUS',
    description: 'Partial status. Returns a1, a2, a3 only (18B).',
    payloads: { 'query': 'a10121' },
  },
  '404a': {
    name: 'AC_TOGGLE',
    description: 'Toggle AC output on/off.',
    payloads: { 'on': 'a10121a2020101', 'off': 'a10121a2020100' },
  },
  '404b': {
    name: 'DC_TOGGLE',
    description: 'Toggle DC output on/off.',
    payloads: { 'on': 'a10121a2020101', 'off': 'a10121a2020100' },
  },
  '404c': {
    name: 'DISPLAY_MODE',
    description: 'Set display mode.',
    payloads: { 'set': 'a10121a20201XX' },
  },
  '404f': {
    name: 'LIGHT_MODE',
    description: 'Set LED light mode (0=off, 1=on, 2=auto).',
    payloads: { 'set': 'a10121a20201XX' },
  },
  '4046': {
    name: 'DISPLAY_TIMEOUT',
    description: 'Set display auto-off timeout.',
    payloads: { 'set': 'a10121a20302XXXX' },
  },
  '4052': {
    name: 'DISPLAY_TOGGLE',
    description: 'Toggle display on/off.',
    payloads: { 'on': 'a10121a2020101', 'off': 'a10121a2020100' },
  },
  // Discovered via command scan 0x4000-0x403f on C1000
  '4023': {
    name: 'QUERY_23',
    description: 'Returns single byte (01). Protocol info?',
    payloads: { 'query': 'a10121' },
  },
  '4024': {
    name: 'QUERY_PORT_COUNT',
    description: 'Returns single byte (04). Possibly number of output ports.',
    payloads: { 'query': 'a10121' },
  },
  '4029': {
    name: 'QUERY_DEVICE_TYPE',
    description: 'Returns TLV a1=5. Device type/category.',
    payloads: { 'query': 'a10121' },
  },
  '4030': {
    name: 'FIRMWARE_VERSIONS',
    description: 'Returns firmware version strings: a1="v0.1.3.0" (main), a2="v1.5.1" (module).',
    payloads: { 'query': 'a10121' },
  },
};

// TLV type byte meanings (first byte of each TLV param value)
export const TLV_TYPE_BYTES: Record<number, string> = {
  0x00: 'string (ASCII)',
  0x01: 'uint8',
  0x02: 'uint16 LE',
  0x03: 'uint32 LE',
  0x04: 'bytes/string',
  0x05: 'float32 LE (IEEE 754)',
};

// Negotiation response fields (cmd 0x29)
export const NEGOTIATION_INFO_FIELDS: Record<number, string> = {
  0xa1: 'protocol_version',
  0xa2: 'chip',           // e.g. "ESP32"
  0xa3: 'firmware',       // e.g. "0.0.0.3"
  0xa4: 'serial',         // device serial number
  0xa5: 'feature_flags',
};
