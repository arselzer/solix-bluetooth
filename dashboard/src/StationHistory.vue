<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue';
import HistoryChart from './HistoryChart.vue';
import type { Sample, Station } from './types';

const props = defineProps<{ station: Station; samples: Sample[]; now: number;
  request: (path: string, body?: Record<string, unknown>) => Promise<unknown> }>();
const mode = ref('session');
const enabled = ref(false);
const pending = ref(false);
const report = ref<Record<string, unknown> | null>(null);
const error = ref('');
let generation = 0;
let timer: ReturnType<typeof setInterval> | undefined;
function object(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : null;
}
function numeric(value: unknown): number | null { return typeof value === 'number' && Number.isFinite(value) ? value : null; }
const duration = computed(() => mode.value === 'week' ? 7 * 86400000 : mode.value === 'day' ? 86400000 : 1800000);
const saved = computed(() => mode.value !== 'session');
const chartNow = computed(() => saved.value ? (numeric(object(report.value?.window)?.until) ?? props.now / 1000) * 1000 : props.now);
const history = computed<Sample[]>(() => {
  if (!saved.value) return props.samples;
  const points = report.value?.points;
  if (!Array.isArray(points)) return [];
  return points.slice(0, 2000).flatMap((value) => {
    const point = object(value);
    if (!point || numeric(point.timestamp) === null || typeof point.gap !== 'boolean') return [];
    return [{ time: Number(point.timestamp) * 1000, input: numeric(point.ac_input_power_w),
      output: numeric(point.ac_output_power_w), battery: numeric(point.battery_percentage), gap: point.gap }];
  });
});
const totals = computed(() => object(report.value?.totals));
const kwh = (key: string) => { const value = numeric(totals.value?.[key]); return value === null ? '—' : value.toFixed(3); };
const hours = (key: string) => { const value = numeric(totals.value?.[key]); return value === null ? '—' : (value / 3600).toFixed(2); };
async function load() {
  const revision = ++generation;
  if (!saved.value) { report.value = null; pending.value = false; error.value = ''; return; }
  pending.value = true; error.value = '';
  const since = props.now / 1000 - duration.value / 1000;
  const result = object(await props.request(`/devices/${encodeURIComponent(props.station.name)}/history?since=${since}&limit=1000`));
  if (revision !== generation) return;
  pending.value = false;
  if (result?.estimated === true && result.name === props.station.name && Array.isArray(result.points)) report.value = result;
  else { report.value = null; error.value = 'Saved readings are unavailable or have not been recorded yet.'; }
}
watch(mode, () => { report.value = null; void load(); });
onMounted(async () => {
  const revision = generation;
  const info = object(await props.request('/history'));
  if (revision !== generation) return;
  enabled.value = info?.enabled === true;
  timer = setInterval(() => { if (saved.value && !pending.value) void load(); }, 30000);
});
onUnmounted(() => { generation++; if (timer) clearInterval(timer); });
</script>

<template>
  <section class="history-section" data-testid="station-history">
    <div class="history-toolbar"><label>Chart history<select v-model="mode" data-testid="history-range"><option value="session">This tab · 30 minutes</option><option value="day" :disabled="!enabled">Saved · 24 hours</option><option value="week" :disabled="!enabled">Saved · 7 days</option></select></label><span>{{ pending ? 'Loading saved readings…' : enabled ? 'Saved history available · 30-second refresh' : 'Saved history is not enabled on this gateway' }}</span></div>
    <p v-if="error" class="validation-error" role="status">{{ error }}</p>
    <div class="chart-grid"><HistoryChart :samples="history" :now="chartNow" :window-ms="duration" :saved="saved" kind="power" /><HistoryChart :samples="history" :now="chartNow" :window-ms="duration" :saved="saved" kind="battery" /></div>
    <div v-if="saved && totals" class="station-details energy-estimates" data-testid="history-energy"><span>AC input estimate <strong>{{ kwh('ac_input_energy_kwh_estimate') }} kWh</strong></span><span>AC output estimate <strong>{{ kwh('ac_output_energy_kwh_estimate') }} kWh</strong></span><span>Input / output coverage <strong>{{ hours('ac_input_coverage_seconds') }} / {{ hours('ac_output_coverage_seconds') }} h</strong></span><span>Excluded gaps <strong>{{ totals.gap_count }}</strong></span><p>Estimates over covered intervals only. AC input includes bypass loads; these totals do not measure energy stored in the battery. Missing readings and restarts break integration.</p></div>
  </section>
</template>
