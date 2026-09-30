<script setup lang="ts">
import { computed } from 'vue';
import type { Command, Draft, Proposal, Station } from './types';
import { numberMetric } from './types';

const props = defineProps<{ station: Station; draft: Draft; writable: boolean }>();
const emit = defineEmits<{ propose: [proposal: Proposal] }>();
const allowed = (command: string) => props.station.controls.includes(command);
const observed = (key: string, unit = '') => {
  const value = props.station.metrics[key];
  return value === undefined || value === null ? 'Not reported' : `${value}${unit}`;
};
const powers = computed(() => {
  if (props.station.model === 'c300') return [100, 200, 300, 330];
  const maximum = props.station.model === 'c2000_gen2' ? 1800 : props.station.model === 'c1000_gen2' ? 1200 : 1000;
  const minimum = props.station.model === 'c1000' ? 100 : 300;
  return Array.from({ length: (maximum - minimum) / 100 + 1 }, (_, index) => minimum + index * 100);
});
const reserves = computed(() => {
  const lower = numberMetric(props.station, 'min_charge_percentage');
  const upper = numberMetric(props.station, 'max_charge_percentage');
  if (lower === null || upper === null) return [];
  const minimum = Math.max(5, Math.ceil((lower + 5) / 5) * 5);
  return Array.from({ length: Math.max(0, Math.floor((upper - minimum) / 5) + 1) }, (_, index) => minimum + index * 5);
});
const dischargeFloors = [1, 5, 10, 15, 20];
const floorAvailable = computed(() => props.station.model === 'c1000_gen2' && allowed('set-discharge-floor'));
const floorValid = computed(() => {
  const lower = Number(props.draft.lower);
  const reserve = numberMetric(props.station, 'backup_reserve_percentage');
  const upper = numberMetric(props.station, 'max_charge_percentage');
  return dischargeFloors.includes(lower) && reserve !== null && upper !== null
    && lower + 5 <= reserve && reserve <= upper;
});
const displayTimes = computed(() => props.station.model === 'c1000' ? [20, 30, 60, 300, 1800] : [30, 60]);
const lights = computed(() => props.station.model === 'c1000' ? ['Off', 'Low', 'Medium', 'High', 'SOS'] : ['Off', 'Low', 'Medium', 'High']);
const clock = computed(() => {
  if (!props.station.timezone_name) return 'Timezone unknown';
  try { return new Intl.DateTimeFormat([], { timeZone: props.station.timezone_name, hour: '2-digit', minute: '2-digit' }).format(new Date()); }
  catch { return 'Timezone unknown'; }
});
const planError = computed(() => {
  if (props.draft.periods.length > 6) return 'Use no more than six periods.';
  const periods = props.draft.periods;
  for (const period of periods) {
    if (!['peak', 'mid_peak', 'off_peak'].includes(period.tariff)
      || !/^\d{1,2}$/.test(period.start) || !/^\d{1,2}$/.test(period.end)
      || Number(period.start) < 0 || Number(period.start) >= Number(period.end) || Number(period.end) > 24) {
      return 'Each period needs whole hours with 0 ≤ start < end ≤ 24. Split overnight periods at midnight.';
    }
  }
  const sorted = [...periods].sort((a, b) => Number(a.start) - Number(b.start));
  if (sorted.some((period, index) => index > 0 && Number(period.start) < Number(sorted[index - 1]!.end))) return 'Periods must not overlap.';
  return '';
});

function propose(body: Command, title: string, detail: string, summary: string[]) {
  if (!props.writable || !allowed(body.command)) return;
  emit('propose', { station: props.station.name, body, title, detail, summary });
}

function plan(enabled: boolean) {
  if (planError.value || (enabled && !props.draft.periods.length)) return;
  const periods = props.draft.periods.map((period) => ({ tariff: period.tariff, start_hour: Number(period.start), end_hour: Number(period.end) }));
  propose({ command: 'set-tou-plan', periods, enabled }, enabled ? 'Activate hourly plan?' : 'Save plan in Standard mode?',
    enabled ? 'This replaces the station schedule and activates Time-of-Use. It persists until you change the plan or return to grid.'
      : 'This replaces the station schedule in Standard mode. Use Return to grid to confirm grid supply.',
    periods.length ? periods.map((period) => `${period.tariff.replace('_', ' ')} · ${period.start_hour}:00–${period.end_hour}:00`) : ['Clear all schedule periods']);
}

function addPeriod() {
  if (props.draft.periods.length >= 6) return;
  props.draft.periods.push({ id: Math.max(0, ...props.draft.periods.map((period) => period.id)) + 1,
    tariff: 'off_peak', start: '0', end: '24' });
}
</script>

