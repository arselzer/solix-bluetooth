<script setup lang="ts">
import { computed, onUnmounted, reactive, ref, watch } from 'vue';
import type { Station } from './types';

const props = defineProps<{ station: Station; busy: boolean; request: (path: string, body?: Record<string, unknown>) => Promise<unknown> }>();
const form = reactive({ mode: 'export', exportValue: '', priceValue: '', exportStart: 600, exportStop: 300,
  priceStart: 0.1, priceStop: 0.2, high: 1000, low: 300, reserve: 20, simulateArmed: true, exportConfirmed: false });
const pending = ref(false);
const error = ref('');
const result = ref<Record<string, unknown> | null>(null);
const signalTime = reactive({ export: 0, price: 0 });
let revision = 0;
const reasons: Record<string, string> = {
  policy_disarmed: 'The simulated policy is disarmed.', command_latched: 'The command latch needs review.',
  cooldown_active: 'Cooldown has not elapsed.', station_disconnected: 'The station is disconnected.',
  station_unavailable: 'The monitor reports unavailable.', station_error: 'The monitor reports a station error.',
  telemetry_missing: 'No usable telemetry timestamp.', telemetry_future: 'Telemetry is ahead of the gateway clock.',
  telemetry_stale: 'Telemetry is stale.', mains_not_confirmed: 'AC input is not confirmed connected.',
  ac_output_not_confirmed: 'AC output is not confirmed on.', fast_not_confirmed_off: 'Fast charging must already be off.',
  standard_mode_required: 'Standard mode is required.', no_active_tariff_required: 'An active tariff blocks this policy.',
  battery_invalid: 'Battery percentage is unavailable.', current_settings_invalid: 'Current limits are incomplete or invalid.',
  policy_power_outside_model_range: 'Proposed watts are outside this model’s range.',
  policy_reserve_outside_current_caps: 'Proposed reserve conflicts with the current cap or discharge floor.',
  export_signal_stale: 'The manual export sample is stale. Enter a new sample.', price_signal_stale: 'The manual price sample is stale. Enter a new sample.',
  export_signal_future: 'Export sample time is ahead of the gateway.', price_signal_future: 'Price sample time is ahead of the gateway.',
  below_effective_reserve: 'Battery is below the effective reserve.', price_opportunity: 'Price meets the opportunity threshold.',
  export_opportunity: 'Export meets the opportunity threshold.', no_opportunity: 'No opportunity threshold is met.',
  reserve_raise_proposed: 'Raise the saved reserve.', charging_power_change_proposed: 'Change the saved charging-power limit.',
  settings_already_match: 'Saved settings already match.', unsupported_model: 'This model is unsupported.',
  unsupported_transport: 'This preview requires native MQTT.', clock_invalid: 'The gateway clock is invalid.',
};
const explanation = computed(() => Array.isArray(result.value?.reasons)
  ? result.value.reasons.flatMap((code) => typeof code === 'string' && Object.hasOwn(reasons, code) ? [reasons[code]] : []) : []);
const proposals = computed(() => Array.isArray(result.value?.proposed_settings)
  ? result.value.proposed_settings.flatMap((value) => {
    if (!value || typeof value !== 'object') return [];
    const setting = value as Record<string, unknown>;
    if (setting.command === 'set-charge-power' && typeof setting.watts === 'number') return [`Charging power → ${setting.watts} W`];
    if (setting.command === 'set-backup-reserve' && typeof setting.reserve === 'number') return [`Reserve → ${setting.reserve}%`];
    return [];
  }) : []);

