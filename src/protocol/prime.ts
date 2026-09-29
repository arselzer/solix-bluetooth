import { GCM_AUTH_DATA, PATTERN_NEGOTIATION } from './constants';
import { deriveSharedSecret, decryptAesGcm, encryptAesGcm, generateFreshECDHKeyPair, type SessionKeys } from './crypto';
import { buildPacket } from './packet';
import { parseC1000Gen2Telemetry, parseTelemetryDetailed } from './telemetry';
import { concatBytes, fromHex, toHex, writeUint32LE } from './utils';
import type { SolixPacket, TelemetryData } from './types';

// C2000 Gen 2 and C1000 Gen 2 firmware 1.1.4.9 use Prime negotiation.
const NEGOTIATION_KEY = fromHex('b8ff7422955d4eb6d554a2c470280559');
const NEGOTIATION_NONCE = fromHex('6ba3e3f2f3a60f2971ce5d1f');
const DATA_PATTERN = fromHex('03000f');
// The Prime 4100 subscription uses this additional read-only field on both tested models.
const SUBSCRIBE_EXTRA = fromHex('a20a040100e3fbfcfe000000');

function tlv(id: number, value: Uint8Array): Uint8Array {
  return concatBytes(new Uint8Array([id, value.length]), value);
}

function timestamp(): Uint8Array {
  return writeUint32LE(Math.floor(Date.now() / 1000));
}

function parameters(payload: Uint8Array): Map<number, Uint8Array> {
  const result = new Map<number, Uint8Array>();
  let offset = payload[0] === 0 ? 1 : 0;
  while (offset + 2 <= payload.length) {
    const id = payload[offset++];
    const length = payload[offset++];
    if (offset + length > payload.length) break;
    result.set(id, payload.slice(offset, offset + length));
    offset += length;
  }
  return result;
}

export class PrimeSession {
  private privateKey: CryptoKey | null = null;
  private publicKey: Uint8Array | null = null;
  private sessionKeys: SessionKeys | null = null;
  private pairingRequired = false;
  private fragments = new Map<string, Uint8Array[]>();
  private hooks: {
    send: (packet: Uint8Array) => Promise<void>;
    log: (message: string, data?: string) => void;
    ready: () => void;
    pairingRequired: () => void;
    telemetry: (data: TelemetryData) => void;
  };
  private clientId: string;
  private isC2000Gen2: boolean;

  constructor(hooks: {
    send: (packet: Uint8Array) => Promise<void>;
    log: (message: string, data?: string) => void;
    ready: () => void;
    pairingRequired: () => void;
    telemetry: (data: TelemetryData) => void;
  }, clientId: string, isC2000Gen2 = false) {
    if (!/^[0-9a-fA-F]{40}$/.test(clientId)) {
      throw new Error('Gen 2 client ID must be 40 hexadecimal characters');
    }
    this.hooks = hooks;
    this.clientId = clientId;
    this.isC2000Gen2 = isC2000Gen2;
  }

  reset(): void {
    this.privateKey = null;
    this.publicKey = null;
    this.sessionKeys = null;
    this.pairingRequired = false;
    this.fragments.clear();
  }

  async start(): Promise<void> {
    const pair = await generateFreshECDHKeyPair();
    this.privateKey = pair.privateKey;
    this.publicKey = pair.publicKeyRaw;
    await this.send(PATTERN_NEGOTIATION, '4001', tlv(0xa1, timestamp()));
  }

  private async send(pattern: Uint8Array, command: string, plaintext: Uint8Array): Promise<void> {
    const key = this.sessionKeys?.aesKey ?? NEGOTIATION_KEY;
    const nonce = this.sessionKeys?.iv.slice(0, 12) ?? NEGOTIATION_NONCE;
    const encrypted = await encryptAesGcm(plaintext, key, nonce, GCM_AUTH_DATA);
    const packet = buildPacket(pattern, fromHex(command), encrypted);
    this.hooks.log(`Prime TX ${command} (${packet.length}B)`);
    await this.hooks.send(packet);
  }

  private async decrypt(payload: Uint8Array): Promise<Uint8Array> {
    const key = this.sessionKeys?.aesKey ?? NEGOTIATION_KEY;
    const nonce = this.sessionKeys?.iv.slice(0, 12) ?? NEGOTIATION_NONCE;
    return decryptAesGcm(payload, key, nonce, GCM_AUTH_DATA);
  }

  async handle(packet: SolixPacket): Promise<void> {
    const command = toHex(packet.command);
    if (packet.pattern[0] === 3 && packet.pattern[1] === 0 && packet.pattern[2] === 1) {
      await this.negotiate(command, packet.payload);
      return;
    }
    if (packet.pattern[0] === 3 && packet.pattern[1] === 1 && packet.pattern[2] === 15) {
      if (packet.command[0] === 0xc4 || packet.command[0] === 0xc9) {
        if (!packet.payload.length) return;
        const index = packet.payload[0] >> 4;
        const total = packet.payload[0] & 0x0f;
        if (index < 1 || total < 1 || index > total) return;
        const fragments = index === 1 ? [] : this.fragments.get(command);
        if (!fragments || fragments.length !== index - 1) return;
        fragments.push(packet.payload);
        this.fragments.set(command, fragments);
        if (index === total) await this.assemble(command);
      } else {
        try { this.processTelemetry(await this.decrypt(packet.payload)); }
        catch (error) { this.hooks.log(`Prime response ${command}: ${error}`); }
      }
    }
  }

