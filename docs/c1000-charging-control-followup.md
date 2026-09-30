# C1000 Gen 2 charging-control follow-up

## Scope and evidence

The offline investigation uses the retained **A1763 C1000 Gen 2 main 1.1.4.9** firmware and performed no device, network, flash, or cloud operations. A subsequent C1000 live finding is distinguished below. These findings do not establish C2000 behavior.

**245 offline instruction-replay cases passed:** 11 settings-validator cases, 88 charging-policy cases, 15 lower-limit round trips, 32 downstream bounds cases, 11 native power-handler cases, 32 native fast-charge cases, one default-initializer case, five settings-load decisions, and 50 A4/D9 mirror round trips. Original ARM instructions execute for the parsers, handlers, setters, getters, serializers and selected policy paths. Synthetic sensors, RTC/RAM, persistence, response transport, timer setup, LCD delivery, charge-plan destination, allocation/queue and backup-state calculation replace hardware or surrounding services. The settings-load replay starts after a synthetic successful file read and CRC check. The mirror replay also substitutes zero for the full A4 branch's two remaining-time getters.

## Native lower discharge limit

The native app command is `0103` with typed field `AB = 01 <percent>`. It needs no upper-limit, reserve, output, or timer field. Handler `0800c530` calls setter `0802b528`; readback is **D9[5] and A4[25]**, with D9[5] exposed as `min_charge_percentage`. Offsets include each field's leading type byte. D9[3] is backup reserve. The upper cap also has two copies: **D9[4] and A4[24]**.

The first live 1% → 5% → 1% trial established the lower mirror: only A4[25] and D9[5] changed in the captured configuration blocks, and an independent BLE check confirmed restoration. The initial protective SDK check rejected that expected A4 change because it had omitted the mirror. This supersedes the earlier claim that the lower limit was absent from A4. The corrected guard must check that both copies agree before writing and allow both to change together.

Offline verification matches the capture. A4 builder `0801a018` calls lower getter `0801a678` and writes offset `0x19` at `0801a0ec..0801a0f0` in descriptor mode 3 and `0801a1a6..0801a1aa` in mode 1. The adjacent upper getter `0801a660` writes offset `0x18`. Fifty actual-parser/handler/serializer round trips cover all five supported upper caps and lower limits in both A4 branches. The replay shares one RAM image across the write and both serializers; only the two lower mirrors change, and restoration reproduces both original blocks exactly.

The setter stores the supplied byte and raises reserve to `lower + 5` when needed. Restoring the lower limit alone does **not** restore a reserve raised by that side effect. With reserve 10%, the sequence **1% → 5% → 1%** preserves reserve and upper cap. The existing supported C1000 app/BLE domain is **1, 5, 10, 15, 20%**; the handler accepting other byte values does not establish a supported wider domain.

The native library provides `discharge_floor(lower)` and guarded `set_discharge_floor(lower)`, with gateway command shape `{"command":"set-discharge-floor","lower":5}`. The guard requires `lower + 5 <= reserve <= upper`, a fresh complete A4/D9 baseline, and valid output/mains states. It confirms the requested value while protecting all other settings, including Device Timeout Never. Natural countdown decreases and a change of active tariff at an existing schedule boundary are permitted. There is no retry, automatic reserve adjustment, or automatic restoration after failure. **The corrected SDK check passed a live 1% → 5% → 1% trial.** The CLI (`ap-service-set-discharge-floor --lower 5`), terminal UI and optional browser dashboard expose this guarded control.

The limit can affect operation near empty SOC; that behavior is not exercised by a full-battery setting test.

### Live validation, 2026-09-30

The C1000 Gen 2 A1763, main **1.1.4.9**, radio **0.3.3.0**, started at 100% SOC with AC input/output on, DC off, Standard mode, no tariff slots, reserve 10%, charge caps 100%/1%, charging power 1200 W and Device Timeout Never. The SDK confirmed 5%, then restored 1%, including both A4/D9 mirrors. Three final native samples and three independent BLE samples matched all 18 protected baseline fields. AC output remained on. No C2000 settings were changed.

