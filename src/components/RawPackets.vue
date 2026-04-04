<script setup lang="ts">
import { ref, watch, nextTick } from 'vue';
import { toHex } from '../protocol';

interface RawPacket {
  timestamp: number;
  direction: 'tx' | 'rx';
  data: Uint8Array;
}

const props = defineProps<{
  packets: RawPacket[];
}>();

const container = ref<HTMLElement | null>(null);
const autoScroll = ref(true);

watch(() => props.packets.length, async () => {
  if (autoScroll.value) {
    await nextTick();
    if (container.value) {
      container.value.scrollTop = container.value.scrollHeight;
    }
  }
});

function formatTime(ts: number): string {
  const d = new Date(ts);
  return d.toLocaleTimeString([], { hour12: false }) + '.' + d.getMilliseconds().toString().padStart(3, '0');
}

function formatHex(data: Uint8Array): string {
  return toHex(data);
}
</script>

<template>
  <div class="raw-packets">
    <div class="raw-header">
      <h3>Raw Packets</h3>
      <div class="controls">
        <span class="count">{{ packets.length }} packets</span>
        <label class="auto-scroll">
          <input type="checkbox" v-model="autoScroll" />
          Auto-scroll
        </label>
      </div>
    </div>
    <div class="raw-container" ref="container">
      <div
        v-for="(pkt, i) in packets"
        :key="i"
        class="raw-entry"
        :class="pkt.direction"
      >
        <span class="time">{{ formatTime(pkt.timestamp) }}</span>
        <span class="dir">{{ pkt.direction === 'tx' ? 'TX' : 'RX' }}</span>
        <span class="size">{{ pkt.data.length }}B</span>
        <span class="hex">{{ formatHex(pkt.data) }}</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.raw-packets {
  background: #1e1e2e;
  border-radius: 8px;
  border: 1px solid #333;
  overflow: hidden;
}

.raw-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 16px;
  border-bottom: 1px solid #333;
}

.raw-header h3 {
  margin: 0;
  color: #e0e0e0;
  font-size: 1.1em;
}

.controls {
  display: flex;
  align-items: center;
  gap: 12px;
}

.count {
  color: #666;
  font-size: 0.85em;
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

.raw-container {
  height: 250px;
  overflow-y: auto;
  padding: 8px;
  font-family: monospace;
  font-size: 0.75em;
  line-height: 1.6;
}

.raw-entry {
  display: flex;
  gap: 8px;
}

.raw-entry.tx {
  color: #3b82f6;
}

.raw-entry.rx {
  color: #5cb85c;
}

.time {
  color: #555;
  flex-shrink: 0;
}

.dir {
  flex-shrink: 0;
  font-weight: bold;
  width: 20px;
}

.size {
  flex-shrink: 0;
  color: #666;
  width: 40px;
  text-align: right;
}

.hex {
  word-break: break-all;
  color: inherit;
  opacity: 0.8;
}
</style>
