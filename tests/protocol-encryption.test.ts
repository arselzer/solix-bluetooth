import assert from 'node:assert/strict';
import { webcrypto } from 'node:crypto';
import test from 'node:test';
import { SolixConnection } from '../src/protocol/connection';
import { PrimeSession } from '../src/protocol/prime';
import { buildPacket, parsePacket } from '../src/protocol/packet';
import { GCM_AUTH_DATA, PATTERN_NEGOTIATION } from '../src/protocol/constants';
import {
  decryptAesCbc, decryptAesGcm, deriveSharedSecret, encryptAesGcm,
  generateECDHKeyPair,
} from '../src/protocol/crypto';
import { concatBytes, fromHex, toHex } from '../src/protocol/utils';

if (!globalThis.crypto) Object.defineProperty(globalThis, 'crypto', { value: webcrypto });

const negotiationKey = fromHex('b8ff7422955d4eb6d554a2c470280559');
const negotiationNonce = fromHex('6ba3e3f2f3a60f2971ce5d1f');
const invalidCommands = ['', '40', '404000', '0040', '0000'];
const invalidHeader = /exactly two bytes|0x4000 encryption flag/;

test('Prime rejects inconsistent headers before sending and preserves negotiated GCM requests', async () => {
  const sent: Uint8Array[] = [];
  const session = new PrimeSession({
    send: async packet => { sent.push(packet); },
    log: () => {}, ready: () => {}, pairingRequired: () => {}, telemetry: () => {},
  }, 'a'.repeat(40));
  await session.start();
  async function reply(command: string, body: Uint8Array) {
    const encrypted = await encryptAesGcm(body, negotiationKey, negotiationNonce, GCM_AUTH_DATA);
    const packet = parsePacket(buildPacket(PATTERN_NEGOTIATION, fromHex(command), encrypted));
    assert.ok(packet);
    await session.handle(packet);
  }
  await reply('4805', fromHex('00'));
  const publicReply = parsePacket(sent.at(-1)!);
  assert.ok(publicReply);
  assert.equal(toHex(publicReply.command), '4021');
  const publicBody = await decryptAesGcm(publicReply.payload, negotiationKey, negotiationNonce, GCM_AUTH_DATA);
  assert.equal(toHex(publicBody.slice(0, 2)), 'a140');
  const peer = await generateECDHKeyPair();
  const shared = await deriveSharedSecret(peer.privateKey, concatBytes(fromHex('04'), publicBody.slice(2)));
  await reply('4821', concatBytes(fromHex('a140'), peer.publicKeyRaw.slice(1)));
  const before = sent.length;
  for (const command of invalidCommands) {
    await assert.rejects(session.sendCommand(fromHex(command), fromHex('a10121')), invalidHeader);
  }
  assert.equal(sent.length, before);
  await session.sendCommand(fromHex('4100'), fromHex('a10121'));
  assert.equal(sent.length, before + 1);
  const request = parsePacket(sent.at(-1)!);
  assert.ok(request);
  assert.equal(toHex(request.command), '4100');
  const plain = await decryptAesGcm(request.payload, shared.aesKey, shared.iv.slice(0, 12), GCM_AUTH_DATA);
  assert.equal(toHex(plain.slice(0, 17)), 'a10121a20a040100e3fbfcfe000000fe04');
});

test('Legacy rejects inconsistent headers before GATT writes and preserves CBC requests', async () => {
  const sent: Uint8Array[] = [];
  const connection = new SolixConnection({
    onStateChange: () => {}, onTelemetry: () => {}, onLog: () => {}, onRawPacket: () => {},
  });
  const secret = Uint8Array.from({ length: 32 }, (_, index) => index);
  const keys = { aesKey: secret.slice(0, 16), iv: secret.slice(16), sharedSecret: secret };
  // Synthetic BLE endpoint and negotiated keys; no browser/device connection.
  Object.assign(connection, {
    device: { name: 'SOLIX C1000' }, sessionKeys: keys,
    commandChar: { writeValueWithoutResponse: async (packet: Uint8Array) => { sent.push(packet); } },
  });
  for (const command of invalidCommands) {
    await assert.rejects(connection.sendCommand(fromHex(command), fromHex('a10121')), invalidHeader);
  }
  assert.equal(sent.length, 0);
  await connection.sendCommand(fromHex('4040'), fromHex('a10121'));
  assert.equal(sent.length, 1);
  const request = parsePacket(sent[0]);
  assert.ok(request);
  assert.equal(toHex(request.command), '4040');
  assert.equal(toHex(await decryptAesCbc(request.payload, keys.aesKey, keys.iv)), 'a10121');
});
