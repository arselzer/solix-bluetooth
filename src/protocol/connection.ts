import {
  SERVICE_UUID, UUID_COMMAND, UUID_TELEMETRY,
  NEGOTIATION_COMMAND_0, NEGOTIATION_COMMAND_1, NEGOTIATION_COMMAND_2,
  NEGOTIATION_COMMAND_3, NEGOTIATION_COMMAND_4_PREFIX,
  PATTERN_ENCRYPTED, getParamMap,
} from './constants';
import { generateECDHKeyPair, deriveSharedSecret, decryptAesCbc, encryptAesCbc, type SessionKeys } from './crypto';
import { buildPacket, parsePacket, isNegotiationPacket, isEncryptedPacket } from './packet';
import { parseTelemetry } from './telemetry';
import { toHex, fromHex, concatBytes, xorChecksum } from './utils';
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

export class SolixConnection {
  private device: BluetoothDevice | null = null;
  private server: BluetoothRemoteGATTServer | null = null;
  private commandChar: BluetoothRemoteGATTCharacteristic | null = null;
  private telemetryChar: BluetoothRemoteGATTCharacteristic | null = null;

  private privateKey: CryptoKey | null = null;
  private publicKeyRaw: Uint8Array | null = null;
  private sessionKeys: SessionKeys | null = null;
  private negotiationStage = -1;
  private negotiationTimestamp = 0;

  private telemetryFragments: Uint8Array[] = [];

  private handlers: ConnectionEventHandler;

  constructor(handlers: ConnectionEventHandler) {
    this.handlers = handlers;
  }

  get deviceName(): string | null {
    return this.device?.name ?? null;
  }

  private log(direction: LogEntry['direction'], message: string, data?: string) {
    this.handlers.onLog({ timestamp: Date.now(), direction, message, data });
  }

  async connect(): Promise<void> {
    this.handlers.onStateChange('connecting');
    this.log('info', 'Requesting Bluetooth device...');

    try {
      this.device = await navigator.bluetooth.requestDevice({
        filters: [
          { namePrefix: 'Solarbank' },
          { namePrefix: 'A17C' },
          { namePrefix: 'C1000' },
          { namePrefix: 'A17X' },
          { namePrefix: 'Anker' },
        ],
        optionalServices: [SERVICE_UUID],
      });

      this.log('info', `Found device: ${this.device.name}`);

      this.device.addEventListener('gattserverdisconnected', () => {
        this.log('info', 'Device disconnected');
        this.cleanup();
        this.handlers.onStateChange('disconnected');
      });

      // BLE connection often fails on first attempt — retry up to 3 times
      // Race against a 5s timeout since the browser's default timeout is ~30s
      this.log('info', 'Connecting to GATT server...');
      for (let attempt = 1; attempt <= 3; attempt++) {
        try {
          this.server = await Promise.race([
            this.device.gatt!.connect(),
            new Promise<never>((_, reject) =>
              setTimeout(() => reject(new Error('Connection timeout')), 5000)
            ),
          ]);
          break;
        } catch (e) {
          if (attempt < 3) {
            this.log('info', `Attempt ${attempt} failed (${e}), retrying...`);
            await new Promise(r => setTimeout(r, 500));
          } else {
            throw e;
          }
        }
      }

      this.log('info', 'Getting primary service...');
      const service = await this.server.getPrimaryService(SERVICE_UUID);

      this.log('info', 'Getting characteristics...');
      this.commandChar = await service.getCharacteristic(UUID_COMMAND);
      this.telemetryChar = await service.getCharacteristic(UUID_TELEMETRY);

      this.log('info', 'Subscribing to notifications...');
      await this.telemetryChar.startNotifications();
      this.telemetryChar.addEventListener('characteristicvaluechanged', this.onNotification.bind(this));

      this.log('info', 'Starting encryption negotiation...');
      this.handlers.onStateChange('negotiating');
      await this.startNegotiation();

    } catch (error) {
      this.log('error', `Connection failed: ${error}`);
      this.handlers.onStateChange('disconnected');
      throw error;
    }
  }

