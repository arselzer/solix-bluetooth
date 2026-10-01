import {
  SERVICE_UUID, UUID_COMMAND, UUID_TELEMETRY, UUID_IDENTIFIER,
  NEGOTIATION_COMMAND_0, NEGOTIATION_COMMAND_1, NEGOTIATION_COMMAND_2,
  NEGOTIATION_COMMAND_3, NEGOTIATION_COMMAND_4_PREFIX,
  PATTERN_ENCRYPTED, PATTERN_NEGOTIATION, getParamMap,
} from './constants';
import { generateECDHKeyPair, deriveSharedSecret, decryptAesCbc, encryptAesCbc, type SessionKeys } from './crypto';
import { buildPacket, parsePacket, isNegotiationPacket, isEncryptedPacket, requireEncryptedCommand } from './packet';
import { parseTelemetryDetailed, parseC1000Gen2Telemetry } from './telemetry';
import { PrimeSession } from './prime';
import { toHex, fromHex, concatBytes, xorChecksum, writeUint32LE } from './utils';
import type { ConnectionState, TelemetryData, LogEntry } from './types';

export type ConnectionEventHandler = {
  onStateChange: (state: ConnectionState) => void;
  onTelemetry: (data: TelemetryData) => void;
  onLog: (entry: LogEntry) => void;
  onRawPacket: (direction: 'tx' | 'rx', data: Uint8Array) => void;
};

// Pre-built negotiation commands (stages 0-3 are fixed, 4+ are dynamic)
const FIXED_NEGOTIATION_PACKETS: Record<number, string> = {
  0: NEGOTIATION_COMMAND_0,
  1: NEGOTIATION_COMMAND_1,
  2: NEGOTIATION_COMMAND_2,
  3: NEGOTIATION_COMMAND_3,
};

const LEGACY_CLIENT_UUID = new TextEncoder().encode('b2dc0b17-b75d-4abf-ba6e-ec7c997c23e7');
function tlv(id: number, value: Uint8Array): Uint8Array {
  return concatBytes(new Uint8Array([id, value.length]), value);
}

export class SolixConnection {
  private device: BluetoothDevice | null = null;
  private server: BluetoothRemoteGATTServer | null = null;
  private commandChar: BluetoothRemoteGATTCharacteristic | null = null;
  private telemetryChar: BluetoothRemoteGATTCharacteristic | null = null;
  private prime: PrimeSession | null = null;

  private privateKey: CryptoKey | null = null;
  private publicKeyRaw: Uint8Array | null = null;
  private sessionKeys: SessionKeys | null = null;

  private telemetryFragments: Uint8Array[] = [];
  private assemblyTimer: ReturnType<typeof setTimeout> | null = null;
  private statusPollTimer: ReturnType<typeof setInterval> | null = null;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private autoReconnect = true;
  private reconnecting = false;
  private ownerUserId: string | null = null;
  private c1000Protocol: 'prime' | 'legacy' = 'prime';
  private notificationHandler = this.onNotification.bind(this);

  private handlers: ConnectionEventHandler;

  constructor(handlers: ConnectionEventHandler) {
    this.handlers = handlers;
  }

  get deviceName(): string | null {
    return this.device?.name ?? null;
  }

  private get isC1000Gen2(): boolean {
    return /C1000.*Gen 2|A1763/i.test(this.device?.name ?? '');
  }

  private get isC2000Gen2(): boolean {
    return /C2000.*Gen 2|A1783/i.test(this.device?.name ?? '');
  }

  private log(direction: LogEntry['direction'], message: string, data?: string) {
    this.handlers.onLog({ timestamp: Date.now(), direction, message, data });
  }

