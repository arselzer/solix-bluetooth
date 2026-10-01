// Exercise the bundled UI against synthetic stations. Never use a real gateway.
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { mkdir } from 'node:fs/promises';
import net from 'node:net';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const root = fileURLToPath(new URL('../../', import.meta.url));
const fixturePath = fileURLToPath(new URL('./fixture_server.py', import.meta.url));
const listener = net.createServer();
listener.listen(0, '127.0.0.1');
await once(listener, 'listening');
const port = listener.address().port;
await new Promise((resolve) => listener.close(resolve));
const base = `http://127.0.0.1:${port}`;
const authorization = 'Bearer demo-token-not-secret';
const python = process.env.SOLIX_TEST_PYTHON || 'python3';
const fixture = spawn(python, [fixturePath], {
  env: { ...process.env, PYTHONPATH: `${root}/python`, SOLIX_TEST_PORT: String(port) },
  stdio: ['ignore', 'ignore', 'inherit'],
});
let browser;
let cases = 0;
const errors = [];
const external = [];
async function recorded() {
  const response = await fetch(`${base}/fixture`, { headers: { Authorization: authorization } });
  const result = await response.json();
  assert.equal(result.synthetic_fixture, true);
  return result.commands;
}
async function change(values) {
  const response = await fetch(`${base}/fixture`, { method: 'POST', headers: { Authorization: authorization, 'Content-Type': 'application/json' }, body: JSON.stringify(values) });
  assert.equal(response.status, 200);
}
try {
  const deadline = Date.now() + 10000;
  while (true) {
    try { await recorded(); break; }
    catch {
      if (fixture.exitCode !== null || Date.now() > deadline) throw new Error('Synthetic fixture failed to start');
      await new Promise((resolve) => setTimeout(resolve, 50));
    }
  }
  browser = await chromium.launch({ headless: true, ...(process.env.SOLIX_CHROMIUM_PATH ? { executablePath: process.env.SOLIX_CHROMIUM_PATH } : {}), args: ['--no-sandbox'] });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1100 } });
  const page = await context.newPage();
  page.setDefaultTimeout(10000);
  let simulatedNow = Date.now();
  await page.clock.install({ time: simulatedNow });
  page.on('pageerror', (error) => errors.push(error.message));
  page.on('console', (message) => { if (message.type() === 'error' && message.text().includes('Content Security Policy')) errors.push(message.text()); });
  let posts = 0;
  page.on('request', (request) => {
    if (!request.url().startsWith(base + '/')) external.push(request.url());
    if (request.method() === 'POST' && request.url().includes('/commands')) posts++;
  });
  await page.goto(base);
  const token = page.getByTestId('token-input');
  assert.equal(await token.getAttribute('type'), 'password');
  await token.fill('wrong-token');
  await page.getByTestId('connect').click();
  await page.getByTestId('gateway-notice').filter({ hasText: 'Access denied' }).waitFor();
  assert.equal(await token.inputValue(), '');
  assert.equal(await recorded().then((calls) => calls.length), 0);
  cases++; console.log(`Scenario ${cases} passed`);

  async function connect() {
    await page.getByTestId('token-input').fill('demo-token-not-secret');
    await page.getByTestId('connect').click();
    await page.getByText('Live telemetry', { exact: true }).waitFor();
  }
  async function refresh() {
    await page.getByTestId('refresh').click();
    await page.waitForFunction(() => !document.querySelector('[data-testid="refresh"]')?.disabled);
  }
  async function propose(label) {
    const setting = page.locator('.setting').filter({ has: page.locator(`label[for="${label}"]`) });
    await setting.getByRole('button', { name: 'Apply', exact: true }).click();
    await page.getByTestId('command-review').waitFor();
  }
  await connect();
  assert.equal(await page.locator('#temperature-unit').count(), 1);
  assert.equal(await page.locator('#off-grid-alert').count(), 1);
  assert.equal(await page.locator('#device-timeout').inputValue(), '0');
  assert.match(await page.getByTestId('pv-weak-light-lock').textContent(), /Inactive/);
  assert.match(await page.getByTestId('pv-weak-light-lock').getAttribute('title'), /physical PV behavior untested/);
  assert.deepEqual(await page.evaluate(() => [localStorage.length, sessionStorage.length]), [0, 0]);
  assert.equal((await context.cookies()).length, 0);
  assert.equal(await page.getByRole('button', { name: /AC output/i }).count(), 0);
  cases++; console.log(`Scenario ${cases} passed`);

  await page.locator('#charging-power').selectOption('100');
  await page.getByRole('button', { name: 'Add period' }).click();
  await page.locator('.period-row').first().locator('input').nth(1).fill('6');
  await refresh();
  assert.equal(await page.locator('#charging-power').inputValue(), '100');
  assert.equal(await page.locator('.period-row').first().locator('input').nth(1).inputValue(), '6');
  cases++; console.log(`Scenario ${cases} passed`);

  await propose('charging-power');
  await page.getByTestId('cancel-command').click();
  assert.equal(posts, 0);
  await propose('charging-power');
  await page.getByTestId('confirm-command').click();
  await page.getByTestId('gateway-notice').filter({ hasText: 'Command confirmed' }).waitFor();
  let calls = await recorded();
  assert.deepEqual(calls[0], { name: 'Office · C1000 Gen 2', command: 'set-charge-power', watts: 100 });
  assert.equal(posts, 1);
  cases++; console.log(`Scenario ${cases} passed`);

  await page.locator('#temperature-unit').selectOption('1');
  await propose('temperature-unit');
  await page.getByTestId('confirm-command').click();
  await page.getByTestId('gateway-notice').filter({ hasText: 'Command confirmed' }).waitFor();
  calls = await recorded();
  assert.deepEqual(calls[1], { name: 'Office · C1000 Gen 2', command: 'set-temperature-unit', fahrenheit: true });
  cases++; console.log(`Scenario ${cases} passed`);

  await page.locator('#discharge-floor').selectOption('5');
  await propose('discharge-floor');
  await page.getByTestId('confirm-command').click();
  await page.getByTestId('gateway-notice').filter({ hasText: 'Command confirmed' }).waitFor();
  calls = await recorded();
  assert.deepEqual(calls[2], { name: 'Office · C1000 Gen 2', command: 'set-discharge-floor', lower: 5 });
  cases++; console.log(`Scenario ${cases} passed`);

  await page.locator('#device-timeout').selectOption('30');
  await refresh();
  assert.equal(await page.locator('#device-timeout').inputValue(), '30');
  await propose('device-timeout');
  assert.ok((await page.getByTestId('command-review').textContent()).includes('interrupting remote access'));
  await page.getByTestId('cancel-command').click();
  assert.equal((await recorded()).length, 3);
  await page.locator('#device-timeout').selectOption('0');
  await propose('device-timeout');
  assert.ok((await page.getByTestId('command-review').textContent()).includes('other sleep behavior'));
  await page.getByTestId('confirm-command').click();
  await page.getByTestId('gateway-notice').filter({ hasText: 'Command confirmed' }).waitFor();
  assert.deepEqual((await recorded())[3], { name: 'Office · C1000 Gen 2', command: 'set-device-timeout', minutes: 0 });
  await page.getByTestId('station-select').selectOption('Spare · C1000');
  assert.ok((await page.getByTestId('input-reading').textContent()).includes('AC input not reported'));
  assert.ok((await page.getByTestId('battery-reading').textContent()).includes('Activity unknown'));
  assert.equal(await page.getByTestId('supply-reading').locator('.source-value').textContent(), 'Unknown');
  assert.ok((await page.getByTestId('supply-reading').textContent()).includes('Mode unknown'));
  assert.equal(await page.locator('#device-timeout').inputValue(), '0');
  await page.locator('#device-timeout').selectOption('120');
  await propose('device-timeout');
  await page.getByTestId('confirm-command').click();
  await page.getByTestId('gateway-notice').filter({ hasText: 'Command confirmed' }).waitFor();
  assert.deepEqual((await recorded())[4], { name: 'Spare · C1000', command: 'set-device-timeout', minutes: 120 });
  cases++; console.log(`Scenario ${cases} passed`);

  await page.locator('#temperature-unit').selectOption('1');
  await propose('temperature-unit');
  await page.getByTestId('confirm-command').click();
  await page.getByTestId('gateway-notice').filter({ hasText: 'Command confirmed' }).waitFor();
  assert.deepEqual((await recorded())[5], { name: 'Spare · C1000', command: 'set-temperature-unit', fahrenheit: true });
  await page.locator('#ac-power-saving').selectOption('1');
  await propose('ac-power-saving');
  assert.ok((await page.getByTestId('command-review').textContent()).includes('automatically turn the output off at low load'));
  await page.getByTestId('cancel-command').click();
  assert.equal((await recorded()).length, 6);
  await propose('ac-power-saving');
  await page.getByTestId('confirm-command').click();
  await page.getByTestId('gateway-notice').filter({ hasText: 'Command confirmed' }).waitFor();
  assert.deepEqual((await recorded())[6], { name: 'Spare · C1000', command: 'set-ac-power-saving', enabled: true });
  await page.locator('#dc-power-saving').selectOption('1');
  await propose('dc-power-saving');
  await page.getByTestId('confirm-command').click();
  await page.getByTestId('gateway-notice').filter({ hasText: 'Command confirmed' }).waitFor();
  assert.deepEqual((await recorded())[7], { name: 'Spare · C1000', command: 'set-dc-power-saving', enabled: true });
  await page.locator('#fast-charge').selectOption('1');
  await propose('fast-charge');
  await page.getByTestId('confirm-command').click();
  await page.getByTestId('gateway-notice').filter({ hasText: 'Command confirmed' }).waitFor();
  assert.deepEqual((await recorded())[8], { name: 'Spare · C1000', command: 'set-fast-charge', enabled: true });
  cases++; console.log(`Scenario ${cases} passed`);

  await page.getByTestId('station-select').selectOption('Office · C1000 Gen 2');
  assert.equal(await page.locator('#ac-power-saving').count(), 0);
  assert.equal(await page.locator('#dc-power-saving').count(), 0);
  await page.locator('#fast-charge').selectOption('1');
  const fastApply = page.locator('.setting').filter({ has: page.locator('label[for="fast-charge"]') }).getByRole('button', { name: 'Apply', exact: true });
  assert.equal(await fastApply.isDisabled(), true);
  await change({ standard: true });
  await refresh();
  assert.equal(await fastApply.isDisabled(), false);
  await propose('fast-charge');
  await page.getByTestId('confirm-command').click();
  await page.getByTestId('gateway-notice').filter({ hasText: 'Command confirmed' }).waitFor();
  assert.deepEqual((await recorded())[9], { name: 'Office · C1000 Gen 2', command: 'set-fast-charge', enabled: true });
  await change({ standard: false });
  await refresh();
  await page.locator('#fast-charge').selectOption('0');
  await propose('fast-charge');
  await page.getByTestId('confirm-command').click();
  await page.getByTestId('gateway-notice').filter({ hasText: 'Command confirmed' }).waitFor();
  assert.deepEqual((await recorded())[10], { name: 'Office · C1000 Gen 2', command: 'set-fast-charge', enabled: false });
  await change({ mains: false });
  await refresh();
  assert.equal(await fastApply.isDisabled(), true);
  await change({ mains: true });
  await refresh();
  cases++; console.log(`Scenario ${cases} passed`);

  await page.getByTestId('station-select').selectOption('Server · C2000 Gen 2');
  assert.equal(await page.locator('#charging-power option[value="100"]').count(), 0);
  assert.equal(await page.locator('#charging-power option[value="200"]').count(), 0);
  assert.equal(await page.locator('#temperature-unit').count(), 0);
  assert.equal(await page.locator('#discharge-floor').count(), 0);
  assert.equal(await page.locator('#off-grid-alert').count(), 0);
  assert.equal(await page.locator('#device-timeout').count(), 0);
  assert.equal(await page.locator('#fast-charge').count(), 0);
  assert.equal(await page.locator('#ac-power-saving').count(), 0);
  assert.equal(await page.locator('#dc-power-saving').count(), 0);
  assert.equal(await page.locator('#display-brightness').count(), 0);
  assert.equal(await page.locator('#display-timeout').count(), 0);
  assert.equal(await page.locator('#port-memory').count(), 0);
  await change({ readonly: true });
  await refresh();
  await page.getByText('This gateway is read-only. Monitoring remains available.').waitFor();
  assert.equal(await page.locator('#charging-power').count(), 0);
  await change({ readonly: false });
  await refresh();
  await change({ available: false });
  await refresh();
  await page.getByText('Stale / unavailable', { exact: true }).waitFor();
  assert.equal(await page.locator('#charging-power').isDisabled(), true);
  await change({ available: true });
  await refresh();
  cases++; console.log(`Scenario ${cases} passed`);

  await page.route('**/devices/*/commands', async (route) => {
    await route.fulfill({ status: 504, contentType: 'application/json', body: JSON.stringify({ error: 'PRIVATE-ERROR-TEXT', settings_may_have_changed: true }) });
  }, { times: 1 });
  await propose('charging-power');
  await page.getByTestId('confirm-command').click();
  await page.getByTestId('gateway-notice').filter({ hasText: 'Confirmation timed out' }).waitFor();
  await refresh();
  assert.equal(posts, 12);
  assert.equal((await recorded()).length, 11);
  assert.equal((await page.textContent('body')).includes('PRIVATE-ERROR-TEXT'), false);
  cases++; console.log(`Scenario ${cases} passed`);

  let release;
  const barrier = new Promise((resolve) => { release = resolve; });
  let intercept;
  const intercepted = new Promise((resolve) => { intercept = resolve; });
  await page.route('**/devices', async (route) => {
    intercept();
    await barrier;
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ devices: [] }) }).catch(() => {});
  }, { times: 1 });
  await page.getByTestId('refresh').click();
  await intercepted;
  await page.getByTestId('disconnect').click();
  release();
  await page.getByTestId('connect').waitFor();
  assert.equal(await page.getByTestId('station-select').count(), 0);
  await connect();
  cases++; console.log(`Scenario ${cases} passed`);

  await page.getByTestId('station-select').selectOption('Office · C1000 Gen 2');
  assert.equal(await page.locator('#display-brightness').inputValue(), '1');
  assert.equal(await page.locator('#display-timeout option[value="0"]').textContent(), 'Never');
  assert.equal(await page.locator('#port-memory').inputValue(), '1');
  const beforePreferences = (await recorded()).length;
  for (const [label, value, command, fields] of [
    ['display-brightness', '2', 'set-display-brightness', { level: 2 }],
    ['display-timeout', '60', 'set-display-timeout', { seconds: 60 }],
    ['port-memory', '0', 'set-port-memory', { enabled: false }],
  ]) {
    await page.locator(`#${label}`).selectOption(value);
    await refresh();
    assert.equal(await page.locator(`#${label}`).inputValue(), value);
    await propose(label);
    if (label === 'port-memory') {
      assert.ok((await page.getByTestId('command-review').textContent()).includes('does not restore that transient state'));
      await page.getByTestId('cancel-command').click();
      assert.equal((await recorded()).length, beforePreferences + 2);
      await propose(label);
    }
    await page.getByTestId('confirm-command').click();
    await page.getByTestId('gateway-notice').filter({ hasText: 'Command confirmed' }).waitFor();
    assert.deepEqual((await recorded()).at(-1), { name: 'Office · C1000 Gen 2', command, ...fields });
  }
  await change({ available: false });
  await refresh();
  for (const label of ['display-brightness', 'display-timeout', 'port-memory']) {
    assert.equal(await page.locator(`#${label}`).isDisabled(), true);
  }
  await change({ available: true });
  await refresh();
  cases++; console.log(`Scenario ${cases} passed`);

  await page.getByTestId('station-select').selectOption('Updated · C1000');
  assert.equal(await page.locator('#charging-power').inputValue(), '1000');
  assert.equal(await page.locator('#display-brightness').inputValue(), '2');
  assert.equal(await page.locator('#device-timeout').inputValue(), '720');
  assert.equal(await page.locator('#display-timeout').inputValue(), '30');
  assert.equal(await page.locator('#display-timeout option[value="0"]').count(), 0);
  assert.equal(await page.locator('#display-timeout option[value="10"]').count(), 0);
  assert.equal(await page.locator('#light-mode').inputValue(), '0');
  assert.equal(await page.locator('#temperature-unit').inputValue(), '0');
  for (const label of ['port-memory', 'ac-power-saving']) {
    assert.equal(await page.locator(`#${label}`).count(), 0);
  }
  const beforeOriginalPrime = (await recorded()).length;
  for (const [label, value, command, fields] of [
    ['charging-power', '900', 'set-charge-power', { watts: 900 }],
    ['display-brightness', '1', 'set-display-brightness', { level: 1 }],
    ['device-timeout', '0', 'set-device-timeout', { minutes: 0 }],
    ['display-timeout', '60', 'set-display-timeout', { seconds: 60 }],
    ['light-mode', '1', 'set-light', { mode: 1 }],
    ['temperature-unit', '1', 'set-temperature-unit', { fahrenheit: true }],
    ['dc-power-saving', '1', 'set-dc-power-saving', { enabled: true }],
    ['fast-charge', '1', 'set-fast-charge', { enabled: true }],
  ]) {
    await page.locator(`#${label}`).selectOption(value);
    await propose(label);
    if (label === 'dc-power-saving') {
      assert.match(await page.getByTestId('command-review').textContent(), /DC output OFF.*inactivity counter/s);
    }
    if (label === 'fast-charge') {
      assert.match(await page.getByTestId('command-review').textContent(), /adequate AC supply.*reboot persistence/s);
    }
    await page.getByTestId('cancel-command').click();
    assert.equal((await recorded()).length, beforeOriginalPrime + ['charging-power', 'display-brightness', 'device-timeout', 'display-timeout', 'light-mode', 'temperature-unit', 'dc-power-saving', 'fast-charge'].indexOf(label));
    await propose(label);
    await page.getByTestId('confirm-command').click();
    await page.getByTestId('gateway-notice').filter({ hasText: 'Command confirmed' }).waitFor();
    assert.deepEqual((await recorded()).at(-1), { name: 'Updated · C1000', command, ...fields });
  }
  await change({ available: false });
  await refresh();
  for (const label of ['charging-power', 'display-brightness', 'device-timeout', 'display-timeout', 'light-mode', 'temperature-unit', 'dc-power-saving', 'fast-charge']) {
    assert.equal(await page.locator(`#${label}`).isDisabled(), true);
  }
  await change({ available: true });
  await refresh();
  const afterOriginalPrime = (await recorded()).length;
  await change({ dc_output: 1 });
  await refresh();
  assert.equal(await page.locator('#dc-power-saving').isDisabled(), true);
  assert.equal(await page.locator('.setting').filter({ has: page.locator('#dc-power-saving') }).getByRole('button', { name: 'Apply', exact: true }).isDisabled(), true);
  assert.equal((await recorded()).length, afterOriginalPrime);
  await change({ dc_output: 0 });
  await refresh();
  assert.equal(await page.locator('#dc-power-saving').isDisabled(), false);
  cases++; console.log(`Scenario ${cases} passed`);

  await page.getByTestId('station-select').selectOption('Local · C1000');
  assert.equal(await page.getByTestId('pv-weak-light-lock').count(), 0);
  assert.equal(await page.locator('#charging-power option').first().getAttribute('value'), '100');
  assert.equal(await page.locator('#charging-power option').last().getAttribute('value'), '1000');
  assert.deepEqual(await page.locator('#display-timeout option').evaluateAll((options) => options.map((option) => option.value)), ['20', '30', '60', '300', '1800']);
  for (const label of ['charge-cap', 'backup-reserve', 'discharge-floor', 'port-memory', 'ac-power-saving', 'off-grid-alert']) {
    assert.equal(await page.locator(`#${label}`).count(), 0);
  }
  const beforeNativeOriginal = (await recorded()).length;
  for (const [index, [label, value, command, fields]] of [
    ['charging-power', '900', 'set-charge-power', { watts: 900 }],
    ['display-brightness', '1', 'set-display-brightness', { level: 1 }],
    ['device-timeout', '0', 'set-device-timeout', { minutes: 0 }],
    ['display-timeout', '60', 'set-display-timeout', { seconds: 60 }],
    ['light-mode', '1', 'set-light', { mode: 1 }],
    ['temperature-unit', '1', 'set-temperature-unit', { fahrenheit: true }],
    ['dc-power-saving', '0', 'set-dc-power-saving', { enabled: false }],
    ['fast-charge', '1', 'set-fast-charge', { enabled: true }],
  ].entries()) {
    await page.locator(`#${label}`).selectOption(value);
    await propose(label);
    if (label === 'dc-power-saving') assert.match(await page.getByTestId('command-review').textContent(), /DC output OFF.*inactivity counter/s);
    if (label === 'fast-charge') assert.match(await page.getByTestId('command-review').textContent(), /adequate AC supply.*reboot persistence/s);
    await page.getByTestId('cancel-command').click();
    assert.equal((await recorded()).length, beforeNativeOriginal + index);
    await propose(label);
    await page.getByTestId('confirm-command').click();
    await page.getByTestId('gateway-notice').filter({ hasText: 'Command confirmed' }).waitFor();
    assert.deepEqual((await recorded()).at(-1), { name: 'Local · C1000', command, ...fields });
  }
  await change({ readonly: true });
  await refresh();
  await page.getByText('This gateway is read-only. Monitoring remains available.').waitFor();
  assert.equal(await page.locator('#charging-power').count(), 0);
  assert.equal(await page.locator('#device-timeout').count(), 0);
  assert.equal(await page.locator('#light-mode').count(), 0);
  assert.ok((await page.getByTestId('input-reading').textContent()).includes('AC input not reported'));
  assert.equal(await page.getByTestId('supply-reading').locator('.source-value').textContent(), 'Unknown');
  await change({ readonly: false, available: false });
  await refresh();
  for (const label of ['charging-power', 'display-brightness', 'device-timeout', 'display-timeout', 'light-mode', 'temperature-unit', 'dc-power-saving', 'fast-charge']) {
    assert.equal(await page.locator(`#${label}`).isDisabled(), true);
  }
  await change({ available: true });
  await refresh();
  const afterNativeOriginal = (await recorded()).length;
  await change({ dc_output: 1 });
  await refresh();
  assert.equal(await page.locator('#dc-power-saving').isDisabled(), true);
  assert.equal((await recorded()).length, afterNativeOriginal);
  await change({ dc_output: 0 });
  await refresh();
  cases++; console.log(`Scenario ${cases} passed`);

  // Controlled browser clock and synthetic read-only responses produce a chart
  // without waiting 20 real minutes or contacting a station.
  const initial = await fetch(`${base}/devices`, { headers: { Authorization: authorization } }).then((response) => response.json());
  let tick = 0;
  await page.route('**/devices', async (route) => {
    const devices = initial.devices.map((station, index) => ({ ...station, last_seen_timestamp: simulatedNow / 1000,
      metrics: { ...station.metrics, battery_percentage: index ? 88 : 92 - tick / 180,
        ac_input_power_w: Math.round(340 + 100 * Math.sin(tick / 17 + index)),
        total_input_power_w: Math.round(340 + 100 * Math.sin(tick / 17 + index)),
        ac_output_power_w: Math.round(490 + 130 * Math.sin(tick / 11 + index)),
        total_output_power_w: Math.round(490 + 130 * Math.sin(tick / 11 + index)),
      } }));
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify({ devices }) });
  });
  for (tick = 0; tick < 210; tick++) {
    simulatedNow += 5000;
    const response = page.waitForResponse((value) => value.url() === base + '/devices');
    await page.clock.runFor(5000);
    await response;
    await page.waitForFunction(() => !document.querySelector('[data-testid="refresh"]')?.disabled);
  }
  await page.getByTestId('station-select').selectOption('Office · C1000 Gen 2');
  await page.getByRole('button', { name: 'Add period' }).click();
  const second = page.locator('.period-row').nth(1);
  await second.locator('select').selectOption('peak');
  await second.locator('input').nth(0).fill('17');
  await second.locator('input').nth(1).fill('23');
  assert.ok((await page.locator('.input-line').getAttribute('d')).includes('L'));
  await page.locator('footer').evaluate((element) => { element.textContent = 'Synthetic demonstration data · No live stations used'; });
  const screenshots = `${root}/docs/images`;
  await mkdir(screenshots, { recursive: true });
  await page.screenshot({ path: `${screenshots}/web-dashboard-desktop.png`, fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: `${screenshots}/web-dashboard-mobile.png`, fullPage: true });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  await page.setViewportSize({ width: 1440, height: 1100 });
  await page.getByTestId('station-select').selectOption('Spare · C1000');
  await page.screenshot({ path: `${screenshots}/web-dashboard-c1000-preferences.png`, fullPage: true });
  await page.getByTestId('station-select').selectOption('Updated · C1000');
  await page.screenshot({ path: `${screenshots}/web-dashboard-original-prime.png`, fullPage: true });
  await page.getByTestId('station-select').selectOption('Local · C1000');
  await page.screenshot({ path: `${screenshots}/web-dashboard-original-native.png`, fullPage: true });
  assert.deepEqual(errors, []);
  assert.deepEqual(external, []);
  cases++; console.log(`Scenario ${cases} passed`);
  console.log(`${cases} browser scenarios passed; screenshots contain only synthetic data.`);
} finally {
  if (browser) await browser.close();
  if (fixture.exitCode === null && fixture.signalCode === null) {
    fixture.kill('SIGTERM');
    await once(fixture, 'exit').catch(() => {});
  }
}
