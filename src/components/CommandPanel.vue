<script setup lang="ts">
import { ref } from 'vue';
import { fromHex } from '../protocol';

const props = defineProps<{
  deviceName: string | null;
  connected: boolean;
}>();

const emit = defineEmits<{
  command: [commandCode: Uint8Array, payload: Uint8Array];
}>();

const customCmd = ref('4040');
const customPayload = ref('a10121');

const isC1000 = props.deviceName?.includes('C1000') || props.deviceName?.includes('A17X');

function sendCommand(cmdHex: string, payloadHex: string) {
  emit('command', fromHex(cmdHex), fromHex(payloadHex));
}

function sendCustom() {
  try {
    sendCommand(customCmd.value, customPayload.value);
  } catch (e) {
    console.error('Invalid hex:', e);
  }
}

// C1000 known commands (confirmed via live testing)
const c1000Commands = [
  { label: 'Status Request', cmd: '4040', payload: 'a10121' },
  { label: 'AC On', cmd: '404a', payload: 'a10121a2020101' },
  { label: 'AC Off', cmd: '404a', payload: 'a10121a2020100' },
  { label: 'DC On', cmd: '404b', payload: 'a10121a2020101' },
  { label: 'DC Off', cmd: '404b', payload: 'a10121a2020100' },
  { label: 'Display On', cmd: '4052', payload: 'a10121a2020101' },
  { label: 'Display Off', cmd: '4052', payload: 'a10121a2020100' },
  { label: 'Light Off', cmd: '404f', payload: 'a10121a2020100' },
  { label: 'Light On', cmd: '404f', payload: 'a10121a2020101' },
  { label: 'Light Auto', cmd: '404f', payload: 'a10121a2020102' },
];

// General commands (work on all devices)
const generalCommands = [
  { label: 'Status Request', cmd: '4040', payload: 'a10121' },
  { label: 'Partial Status', cmd: '4041', payload: 'a10121' },
];
</script>

<template>
  <div class="commands">
    <h3>Commands</h3>

    <div v-if="!connected" class="disabled-notice">
      Connect to a device to send commands
    </div>

    <template v-else>
      <div class="command-group">
        <h4>Quick Commands</h4>
        <div class="button-grid">
          <template v-if="isC1000">
            <button
              v-for="cmd in c1000Commands"
              :key="cmd.label"
              class="cmd-btn"
              @click="sendCommand(cmd.cmd, cmd.payload)"
            >
              {{ cmd.label }}
            </button>
          </template>
          <template v-else>
            <button
              v-for="cmd in generalCommands"
              :key="cmd.label"
              class="cmd-btn"
              @click="sendCommand(cmd.cmd, cmd.payload)"
            >
              {{ cmd.label }}
            </button>
          </template>
        </div>
      </div>

      <div class="command-group">
        <h4>Custom Command</h4>
        <div class="custom-form">
          <div class="field">
            <label>Command (hex)</label>
            <input v-model="customCmd" placeholder="4040" />
          </div>
          <div class="field">
            <label>Payload (hex)</label>
            <input v-model="customPayload" placeholder="a10121" />
          </div>
          <button class="cmd-btn send-btn" @click="sendCustom">Send</button>
        </div>
      </div>

      <div class="warning">
        Commands are experimental. AC/DC toggles are only tested on C1000.
        Solarbank control commands have not been reverse-engineered.
      </div>
    </template>
  </div>
</template>

<style scoped>
.commands {
  padding: 16px;
  background: #1e1e2e;
  border-radius: 8px;
  border: 1px solid #333;
}

h3 {
  margin: 0 0 12px 0;
  color: #e0e0e0;
  font-size: 1.1em;
}

h4 {
  margin: 0 0 8px 0;
  color: #888;
  font-size: 0.85em;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.disabled-notice {
  color: #666;
  font-style: italic;
  padding: 20px;
  text-align: center;
}

.command-group {
  margin-bottom: 16px;
}

.button-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.cmd-btn {
  padding: 8px 16px;
  background: #2a2a3e;
  border: 1px solid #444;
  border-radius: 6px;
  color: #e0e0e0;
  cursor: pointer;
  font-size: 0.9em;
}

.cmd-btn:hover {
  background: #3b82f6;
  border-color: #3b82f6;
}

.cmd-btn:active {
  background: #2563eb;
}

.custom-form {
  display: flex;
  gap: 8px;
  align-items: flex-end;
}

.field {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.field label {
  font-size: 0.75em;
  color: #888;
}

.field input {
  padding: 6px 10px;
  background: #2a2a3e;
  border: 1px solid #444;
  border-radius: 4px;
  color: #e0e0e0;
  font-family: monospace;
  font-size: 0.9em;
  width: 140px;
}

.field input:focus {
  outline: none;
  border-color: #3b82f6;
}

.send-btn {
  flex-shrink: 0;
}

.warning {
  margin-top: 12px;
  padding: 8px 12px;
  background: rgba(245, 158, 11, 0.1);
  border: 1px solid rgba(245, 158, 11, 0.3);
  border-radius: 6px;
  color: #f59e0b;
  font-size: 0.8em;
}
</style>