<template>
  <section class="panel controls-panel">
    <div class="panel-heading"><div><p class="eyebrow">Station settings</p><h2>Charging & preferences</h2></div><span class="tag">Confirm before applying</span></div>
    <p v-if="!station.controls.length" class="empty-note">This gateway is read-only. Monitoring remains available.</p>
    <p v-else-if="!writable" class="disabled-note">Controls need a fresh connection and no command in progress.</p>
    <div class="settings-grid">
      <div v-if="allowed('set-charge-power')" class="setting">
        <label for="charging-power">AC charging power</label><p>Current {{ observed('ac_charging_power_limit_w', ' W') }}</p>
        <div class="setting-input"><select id="charging-power" v-model="draft.watts" :disabled="!writable"><option v-for="watts in powers" :key="watts" :value="String(watts)">{{ watts }} W</option></select>
          <button class="secondary" :disabled="!writable || !powers.includes(Number(draft.watts))" @click="propose({ command: 'set-charge-power', watts: Number(draft.watts) }, 'Change charging power?', 'Set the station’s AC charging-power limit.', [`${draft.watts} W`])">Apply</button></div>
      </div>
      <div v-if="allowed('set-charge-cap')" class="setting">
        <label for="charge-cap">Upper charge limit</label><p>Current {{ observed('max_charge_percentage', '%') }}</p>
        <div class="setting-input"><select id="charge-cap" v-model="draft.upper" :disabled="!writable"><option v-for="upper in [80, 85, 90, 95, 100]" :key="upper" :value="String(upper)">{{ upper }}%</option></select>
          <button class="secondary" :disabled="!writable || ![80, 85, 90, 95, 100].includes(Number(draft.upper)) || Number(draft.upper) < (numberMetric(station, 'backup_reserve_percentage') ?? 0)" @click="propose({ command: 'set-charge-cap', upper: Number(draft.upper) }, 'Change upper charge limit?', 'Set the maximum battery charge percentage.', [`${draft.upper}%`])">Apply</button></div>
      </div>
      <div v-if="allowed('set-backup-reserve')" class="setting">
        <label for="backup-reserve">Backup reserve</label><p>Current {{ observed('backup_reserve_percentage', '%') }}</p>
        <div class="setting-input"><select id="backup-reserve" v-model="draft.reserve" :disabled="!writable || !reserves.length"><option v-for="reserve in reserves" :key="reserve" :value="String(reserve)">{{ reserve }}%</option></select>
          <button class="secondary" :disabled="!writable || !reserves.includes(Number(draft.reserve))" @click="propose({ command: 'set-backup-reserve', reserve: Number(draft.reserve) }, 'Change backup reserve?', 'Set reserve within the station’s current charge limits.', [`${draft.reserve}%`])">Apply</button></div>
      </div>
      <div v-if="floorAvailable" class="setting">
        <label for="discharge-floor">Lower discharge limit</label><p>Current {{ observed('min_charge_percentage', '%') }}</p>
        <div class="setting-input"><select id="discharge-floor" v-model="draft.lower" :disabled="!writable"><option v-for="lower in dischargeFloors" :key="lower" :value="String(lower)">{{ lower }}%</option></select>
          <button class="secondary" :disabled="!writable || !floorValid" @click="propose({ command: 'set-discharge-floor', lower: Number(draft.lower) }, 'Change lower discharge limit?', 'Set the station’s lower discharge limit.', [`${draft.lower}% lower limit`, `Observed backup reserve: ${observed('backup_reserve_percentage', '%')}`])">Apply</button></div>
        <p v-if="!floorValid" class="validation-error">Reserve must be at least 5 percentage points above this limit and within the upper charge limit.</p>
      </div>
      <div v-if="allowed('set-display-timeout')" class="setting">
        <label for="display-timeout">Screen timeout</label><p>Current {{ observed('display_timeout_seconds', ' s') }}</p>
        <div class="setting-input"><select id="display-timeout" v-model="draft.seconds" :disabled="!writable"><option v-for="seconds in displayTimes" :key="seconds" :value="String(seconds)">{{ seconds }} seconds</option></select>
          <button class="secondary" :disabled="!writable || !displayTimes.includes(Number(draft.seconds))" @click="propose({ command: 'set-display-timeout', seconds: Number(draft.seconds) }, 'Change screen timeout?', 'Set the display timeout.', [`${draft.seconds} seconds`])">Apply</button></div>
      </div>
      <div v-if="allowed('set-fast-charge')" class="setting">
        <label for="fast-charge">Fast charging</label><p>Current {{ numberMetric(station, 'ac_fast_charge_enabled') === 1 ? 'On' : numberMetric(station, 'ac_fast_charge_enabled') === 0 ? 'Off' : 'Not reported' }}</p>
        <div class="setting-input"><select id="fast-charge" v-model="draft.fast" :disabled="!writable"><option value="0">Off</option><option value="1">On</option></select>
          <button class="secondary" :disabled="!writable" @click="propose({ command: 'set-fast-charge', enabled: draft.fast === '1' }, 'Change fast charging?', 'Set the station’s fast-charging switch.', [draft.fast === '1' ? 'On' : 'Off'])">Apply</button></div>
      </div>
      <div v-if="allowed('set-light')" class="setting">
        <label for="light-mode">Light</label><p>Current {{ lights[numberMetric(station, 'light_mode') ?? -1] ?? 'Not reported' }}</p>
        <div class="setting-input"><select id="light-mode" v-model="draft.light" :disabled="!writable"><option v-for="(light, mode) in lights" :key="mode" :value="String(mode)">{{ light }}</option></select>
          <button class="secondary" :disabled="!writable || !lights[Number(draft.light)]" @click="propose({ command: 'set-light', mode: Number(draft.light) }, 'Change light mode?', 'Set the station light.', [lights[Number(draft.light)] ?? 'Off'])">Apply</button></div>
      </div>
      <div v-if="allowed('set-temperature-unit')" class="setting">
        <label for="temperature-unit">Temperature display</label><p>Current {{ numberMetric(station, 'temperature_unit_fahrenheit') === 1 ? 'Fahrenheit' : numberMetric(station, 'temperature_unit_fahrenheit') === 0 ? 'Celsius' : 'Not reported' }}</p>
        <div class="setting-input"><select id="temperature-unit" v-model="draft.fahrenheit" :disabled="!writable"><option value="0">Celsius · °C</option><option value="1">Fahrenheit · °F</option></select>
          <button class="secondary" :disabled="!writable" @click="propose({ command: 'set-temperature-unit', fahrenheit: draft.fahrenheit === '1' }, 'Change temperature display?', 'Set the station’s temperature display unit.', [draft.fahrenheit === '1' ? 'Fahrenheit' : 'Celsius'])">Apply</button></div>
      </div>
      <div v-if="allowed('set-off-grid-alert')" class="setting">
        <label for="off-grid-alert">Off-grid alert</label><p>Current {{ numberMetric(station, 'ac_off_grid_alert_enabled') === 1 ? 'On' : numberMetric(station, 'ac_off_grid_alert_enabled') === 0 ? 'Off' : 'Not reported' }}</p>
        <div class="setting-input"><select id="off-grid-alert" v-model="draft.alert" :disabled="!writable"><option value="0">Off</option><option value="1">On</option></select>
          <button class="secondary" :disabled="!writable" @click="propose({ command: 'set-off-grid-alert', enabled: draft.alert === '1' }, 'Change off-grid alert?', 'Set the station’s AC off-grid alert preference.', [draft.alert === '1' ? 'On' : 'Off'])">Apply</button></div>
      </div>
    </div>
  </section>

  <section v-if="allowed('set-tou-plan') || allowed('return-grid')" class="panel schedule-panel">
    <div class="panel-heading"><div><p class="eyebrow">Energy scheduling</p><h2>Hourly plan <span class="draft-label">Draft</span></h2></div><span class="tag">{{ station.timezone_name || 'Timezone unknown' }} · {{ clock }}</span></div>
    <p class="schedule-note">The station reports {{ observed('tou_schedule_slot_count') }} saved periods; their times are unavailable. This editor starts as a new draft. Changes persist until you replace the plan or return to grid.</p>
    <div v-if="allowed('set-tou-plan')" class="plan-editor">
      <div v-if="!draft.periods.length" class="empty-plan"><span class="empty-symbol">⌁</span><strong>No periods in this draft</strong><span>Add whole-hour periods, or save this empty draft to clear the schedule.</span></div>
      <div v-for="(period, index) in draft.periods" :key="period.id" class="period-row">
        <span class="period-index">{{ String(index + 1).padStart(2, '0') }}</span>
        <label><span>Tariff</span><select v-model="period.tariff" :disabled="!writable"><option value="off_peak">Off-Peak · grid / charge</option><option value="mid_peak">Mid-Peak · grid</option><option value="peak">Peak · battery</option></select></label>
        <label><span>From</span><input v-model="period.start" type="number" min="0" max="23" step="1" :disabled="!writable" /></label>
        <label><span>Until</span><input v-model="period.end" type="number" min="1" max="24" step="1" :disabled="!writable" /></label>
        <button class="icon-button" :aria-label="`Remove period ${index + 1}`" :disabled="!writable" @click="draft.periods.splice(index, 1)">×</button>
      </div>
      <p v-if="planError" class="validation-error" role="status">{{ planError }}</p>
      <div class="plan-actions">
        <button class="secondary" :disabled="!writable || draft.periods.length >= 6" @click="addPeriod">＋ Add period</button>
        <div class="plan-actions-right"><button class="secondary" :disabled="!writable || !!planError" @click="plan(false)">Save in Standard</button><button class="primary" :disabled="!writable || !!planError || !draft.periods.length" @click="plan(true)">Activate plan</button></div>
      </div>
    </div>
    <div v-if="allowed('return-grid')" class="grid-return"><div><strong>Return to grid</strong><p>Clear the plan and wait for observed grid supply. AC output stays enabled.</p></div><button class="secondary" :disabled="!writable" @click="propose({ command: 'return-grid', timeout: 30 }, 'Return to grid power?', 'Clear the hourly plan and wait for fresh telemetry to confirm grid supply. Charging limits stay unchanged.', ['Keep AC output enabled', 'Clear the saved hourly plan', 'Confirm actual grid supply'])">Return to grid <span aria-hidden="true">↗</span></button></div>
  </section>
</template>
