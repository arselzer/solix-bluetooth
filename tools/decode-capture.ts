#!/usr/bin/env npx tsx
/**
 * Decode an Anker Solix BLE capture from tshark output.
 * Uses the hardcoded ECDH private key to derive the shared secret,
 * then decrypts all post-negotiation packets and parses TLV data.
 *
 * Usage: npx tsx tools/decode-capture.ts < capture.tsv
 *   or:  cat capture.tsv | npx tsx tools/decode-capture.ts
 *
 * Input format (tab-separated, from tshark):
 *   frame_number  time_relative  opcode  handle  value_hex
 *
 * Or just pipe the "Write Commands Only" or "Notifications Only"
 * sections from the decode-anker-ble.sh script.
 *
 * IMPORTANT: This only works for captures from OUR web app or SolixBLE,
 * which use the hardcoded ECDH private key. The official Anker app
 * generates a fresh keypair each session, so its captures cannot be
 * decrypted without intercepting the runtime private key (via Frida).
 *
 * To decrypt Anker app captures, use Frida to hook Cipher.doFinal()
 * and capture the plaintext directly.
 *
 * For captures from our app, use:
 *   PRIVATE_KEY=<hex> npx tsx tools/decode-capture.ts < capture.tsv
 */

import { createECDH } from 'crypto';
import { createDecipheriv } from 'crypto';

// SolixBLE hardcoded private key - only works for SolixBLE/our web app captures
const PRIVATE_KEY_HEX = process.env.PRIVATE_KEY || '7dfbea61cd95cee49c458ad7419e817f1ade9a66136de3c7d5787af1458e39f4';

// Show what public key this private key generates
const _ecdh = createECDH('prime256v1');
_ecdh.setPrivateKey(fromHex(PRIVATE_KEY_HEX));
const OUR_PUBLIC_KEY = _ecdh.getPublicKey();
console.log(`Using private key: ${PRIVATE_KEY_HEX.substring(0, 16)}...`);
console.log(`Corresponding public key: ${toHex(OUR_PUBLIC_KEY).substring(0, 40)}...\n`);

function fromHex(hex: string): Buffer {
  return Buffer.from(hex.replace(/[:\s]/g, ''), 'hex');
}

function toHex(buf: Buffer | Uint8Array): string {
  return Buffer.from(buf).toString('hex');
}

function xorChecksum(data: Buffer): number {
  let c = 0;
  for (const b of data) c ^= b;
  return c;
}

interface Packet {
  frame: number;
  time: number;
  opcode: number; // 0x52 = write, 0x1b = notify
  direction: 'TX' | 'RX';
  raw: Buffer;
  // Parsed
  pattern: Buffer;
  command: Buffer;
  payload: Buffer;
}

function parsePacket(frame: number, time: number, opcode: number, valueHex: string): Packet | null {
  const raw = fromHex(valueHex);
  if (raw.length < 8 || raw[0] !== 0xff || raw[1] !== 0x09) return null;

  const pattern = raw.subarray(4, 7);
  const command = raw.subarray(7, 9);
  const payload = raw.subarray(9, raw.length - 1);

  return {
    frame, time,
    opcode,
    direction: opcode === 0x52 ? 'TX' : 'RX',
    raw, pattern, command, payload,
  };
}

function isNegotiation(p: Packet): boolean {
  return p.pattern[0] === 0x03 && p.pattern[1] === 0x00 && p.pattern[2] === 0x01;
}

function isEncrypted(p: Packet): boolean {
  return p.pattern[0] === 0x03 && p.pattern[1] === 0x01;
}

function deriveSharedSecret(devicePublicKeyRaw: Buffer): { key: Buffer; iv: Buffer } {
  const ecdh = createECDH('prime256v1');
  ecdh.setPrivateKey(fromHex(PRIVATE_KEY_HEX));
  const shared = ecdh.computeSecret(devicePublicKeyRaw);
  return {
    key: shared.subarray(0, 16),
    iv: shared.subarray(16, 32),
  };
}