watch(form, () => { revision++; result.value = null; error.value = ''; });
onUnmounted(() => { revision++; });
function sample(role: 'export' | 'price') { signalTime[role] = Date.now() / 1000; }
async function preview() {
  if (pending.value || props.busy) return;
  const now = Date.now() / 1000;
  const signals: Record<string, unknown> = {};
  if (form.mode !== 'price') {
    if (!form.exportConfirmed || !String(form.exportValue).trim() || !Number.isFinite(Number(form.exportValue))) {
      error.value = 'Enter an export sample in watts and confirm that positive means export.'; return;
    }
    signals.export = { value: Number(form.exportValue), timestamp: signalTime.export, unit: 'W', positive_means: 'export' };
  }
  if (form.mode !== 'export') {
    if (!String(form.priceValue).trim() || !Number.isFinite(Number(form.priceValue))) { error.value = 'Enter a numeric price sample.'; return; }
    signals.price = { value: Number(form.priceValue), timestamp: signalTime.price };
  }
  const currentRevision = revision;
  pending.value = true; error.value = ''; result.value = null;
  try {
    const response = await props.request(`/devices/${encodeURIComponent(props.station.name)}/charging-preview`, {
      config: { signal_mode: form.mode, armed: form.simulateArmed, command_latch: false, latch_changed_at: now - 181,
        charging_watts: Number(form.high), idle_watts: Number(form.low), minimum_reserve: Number(form.reserve), cooldown: 180,
        price_start: Number(form.priceStart), price_stop: Number(form.priceStop), price_max_age: 3600,
        export_start: Number(form.exportStart), export_stop: Number(form.exportStop), export_max_age: 120 }, signals,
    });
    if (currentRevision !== revision) return;
    const parsed = response && typeof response === 'object' ? response as Record<string, unknown> : null;
    if (parsed?.schema_version === 1 && parsed.dry_run === true && parsed.commands_sent === 0) result.value = parsed;
    else error.value = 'Preview unavailable. Check the input ranges and gateway version.';
  } finally { pending.value = false; }
}
</script>

<template>
  <section v-if="['c1000_gen2', 'c2000_gen2'].includes(station.model) && station.protocol === 'native_mqtt'" class="panel controls-panel preview-panel" data-testid="charging-preview-panel">
    <div class="panel-heading"><div><p class="eyebrow">Read-only simulation</p><h2>Charging policy preview</h2></div><span class="tag">No commands</span></div>
    <p class="schedule-note">Enter manual signal samples to see a proposed decision against fresh station telemetry. This does not enable your HA automation or change station settings.</p>
    <form @submit.prevent="preview">
      <div class="preview-grid">
        <label>Opportunity source<select v-model="form.mode" data-testid="preview-mode"><option value="export">Solar export</option><option value="price">Electricity price</option><option value="either">Either</option></select></label>
        <label>Opportunity charging (W)<input v-model.number="form.high" type="number" step="100" data-testid="preview-high" /></label>
        <label>Other-times charging (W)<input v-model.number="form.low" type="number" step="100" /></label>
        <label>Minimum reserve (%)<input v-model.number="form.reserve" type="number" step="5" /></label>
        <template v-if="form.mode !== 'price'">
          <label>Manual export sample (W)<input v-model="form.exportValue" type="number" step="any" data-testid="preview-export" @input="sample('export')" /></label>
          <label>Export start / stop (W)<span class="preview-pair"><input v-model.number="form.exportStart" type="number" aria-label="Export start watts" /><input v-model.number="form.exportStop" type="number" aria-label="Export stop watts" /></span></label>
        </template>
        <template v-if="form.mode !== 'export'">
          <label>Manual price sample<input v-model="form.priceValue" type="number" step="any" @input="sample('price')" /></label>
          <label>Price start / stop<span class="preview-pair"><input v-model.number="form.priceStart" type="number" step="any" aria-label="Price start" /><input v-model.number="form.priceStop" type="number" step="any" aria-label="Price stop" /></span></label>
        </template>
      </div>
      <label v-if="form.mode !== 'price'" class="preview-toggle"><input v-model="form.exportConfirmed" type="checkbox" data-testid="preview-export-sign" />Positive values in this manual sample mean export to the grid.</label>
      <label class="preview-toggle"><input v-model="form.simulateArmed" type="checkbox" />Evaluate an armed policy (simulation only).</label>
      <p class="schedule-note">Assumes a clear latch and elapsed 180-second cooldown. Prices and thresholds use the same units. Signal validity, saved watts and reserve do not predict actual charging or zero grid import.</p>
      <button class="secondary" type="submit" data-testid="run-charging-preview" :disabled="pending || busy">{{ pending ? 'Evaluating…' : 'Preview decision' }}</button>
    </form>
    <p v-if="error" class="validation-error" role="status">{{ error }}</p>
    <div v-if="result" class="preview-result" data-testid="charging-preview-result" role="status">
      <strong>{{ result.eligible ? 'Proposed decision' : 'Blocked' }} · No commands sent</strong>
      <ul><li v-for="reason in explanation" :key="reason">{{ reason }}</li></ul>
      <p v-for="setting in proposals" :key="setting">{{ setting }}</p>
    </div>
  </section>
</template>
