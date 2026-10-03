<script setup lang="ts">
import { computed, ref } from 'vue';
import type { Sample } from './types';

const props = withDefaults(defineProps<{ samples: Sample[]; now: number; kind: 'power' | 'battery';
  windowMs?: number; saved?: boolean }>(), { windowMs: 30 * 60 * 1000, saved: false });
const hovering = ref<number | null>(null);
const left = 45;
const top = 15;
const width = 570;
const height = 130;
const start = computed(() => props.now - props.windowMs);
const minutes = computed(() => props.windowMs / 60000);
const duration = computed(() => minutes.value >= 60 ? `${minutes.value / 60} hours` : `${minutes.value} minutes`);
const maximum = computed(() => props.kind === 'battery' ? 100
  : Math.max(250, Math.ceil(Math.max(...props.samples.map((sample) => Math.max(sample.input ?? 0, sample.output ?? 0)), 0) / 250) * 250));
const x = (time: number) => left + Math.max(0, Math.min(1, (time - start.value) / props.windowMs)) * width;
const y = (value: number) => top + height - Math.max(0, Math.min(1, value / maximum.value)) * height;

function line(key: 'input' | 'output' | 'battery') {
  let drawing = false;
  let previous = 0;
  return props.samples.filter((sample) => sample.time >= start.value).map((sample) => {
    const value = sample[key];
    if (value === null) { drawing = false; previous = sample.time; return ''; }
    const command = drawing && !sample.gap && (props.saved || sample.time - previous < 15000) ? 'L' : 'M';
    drawing = true;
    previous = sample.time;
    return `${command}${x(sample.time).toFixed(1)},${y(value).toFixed(1)}`;
  }).join(' ');
}

const selected = computed(() => {
  if (!props.samples.length) return null;
  if (hovering.value === null) return props.samples.at(-1) ?? null;
  return props.samples.reduce((nearest, sample) => Math.abs(sample.time - hovering.value!) < Math.abs(nearest.time - hovering.value!) ? sample : nearest);
});

function inspect(event: PointerEvent) {
  const bounds = (event.currentTarget as SVGElement).getBoundingClientRect();
  const position = (event.clientX - bounds.left) / bounds.width * 640;
  hovering.value = start.value + Math.max(0, Math.min(1, (position - left) / width)) * props.windowMs;
}

const format = (value: number | null | undefined, unit: string) => value === null || value === undefined ? 'No fresh reading' : `${Math.round(value)}${unit}`;
</script>

<template>
  <section class="panel chart-panel">
    <div class="panel-heading">
      <div>
        <p class="eyebrow">Last {{ duration }} · {{ saved ? 'Saved readings' : 'this session' }}</p>
        <h2>{{ kind === 'power' ? saved ? 'AC power' : 'Power flow' : 'Battery level' }}</h2>
      </div>
      <div v-if="kind === 'power'" class="legend"><span class="input-dot">Input</span><span class="output-dot">Output</span></div>
      <span v-else class="legend battery-dot">State of charge</span>
    </div>
    <svg class="history-chart" viewBox="0 0 640 180" role="img"
      :aria-label="`${kind === 'power' ? 'Input and output power' : 'Battery percentage'} over the last ${duration}; gaps indicate missing fresh readings`"
      @pointermove="inspect" @pointerleave="hovering = null">
      <g v-for="tick in 5" :key="tick">
        <line class="chart-grid-line" :x1="left" :x2="left + width" :y1="top + height * (tick - 1) / 4" :y2="top + height * (tick - 1) / 4" />
        <text class="chart-label" :x="left - 9" :y="top + height * (tick - 1) / 4 + 4" text-anchor="end">{{ Math.round(maximum * (1 - (tick - 1) / 4)) }}</text>
      </g>
      <text class="chart-label" :x="left" y="170">−{{ minutes >= 60 ? `${minutes / 60}h` : `${minutes}m` }}</text>
      <text class="chart-label" :x="left + width / 2" y="170" text-anchor="middle">−{{ minutes >= 60 ? `${minutes / 120}h` : `${minutes / 2}m` }}</text>
      <text class="chart-label" :x="left + width" y="170" text-anchor="end">Now</text>
      <template v-if="kind === 'power'">
        <path class="chart-line input-line" :d="line('input')" />
        <path class="chart-line output-line" :d="line('output')" />
      </template>
      <path v-else class="chart-line battery-line" :d="line('battery')" />
      <line v-if="hovering !== null && selected" class="chart-crosshair" :x1="x(selected.time)" :x2="x(selected.time)" :y1="top" :y2="top + height" />
    </svg>
    <div class="chart-caption">
      <span>{{ selected ? new Date(selected.time).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }) : 'Waiting for the first reading' }}</span>
      <span v-if="kind === 'power'">{{ format(selected?.input, ' W') }} in · {{ format(selected?.output, ' W') }} out</span>
      <span v-else>{{ format(selected?.battery, '%') }}</span>
    </div>
  </section>
</template>