The first attempt timed out waiting for MQTT without sending a settings command. A second attempt revealed the A4 mirror and restored the lower limit despite its protective check reporting that previously unmapped byte. Both attempts and the successful corrected trial are retained privately. Rejoining used the station's existing isolated Wi-Fi/API profile; no cloud request or firmware update was made.

Successful trial archive SHA256:
`51fb17db08ee22734d07479ceae7033f41f84177dabae11831720ed7269ce652`.
Its owner-only results and manifest are in
`.solix-private/solix-c1000-floor-confirm-20260930-103810/`.

### Downstream bounds

The controller routine at `0800dc50` feeds the internal `F015` descriptor builder at `08029542`. Its three data bytes are `[upper, lower, effective_lower]`. Standard/no active tariff uses `effective_lower = lower`. With an active tariff, replay establishes:

```text
effective_lower = max(lower, min(effective_upper, reserve))
```

Here `effective_upper` normally equals the upper cap, or 100 when the tested backup override is active. This is an internal queued descriptor, not a newly exposed app command; battery-controller execution was not replayed.

## Zero watts is not a supported pause setting

The `0101` power field A4 calls `0802b274`, which stores a supplied 16-bit value without clamping. A4 telemetry bytes [5:7] echo it. This does **not** establish a safe usable range.

The persisted-settings validator at `0802c890` accepts charging power only within **100–1200**. The settings loader `080288c8`, after file/CRC checks, reaches `08028944`; invalid settings call the default initializer `08029aa0`. Actual replay with a loaded zero-watt value restored charging power to 1200 and changed **Device Timeout from Never to 720 minutes (12 hours)**, alongside other defaults. Values 99 and 1201 took the same branch; 100 and 1200 preserved the tested settings. No real restart was performed.

The charging-policy replay also does not identify zero as a clean enable/disable operation. With synthetic voltage/current limits it produced a zero power member but a nonzero current member in the normal charging descriptor. Fast charge bypassed the configured power and produced a different power member. These are internal descriptor values, not proof of physical current or safe inverter behavior. Keep zero watts unavailable; use the verified charge-cap/tariff controls for charging policy.

## Native fast charge

`0101`, typed A7 `01 00/01`, is the native counterpart of the existing BLE fast-charge field. Readback is **A4[21]**. At `0800be20`, a nonzero enable request is ignored if tariff selector `0801bcc4` returns any active tariff, but the handler still acknowledges it. Disable is accepted in every tested tariff state. Nonzero request bytes are normalized to one when accepted; public controls should accept strict booleans only.

Setter `0802b20c` changes a volatile flag and signals LCD feedback, including feedback code 9 on an off-to-on transition; it does not itself schedule persistence. The charging policy can clear this flag when a tariff is selected or its power-readiness condition is false. Any future native setter therefore needs a fresh inactive-tariff/readiness baseline, protected-state comparison and actual A4 confirmation. Native live validation remains separate from the existing BLE evidence.

## DC and solar distinction

Audited `0102` A2 operates the **car/DC output** switch. Its “Car Charge Switch” firmware label does not make it a solar-input or AC-charging enable. No dedicated charging-pause or solar-input switch was identified in the audited `0101`/`0102` handler fields. This is a bounded finding, not proof that no other command exists.

## Reproducibility

The owner-only replay script, results and SHA-256 manifest are retained under the ignored `.solix-private/firmware-analysis/` directory as `emulate_charging_followup.py`, `charging-followup-results.json` and `charging-followup-manifest.json`. The original 195-case artifacts remain in `charging-followup-before-a4-mirror/`. Public synthetic tests cover native framing, reserve/cap rejection before writes, incomplete or stale baselines, ignored acknowledgements, unrelated changes, countdowns, tariff boundaries and lost acknowledgements. Private captures and identifiers are not included here.

Offline manifest SHA256:
`a9de32224089a96ad493f84990a2c0128642f5a0e35ced41e844f0091020c43e`.