function decryptAesCbc(data: Buffer, key: Buffer, iv: Buffer): Buffer | null {
  try {
    const decipher = createDecipheriv('aes-128-cbc', key, iv);
    decipher.setAutoPadding(true);
    const dec = Buffer.concat([decipher.update(data), decipher.final()]);
    return dec;
  } catch {
    return null;
  }
}

// TLV type byte meanings
const TYPE_NAMES: Record<number, string> = {
  0x00: 'str', 0x01: 'u8', 0x02: 'u16', 0x03: 'u32', 0x04: 'bytes', 0x05: 'f32',
};

function decodeTlvValue(data: Buffer): string {
  if (data.length === 0) return '(empty)';
  if (data.length === 1) return data[0].toString();

  const typeByte = data[0];
  const value = data.subarray(1);

  switch (typeByte) {
    case 0x00: return `"${value.toString('ascii')}"`;
    case 0x01: return value.length >= 1 ? value[0].toString() : '0';
    case 0x02: return value.length >= 2 ? value.readUInt16LE(0).toString() : '0';
    case 0x03: return value.length >= 4 ? value.readUInt32LE(0).toString() : '0';
    case 0x04: {
      const ascii = value.toString('ascii');
      if (/^[\x20-\x7e]+$/.test(ascii)) return `"${ascii}"`;
      return toHex(value);
    }
    case 0x05: {
      if (value.length >= 4) return value.readFloatLE(0).toFixed(2);
      return '0';
    }
    default:
      if (value.length === 1) return value[0].toString();
      if (value.length === 2) return value.readUInt16LE(0).toString();
      return toHex(data);
  }
}

function parseTlv(data: Buffer): Array<{ id: number; len: number; raw: string; value: string }> {
  const entries: Array<{ id: number; len: number; raw: string; value: string }> = [];
  let offset = 0;
  if (data.length > 0 && data[0] === 0x00) offset = 1;

  while (offset < data.length) {
    const id = data[offset++];
    if (offset >= data.length) break;
    const len = data[offset++];
    if (len === 0) continue;
    if (offset + len > data.length) break;
    const paramData = data.subarray(offset, offset + len);
    offset += len;
    entries.push({
      id,
      len,
      raw: toHex(paramData),
      value: decodeTlvValue(paramData),
    });
  }
  return entries;
}

// Extract a 64-byte public key from a negotiation payload
// Try multiple strategies since the key location varies
function extractPublicKey(payload: Buffer, label: string): Buffer | null {
  const candidates: Buffer[] = [];

  // Strategy 1: After "00 a1 40" prefix (3 bytes), take next 64
  const prefixIdx = payload.indexOf(Buffer.from([0x00, 0xa1, 0x40]));
  if (prefixIdx >= 0 && prefixIdx + 3 + 64 <= payload.length) {
    candidates.push(payload.subarray(prefixIdx + 3, prefixIdx + 3 + 64));
  }

  // Strategy 2: After "a1 40" prefix (2 bytes)
  const prefix2Idx = payload.indexOf(Buffer.from([0xa1, 0x40]));
  if (prefix2Idx >= 0 && prefix2Idx + 2 + 64 <= payload.length) {
    candidates.push(payload.subarray(prefix2Idx + 2, prefix2Idx + 2 + 64));
  }

  // Strategy 3: Last 64 bytes
  if (payload.length >= 64) {
    candidates.push(payload.subarray(payload.length - 64));
  }

  // Strategy 4: If payload starts with 0x04 (uncompressed point), take 65 bytes
  if (payload[0] === 0x04 && payload.length >= 65) {
    return payload.subarray(0, 65);
  }

  // Strategy 5: Look for any 0x04 byte followed by 64 bytes
  for (let i = 0; i < payload.length - 64; i++) {
    if (payload[i] === 0x04) {
      candidates.push(payload.subarray(i, i + 65));
    }
  }

  // Try each candidate
  for (const candidate of candidates) {
    const pubKey = candidate.length === 65 ? candidate : Buffer.concat([Buffer.from([0x04]), candidate]);
    const result = tryDeriveSecret(pubKey);
    if (result) {
      console.log(`${label}: found valid key at offset, ${toHex(pubKey).substring(0, 40)}...`);
      return pubKey;
    }
  }

  // Fallback: return first 64-byte candidate with 0x04 prepended
  if (candidates.length > 0) {
    const c = candidates[0];
    const pubKey = c.length === 65 ? c : Buffer.concat([Buffer.from([0x04]), c]);
    console.log(`${label}: using best guess key ${toHex(pubKey).substring(0, 40)}... (may be invalid)`);
    return pubKey;
  }

  console.log(`${label}: could not extract key from ${payload.length}B payload`);
  return null;
}