  async connect(showAllDevices = false, ownerUserId: string | null = null,
    c1000Protocol: 'prime' | 'legacy' = 'prime'): Promise<void> {
    this.handlers.onStateChange('connecting');
    this.log('info', 'Requesting Bluetooth device...');

    try {
      this.autoReconnect = true;
      this.ownerUserId = ownerUserId;
      this.c1000Protocol = c1000Protocol;
      this.device = await navigator.bluetooth.requestDevice(showAllDevices ? {
        acceptAllDevices: true,
        optionalServices: [SERVICE_UUID],
      } : {
        filters: [
          { services: [UUID_IDENTIFIER] },
          { namePrefix: 'Solarbank' },
          { namePrefix: 'SOLIX' },
          { namePrefix: 'A17C' },
          { namePrefix: 'C1000' },
          { namePrefix: 'C2000' },
          { namePrefix: 'A1763' },
          { namePrefix: 'A1783' },
          { namePrefix: 'A17X' },
          { namePrefix: 'Anker' },
        ],
        optionalServices: [SERVICE_UUID],
      });

      this.log('info', `Found device: ${this.device.name}`);

      this.device.addEventListener('gattserverdisconnected', () => {
        this.log('info', 'Device disconnected');
        this.cleanup();
        if (this.autoReconnect && !this.reconnecting) {
          this.handlers.onStateChange('connecting');
          this.reconnectTimer = setTimeout(() => {
            this.reconnectTimer = null;
            void this.attemptReconnect();
          }, 1000);
        } else {
          this.handlers.onStateChange('disconnected');
        }
      });

      // BLE connection often fails on first attempt — retry up to 3 times
      // Use the browser's default timeout (more reliable than racing with our own)
      this.log('info', 'Connecting to GATT server...');
      for (let attempt = 1; attempt <= 3; attempt++) {
        try {
          this.server = await this.device.gatt!.connect();
          break;
        } catch (e) {
          if (attempt < 3) {
            this.log('info', `Attempt ${attempt} failed, retrying in 1s...`);
            await new Promise(r => setTimeout(r, 1000));
          } else {
            throw e;
          }
        }
      }

      this.log('info', 'Getting primary service...');
      let service: BluetoothRemoteGATTService;
      try {
        service = await this.server!.getPrimaryService(SERVICE_UUID);
      } catch (error) {
        throw new Error(`Device does not expose the expected Anker BLE service (${SERVICE_UUID}): ${error}`);
      }

      this.log('info', 'Getting characteristics...');
      this.commandChar = await service.getCharacteristic(UUID_COMMAND);
      this.telemetryChar = await service.getCharacteristic(UUID_TELEMETRY);

      this.log('info', 'Subscribing to notifications...');
      await this.telemetryChar.startNotifications();
      this.telemetryChar.addEventListener('characteristicvaluechanged', this.notificationHandler);

      this.log('info', 'Starting encryption negotiation...');
      this.handlers.onStateChange('negotiating');
      await this.startSelectedNegotiation();

    } catch (error) {
      this.log('error', `Connection failed: ${error}`);
      this.autoReconnect = false;
      this.server?.disconnect();
      this.cleanup();
      this.handlers.onStateChange('disconnected');
      throw error;
    }
  }

  async disconnect(): Promise<void> {
    this.autoReconnect = false;
    this.stopStatusPolling();
    if (this.server?.connected) {
      this.server.disconnect();
    }
    this.cleanup();
    this.handlers.onStateChange('disconnected');
  }

  private startStatusPolling() {
    this.stopStatusPolling();
    this.statusPollTimer = setInterval(() => {
      if ((this.prime || this.sessionKeys) && this.commandChar && this.server?.connected) {
        void this.requestStatus().catch(error => this.log('error', `Status request failed: ${error}`));
      }
    }, 10000);
  }

  private stopStatusPolling() {
    if (this.statusPollTimer) {
      clearInterval(this.statusPollTimer);
      this.statusPollTimer = null;
    }
  }

