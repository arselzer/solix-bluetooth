<script setup lang="ts">
import type { ConnectionState } from '../protocol';

defineProps<{
  state: ConnectionState;
  deviceName: string | null;
}>();

defineEmits<{
  connect: [];
  disconnect: [];
  clear: [];
}>();

const stateLabels: Record<ConnectionState, string> = {
  disconnected: 'Disconnected',
  connecting: 'Connecting...',
  negotiating: 'Negotiating encryption...',
  connected: 'Connected',
};

const stateColors: Record<ConnectionState, string> = {
  disconnected: '#888',
  connecting: '#f0ad4e',
  negotiating: '#f0ad4e',
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
    <div class="actions">
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
  padding: 16px;
  background: #1e1e2e;
  border-radius: 8px;
  border: 1px solid #333;
}

.actions {
  display: flex;
  gap: 12px;
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
