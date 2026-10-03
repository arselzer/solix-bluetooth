<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { modelLabel } from './types';

const props = defineProps<{ checking: boolean; reports: { diagnostics: unknown; setup: unknown } | null }>();
const emit = defineEmits<{ close: []; refresh: [] }>();
const dialog = ref<HTMLDialogElement | null>(null);
const closeButton = ref<HTMLButtonElement | null>(null);
const models = ['c1000', 'c1000_gen2', 'c2000_gen2', 'c300'];
const reasons: Record<string, string> = {
  ready: 'Fresh telemetry', disconnected: 'Disconnected', awaiting_telemetry: 'Awaiting telemetry',
  stale_telemetry: 'Stale telemetry', clock_skew: 'Clock mismatch', unavailable: 'Monitor unavailable',
};
function object(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : null;
}
function report(value: unknown, versionKey = 'schema_version') {
  const result = object(value);
  return result?.[versionKey] === 1 ? result : null;
}
const diagnostics = computed(() => report(props.reports?.diagnostics));
const stations = computed(() => {
  const values = diagnostics.value?.stations;
  return Array.isArray(values) ? values.slice(0, 64).flatMap((value) => {
    const station = object(value);
    if (!station || typeof station.model !== 'string' || !models.includes(station.model)
      || !Number.isInteger(station.station) || typeof station.availability_reason !== 'string'
      || !Object.hasOwn(reasons, station.availability_reason)) return [];
    return [{ ordinal: Number(station.station), model: modelLabel(station.model),
      ready: station.availability_reason === 'ready', reason: reasons[station.availability_reason],
      age: typeof station.telemetry_age_seconds === 'number' && Number.isFinite(station.telemetry_age_seconds)
        ? `${Math.max(0, Math.round(station.telemetry_age_seconds))}s` : 'Unknown' }];
  }) : [];
});
const setup = computed(() => report(props.reports?.setup, 'schema'));
const findings = computed(() => {
  const values = setup.value?.findings;
  return Array.isArray(values) ? values.slice(0, 64).flatMap((value) => {
    const finding = object(value);
    if (!finding || !['error', 'warning', 'info'].includes(String(finding.severity))
      || typeof finding.code !== 'string' || !/^[a-z0-9_]{1,80}$/.test(finding.code)
      || typeof finding.message !== 'string' || finding.message.length > 500) return [];
    return [{ severity: String(finding.severity), code: finding.code, message: finding.message }];
  }) : [];
});
onMounted(() => { dialog.value?.showModal(); closeButton.value?.focus(); });
</script>

<template>
  <dialog ref="dialog" class="confirm-dialog checks-dialog" data-testid="gateway-checks" aria-labelledby="checks-title" @cancel.prevent="emit('close')">
    <p class="eyebrow">Read-only checks</p><h2 id="checks-title">Gateway & saved AP setup</h2>
    <p>Checks cached telemetry and local provisioning files. No station requests or setting changes.</p>
    <p v-if="checking" role="status">Checking…</p>
    <template v-else>
      <section class="checks-section"><h3>Monitoring</h3>
        <p v-if="!diagnostics">Diagnostic report unavailable. This gateway may need an update.</p>
        <p v-else-if="!stations.length">No stations reported.</p>
        <ul v-else class="checks-stations"><li v-for="station in stations" :key="station.ordinal"><span>Station {{ station.ordinal }} · {{ station.model }}</span><span :class="{ 'check-ready': station.ready }">{{ station.reason }} · {{ station.age }}</span></li></ul>
      </section>
      <section class="checks-section"><h3>Saved AP files</h3>
        <p v-if="!setup" data-testid="setup-unavailable">Not available on this gateway. A native AP gateway is required.</p>
        <template v-else><p data-testid="setup-result" :class="{ 'check-ready': setup.ok === true }">{{ setup.ok === true ? 'Local file checks passed.' : 'Local file errors found.' }} Live binding and connectivity still need confirmation.</p>
          <ul v-if="findings.length" class="checks-findings"><li v-for="(finding, index) in findings" :key="index" :class="finding.severity"><strong>{{ finding.severity }}</strong> · {{ finding.message }}</li></ul>
        </template>
      </section>
    </template>
    <div class="confirmation-actions"><button class="secondary" data-testid="recheck-gateway" :disabled="checking" @click="emit('refresh')">Check again</button><button ref="closeButton" class="primary" data-testid="close-checks" @click="emit('close')">Close</button></div>
  </dialog>
</template>