  private async attemptReconnect(): Promise<void> {
    if (!this.autoReconnect || !this.device || this.reconnecting) return;
    this.reconnecting = true;

    this.log('info', 'Attempting auto-reconnect...');
    this.handlers.onStateChange('connecting');

    try {
      for (let attempt = 1; attempt <= 3 && this.autoReconnect; attempt++) {
        try {
          this.server = await this.device.gatt!.connect();
          if (!this.autoReconnect) {
            this.server.disconnect();
            return;
          }
          this.log('info', `Reconnected on attempt ${attempt}`);

          const service = await this.server.getPrimaryService(SERVICE_UUID);
          this.commandChar = await service.getCharacteristic(UUID_COMMAND);
          this.telemetryChar = await service.getCharacteristic(UUID_TELEMETRY);
          await this.telemetryChar.startNotifications();
          this.telemetryChar.addEventListener('characteristicvaluechanged', this.notificationHandler);

          this.handlers.onStateChange('negotiating');
          await this.startSelectedNegotiation();
          return;
        } catch (e) {
          this.server?.disconnect();
          this.cleanup();
          if (attempt < 3 && this.autoReconnect) {
            this.log('info', `Reconnect attempt ${attempt} failed, retrying in 2s...`);
            await new Promise(r => setTimeout(r, 2000));
          }
        }
      }

      this.log('error', 'Auto-reconnect failed after 3 attempts');
      this.handlers.onStateChange('disconnected');
    } finally {
      this.reconnecting = false;
    }
  }

  async confirmPairing(): Promise<void> {
    if (!this.prime || !this.server?.connected) {
      throw new Error('Station is not connected for pairing');
    }
    this.handlers.onStateChange('negotiating');
    await this.prime.confirmPairing();
  }

  private cleanup() {
    this.prime?.reset();
    this.prime = null;
    this.sessionKeys = null;
    this.stopStatusPolling();
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    this.telemetryChar?.removeEventListener('characteristicvaluechanged', this.notificationHandler);
    this.telemetryChar = null;
    this.commandChar = null;
    this.telemetryFragments = [];
    if (this.assemblyTimer) {
      clearTimeout(this.assemblyTimer);
      this.assemblyTimer = null;
    }
  }

  private async startSelectedNegotiation(): Promise<void> {
    if (this.isC2000Gen2 || (this.isC1000Gen2 && this.c1000Protocol === 'prime')) {
      if (this.ownerUserId && !/^[0-9a-fA-F]{40}$/.test(this.ownerUserId)) {
        throw new Error('Gen 2 client ID must be 40 hexadecimal characters');
      }
      const storageKey = `solix-prime-client:${this.device!.id}`;
      let clientId = this.ownerUserId;
      if (!clientId) {
        try { clientId = localStorage.getItem(storageKey); }
        catch { /* Storage may be disabled by the browser. */ }
      }
      if (!clientId || !/^[0-9a-fA-F]{40}$/.test(clientId)) {
        clientId = Array.from(crypto.getRandomValues(new Uint8Array(20)),
          byte => byte.toString(16).padStart(2, '0')).join('');
        try { localStorage.setItem(storageKey, clientId); }
        catch { this.log('info', `Browser storage unavailable; save this client ID: ${clientId}`); }
        this.log('info', 'Generated a local Gen 2 client ID. Pair it with one short main power button press if prompted.');
      }
      this.ownerUserId = clientId;
      this.prime = new PrimeSession({
        send: async (packet) => {
          this.handlers.onRawPacket('tx', packet);
          await this.commandChar!.writeValueWithoutResponse(packet);
        },
        log: (message, data) => this.log('info', message, data),
        ready: () => {
          this.handlers.onStateChange('connected');
          this.startStatusPolling();
        },
        pairingRequired: () => this.handlers.onStateChange('pairing'),
        telemetry: (data) => this.handlers.onTelemetry(data),
      }, clientId, this.isC2000Gen2);
      await this.prime.start();
    } else {
      await this.startNegotiation();
    }
  }

  private async startNegotiation(): Promise<void> {
    const keyPair = await generateECDHKeyPair();
    this.privateKey = keyPair.privateKey;
    this.publicKeyRaw = keyPair.publicKeyRaw;

    this.log('info', 'Generated ECDH key pair');
    this.log('info', `Public key: ${toHex(this.publicKeyRaw).substring(0, 40)}...`);

    // Send negotiation stage 0
    await this.sendNegotiationStage(0);
  }

