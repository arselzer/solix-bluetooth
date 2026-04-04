<script setup lang="ts">
import { ref, computed } from 'vue';
import { fromHex, toHex } from '../protocol';

const props = defineProps<{
  connected: boolean;
}>();

const emit = defineEmits<{
  command: [commandCode: Uint8Array, payload: Uint8Array];
}>();

const scanning = ref(false);
const firstByte = ref('40');
const scanFrom = ref('40');
const scanTo = ref('60');
const scanPayload = ref('a10121');
const scanDelay = ref(500);
const scanResults = ref<{ cmd: string; sent: boolean; responses: string[] }[]>([]);
const currentCmd = ref('');
const scanProgress = ref(0);
const scanTotal = ref(0);

async function startScan() {
  const from = parseInt(scanFrom.value, 16);
  const to = parseInt(scanTo.value, 16);
  if (isNaN(from) || isNaN(to) || from > to) return;

  scanning.value = true;
  scanResults.value = [];
  scanTotal.value = to - from + 1;
  scanProgress.value = 0;

  const payload = scanPayload.value ? fromHex(scanPayload.value) : new Uint8Array(0);
  const fb = firstByte.value;

  for (let cmd2 = from; cmd2 <= to; cmd2++) {
    if (!scanning.value) break;

    const cmdHex = fb + cmd2.toString(16).padStart(2, '0');
    currentCmd.value = cmdHex;
    scanProgress.value = cmd2 - from + 1;

    const result = { cmd: cmdHex, sent: true, responses: [] as string[] };
    scanResults.value.push(result);

    emit('command', fromHex(cmdHex), payload);

    await new Promise(r => setTimeout(r, scanDelay.value));
  }

  currentCmd.value = '';
  scanning.value = false;
}

function stopScan() {
  scanning.value = false;
}
</script>

<template>
  <div class="scanner">
    <h3>Command Scanner</h3>
    <p class="description">
      Sends commands 0x40XX for a range of XX values and logs responses.
      Watch the Protocol Log tab to see which commands get responses.
    </p>

    <div class="config">
      <div class="field">
        <label>First byte (hex)</label>
        <input v-model="firstByte" placeholder="40" :disabled="scanning" />
      </div>
      <div class="field">
        <label>Second byte from</label>
        <input v-model="scanFrom" placeholder="40" :disabled="scanning" />
      </div>
      <div class="field">
        <label>Second byte to</label>
        <input v-model="scanTo" placeholder="ff" :disabled="scanning" />
      </div>
      <div class="field">
        <label>Payload (hex)</label>
        <input v-model="scanPayload" placeholder="a10121" :disabled="scanning" class="wide" />
      </div>
      <div class="field">
        <label>Delay (ms)</label>
        <input v-model.number="scanDelay" type="number" :disabled="scanning" />
      </div>
    </div>

    <div class="actions">
      <button
        v-if="!scanning"
        class="btn start"
        :disabled="!connected"
        @click="startScan"
      >
        Scan 0x{{ firstByte }}{{ scanFrom }} → 0x{{ firstByte }}{{ scanTo }}
      </button>
      <button v-else class="btn stop" @click="stopScan">
        Stop ({{ scanProgress }}/{{ scanTotal }} — 0x{{ currentCmd }})
      </button>
    </div>

    <div v-if="scanResults.length > 0" class="results">
      <div class="result-summary">
        Scanned {{ scanResults.length }} commands.
        Check the Log tab for detailed responses.
      </div>
      <div class="commands-sent">
        <span v-for="r in scanResults" :key="r.cmd" class="cmd-badge">
          {{ r.cmd }}
        </span>
      </div>
    </div>

    <div class="presets">
      <h4>Preset Ranges</h4>
      <div class="preset-grid">
        <button class="preset" @click="scanFrom = '40'; scanTo = '5f'" :disabled="scanning">
          0x40-0x5F (control)
        </button>
        <button class="preset" @click="scanFrom = '60'; scanTo = '7f'" :disabled="scanning">
          0x60-0x7F
        </button>
        <button class="preset" @click="scanFrom = '80'; scanTo = '9f'" :disabled="scanning">
          0x80-0x9F
        </button>
        <button class="preset" @click="scanFrom = 'a0'; scanTo = 'bf'" :disabled="scanning">
          0xA0-0xBF
        </button>
        <button class="preset" @click="scanFrom = 'c0'; scanTo = 'df'" :disabled="scanning">
          0xC0-0xDF
        </button>
        <button class="preset" @click="scanFrom = 'e0'; scanTo = 'ff'" :disabled="scanning">
          0xE0-0xFF
        </button>
        <button class="preset full" @click="scanFrom = '00'; scanTo = 'ff'" :disabled="scanning">
          Full 0x00-0xFF (256 cmds)
        </button>
      </div>
    </div>

    <div class="presets">
      <h4>Payload Presets</h4>
      <div class="preset-grid">
        <button class="preset" @click="scanPayload = 'a10121'" :disabled="scanning">
          a10121 (status)
        </button>
        <button class="preset" @click="scanPayload = 'a10121a2020101'" :disabled="scanning">
          ...a2020101 (ON)
        </button>
        <button class="preset" @click="scanPayload = 'a10121a2020100'" :disabled="scanning">
          ...a2020100 (OFF)
        </button>
        <button class="preset" @click="scanPayload = ''" :disabled="scanning">
          empty
        </button>
      </div>
    </div>

    <div class="presets">
      <h4>First Byte Prefix</h4>
      <div class="preset-grid">
        <button class="preset" @click="firstByte = '40'" :disabled="scanning">
          0x40 (default)
        </button>
        <button class="preset" @click="firstByte = 'c4'" :disabled="scanning">
          0xC4 (telemetry)
        </button>
        <button class="preset" @click="firstByte = '44'" :disabled="scanning">
          0x44 (single)
        </button>
        <button class="preset" @click="firstByte = '48'" :disabled="scanning">
          0x48 (response)
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.scanner {
  padding: 16px;
  background: #1e1e2e;
  border-radius: 8px;
  border: 1px solid #333;
}