  private async negotiate(command: string, payload: Uint8Array): Promise<void> {
    const decrypted = await this.decrypt(payload);
    const entries = parameters(decrypted);
    this.hooks.log(`Prime negotiation ${command}`);
    const ts = () => tlv(0xa1, timestamp());
    switch (command) {
      case '4801':
        await this.send(PATTERN_NEGOTIATION, '4003', concatBytes(
          ts(), tlv(0xa3, fromHex('20')), tlv(0xa4, fromHex('00f0'))));
        break;
      case '4803':
        await this.send(PATTERN_NEGOTIATION, '4029', ts());
        break;
      case '4829':
        await this.send(PATTERN_NEGOTIATION, '4005', concatBytes(
          ts(), tlv(0xa3, fromHex('20')), tlv(0xa4, fromHex('2901')),
          tlv(0xa5, fromHex('44')), tlv(0xa6, fromHex('02'))));
        break;
      case '4805':
        if (!this.publicKey) throw new Error('Missing Prime public key');
        await this.send(PATTERN_NEGOTIATION, '4021', tlv(0xa1, this.publicKey.slice(1)));
        break;
      case '4821': {
        const deviceKey = entries.get(0xa1);
        if (!deviceKey || deviceKey.length !== 64 || !this.privateKey) {
          throw new Error('Invalid Prime device public key');
        }
        this.sessionKeys = await deriveSharedSecret(this.privateKey, concatBytes(fromHex('04'), deviceKey));
        await this.send(PATTERN_NEGOTIATION, '4022', concatBytes(
          ts(), tlv(0xa3, fromHex('00000000')), tlv(0xa5, new TextEncoder().encode('UTC0'))));
        break;
      }
      case '4822':
        await this.sendRegistration();
        break;
      case '4827':
        if (decrypted[0] === 9) {
          this.pairingRequired = true;
          this.hooks.pairingRequired();
          break;
        }
        if (decrypted[0] !== 0) {
          throw new Error(`Prime registration failed: ${decrypted[0]?.toString(16)}`);
        }
        this.pairingRequired = false;
        this.hooks.ready();
        await this.sendCommand(fromHex('4100'), fromHex('a10121'));
        break;
      default:
        this.hooks.log(`Unexpected Prime negotiation response ${command}`, toHex(decrypted));
    }
  }

  private async sendRegistration(): Promise<void> {
    await this.send(PATTERN_NEGOTIATION, '4027', concatBytes(
      tlv(0xa1, timestamp()), tlv(0xa2, new TextEncoder().encode(this.clientId))));
  }

  async confirmPairing(): Promise<void> {
    if (!this.pairingRequired || !this.sessionKeys) {
      throw new Error('Station is not awaiting physical pairing confirmation');
    }
    this.pairingRequired = false;
    await this.sendRegistration();
  }

  async sendCommand(command: Uint8Array, payload: Uint8Array,
    trailer: 'fe' | 'fd' | 'none' = 'fe'): Promise<void> {
    if (!this.sessionKeys) throw new Error('Prime session is not negotiated');
    const subscribe = toHex(command) === '4100' && toHex(payload) === 'a10121';
    const suffix = trailer === 'fe' ? tlv(0xfe, timestamp())
      : trailer === 'fd' ? tlv(0xfd, concatBytes(
        new Uint8Array([0]), new TextEncoder().encode(String(Date.now()))))
        : new Uint8Array();
    await this.send(DATA_PATTERN, toHex(command), concatBytes(
      payload, subscribe ? SUBSCRIBE_EXTRA : new Uint8Array(), suffix));
  }

  private async assemble(command: string): Promise<void> {
    const fragments = this.fragments.get(command) ?? [];
    this.fragments.delete(command);
    if (fragments.length === 0) return;
    for (const bytes of [concatBytes(...fragments.map(f => f.slice(1))), concatBytes(...fragments)]) {
      try {
        this.processTelemetry(await this.decrypt(bytes));
        return;
      } catch { /* Try the other fragment layout. */ }
    }
    this.hooks.log(`Prime telemetry decrypt failed (${command}, ${fragments.length} fragments)`);
  }

  private processTelemetry(plaintext: Uint8Array): void {
    this.hooks.log(`Prime plaintext (${plaintext.length}B)`, toHex(plaintext));
    const parsed = parseC1000Gen2Telemetry(plaintext, this.isC2000Gen2 ? 'c2000' : 'c1000');
    const data = Object.keys(parsed.data).length > 0
      ? parsed.data : parseTelemetryDetailed(plaintext, {}).data;
    if (Object.keys(data).length) this.hooks.telemetry(data);
  }
}
