<script setup lang="ts">
import { computed, ref, reactive, onMounted, onUnmounted } from 'vue';
import { SolixConnection } from './protocol';
import type { ConnectionState, TelemetryData, LogEntry } from './protocol';
import ConnectionPanel from './components/ConnectionPanel.vue';
import TelemetryDisplay from './components/TelemetryDisplay.vue';
import LogViewer from './components/LogViewer.vue';
import RawPackets from './components/RawPackets.vue';
import CommandPanel from './components/CommandPanel.vue';
import CommandScanner from './components/CommandScanner.vue';

const connectionState = ref<ConnectionState>('disconnected');
const deviceName = ref<string | null>(null);
const telemetry = reactive<TelemetryData>({});
const logEntries = ref<LogEntry[]>([]);
const connectionError = ref<string | null>(null);
const rawPackets = ref<{ timestamp: number; direction: 'tx' | 'rx'; data: Uint8Array }[]>([]);
const activeTab = ref<'telemetry' | 'commands' | 'scanner' | 'log' | 'raw'>('telemetry');
const bleSupported = ref(!!navigator.bluetooth);
const ownerUserId = ref('');
const c1000Protocol = ref<'prime' | 'legacy'>('prime');
const isGen2 = computed(() => /C1000.*Gen 2|C2000.*Gen 2|A1763|A1783/i.test(deviceName.value ?? ''));

// Wake Lock to prevent browser from suspending when backgrounded
let wakeLock: WakeLockSentinel | null = null;

async function requestWakeLock() {
  try {
    if ('wakeLock' in navigator) {
      wakeLock = await (navigator as any).wakeLock.request('screen');
      wakeLock?.addEventListener('release', () => {
        // Re-acquire on release (e.g., after screen off/on)
        if (connectionState.value !== 'disconnected') {
          requestWakeLock();
        }
      });
    }
  } catch { /* Wake Lock not supported or denied */ }
}

function releaseWakeLock() {
  wakeLock?.release();
  wakeLock = null;
}

// Re-acquire wake lock when page becomes visible again
function handleVisibilityChange() {
  if (document.visibilityState === 'visible' && connectionState.value !== 'disconnected') {
    requestWakeLock();
  }
}

// Persist logs to sessionStorage periodically
function saveLogsToStorage() {
  try {
    const logsToSave = logEntries.value.slice(-200);
    sessionStorage.setItem('solix_logs', JSON.stringify(logsToSave));
    sessionStorage.setItem('solix_telemetry', JSON.stringify(telemetry));
    sessionStorage.setItem('solix_tab', activeTab.value);
    sessionStorage.setItem('solix_device', deviceName.value || '');
  } catch { /* Storage full or unavailable */ }
}

function restoreLogsFromStorage() {
  try {
    const savedLogs = sessionStorage.getItem('solix_logs');
    if (savedLogs) {
      const parsed = JSON.parse(savedLogs) as LogEntry[];
      if (parsed.length > 0) {
        logEntries.value = [
          { timestamp: Date.now(), direction: 'info', message: `--- Restored ${parsed.length} log entries from previous session ---` },
          ...parsed,
        ];
      }
    }
    const savedTelemetry = sessionStorage.getItem('solix_telemetry');
    if (savedTelemetry) {
      Object.assign(telemetry, JSON.parse(savedTelemetry));
    }
    const savedTab = sessionStorage.getItem('solix_tab');
    if (savedTab) {
      activeTab.value = savedTab as any;
    }
    const savedDevice = sessionStorage.getItem('solix_device');
    if (savedDevice) {
      deviceName.value = savedDevice || null;
    }
    if (isGen2.value || 'battery_health_raw' in telemetry) delete telemetry.battery_health;
  } catch { /* Parse error */ }
}

let saveInterval: ReturnType<typeof setInterval> | null = null;

onMounted(() => {
  restoreLogsFromStorage();
  document.addEventListener('visibilitychange', handleVisibilityChange);
  // Save logs every 3 seconds
  saveInterval = setInterval(saveLogsToStorage, 3000);
});

onUnmounted(() => {
  releaseWakeLock();
  document.removeEventListener('visibilitychange', handleVisibilityChange);
  if (saveInterval) clearInterval(saveInterval);
  saveLogsToStorage();
});

let connection: SolixConnection | null = null;

function createConnection(): SolixConnection {
  return new SolixConnection({
    onStateChange(state) {
      connectionState.value = state;
      if (connection) {
        deviceName.value = connection.deviceName;
      }
      if (isGen2.value && activeTab.value === 'scanner') activeTab.value = 'telemetry';
    },
    onTelemetry(data) {
      if ('battery_health_raw' in data) delete telemetry.battery_health;
      Object.assign(telemetry, data);
    },
    onLog(entry) {
      logEntries.value.push(entry);
      if (entry.direction === 'error') connectionError.value = entry.message;
      if (logEntries.value.length > 500) {
        logEntries.value = logEntries.value.slice(-400);
      }
    },
    onRawPacket(direction, data) {
      rawPackets.value.push({ timestamp: Date.now(), direction, data });
      if (rawPackets.value.length > 1000) {
        rawPackets.value = rawPackets.value.slice(-800);
      }
    },
  });
}