h3 {
  margin: 0 0 4px 0;
  color: #e0e0e0;
  font-size: 1.1em;
}

h4 {
  margin: 12px 0 8px 0;
  color: #888;
  font-size: 0.85em;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.description {
  color: #888;
  font-size: 0.85em;
  margin: 0 0 12px 0;
}

.config {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 12px;
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
  width: 80px;
}

.field input.wide {
  width: 140px;
}

.field input:focus {
  outline: none;
  border-color: #3b82f6;
}

.field input:disabled {
  opacity: 0.5;
}

.actions {
  margin-bottom: 12px;
}

.btn {
  padding: 8px 20px;
  border: none;
  border-radius: 6px;
  font-weight: 600;
  cursor: pointer;
  font-size: 0.95em;
}

.start {
  background: #f59e0b;
  color: #000;
}

.start:hover:not(:disabled) {
  background: #d97706;
}

.start:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.stop {
  background: #ef4444;
  color: white;
}

.stop:hover {
  background: #dc2626;
}

.results {
  margin-bottom: 12px;
}

.result-summary {
  color: #888;
  font-size: 0.85em;
  margin-bottom: 8px;
}

.commands-sent {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}

.cmd-badge {
  font-family: monospace;
  font-size: 0.75em;
  padding: 2px 6px;
  background: #2a2a3e;
  border-radius: 3px;
  color: #888;
}

.presets {
  border-top: 1px solid #333;
  padding-top: 8px;
}

.preset-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.preset {
  padding: 6px 12px;
  background: #2a2a3e;
  border: 1px solid #444;
  border-radius: 4px;
  color: #e0e0e0;
  cursor: pointer;
  font-size: 0.8em;
  font-family: monospace;
}

.preset:hover:not(:disabled) {
  background: #333;
}

.preset.full {
  border-color: #f59e0b;
  color: #f59e0b;
}

.preset:disabled {
  opacity: 0.5;
}
</style>
