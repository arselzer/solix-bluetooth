<script setup lang="ts">
import type { ConnectionState } from '../protocol';

defineProps<{
  state: ConnectionState;
  deviceName: string | null;
  ownerUserId: string;
  c1000Protocol: 'prime' | 'legacy';
}>();

defineEmits<{
  connect: [];
  connectAny: [];
  confirmPairing: [];
  disconnect: [];
  clear: [];
  'update:ownerUserId': [value: string];
  'update:c1000Protocol': [value: 'prime' | 'legacy'];
}>();

const stateLabels: Record<ConnectionState, string> = {
  disconnected: 'Disconnected',
  connecting: 'Connecting...',
  negotiating: 'Negotiating encryption...',
  pairing: 'Waiting for main button press',
  connected: 'Connected',
};

const stateColors: Record<ConnectionState, string> = {
  disconnected: '#888',
  connecting: '#f0ad4e',
  negotiating: '#f0ad4e',
  pairing: '#f0ad4e',
  connected: '#5cb85c',
};
</script>

<template>
  <div class="connection-panel">
    <div class="status">
      <span class="dot" :style="{ backgroundColor: stateColors[state] }"></span>
      <span class="label">{{ stateLabels[state] }}</span>
      <span v-if="deviceName" class="device-name">{{ deviceName }}</span>
    </div>
    <div v-if="state === 'disconnected'" class="owner-field">
      <label for="owner-id">Gen 2 client ID (optional)</label>
      <input id="owner-id" :value="ownerUserId" maxlength="40" autocomplete="off"
        placeholder="40 hex characters"
        @input="$emit('update:ownerUserId', ($event.target as HTMLInputElement).value)" />
    </div>
    <div v-if="state === 'disconnected'" class="owner-field">
      <label for="c1000-protocol">C1000 Gen 2 firmware</label>
      <select id="c1000-protocol" :value="c1000Protocol"
        @change="$emit('update:c1000Protocol', ($event.target as HTMLSelectElement).value as 'prime' | 'legacy')">
        <option value="prime">1.1.4.9 or newer (Prime)</option>
        <option value="legacy">1.1.4.3 (Legacy)</option>
      </select>
    </div>
    <div v-if="state === 'pairing'" class="pairing-tip">
      Press the station's main power button once, then click below. Do not hold it or press AC output.
    </div>
    <div class="actions">
      <button v-if="state === 'pairing'" class="btn connect" @click="$emit('confirmPairing')">
        I pressed the main button
      </button>
      <button
        v-if="state === 'disconnected'"
        class="btn clear"
        @click="$emit('clear')"
      >
        Clear
      </button>
      <button
        v-if="state === 'disconnected'"
        class="btn connect"
        @click="$emit('connect')"
      >
        Connect
      </button>
      <button
        v-if="state === 'disconnected'"
        class="btn"
        title="Choose from all nearby Bluetooth devices if your Anker device is missing"
        @click="$emit('connectAny')"
      >
        Show all devices
      </button>
      <button
        v-else
        class="btn disconnect"
        @click="$emit('disconnect')"
        :disabled="state === 'connecting'"
      >
        Disconnect
      </button>
    </div>
  </div>
</template>

<style scoped>
.connection-panel {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 12px;
  padding: 16px;
  background: #1e1e2e;
  border-radius: 8px;
  border: 1px solid #333;
}

.pairing-tip {
  max-width: 370px;
  color: #f0c674;
  font-size: 0.9em;
}

.status {
  display: flex;
  align-items: center;
  gap: 8px;
}

.dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  display: inline-block;
}

.label {
  font-weight: 600;
  color: #e0e0e0;
}

.device-name {
  color: #888;
  font-size: 0.9em;
}

.btn {
  padding: 8px 20px;
  border: none;
  border-radius: 6px;
  font-weight: 600;
  cursor: pointer;
  font-size: 0.95em;
}

.clear {
  background: transparent;
  color: #888;
  border: 1px solid #444;
}

.clear:hover {
  background: #333;
  color: #e0e0e0;
}

.connect {
  background: #3b82f6;
  color: white;
}

.connect:hover {
  background: #2563eb;
}

.actions {
  display: flex;
  gap: 12px;
}

.owner-field {
  display: flex;
  flex-direction: column;
  gap: 4px;
  color: #999;
  font-size: 0.75em;
}

.owner-field input, .owner-field select {
  width: 190px;
  padding: 7px;
  color: #eee;
  background: #2a2a3e;
  border: 1px solid #444;
  border-radius: 5px;
}

.disconnect {
  background: #ef4444;
  color: white;
}

.disconnect:hover {
  background: #dc2626;
}

.btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
</style>
