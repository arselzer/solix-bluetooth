<script setup lang="ts">
import { computed, ref } from 'vue';
import type { TelemetryData } from '../protocol';
import { PARAM_LABELS, PARAM_GROUPS } from '../protocol';

const props = defineProps<{
  data: TelemetryData;
}>();

const csvHistory = ref<{ timestamp: number; data: Record<string, string | number> }[]>([]);
const recording = ref(false);
let recordInterval: ReturnType<typeof setInterval> | null = null;

function startRecording() {
  recording.value = true;
  csvHistory.value = [];
  recordInterval = setInterval(() => {
    if (Object.keys(props.data).length > 0) {
      csvHistory.value.push({ timestamp: Date.now(), data: { ...props.data } });
    }
  }, 10000);
}

function stopRecording() {
  recording.value = false;
  if (recordInterval) {
    clearInterval(recordInterval);
    recordInterval = null;
  }
}

function exportCsv() {
  if (csvHistory.value.length === 0) {
    // Export single snapshot
    const keys = Object.keys(props.data);
    const header = 'timestamp,' + keys.join(',');
    const row = new Date().toISOString() + ',' + keys.map(k => props.data[k]).join(',');
    downloadCsv(header + '\n' + row, 'solix-snapshot.csv');
    return;
  }

  // Export recorded history
  const allKeys = new Set<string>();
  csvHistory.value.forEach(entry => Object.keys(entry.data).forEach(k => allKeys.add(k)));
  const keys = Array.from(allKeys).sort();
  const header = 'timestamp,' + keys.join(',');
  const rows = csvHistory.value.map(entry => {
    const ts = new Date(entry.timestamp).toISOString();
    return ts + ',' + keys.map(k => entry.data[k] ?? '').join(',');
  });
  downloadCsv(header + '\n' + rows.join('\n'), 'solix-telemetry.csv');
}

function downloadCsv(content: string, filename: string) {
  const blob = new Blob([content], { type: 'text/csv' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

interface DisplayItem {
  key: string;
  label: string;
  value: string | number;
}

interface DisplayGroup {
  name: string;
  items: DisplayItem[];
}

const groups = computed<DisplayGroup[]>(() => {
  const result: DisplayGroup[] = [];
  const usedKeys = new Set<string>();

  for (const [groupName, keys] of Object.entries(PARAM_GROUPS)) {
    const items: DisplayItem[] = [];
    for (const key of keys) {
      if (key in props.data) {
        items.push({
          key,
          label: PARAM_LABELS[key] || key,
          value: props.data[key],
        });
        usedKeys.add(key);
      }
    }
    if (items.length > 0) {
      result.push({ name: groupName, items });
    }
  }

  // Ungrouped known params
  const ungrouped: DisplayItem[] = [];
  for (const [key, value] of Object.entries(props.data)) {
    if (!usedKeys.has(key) && !key.startsWith('unknown_') && !key.startsWith('raw_')) {
      ungrouped.push({ key, label: PARAM_LABELS[key] || key, value });
      usedKeys.add(key);
    }
  }
  if (ungrouped.length > 0) {
    result.push({ name: 'Other', items: ungrouped });
  }

  return result;
});

const unknownItems = computed(() => {
  return Object.entries(props.data)
    .filter(([key]) => key.startsWith('unknown_') || key.startsWith('raw_'))
    .map(([key, value]) => ({ key, value }));
});

const hasData = computed(() => Object.keys(props.data).length > 0);

function formatValue(value: string | number): string {
  if (typeof value === 'number') {
    return Number.isInteger(value) ? value.toString() : value.toFixed(1);
  }
  return value;
}
</script>

<template>
  <div class="telemetry">
    <div v-if="hasData" class="export-bar">
      <button class="export-btn" @click="exportCsv">
        {{ csvHistory.length > 0 ? `Export CSV (${csvHistory.length} samples)` : 'Export Snapshot' }}
      </button>
      <button v-if="!recording" class="export-btn record" @click="startRecording">
        Record (10s interval)
      </button>
      <button v-else class="export-btn stop" @click="stopRecording">
        Stop Recording ({{ csvHistory.length }} samples)
      </button>
    </div>

    <div v-if="!hasData" class="empty">
      Waiting for data...
    </div>

    <div v-for="group in groups" :key="group.name" class="group">
      <h4>{{ group.name }}</h4>
      <div class="grid">
        <div v-for="item in group.items" :key="item.key" class="item">
          <div class="item-label">{{ item.label }}</div>
          <div class="item-value">{{ formatValue(item.value) }}</div>
        </div>
      </div>
    </div>

    <div v-if="unknownItems.length > 0" class="group unknown-section">
      <h4>Unknown Parameters</h4>
      <div class="unknown-grid">
        <div v-for="item in unknownItems" :key="item.key" class="unknown-item">
          <span class="unknown-key">{{ item.key }}:</span>
          <span class="unknown-value">{{ item.value }}</span>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.telemetry {
  padding: 16px;
  background: #1e1e2e;
  border-radius: 8px;
  border: 1px solid #333;
}

.export-bar {
  display: flex;
  gap: 8px;
  margin-bottom: 12px;
}

.export-btn {
  padding: 6px 14px;
  background: #2a2a3e;
  border: 1px solid #444;
  border-radius: 4px;
  color: #e0e0e0;
  cursor: pointer;
  font-size: 0.8em;
}

.export-btn:hover { background: #3b82f6; border-color: #3b82f6; }
.export-btn.record { border-color: #ef4444; color: #ef4444; }
.export-btn.record:hover { background: #ef4444; color: white; }
.export-btn.stop { background: #ef4444; color: white; border-color: #ef4444; }

.group {
  margin-bottom: 16px;
}

.group:last-child {
  margin-bottom: 0;
}

h4 {
  margin: 0 0 8px 0;
  color: #888;
  font-size: 0.85em;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.empty {
  color: #666;
  font-style: italic;
  padding: 20px;
  text-align: center;
}

.grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
  gap: 8px;
}

.item {
  padding: 10px;
  background: #2a2a3e;
  border-radius: 6px;
}

.item-label {
  font-size: 0.75em;
  color: #888;
  margin-bottom: 4px;
}

.item-value {
  font-size: 1.2em;
  font-weight: 600;
  color: #3b82f6;
  font-family: monospace;
}

.unknown-section {
  border-top: 1px solid #333;
  padding-top: 12px;
}

.unknown-grid {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.unknown-item {
  font-family: monospace;
  font-size: 0.8em;
  color: #666;
}

.unknown-key {
  color: #888;
}

.unknown-value {
  color: #555;
  word-break: break-all;
}
</style>
