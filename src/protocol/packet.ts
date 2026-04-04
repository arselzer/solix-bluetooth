import { HEADER, PATTERN_NEGOTIATION, PATTERN_ENCRYPTED } from './constants';
import { xorChecksum, concatBytes, toHex } from './utils';
import type { SolixPacket } from './types';

export function buildPacket(pattern: Uint8Array, command: Uint8Array, payload: Uint8Array): Uint8Array {
  // Total length = 2(header) + 2(length) + 3(pattern) + 2(command) + payload.length + 1(checksum)
  const totalLength = 2 + 2 + pattern.length + command.length + payload.length + 1;
  const lengthBytes = new Uint8Array(2);
  // Length field encodes the bytes after header (length itself + pattern + command + payload + checksum)
  const innerLength = totalLength - 2; // everything after header
  lengthBytes[0] = (innerLength >> 8) & 0xff;
  lengthBytes[1] = innerLength & 0xff;

  const headerBytes = new Uint8Array([(HEADER >> 8) & 0xff, HEADER & 0xff]);

  const withoutChecksum = concatBytes(headerBytes, lengthBytes, pattern, command, payload);
  const checksum = xorChecksum(withoutChecksum);

  return concatBytes(withoutChecksum, new Uint8Array([checksum]));
}

export function buildNegotiationPacket(command: Uint8Array, payload: Uint8Array): Uint8Array {
  return buildPacket(PATTERN_NEGOTIATION, command, payload);
}

export function buildEncryptedPacket(command: Uint8Array, payload: Uint8Array): Uint8Array {
  return buildPacket(PATTERN_ENCRYPTED, command, payload);
}

export function parsePacket(data: Uint8Array): SolixPacket | null {
  if (data.length < 8) return null;

  // Check header
  if (data[0] !== 0xff || data[1] !== 0x09) {
    console.warn('[Packet] Invalid header:', toHex(data.slice(0, 2)));
    return null;
  }

  const length = (data[2] << 8) | data[3];
  const pattern = data.slice(4, 7);
  const command = data.slice(7, 9);
  const payload = data.slice(9, data.length - 1);
  const checksum = data[data.length - 1];

  // Verify checksum
  const computed = xorChecksum(data.slice(0, data.length - 1));
  if (computed !== checksum) {
    console.warn('[Packet] Checksum mismatch: expected', checksum.toString(16), 'got', computed.toString(16));
  }

  return {
    header: HEADER,
    length,
    pattern,
    command,
    payload,
    checksum,
    raw: data,
  };
}

export function isNegotiationPacket(packet: SolixPacket): boolean {
  return arraysEqual(packet.pattern, PATTERN_NEGOTIATION);
}

export function isEncryptedPacket(packet: SolixPacket): boolean {
  return packet.pattern[0] === 0x03 && packet.pattern[1] === 0x01;
}

export function isTelemetryPacket(packet: SolixPacket): boolean {
  return isEncryptedPacket(packet) &&
    (packet.command[0] === 0xc4 || packet.command[0] === 0x44 || packet.command[0] === 0xc8);
}

function arraysEqual(a: Uint8Array, b: Uint8Array): boolean {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) {
    if (a[i] !== b[i]) return false;
  }
  return true;
}