async function handleConnect(showAllDevices = false) {
  connectionError.value = null;
  connection = createConnection();
  try {
    await connection.connect(showAllDevices, ownerUserId.value.trim() || null, c1000Protocol.value);
    await requestWakeLock();
  } catch (e) {
    console.error('Connection failed:', e);
  }
}

async function handleDisconnect() {
  if (connection) {
    await connection.disconnect();
    connection = null;
  }
  releaseWakeLock();
}

async function handleConfirmPairing() {
  try {
    await connection?.confirmPairing();
  } catch (error) {
    connectionError.value = String(error);
  }
}

async function handleCommand(commandCode: Uint8Array, payload: Uint8Array) {
  if (connection) {
    await connection.sendCommand(commandCode, payload);
  }
}

function clearState() {
  logEntries.value = [];
  rawPackets.value = [];
  connectionError.value = null;
  Object.keys(telemetry).forEach(k => delete telemetry[k]);
  deviceName.value = null;
  sessionStorage.removeItem('solix_logs');
  sessionStorage.removeItem('solix_telemetry');
  sessionStorage.removeItem('solix_device');
}

async function handleChargeLimits(upper: number, lower: number) {
  if (!connection) return;
  try {
    await connection.setChargeLimits(upper, lower);
  } catch (error) {
    connectionError.value = String(error);
  }
}

async function handleChargePower(watts: number) {
  if (!connection) return;
  try {
    await connection.setAcChargingPower(watts);
  } catch (error) {
    connectionError.value = String(error);
  }
}
</script>

<template>
  <div class="app">
    <header>
      <h1>Anker Solix Bluetooth</h1>
      <p class="subtitle">Local BLE monitoring for Anker Solix devices</p>
    </header>

    <div v-if="!bleSupported" class="warning">
      Web Bluetooth is not supported in this browser. Use Chrome or Edge.
    </div>

    <main v-else>
      <ConnectionPanel
        :state="connectionState"
        :device-name="deviceName"
        v-model:owner-user-id="ownerUserId"
        v-model:c1000-protocol="c1000Protocol"
        @connect="handleConnect"
        @connect-any="handleConnect(true)"
        @confirm-pairing="handleConfirmPairing"
        @disconnect="handleDisconnect"
        @clear="clearState"
      />
      <div v-if="connectionError" class="connection-error">{{ connectionError }}</div>

      <div class="tabs">
        <button
          :class="{ active: activeTab === 'telemetry' }"
          @click="activeTab = 'telemetry'"
        >
          Telemetry
        </button>
        <button
          :class="{ active: activeTab === 'commands' }"
          @click="activeTab = 'commands'"
        >
          Commands
        </button>
        <button
          v-if="!isGen2"
          :class="{ active: activeTab === 'scanner' }"
          @click="activeTab = 'scanner'"
        >
          Scanner
        </button>
        <button
          :class="{ active: activeTab === 'log' }"
          @click="activeTab = 'log'"
        >
          Log ({{ logEntries.length }})
        </button>
        <button
          :class="{ active: activeTab === 'raw' }"
          @click="activeTab = 'raw'"
        >
          Raw ({{ rawPackets.length }})
        </button>
      </div>

      <TelemetryDisplay
        v-if="activeTab === 'telemetry'"
        :data="telemetry"
      />

      <CommandPanel
        v-if="activeTab === 'commands'"
        :device-name="deviceName"
        :connected="connectionState === 'connected'"
        :c1000-protocol="c1000Protocol"
        :telemetry="telemetry"
        @command="handleCommand"
        @limits="handleChargeLimits"
        @charge-power="handleChargePower"
      />

      <CommandScanner
        v-if="activeTab === 'scanner' && !isGen2"
        :connected="connectionState === 'connected'"
        @command="handleCommand"
      />

      <LogViewer
        v-if="activeTab === 'log'"
        :entries="logEntries"
      />

      <RawPackets
        v-if="activeTab === 'raw'"
        :packets="rawPackets"
      />
    </main>
  </div>
</template>

<style scoped>
.app {
  max-width: 900px;
  margin: 0 auto;
  padding: 20px;
}

header {
  text-align: center;
  margin-bottom: 24px;
}

h1 {
  margin: 0;
  color: #e0e0e0;
  font-size: 1.6em;
}

.subtitle {
  margin: 4px 0 0;
  color: #666;
  font-size: 0.9em;
}

main {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.warning {
  text-align: center;
  padding: 40px;
  color: #ef4444;
  background: #1e1e2e;
  border-radius: 8px;
  border: 1px solid #ef4444;
}

.connection-error {
  padding: 10px 14px;
  color: #ffb4b4;
  background: #3a2028;
  border: 1px solid #8c3947;
  border-radius: 6px;
}

.tabs {
  display: flex;
  gap: 4px;
}

.tabs button {
  padding: 8px 16px;
  background: #2a2a3e;
  border: 1px solid #333;
  border-radius: 6px 6px 0 0;
  color: #888;
  cursor: pointer;
  font-size: 0.9em;
}

.tabs button.active {
  background: #1e1e2e;
  color: #e0e0e0;
  border-bottom-color: #1e1e2e;
}

.tabs button:hover:not(.active) {
  background: #333;
}
</style>