  private parseNegotiationInfo(payload: Uint8Array): void {
    // The cmd 0x29 response contains device info as simple TLV:
    // a1=version, a2=chip(ESP32), a3=firmware, a4=serial, a5=?
    let offset = 0;
    if (payload[0] === 0x00) offset = 1; // skip leading 0x00

    while (offset < payload.length) {
      const id = payload[offset++];
      if (offset >= payload.length) break;
      const len = payload[offset++];
      if (offset + len > payload.length) break;
      const data = payload.slice(offset, offset + len);
      offset += len;

      const hex = toHex(data);
      const ascii = new TextDecoder().decode(data);
      const isPrintable = /^[\x20-\x7e]+$/.test(ascii);

      const labels: Record<number, string> = {
        0xa1: 'protocol_version',
        0xa2: 'chip',
        0xa3: 'firmware',
        0xa4: 'serial',
        0xa5: 'feature_flags',
      };

      const label = labels[id] || `negotiation_0x${id.toString(16)}`;
      const display = isPrintable ? ascii : hex;
      this.log('info', `Device info: ${label} = ${display}`, hex);
    }
  }

  private async sendNegotiationStage(stage: number): Promise<void> {
    if (!this.commandChar) return;

    let packet: Uint8Array;

    if (this.isC1000Gen2 && stage <= 3) {
      const timestamp = tlv(0xa1, writeUint32LE(Math.floor(Date.now() / 1000)));
      const uuid = tlv(0xa2, LEGACY_CLIENT_UUID);
      const extras = stage === 1 ? concatBytes(tlv(0xa3, fromHex('20')), tlv(0xa4, fromHex('00f0')))
        : stage === 3 ? concatBytes(tlv(0xa3, fromHex('20')), tlv(0xa4, fromHex('00f0')), tlv(0xa5, fromHex('40')))
          : new Uint8Array();
      const command = ['0001', '0003', '0029', '0005'][stage];
      packet = buildPacket(PATTERN_NEGOTIATION, fromHex(command), concatBytes(timestamp, uuid, extras));
    } else if (stage <= 3) {
      // Use exact pre-built packets from SolixBLE
      packet = fromHex(FIXED_NEGOTIATION_PACKETS[stage]);
    } else if (stage === 4 && this.publicKeyRaw && this.isC1000Gen2) {
      packet = buildPacket(PATTERN_NEGOTIATION, fromHex('0021'), tlv(0xa1, this.publicKeyRaw.slice(1)));
    } else if (stage === 4 && this.publicKeyRaw) {
      // Stage 4: send our ECDH public key (uncompressed, 65 bytes starting with 0x04)
      // Build: prefix + public_key_bytes + checksum
      const prefix = fromHex(NEGOTIATION_COMMAND_4_PREFIX);
      const withoutChecksum = concatBytes(prefix, this.publicKeyRaw.slice(1)); // skip 0x04, send raw 64 bytes
      const checksum = xorChecksum(withoutChecksum);
      packet = concatBytes(withoutChecksum, new Uint8Array([checksum]));
    } else {
      this.log('info', `Negotiation stage ${stage}: nothing to send`);
      return;
    }

    this.log('tx', `Negotiation stage ${stage} (${packet.length}B)`, toHex(packet).substring(0, 80));
    this.handlers.onRawPacket('tx', packet);

    try {
      await this.commandChar.writeValueWithoutResponse(packet);
    } catch {
      await this.commandChar.writeValue(packet);
    }

  }

  private async onNotification(event: Event): Promise<void> {
    const target = event.target as BluetoothRemoteGATTCharacteristic;
    const value = target.value!;
    const data = new Uint8Array(value.buffer, value.byteOffset, value.byteLength);

    this.handlers.onRawPacket('rx', data);

    const packet = parsePacket(data);
    if (!packet) {
      this.log('rx', 'Unparseable packet', toHex(data));
      return;
    }

    if (this.prime) {
      try { await this.prime.handle(packet); }
      catch (error) {
        this.log('error', `Prime protocol error: ${error}`);
        this.server?.disconnect();
        this.cleanup();
        this.handlers.onStateChange('disconnected');
      }
      return;
    }

    if (isNegotiationPacket(packet)) {
      await this.handleNegotiationResponse(packet);
    } else if (isEncryptedPacket(packet)) {
      await this.handleEncryptedPacket(packet, data);
    } else {
      this.log('rx', `Unknown pattern: ${toHex(packet.pattern)}`, toHex(data).substring(0, 80));
    }
  }