function tryDeriveSecret(pubKey: Buffer): { key: Buffer; iv: Buffer } | null {
  try {
    return deriveSharedSecret(pubKey);
  } catch {
    return null;
  }
}

// ====== MAIN ======

async function main() {
  const input = await new Promise<string>((resolve) => {
    let data = '';
    process.stdin.setEncoding('utf-8');
    process.stdin.on('data', (chunk) => data += chunk);
    process.stdin.on('end', () => resolve(data));
  });

  // Parse input lines - handle both tab-separated tshark output formats
  const lines = input.trim().split('\n').filter(l => l.trim() && !l.startsWith('===') && !l.startsWith('Total') && !l.startsWith('To decrypt') && !l.startsWith('Filter') && !l.startsWith('These'));

  const packets: Packet[] = [];
  for (const line of lines) {
    const parts = line.split('\t');
    if (parts.length >= 3) {
      // Format: frame time value  (from write/notify only sections)
      // or:     frame time opcode handle value (from full section)
      let frame: number, time: number, valueHex: string, opcode: number;

      if (parts.length >= 5) {
        // Full format: frame time opcode handle value
        frame = parseInt(parts[0]);
        time = parseFloat(parts[1]);
        opcode = parseInt(parts[2], 16);
        valueHex = parts[4];
      } else if (parts.length >= 3) {
        // Short format: frame time value
        frame = parseInt(parts[0]);
        time = parseFloat(parts[1]);
        valueHex = parts[2];
        // Guess opcode from context - we'll mark all as unknown
        opcode = 0x52; // default to TX
      } else {
        continue;
      }

      if (!valueHex || !valueHex.startsWith('ff09')) continue;

      const pkt = parsePacket(frame, time, opcode, valueHex);
      if (pkt) packets.push(pkt);
    }
  }

  console.log(`Parsed ${packets.length} packets\n`);

  // Find device public key from negotiation
  let devicePubKey: Buffer | null = null;
  let appPubKey: Buffer | null = null;

  for (const p of packets) {
    if (!isNegotiation(p)) continue;

    if (p.command[1] === 0x21) {
      console.log(`Frame ${p.frame} ${p.direction} cmd=0x${toHex(p.command)} payload(${p.payload.length}B): ${toHex(p.payload)}`);
    }

    // cmd 0x21 response (RX) contains device public key
    if (p.command[1] === 0x21 && p.direction === 'RX') {
      devicePubKey = extractPublicKey(p.payload, `device (frame ${p.frame})`);
    }

    // cmd 0x21 TX contains app public key
    if (p.command[1] === 0x21 && p.direction === 'TX') {
      appPubKey = extractPublicKey(p.payload, `app (frame ${p.frame})`);
    }
  }

  // Check if the capture was made by the Anker app (different public key)
  if (appPubKey && toHex(appPubKey) !== toHex(OUR_PUBLIC_KEY)) {
    console.log('\n⚠️  WARNING: This capture was NOT made by SolixBLE or our web app.');
    console.log('   The app used a different ECDH keypair (generated at runtime).');
    console.log(`   App public key:  ${toHex(appPubKey).substring(0, 40)}...`);
    console.log(`   Our public key:  ${toHex(OUR_PUBLIC_KEY).substring(0, 40)}...`);
    console.log('   Cannot decrypt without the app\'s private key.');
    console.log('   To capture decryptable traffic, use our web app or SolixBLE.');
    console.log('   To intercept Anker app traffic, use Frida.\n');
    process.exit(1);
  }

  if (!devicePubKey) {
    console.error('Could not find device public key in negotiation!');
    process.exit(1);
  }

  // Derive shared secret
  let key: Buffer, iv: Buffer;
  const derived = tryDeriveSecret(devicePubKey);
  if (derived) {
    ({ key, iv } = derived);
  } else {
    console.error('Failed to derive shared secret with extracted key!');
    process.exit(1);
  }
  console.log(`AES Key: ${toHex(key)}`);
  console.log(`AES IV:  ${toHex(iv)}\n`);

  // Decrypt all encrypted packets
  console.log('='.repeat(120));
  console.log('DECRYPTED PACKETS');
  console.log('='.repeat(120));

  // Track fragment assembly
  let fragments: Buffer[] = [];
  let lastFragTime = 0;

  for (const p of packets) {
    if (isNegotiation(p)) continue;
    if (!isEncrypted(p)) continue;

    const cmdByte = p.command[0];
    const cmdHex = toHex(p.command);
    const timeStr = p.time.toFixed(3).padStart(12);

    // Fragmented telemetry
    if (cmdByte === 0xc4 || cmdByte === 0xc8) {
      fragments.push(p.payload);
      lastFragTime = p.time;
      continue;
    }

    // If we have pending fragments and this is NOT a fragment, assemble first
    if (fragments.length > 0 && (cmdByte === 0x44 || cmdByte === 0x48)) {
      // Try assembling fragments
      tryAssembleAndDecrypt(fragments, key, iv, lastFragTime);
      fragments = [];
    }

    // Single packet (0x40xx = request, 0x44xx/0x48xx = response)
    const dec = decryptAesCbc(p.payload, key, iv);
    if (dec) {
      const dir = p.direction;
      console.log(`\n[${timeStr}] ${dir} cmd=${cmdHex} frame=${p.frame} (${p.raw.length}B → ${dec.length}B decrypted)`);
      console.log(`  hex: ${toHex(dec)}`);

      const tlv = parseTlv(dec);
      if (tlv.length > 0) {
        for (const e of tlv) {
          console.log(`  0x${e.id.toString(16).padStart(2, '0')} [${e.len}B] = ${e.value}  (${e.raw})`);
        }
      }
    } else {
      console.log(`\n[${timeStr}] ${p.direction} cmd=${cmdHex} frame=${p.frame} DECRYPT FAILED (${p.payload.length}B, mod16=${p.payload.length % 16})`);
    }
  }

  // Flush remaining fragments
  if (fragments.length > 0) {
    tryAssembleAndDecrypt(fragments, key, iv, lastFragTime);
  }
}

