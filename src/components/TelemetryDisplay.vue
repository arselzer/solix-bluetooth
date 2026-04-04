<script setup lang="ts">
import { computed } from 'vue';
import type { TelemetryData } from '../protocol';
import { PARAM_LABELS } from '../protocol';

const props = defineProps<{
  data: TelemetryData;
}>();

interface DisplayItem {
  key: string;
  label: string;
  value: string | number;
  isUnknown: boolean;
}

const items = computed<DisplayItem[]>(() => {
  return Object.entries(props.data).map(([key, value]) => ({
    key,
    label: PARAM_LABELS[key] || key,
    value,
    isUnknown: key.startsWith('unknown_') || key.startsWith('raw_'),
  }));
});

const knownItems = computed(() => items.value.filter(i => !i.isUnknown));
const unknownItems = computed(() => items.value.filter(i => i.isUnknown));

function formatValue(value: string | number): string {
  if (typeof value === 'number') {
    return Number.isInteger(value) ? value.toString() : value.toFixed(1);
  }
  return value;
}
</script>

<template>
  <div class="telemetry">
    <h3>Telemetry Data</h3>

    <div v-if="knownItems.length === 0 && unknownItems.length === 0" class="empty">
      Waiting for data...
    </div>

    <div v-if="knownItems.length > 0" class="grid">
      <div v-for="item in knownItems" :key="item.key" class="item">
        <div class="item-label">{{ item.label }}</div>
        <div class="item-value">{{ formatValue(item.value) }}</div>
      </div>
    </div>

    <div v-if="unknownItems.length > 0" class="unknown-section">
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

h3 {
  margin: 0 0 12px 0;
  color: #e0e0e0;
  font-size: 1.1em;
}

h4 {
  margin: 12px 0 8px 0;
  color: #888;
  font-size: 0.9em;
}

.empty {
  color: #666;
  font-style: italic;
  padding: 20px;
  text-align: center;
}

.grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
  gap: 8px;
}

.item {
  padding: 10px;
  background: #2a2a3e;
  border-radius: 6px;
}

.item-label {
  font-size: 0.8em;
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
  margin-top: 12px;
  padding-top: 8px;
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