  async disconnect(): Promise<void> {
    if (this.server?.connected) {
      this.server.disconnect();
    }
    this.cleanup();
    this.handlers.onStateChange('disconnected');
  }

  private cleanup() {
    this.sessionKeys = null;
    this.negotiationStage = -1;
    this.telemetryFragments = [];
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

  private async sendNegotiationStage(stage: number): Promise<void> {
    if (!this.commandChar) return;

    let packet: Uint8Array;

    if (stage <= 3) {
      // Use exact pre-built packets from SolixBLE
      packet = fromHex(FIXED_NEGOTIATION_PACKETS[stage]);
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

    this.negotiationStage = stage;

    // Capture timestamp at stage 2 (cmd 0x29) for anti-replay
    if (stage === 2) {
      this.negotiationTimestamp = Date.now() / 1000;
    }
  }

  private async onNotification(event: Event): Promise<void> {
    const target = event.target as BluetoothRemoteGATTCharacteristic;
    const data = new Uint8Array(target.value!.buffer);

    this.handlers.onRawPacket('rx', data);

    const packet = parsePacket(data);
    if (!packet) {
      this.log('rx', 'Unparseable packet', toHex(data));
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
      toHex(packet.payload).substring(0, 80));

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
        this.log('info', `AES key: ${toHex(this.sessionKeys.aesKey)}`);
        this.log('info', `IV: ${toHex(this.sessionKeys.iv)}`);
        this.handlers.onStateChange('connected');
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
    if (cmdByte === 0xc4 || cmdByte === 0xc8) {
      const seqByte = packet.payload[0];
      const encData = packet.payload.slice(1);

      this.log('rx', `Fragment cmd=${toHex(packet.command)} seq=0x${seqByte.toString(16)} data=${encData.length}B raw=${raw.length}B`);

      if (raw.length < 50) {
        // Small/final fragment — strip seq byte and trigger assembly
        this.telemetryFragments.push(encData);
        await this.assembleTelemetry();
      } else {
        // Large fragment — accumulate
        this.telemetryFragments.push(encData);
      }
    } else if (cmdByte === 0x44 || cmdByte === 0x48) {
      // Single encrypted packet or response
      if (this.sessionKeys) {
        try {
          const decrypted = await decryptAesCbc(packet.payload, this.sessionKeys.aesKey, this.sessionKeys.iv);
          this.log('rx', `Decrypted (${decrypted.length}B)`, toHex(decrypted).substring(0, 80));
          const telemetry = parseTelemetry(decrypted, getParamMap(this.device?.name ?? undefined));
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

    // Concatenate all fragment data (sequence bytes already stripped)
    const combined = concatBytes(...this.telemetryFragments);
    const fragCount = this.telemetryFragments.length;

    this.log('rx', `Assembling: ${fragCount} fragments = ${combined.length}B (mod16=${combined.length % 16})`);

    // Reset
    this.telemetryFragments = [];

    try {
      const decrypted = await decryptAesCbc(combined, this.sessionKeys.aesKey, this.sessionKeys.iv);
      this.log('rx', `Telemetry decrypted (${decrypted.length}B)`, toHex(decrypted).substring(0, 100));

      const telemetry = parseTelemetry(decrypted, getParamMap(this.device?.name ?? undefined));
      if (Object.keys(telemetry).length > 0) {
        this.handlers.onTelemetry(telemetry);
      }
    } catch (e) {
      this.log('error', `Telemetry decrypt failed: ${e}`);
    }
  }

  async sendCommand(commandCode: Uint8Array, payload: Uint8Array): Promise<void> {
    if (!this.commandChar || !this.sessionKeys) {
      this.log('error', 'Not connected or no session keys');
      return;
    }

    const encrypted = await encryptAesCbc(payload, this.sessionKeys.aesKey, this.sessionKeys.iv);
    const packet = buildPacket(PATTERN_ENCRYPTED, commandCode, encrypted);

    this.log('tx', `Command 0x${toHex(commandCode)}`, toHex(packet).substring(0, 80));
    this.handlers.onRawPacket('tx', packet);

    try {
      await this.commandChar.writeValueWithoutResponse(packet);
    } catch {
      await this.commandChar.writeValue(packet);
    }
  }
}
