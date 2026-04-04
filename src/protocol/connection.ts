import { SERVICE_UUID, UUID_COMMAND, UUID_TELEMETRY, NEGOTIATION_COMMANDS, PATTERN_NEGOTIATION, PATTERN_ENCRYPTED } from './constants';
import { generateECDHKeyPair, deriveSharedSecret, decryptAesCbc, encryptAesCbc, type SessionKeys } from './crypto';
import { buildPacket, parsePacket, isNegotiationPacket, isEncryptedPacket } from './packet';
import { parseTelemetry } from './telemetry';
import { toHex, concatBytes } from './utils';
import type { ConnectionState, TelemetryData, LogEntry } from './types';

export type ConnectionEventHandler = {
  onStateChange: (state: ConnectionState) => void;
  onTelemetry: (data: TelemetryData) => void;
  onLog: (entry: LogEntry) => void;
  onRawPacket: (direction: 'tx' | 'rx', data: Uint8Array) => void;
};

export class SolixConnection {
  private device: BluetoothDevice | null = null;
  private server: BluetoothRemoteGATTServer | null = null;
  private commandChar: BluetoothRemoteGATTCharacteristic | null = null;
  private telemetryChar: BluetoothRemoteGATTCharacteristic | null = null;

  private privateKey: CryptoKey | null = null;
  private publicKeyRaw: Uint8Array | null = null;
  private sessionKeys: SessionKeys | null = null;
  private negotiationStage = 0;
  private negotiationStartTime = 0;

