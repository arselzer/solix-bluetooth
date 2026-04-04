<script setup lang="ts">
import { ref, watch, nextTick } from 'vue';
import type { LogEntry } from '../protocol';

const props = defineProps<{
  entries: LogEntry[];
}>();

const container = ref<HTMLElement | null>(null);
const autoScroll = ref(true);
const copyStatus = ref('');

watch(() => props.entries.length, async () => {
  if (autoScroll.value) {
    await nextTick();
    if (container.value) {
      container.value.scrollTop = container.value.scrollHeight;
    }
  }
});

const directionIcons: Record<string, string> = {
  tx: '>>',
  rx: '<<',
  info: '--',
  error: '!!',
};

const directionColors: Record<string, string> = {
  tx: '#3b82f6',
  rx: '#5cb85c',
  info: '#888',
  error: '#ef4444',
};

function formatTime(ts: number): string {
  const d = new Date(ts);
  return d.toLocaleTimeString([], { hour12: false }) + '.' + d.getMilliseconds().toString().padStart(3, '0');
}

function formatLogText(): string {
  return props.entries.map(e => {
    const time = formatTime(e.timestamp);
    const dir = directionIcons[e.direction];
    const data = e.data ? ' ' + e.data : '';
    return `${time} ${dir} ${e.message}${data}`;
  }).join('\n');
}

async function copyLog() {
  try {
    await navigator.clipboard.writeText(formatLogText());
    copyStatus.value = 'Copied!';
    setTimeout(() => copyStatus.value = '', 2000);
  } catch {
    // Fallback for non-HTTPS
    const ta = document.createElement('textarea');
    ta.value = formatLogText();
    document.body.appendChild(ta);
    ta.select();
    document.execCommand('copy');
    document.body.removeChild(ta);
    copyStatus.value = 'Copied!';
    setTimeout(() => copyStatus.value = '', 2000);
  }
}
</script>

<template>
  <div class="log-viewer">
    <div class="log-header">
      <h3>Protocol Log</h3>
      <div class="controls">
        <span v-if="copyStatus" class="copy-status">{{ copyStatus }}</span>
        <button class="copy-btn" @click="copyLog">Copy All</button>
        <label class="auto-scroll">
          <input type="checkbox" v-model="autoScroll" />
          Auto-scroll
        </label>
      </div>
    </div>
    <div class="log-container" ref="container">
      <div
        v-for="(entry, i) in entries"
        :key="i"
        class="log-entry"
        :style="{ color: directionColors[entry.direction] }"
      >
        <span class="time">{{ formatTime(entry.timestamp) }}</span>
        <span class="dir">{{ directionIcons[entry.direction] }}</span>
        <span class="msg">{{ entry.message }}</span>
        <span v-if="entry.data" class="data">{{ entry.data }}</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.log-viewer {
  background: #1e1e2e;
  border-radius: 8px;
  border: 1px solid #333;
  overflow: hidden;
}

.log-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 16px;
  border-bottom: 1px solid #333;
}

.log-header h3 {
  margin: 0;
  color: #e0e0e0;
  font-size: 1.1em;
}

.controls {
  display: flex;
  align-items: center;
  gap: 12px;
}

.copy-btn {
  padding: 4px 12px;
  background: #2a2a3e;
  border: 1px solid #444;
  border-radius: 4px;
  color: #e0e0e0;
  cursor: pointer;
  font-size: 0.8em;
}

.copy-btn:hover {
  background: #3b82f6;
  border-color: #3b82f6;
}

.copy-status {
  color: #5cb85c;
  font-size: 0.8em;
}

.auto-scroll {
  color: #888;
  font-size: 0.85em;
  cursor: pointer;
  user-select: none;
  display: flex;
  align-items: center;
  gap: 4px;
}

.log-container {
  height: 400px;
  overflow-y: auto;
  padding: 8px;
  font-family: monospace;
  font-size: 0.8em;
  line-height: 1.5;
}

.log-entry {
  display: flex;
  gap: 8px;
  white-space: nowrap;
}

.time {
  color: #555;
  flex-shrink: 0;
}

.dir {
  flex-shrink: 0;
  font-weight: bold;
}

.msg {
  flex-shrink: 0;
}

.data {
  color: #555;
  overflow: hidden;
  text-overflow: ellipsis;
}
</style>