  private async handleNegotiationResponse(packet: ReturnType<typeof parsePacket>): Promise<void> {
    if (!packet) return;

    const responseCmd = packet.command[1]; // e.g., 0x01, 0x03, 0x29, 0x05, 0x21
    this.log('rx', `Negotiation response cmd=0x${responseCmd.toString(16)} (${packet.payload.length}B)`,
      toHex(packet.payload));

    // cmd 0x29 response contains device info as TLV
    if (responseCmd === 0x29 && packet.payload.length > 10) {
      this.parseNegotiationInfo(packet.payload);
    }

    // Map response commands to next stage
    const stageMap: Record<number, number> = {
      0x01: 1,  // response to stage 0 -> send stage 1
      0x03: 2,  // response to stage 1 -> send stage 2
      0x29: 3,  // response to stage 2 -> send stage 3
      0x05: 4,  // response to stage 3 -> send stage 4 (our public key)
      0x21: 5,  // response to stage 4 -> device's public key, derive shared secret
    };

    const nextStage = stageMap[responseCmd];

    if (nextStage === 5 && packet.payload.length >= 64) {
      // Device sent its public key - derive shared secret
      // The payload has prefix bytes before the raw 64-byte public key (x || y).
      // With 67 bytes: 3 prefix + 64 key bytes. With 65 bytes: could be 0x04 + 64.
      // Try to find the 64-byte key by looking for known prefix patterns.
      this.log('info', `Key response full payload (${packet.payload.length}B): ${toHex(packet.payload)}`);

      let devicePublicKey: Uint8Array;
      const keyOffset = packet.payload.length - 64; // key is the last 64 bytes

      if (packet.payload[0] === 0x04 && packet.payload.length === 65) {
        // Standard uncompressed point format
        devicePublicKey = packet.payload.slice(0, 65);
      } else {
        // Skip prefix bytes, take last 64 bytes as raw x,y, prepend 0x04
        const rawKey = packet.payload.slice(keyOffset);
        devicePublicKey = concatBytes(new Uint8Array([0x04]), rawKey);
        this.log('info', `Skipped ${keyOffset} prefix bytes: ${toHex(packet.payload.slice(0, keyOffset))}`);
      }

      this.log('info', `Device public key: ${toHex(devicePublicKey).substring(0, 40)}...`);

      try {
        this.sessionKeys = await deriveSharedSecret(this.privateKey!, devicePublicKey);
        this.log('info', 'Session keys derived successfully');
        if (this.isC1000Gen2) {
          const tz = new TextEncoder().encode('UTC0');
          const stage5 = concatBytes(
            tlv(0xa1, writeUint32LE(Math.floor(Date.now() / 1000))),
            tlv(0xa2, LEGACY_CLIENT_UUID), tlv(0xa3, fromHex('20')),
            tlv(0xa4, fromHex('00000000')), tlv(0xa5, tz),
          );
          const encrypted = await encryptAesCbc(stage5, this.sessionKeys.aesKey, this.sessionKeys.iv);
          const finalPacket = buildPacket(PATTERN_NEGOTIATION, fromHex('4022'), encrypted);
          this.handlers.onRawPacket('tx', finalPacket);
          await this.commandChar!.writeValueWithoutResponse(finalPacket);
        }
        this.handlers.onStateChange('connected');

        // Send initial status request and start periodic polling
        setTimeout(() => this.requestStatus(), 500);
        this.startStatusPolling();
      } catch (e) {
        this.log('error', `Key derivation failed: ${e}`);
      }
    } else if (nextStage !== undefined && nextStage <= 4) {
      await this.sendNegotiationStage(nextStage);
    }
  }