  private pendingLargePackets: Uint8Array[] = [];
  private pendingSmallPackets: Uint8Array[] = [];

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
        ],
        optionalServices: [SERVICE_UUID],
      });

      this.log('info', `Found device: ${this.device.name}`);

      this.device.addEventListener('gattserverdisconnected', () => {
        this.log('info', 'Device disconnected');
        this.cleanup();
        this.handlers.onStateChange('disconnected');
      });

      this.log('info', 'Connecting to GATT server...');
      this.server = await this.device.gatt!.connect();

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
    this.negotiationStage = 0;
    this.pendingLargePackets = [];
    this.pendingSmallPackets = [];
  }

  private async startNegotiation(): Promise<void> {
    // Generate ECDH key pair
    const keyPair = await generateECDHKeyPair();
    this.privateKey = keyPair.privateKey;
    this.publicKeyRaw = keyPair.publicKeyRaw;
    this.negotiationStartTime = Date.now() / 1000;

    this.log('info', 'Generated ECDH key pair');
    this.log('info', `Public key: ${toHex(this.publicKeyRaw).substring(0, 40)}...`);

    // Send negotiation stage 0
    await this.sendNegotiationCommand(0);
  }

  private async sendNegotiationCommand(stage: number): Promise<void> {
    if (!this.commandChar || !this.publicKeyRaw) return;

    const cmdDef = NEGOTIATION_COMMANDS[stage];
    if (!cmdDef) {
      this.log('info', `Negotiation stage ${stage}: no more commands to send`);
      return;
    }

    const command = new Uint8Array([0x40, cmdDef[0]]);

    // Build payload with stage-specific data
    let payload: Uint8Array;
    switch (stage) {
      case 0:
      case 1:
        // Send public key x-coordinate (bytes 1-33 of uncompressed public key)
        payload = concatBytes(
          new Uint8Array([cmdDef[1]]),
          this.publicKeyRaw.slice(1, 33)
        );
        break;
      case 2:
        // Send public key y-coordinate (bytes 33-65)
        payload = concatBytes(
          new Uint8Array([cmdDef[1]]),
          this.publicKeyRaw.slice(1, 33),  // x again for verification
          this.publicKeyRaw.slice(33, 65)   // y coordinate
        );
        break;
      case 3:
        // Shorter payload for auth step
        payload = concatBytes(
          new Uint8Array([cmdDef[1]]),
          this.publicKeyRaw.slice(1, 33)
        );
        break;
      case 4:
        // Final negotiation with full public key
        payload = concatBytes(
          new Uint8Array([cmdDef[1]]),
          this.publicKeyRaw
        );
        break;
      default:
        payload = new Uint8Array([cmdDef[1]]);
    }

    const packet = buildPacket(PATTERN_NEGOTIATION, command, payload);
    this.log('tx', `Negotiation stage ${stage}`, toHex(packet));
    this.handlers.onRawPacket('tx', packet);

    try {
      await this.commandChar.writeValueWithoutResponse(packet);
    } catch {
      await this.commandChar.writeValue(packet);
    }

    this.negotiationStage = stage;
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
      this.log('rx', `Unknown packet pattern: ${toHex(packet.pattern)}`, toHex(data).substring(0, 80));
    }
  }

  private async handleNegotiationResponse(packet: ReturnType<typeof parsePacket>): Promise<void> {
    if (!packet) return;

    const responseCmd = packet.command[1];
    this.log('rx', `Negotiation response cmd=0x${responseCmd.toString(16)}`, toHex(packet.payload).substring(0, 80));

    // Check if this is the key exchange response (stage 5 = device sent public key in cmd 0x21)
    if (responseCmd === 0x21 && packet.payload.length >= 64) {
      // Extract device's public key from the payload
      // The public key is typically in the payload as uncompressed point (65 bytes with 0x04 prefix)
      // or just the raw 64 bytes (x || y)
      let devicePublicKey: Uint8Array;
      if (packet.payload[0] === 0x04) {
        devicePublicKey = packet.payload.slice(0, 65);
      } else {
        // Assume raw x,y (64 bytes), prepend 0x04
        devicePublicKey = concatBytes(new Uint8Array([0x04]), packet.payload.slice(0, 64));
      }

      this.log('info', `Received device public key: ${toHex(devicePublicKey).substring(0, 40)}...`);

      try {
        this.sessionKeys = await deriveSharedSecret(this.privateKey!, devicePublicKey);
        this.log('info', 'Session keys derived successfully');
        this.log('info', `AES key: ${toHex(this.sessionKeys.aesKey)}`);
        this.log('info', `IV: ${toHex(this.sessionKeys.iv)}`);
        this.handlers.onStateChange('connected');
      } catch (e) {
        this.log('error', `Key derivation failed: ${e}`);
      }
      return;
    }

    // Send next negotiation stage
    const nextStage = this.negotiationStage + 1;
    if (nextStage <= 4) {
      await this.sendNegotiationCommand(nextStage);
    }
  }

  private async handleEncryptedPacket(packet: ReturnType<typeof parsePacket>, raw: Uint8Array): Promise<void> {
    if (!packet) return;

    const cmdByte = packet.command[0];
    const isLarge = raw.length > 100;
    const isFragmented = cmdByte === 0xc4 || cmdByte === 0xc8;

    if (isFragmented && isLarge) {
      this.pendingLargePackets.push(packet.payload);
      this.log('rx', `Large fragment (${raw.length}B, cmd=0x${cmdByte.toString(16)})`);
    } else if (isFragmented && !isLarge) {
      // Final fragment of a multi-part message
      this.pendingSmallPackets.push(packet.payload);
      this.log('rx', `Small fragment (${raw.length}B, cmd=0x${cmdByte.toString(16)})`);

      // Try to assemble and decrypt
      if (this.pendingLargePackets.length > 0) {
        await this.assembleTelemetry();
      }
    } else if (cmdByte === 0x44 || cmdByte === 0x48) {
      // Single packet or response
      if (this.sessionKeys) {
        try {
          const decrypted = await decryptAesCbc(packet.payload, this.sessionKeys.aesKey, this.sessionKeys.iv);
          const telemetry = parseTelemetry(decrypted);
          if (Object.keys(telemetry).length > 0) {
            this.handlers.onTelemetry(telemetry);
          }
          this.log('rx', `Decrypted single (${decrypted.length}B)`, toHex(decrypted).substring(0, 80));
        } catch (e) {
          this.log('rx', `Decrypt failed (single): ${e}`, toHex(packet.payload).substring(0, 80));
        }
      } else {
        this.log('rx', `Encrypted packet (no keys yet)`, toHex(raw).substring(0, 80));
      }
    } else {
      this.log('rx', `Encrypted cmd=0x${cmdByte.toString(16)} (${raw.length}B)`, toHex(raw).substring(0, 80));
    }
  }

  private async assembleTelemetry(): Promise<void> {
    if (!this.sessionKeys) return;

    // Concatenate all fragments
    const allLarge = concatBytes(...this.pendingLargePackets);
    const allSmall = this.pendingSmallPackets.length > 0
      ? concatBytes(...this.pendingSmallPackets)
      : new Uint8Array(0);

    const combined = concatBytes(allLarge, allSmall);

    this.pendingLargePackets = [];
    this.pendingSmallPackets = [];

    try {
      const decrypted = await decryptAesCbc(combined, this.sessionKeys.aesKey, this.sessionKeys.iv);
      this.log('rx', `Decrypted telemetry (${decrypted.length}B)`, toHex(decrypted).substring(0, 120));

      const telemetry = parseTelemetry(decrypted);
      if (Object.keys(telemetry).length > 0) {
        this.handlers.onTelemetry(telemetry);
      }
    } catch (e) {
      this.log('rx', `Telemetry decrypt failed: ${e}`);

      // Try without combining
      try {
        const decrypted = await decryptAesCbc(allLarge, this.sessionKeys.aesKey, this.sessionKeys.iv);
        this.log('rx', `Decrypted large only (${decrypted.length}B)`, toHex(decrypted).substring(0, 120));

        const telemetry = parseTelemetry(decrypted);
        if (Object.keys(telemetry).length > 0) {
          this.handlers.onTelemetry(telemetry);
        }
      } catch (e2) {
        this.log('error', `All decrypt attempts failed: ${e2}`);
      }
    }
  }

  async sendCommand(commandCode: Uint8Array, payload: Uint8Array): Promise<void> {
    if (!this.commandChar || !this.sessionKeys) {
      this.log('error', 'Cannot send command: not connected or no session keys');
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