function tryAssembleAndDecrypt(fragments: Buffer[], key: Buffer, iv: Buffer, time: number) {
  const timeStr = time.toFixed(3).padStart(12);

  // Try raw concatenation first
  const raw = Buffer.concat(fragments);
  if (raw.length % 16 === 0) {
    const dec = decryptAesCbc(raw, key, iv);
    if (dec) {
      console.log(`\n[${timeStr}] RX TELEMETRY (${fragments.length} frags, raw concat, ${raw.length}B → ${dec.length}B)`);
      console.log(`  hex: ${toHex(dec)}`);
      const tlv = parseTlv(dec);
      for (const e of tlv) {
        console.log(`  0x${e.id.toString(16).padStart(2, '0')} [${e.len}B] = ${e.value}  (${e.raw})`);
      }
      return;
    }
  }

  // Try stripping first byte (sequence byte) from each fragment
  const stripped = Buffer.concat(fragments.map(f => f.subarray(1)));
  if (stripped.length % 16 === 0) {
    const dec = decryptAesCbc(stripped, key, iv);
    if (dec) {
      console.log(`\n[${timeStr}] RX TELEMETRY (${fragments.length} frags, stripped, ${stripped.length}B → ${dec.length}B)`);
      console.log(`  hex: ${toHex(dec)}`);
      const tlv = parseTlv(dec);
      for (const e of tlv) {
        console.log(`  0x${e.id.toString(16).padStart(2, '0')} [${e.len}B] = ${e.value}  (${e.raw})`);
      }
      return;
    }
  }

  console.log(`\n[${timeStr}] RX TELEMETRY DECRYPT FAILED (${fragments.length} frags, raw=${raw.length}B mod16=${raw.length%16}, stripped=${stripped.length}B mod16=${stripped.length%16})`);
}

main().catch(console.error);