  private async handleEncryptedPacket(packet: ReturnType<typeof parsePacket>, raw: Uint8Array): Promise<void> {
    if (!packet) return;

    const cmdByte = packet.command[0];

    // Telemetry arrives as fragmented c4xx/c8xx packets.
    // Each fragment's payload starts with a sequence byte (e.g., 0x13, 0x23, 0x33)
    // that indicates fragment position. The encrypted data follows.
    //
    // C1000: 1 large + 1 small (sequence bytes: 0x13, 0x23 or similar)
    // Solarbank 3: 2 large + 1 small (sequence bytes: 0x13, 0x23, 0x33)
    //
    // We accumulate all fragments, strip the sequence byte from each,
    // and decrypt when the small fragment arrives.
    if (cmdByte === 0xc4 || cmdByte === 0xc8 || cmdByte === 0xc9) {
      this.log('rx', `Fragment cmd=${toHex(packet.command)} byte0=0x${packet.payload[0].toString(16)} payload=${packet.payload.length}B raw=${raw.length}B`);

      this.telemetryFragments.push(packet.payload);

      // Reset the assembly timer — assemble after 150ms of no new fragments
      if (this.assemblyTimer) clearTimeout(this.assemblyTimer);
      this.assemblyTimer = setTimeout(() => {
        this.assemblyTimer = null;
        if (this.telemetryFragments.length > 0) {
          this.assembleTelemetry();
        }
      }, 150);
    } else if (cmdByte === 0x44 || cmdByte === 0x48 || cmdByte === 0x49) {
      // Single encrypted packet or response
      if (this.sessionKeys) {
        try {
          const decrypted = await decryptAesCbc(packet.payload, this.sessionKeys.aesKey, this.sessionKeys.iv);
          this.log('rx', `Decrypted single (${decrypted.length}B)`, toHex(decrypted));
          const { data: telemetry, tlvEntries } = this.isC1000Gen2
            ? parseC1000Gen2Telemetry(decrypted)
            : parseTelemetryDetailed(decrypted, getParamMap(this.device?.name ?? undefined));
          for (const entry of tlvEntries) {
            const nameStr = entry.name ? ` (${entry.name})` : ' [UNKNOWN]';
            const valStr = entry.decoded !== null ? ` = ${entry.decoded}` : '';
            this.log('info', `TLV @${entry.offset}: 0x${entry.paramIdHex} len=${entry.length}${nameStr}${valStr}`, entry.rawHex);
          }
          if (Object.keys(telemetry).length > 0) {
            this.handlers.onTelemetry(telemetry);
          }
        } catch (e) {
          this.log('rx', `Decrypt failed: ${e}`, toHex(packet.payload).substring(0, 60));
        }
      } else {
        this.log('rx', `Encrypted (no keys)`, toHex(raw).substring(0, 60));
      }
    } else {
      this.log('rx', `Encrypted cmd=0x${cmdByte.toString(16)} (${raw.length}B)`, toHex(raw).substring(0, 60));
    }
  }

  private async assembleTelemetry(): Promise<void> {
    if (!this.sessionKeys || this.telemetryFragments.length === 0) return;

    const fragments = this.telemetryFragments;
    this.telemetryFragments = [];
    if (this.assemblyTimer) {
      clearTimeout(this.assemblyTimer);
      this.assemblyTimer = null;
    }

    const paramMap = getParamMap(this.device?.name ?? undefined);

    // Try two strategies:
    // 1. Raw concatenation (C1000 style — no sequence bytes)
    // 2. Strip first byte from each fragment (Solarbank style — sequence bytes)
    const rawCombined = concatBytes(...fragments);
    const strippedCombined = concatBytes(...fragments.map(f => f.slice(1)));

    for (const [label, data] of [['raw', rawCombined], ['stripped', strippedCombined]] as const) {
      if (data.length % 16 !== 0) continue;
      try {
        const decrypted = await decryptAesCbc(data, this.sessionKeys.aesKey, this.sessionKeys.iv);
        this.log('rx', `Telemetry decrypted (${label}, ${decrypted.length}B)`, toHex(decrypted));

        const { data: telemetry, tlvEntries } = this.isC1000Gen2
          ? parseC1000Gen2Telemetry(decrypted)
          : parseTelemetryDetailed(decrypted, paramMap);

        // Log each TLV entry for reverse engineering
        for (const entry of tlvEntries) {
          const nameStr = entry.name ? ` (${entry.name})` : ' [UNKNOWN]';
          const valStr = entry.decoded !== null ? ` = ${entry.decoded}` : '';
          this.log('info', `TLV @${entry.offset}: 0x${entry.paramIdHex} len=${entry.length}${nameStr}${valStr}`, entry.rawHex);
        }

        if (Object.keys(telemetry).length > 0) {
          this.handlers.onTelemetry(telemetry);
        }
        return;
      } catch {
        // Try next strategy
      }
    }

    this.log('error', `Telemetry decrypt failed: raw=${rawCombined.length}B(mod16=${rawCombined.length % 16}) stripped=${strippedCombined.length}B(mod16=${strippedCombined.length % 16})`);
  }

