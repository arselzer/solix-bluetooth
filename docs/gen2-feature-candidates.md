# Gen 2 feature candidates: fast charge and lower AC limits

## Scope

This is **offline C1000 Gen 2 (A1763) main 1.1.4.9** evidence. No device, BLE, SSH, cloud, output, or configuration command was sent. It does not establish C2000 behavior or extend the SDK's currently exposed control ranges.

**Later hardware validation:** the [September 30 charging trials](c1000-charging-and-reserve-validation.md)
confirmed native fast-charge retention and actual 100/200 W charging; the SDK now
allows 100–1200 W for C1000 Gen 2. The findings below describe the preceding
offline investigation and its substitutions, not the live test itself.

The new [feature replay](../tools/firmware_analysis/emulate_feature_candidates.py) executes **212 cases**: 160 fast-charge handler/policy combinations, four readiness/RTC/sensor transitions, and 48 low-power cases. Input is the hash-checked `MainMcu-decoded.bin` described in the [reproduction guide](firmware-analysis-reproduction.md). It reuses the published offline helpers without changing their original suites.

## Fast-charge acceptance and automatic clearing

The native field is `0101` A7 with typed value `01 00/01`; the setting readback is **A4[21]**. The handler at `0800be20..0800be40` accepts enable only when tariff selector `0801bcc4` returns zero. It does not directly check SOC, a battery-status field or mains telemetry. An ignored enable still receives an acknowledgement. Disable remains accepted in every tested state.

The separate charging-policy entry at `08014d72..08014d82` clears fast charge if either:

- Bit 0 at `200004be`, the debounced power-readiness gate, is clear.
- The tariff selector returns an active tariff.

This gives two distinct confirmation stages:

| Synthetic state | Immediately after enable | After a charging-policy pass |
| --- | --- | --- |
| Standard, power gate true | Enabled | Enabled |
| Standard, power gate false | Enabled | Cleared |
| Matching tariff, network and power gates true | Ignored | Cleared |
| TOU schedule outside its current slot, power gate true | Enabled | Enabled until a tariff becomes active |
| Stored TOU slot, network readiness false, power gate true | Enabled in the controller handler | Cleared once readiness permits that tariff |

The final row does **not** demonstrate a working MQTT connection while network readiness is false: transport is substituted. It isolates the controller's selector behavior.

The four transition replays start with enabled fast charge. Losing the power gate clears it; gaining network readiness for a stored active Peak slot clears it; advancing only the synthetic RTC into an existing Peak slot clears it. Changing synthetic SOC to 100 and the BMS current allowance to zero **does not clear it**.

### What telemetry can establish

- **A4[21] is the affirmative setting readback.** An ACK or saved request is insufficient; use fresh samples after the write and watch for automatic clearing.
- **D9[2] Standard and D9[1] None** avoid a presently active tariff. Standard is a stronger test baseline than a temporary gap in an enabled schedule. Preserve the original schedule and other settings.
- **A7[4] mains present is not the power gate.** Its previously traced peripheral source differs from the debounced AC-module predicate. Output enabled is a separate flag too.
- **0089/A1 network readiness is not the power gate.** It does not establish the AC-module fault/quality conditions feeding `200004be`.
- **SOC and battery status do not establish fast-charge acceptance or current.** A retained fast flag at full SOC is not evidence that the battery is charging quickly—or charging at all.

No new public telemetry field reliably exposing the complete debounced power gate was identified in this bounded investigation. A future guarded setter should require a known Standard/no-tariff baseline, preserve unrelated configuration and outputs, confirm A4[21] in fresh samples, and report a setting-confirmation failure if it clears. It should not try to change internal readiness flags.

## Full battery versus charging

The tested Standard policy reads the BMS charge-voltage/current allowance through `08018384` kinds 10 and 11. Its fast-charge retention decision does not depend on the synthetic SOC value. With zero BMS current allowance, the replay emits an AC descriptor with zero current while **A4[21] remains one**. With no synthetic AC-charge-available bit, it can retain that flag without emitting an AC charge descriptor at all.

These inputs are intentionally varied independently. A 100% SOC input with a nonzero BMS allowance, or a false power gate with the AC-available flag still set, may not describe a stable real-device state. The replay does not model battery-full detection, cell balancing, temperature limits or the producer of those allowances. A full-battery live test can validate flag acceptance/restoration, but cannot establish fast-charge power enforcement.

## 100–200 W charging limits

There is stronger firmware support for 100 W and 200 W than for a zero-watt workaround:

1. Native `0101` A4 writes store them and A4[5:7] reports the same values.
2. Validator `0802c890` accepts both within its inclusive 100–1200 range.
3. The actual post-file/CRC settings-load branch at `08028944` preserves both; Device Timeout remains Never in the replay. Neither takes the default-reset branch seen with zero watts.
4. With fast charge disabled, `08015158..080151ae` uses the configured limit in the AC charge descriptor. The stored float coefficient is approximately 0.88.

| Configured limit | Internal descriptor power member, fast off | Fast on |
| ---: | ---: | ---: |
| 100 | 88 | 1400 |
| 200 | 176 | 1400 |
| 300 | 264 | 1400 |
| 1200 | 1056 | 1400 |

These are **internal descriptor members**, not observed wall-power measurements. Fast charge bypasses the configured limit in these tested branches. The 48 cases vary SOC 50/100 and raw BMS current allowances 0/1/10; neither lower limit triggers a default reset or changes unrelated persistent settings.

This offline result justified a bounded **C1000-only** enforcement test below
the upper cap, with fast charge off and a recorded/restored baseline. The later
[live validation](c1000-charging-and-reserve-validation.md) completed both lower
rates; no device test was part of this emulator run. Do not use zero watts as
pause or infer charging enforcement while the battery is full.

## Replay assumptions and reproduction

The actual parser, native handler, tariff selector, A4 serializer, fast setter, selected charging-policy paths, settings validator and post-file-read load decision execute. Persistence, response transport, LCD/timer delivery, charge-plan destination, load-history updates and backup status remain explicit substitutes. Gate RAM, network flags, AC-available bit, RTC and battery sensors are synthetic.

**Writing these synthetic gate inputs bypasses their physical producers inside the emulator.** The tests do not replay the debouncer, AC-module fault chain, DSP, battery firmware, communication session or physical charging. They provide no reason to write internal gates on a station. A4/D9 readback and real power-flow confirmation remain necessary for device tests.

```sh
SOLIX_ANALYSIS_OUTPUT=/tmp/solix-feature-replays \
  python3 tools/firmware_analysis/emulate_feature_candidates.py
cmp /tmp/solix-feature-replays/feature-candidates-results.json \
  tools/firmware_analysis/expected_results/feature-candidates-results.json
```

Use the existing Unicorn requirements and `SOLIX_FIRMWARE_DIR` override when needed. Tested with Python 3.12.3 and Unicorn 2.1.4. The standalone suite writes its own manifest; the combined `run_replays.py` also runs it and compares the complete result against the published fixture. The combined verified manifest covers all 1,842 cases. Published expected results are entirely synthetic and contain no captured identifiers, credentials or session keys.
