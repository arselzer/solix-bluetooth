// Offline GATT peer: exercises real negotiation, reconnect, and command guards.
// Run: node --import tsx --test tools/test-gen2-reconnect.ts
import assert from 'node:assert/strict';
import { setTimeout as delay } from 'node:timers/promises';
import { test } from 'node:test';
import { SolixConnection } from '../src/protocol/connection';
import { GCM_AUTH_DATA, PATTERN_NEGOTIATION, UUID_COMMAND } from '../src/protocol/constants';
import { decryptAesGcm, deriveSharedSecret, encryptAesGcm, generateECDHKeyPair } from '../src/protocol/crypto';
import { buildPacket, parsePacket } from '../src/protocol/packet';
import { concatBytes, fromHex, toHex } from '../src/protocol/utils';

const initialKey = fromHex('b8ff7422955d4eb6d554a2c470280559');
const initialNonce = fromHex('6ba3e3f2f3a60f2971ce5d1f');

function fixture(name: string) {
  const writes: Uint8Array[] = [];
  const states: string[] = [];
  const stored = new Map<string, string>();
  let connectCount = 0;
  class Characteristic extends EventTarget {
    value?: DataView;
    async startNotifications() { return this; }
    async writeValueWithoutResponse(packet: Uint8Array) { writes.push(packet); }
    async writeValue(packet: Uint8Array) { writes.push(packet); }
    receive(packet: Uint8Array) {
      this.value = new DataView(Uint8Array.from(packet).buffer);
      this.dispatchEvent(new Event('characteristicvaluechanged'));
    }
  }
  const command = new Characteristic();
  const notifications = new Characteristic();
  const device = Object.assign(new EventTarget(), {
    id: 'synthetic-station', name,
    gatt: {
      connected: false,
      async connect() { connectCount++; this.connected = true; return this; },
      disconnect() {
        if (!this.connected) return;
        this.connected = false;
        device.dispatchEvent(new Event('gattserverdisconnected'));
      },
      async getPrimaryService() {
        return { getCharacteristic: async (uuid: string) => uuid === UUID_COMMAND ? command : notifications };
      },
    },
  });
  const previousNavigator = Object.getOwnPropertyDescriptor(globalThis, 'navigator');
  const previousStorage = Object.getOwnPropertyDescriptor(globalThis, 'localStorage');
  Object.defineProperty(globalThis, 'navigator', {
    configurable: true, value: { bluetooth: { requestDevice: async () => device } },
  });
  Object.defineProperty(globalThis, 'localStorage', {
    configurable: true,
    value: { getItem: (key: string) => stored.get(key) ?? null, setItem: (key: string, value: string) => stored.set(key, value) },
  });
  const connection = new SolixConnection({
    onStateChange: state => states.push(state), onTelemetry() {}, onLog() {}, onRawPacket() {},
  });
  return {
    connection, device, notifications, writes, states, stored,
    get connectCount() { return connectCount; },
    async close() {
      await connection.disconnect();
      for (const [key, descriptor] of [['navigator', previousNavigator], ['localStorage', previousStorage]] as const) {
        if (descriptor) Object.defineProperty(globalThis, key, descriptor);
        else Reflect.deleteProperty(globalThis, key);
      }
    },
  };
}

async function takeWrite(peer: ReturnType<typeof fixture>, expected: string) {
  for (let i = 0; i < 200 && !peer.writes.length; i++) await delay(10);
  const raw = peer.writes.shift();
  assert.ok(raw, `Missing ${expected} request`);
  const packet = parsePacket(raw);
  assert.ok(packet);
  assert.equal(toHex(packet.command), expected);
  return packet;
}

async function register(peer: ReturnType<typeof fixture>) {
  let key = initialKey;
  let nonce = initialNonce;
  const reply = async (command: string, plaintext = new Uint8Array([0])) => {
    peer.notifications.receive(buildPacket(PATTERN_NEGOTIATION, fromHex(command),
      await encryptAesGcm(plaintext, key, nonce, GCM_AUTH_DATA)));
  };
  await takeWrite(peer, '4001');
  for (const [response, request] of [['4801', '4003'], ['4803', '4029'], ['4829', '4005']] as const) {
    await reply(response);
    await takeWrite(peer, request);
  }
  await reply('4805');
  const exchange = await takeWrite(peer, '4021');
  const plaintext = await decryptAesGcm(exchange.payload, key, nonce, GCM_AUTH_DATA);
  assert.equal(toHex(plaintext.slice(0, 2)), 'a140');
  const pair = await generateECDHKeyPair();
  const session = await deriveSharedSecret(pair.privateKey, concatBytes(fromHex('04'), plaintext.slice(2)));
  await reply('4821', concatBytes(fromHex('00a140'), pair.publicKeyRaw.slice(1)));
  key = session.aesKey;
  nonce = session.iv.slice(0, 12);
  await takeWrite(peer, '4022');
  await reply('4822');
  const registration = await takeWrite(peer, '4027');
  const fields = await decryptAesGcm(registration.payload, key, nonce, GCM_AUTH_DATA);
  assert.equal(toHex(fields.slice(6, 8)), 'a228');
  const clientId = new TextDecoder().decode(fields.slice(8));
  assert.match(clientId, /^[0-9a-f]{40}$/);
  await reply('4827');
  await takeWrite(peer, '4100');
  assert.equal(peer.states.at(-1), 'connected');
  return clientId;
}

for (const name of ['SOLIX C1000 Gen 2', 'SOLIX C2000 Gen 2']) {
  test(`${name} reconnects with Prime and the same paired ID`, async () => {
    const peer = fixture(name);
    try {
      await peer.connection.connect();
      const clientId = await register(peer);
      peer.stored.clear(); // Reconnect must also work when browser storage disappears.
      peer.device.gatt.disconnect();
      assert.equal(await register(peer), clientId);
      assert.equal(peer.connectCount, 2);
      if (name.includes('C2000')) {
        await assert.rejects(peer.connection.sendCommand(fromHex('4101'), fromHex('a10121a2020100')),
          /telemetry subscription/);
      }
      await delay(100);
      assert.equal(peer.writes.length, 0, 'No duplicate listeners or unexpected setting writes');
      peer.device.gatt.disconnect();
      await peer.connection.disconnect();
      await delay(1100);
      assert.equal(peer.connectCount, 2, 'Manual disconnect cancels the queued reconnect');
    } finally {
      await peer.close();
    }
  });
}

test('C1000 legacy firmware retains legacy negotiation on reconnect', async () => {
  const peer = fixture('SOLIX C1000 Gen 2');
  try {
    await peer.connection.connect(false, null, 'legacy');
    await takeWrite(peer, '0001');
    peer.device.gatt.disconnect();
    await takeWrite(peer, '0001');
    assert.equal(peer.connectCount, 2);
  } finally {
    await peer.close();
  }
});