  async requestStatus(): Promise<void> {
    this.log('tx', 'Sending status request');
    if (this.prime) {
      await this.prime.sendCommand(fromHex('4100'), fromHex('a10121'));
      return;
    }
    await this.sendCommand(fromHex(this.isC1000Gen2 ? '4100' : '4040'), fromHex('a10121'));
  }

  async setChargeLimits(upper: number, lower: number): Promise<void> {
    if (!this.isC1000Gen2 || !this.prime || !this.server?.connected) {
      throw new Error('Charge limits require a connected C1000 Gen 2 Prime session');
    }
    if (!Number.isInteger(upper) || upper < 80 || upper > 100 || upper % 5 !== 0 ||
        !Number.isInteger(lower) || ![1, 5, 10, 15, 20].includes(lower)) {
      throw new Error('Choose an upper limit of 80–100% in 5% steps and a lower limit of 1, 5, 10, 15, or 20%');
    }
    const body = concatBytes(fromHex('a10121'), tlv(0xaa, new Uint8Array([1, upper])),
      tlv(0xab, new Uint8Array([1, lower])));
    await this.prime.sendCommand(fromHex('4103'), body, 'none');
    this.log('tx', `Requested C1000 charge limits ${lower}–${upper}%`);
  }

  async setAcChargingPower(watts: number): Promise<void> {
    if (!this.isC1000Gen2 || !this.prime || !this.server?.connected) {
      throw new Error('AC charging power requires a connected C1000 Gen 2 Prime session');
    }
    if (!Number.isInteger(watts) || watts < 300 || watts > 1200 || watts % 100 !== 0) {
      throw new Error('Choose AC charging power from 300 to 1200 W in 100 W steps');
    }
    const body = concatBytes(fromHex('a10121'), tlv(0xa4,
      new Uint8Array([2, watts & 0xff, watts >> 8])), tlv(0xab, fromHex('020000')));
    await this.prime.sendCommand(fromHex('4101'), body, 'fd');
    this.log('tx', `Requested C1000 AC charging power ${watts} W`);
  }

  async sendCommand(commandCode: Uint8Array, payload: Uint8Array): Promise<void> {
    const code = toHex(commandCode);
    const body = toHex(payload);
    if (this.isC2000Gen2 && (code !== '4100' || body !== 'a10121')) {
      throw new Error('C2000 Gen 2 allows only the telemetry subscription');
    }
    if (this.isC1000Gen2 && !(
      (code === '4100' && body === 'a10121') ||
      (code === '4101' && (body === 'a10121a2020101' || body === 'a10121a2020100')) ||
      (code === '4102' && (body === 'a10121a2020101' || body === 'a10121a2020100'))
    )) {
      throw new Error('This C1000 Gen 2 command has not been verified');
    }
    if (this.prime) {
      await this.prime.sendCommand(commandCode, payload);
      return;
    }
    if (!this.commandChar || !this.sessionKeys) {
      this.log('error', 'Not connected or no session keys');
      return;
    }

    requireEncryptedCommand(commandCode);
    const plaintext = this.isC1000Gen2
      ? concatBytes(payload, tlv(0xfe, concatBytes(fromHex('03'), writeUint32LE(Math.floor(Date.now() / 1000)))))
      : payload;
    const encrypted = await encryptAesCbc(plaintext, this.sessionKeys.aesKey, this.sessionKeys.iv);
    const packet = buildPacket(this.isC1000Gen2 ? fromHex('03000f') : PATTERN_ENCRYPTED, commandCode, encrypted);

    this.log('tx', `Command 0x${toHex(commandCode)}`, toHex(packet).substring(0, 80));
    this.handlers.onRawPacket('tx', packet);

    try {
      await this.commandChar.writeValueWithoutResponse(packet);
    } catch {
      await this.commandChar.writeValue(packet);
    }
  }
}
