import assert from 'node:assert/strict';
import { parseC1000Gen2Telemetry } from '../src/protocol/telemetry';

const field = (id: number, bytes: number[]) => [id, bytes.length, ...bytes];
const a3 = [4, 0, 7, ...Array(11).fill(0)];
const a6 = [4, 0, 0, 0, 0, 123, 0, 0, 0, 0];
const a8 = [4, 1, 88, 2]; // Cached 600 differs from complete power value 123.
const payload = Uint8Array.from([
  ...field(0xa3, a3), ...field(0xa5, [4, 25, 0, 90, 100, 0]),
  ...field(0xa6, a6), ...field(0xa8, a8),
]);
const decoded = parseC1000Gen2Telemetry(payload).data;
assert.equal(decoded.dc_input_active, 1);
assert.equal(decoded.dc_input_power_raw, 123);
assert.equal(decoded.controller_error_code, 7);
assert.equal(decoded.battery_health_raw, 100);
assert.equal(decoded.battery_health, undefined);

for (const [id, block, key] of [
  [0xa3, a3, 'controller_error_code'], [0xa6, a6, 'dc_input_power_raw'],
  [0xa8, a8, 'dc_input_active'],
] as const) {
  for (const malformed of [block.slice(0, -1), [...block, 0], [3, ...block.slice(1)]]) {
    assert.equal(parseC1000Gen2Telemetry(Uint8Array.from(field(id, malformed))).data[key], undefined);
  }
  assert.equal(parseC1000Gen2Telemetry(payload, 'c2000').data[key], undefined);
}
assert.equal(parseC1000Gen2Telemetry(Uint8Array.from(field(0xa8, [4, 2, 0, 0]))).data.dc_input_active, undefined);
assert.equal(parseC1000Gen2Telemetry(Uint8Array.from(field(0xa8, a8))).data.dc_input_power_raw, undefined);
console.log('Gen 2 raw diagnostic telemetry checks passed');
