# Original C1000: Prime Fast preference and input-loss behavior

Live investigation on **2026-10-01**, original **A1761 main 1.7.1 / radio
0.3.3.0**, with the existing app pairing identity. The setup was mains →
C1000 Gen 2 → original C1000 → an expressly expendable HP switch. The C2000
was excluded from every connection and command. No Wi-Fi, cloud or firmware
update was used.

## Fast is now a verified Prime Bluetooth control

The original model accepts `405e`, with A1=`21`, A2=`01 00` or `01 01`,
and FE=`03` followed by little-endian Unix seconds. Prime encrypts that body
using the already negotiated session. Fresh original-model E5=`01 value`
reports the retained Fast flag.

The first trial started with both batteries at **100%**, both AC outputs on,
original DC off and Fast off, original charging ceiling **1000 W**, upstream
ceiling **1200 W**, and upstream mains present. It required three complete
baseline snapshots, all eleven original protected preferences, whole
21-byte F8, zero output countdowns, all seventeen upstream protected settings,
whole upstream D9 and A4 except its dynamic LCD-activity byte. The upstream
load was approximately **180–181 W**; the original reported **115 W** output.

Exactly two setting writes changed **off → on → off**. Four explicit full
hold snapshots retained Fast=1, followed by three complete restored snapshots.
The journal contains **ten snapshots**; the last hold sample was about
29 seconds after the final pre-write baseline. All other preferences and
every original F8 byte stayed unchanged. Neither AC output was toggled in
this retention trial.

The shared Python control is:

```sh
solix-link set-fast-charge --name original --enabled on
# Restore the recorded original value after the test:
solix-link set-fast-charge --name original --enabled off
```

The SDK method is `await monitor.set_fast_charge_enabled(True)`. It uses the
fresh original configuration guard and post-write readback. CLI, Bluetooth
HTTP gateway, BLE-to-MQTT bridge, terminal and browser controls, and the gateway
Home Assistant switch share that verified capability. **This stage established
eight Prime controls.** The subsequent [independent native Fast trial](c1000-native-fast-validation.md)
also brought native MQTT to eight; the measurements here describe Bluetooth.
The later [Prime AC/Smart trial](c1000-prime-ac-smart-validation.md) increases
Prime to ten SDK controls and nine gateway preferences; native remains eight.

An independent public-SDK repeat then passed the same off/on/off round trip,
four held samples and three restored checks, without packet overrides. Its
**seventeen snapshots** include seven read-only settling samples while normal
charging finished; no Fast write occurred before full SOC and three stable
low-load samples. Exactly two setting writes were sent. The method's internal
fresh baseline/confirmation reads are additional to these explicit snapshots.

## Input loss clears Fast without disabling original AC

A separate trial enabled Fast once, confirmed it, then disabled **only the
upstream C1000 Gen 2 AC output** to remove the original's input. Four complete
off-phase snapshots showed original Fast=0 and original AC enabled, reporting
**115–121 W** output. Upstream AC input stayed connected. Both battery
percentages initially remained 100%; this is not an energy-capacity test.

Upstream AC was restored once. Six restored-phase snapshots and three final
baseline checks kept Fast off; it was not automatically re-enabled. All
protected settings and complete F8/D9 were restored. This journal contains
**seventeen snapshots and three writes**: original `405e` on, upstream `4101`
off, upstream `4101` on. No original AC-output command or repeated Fast-enable
write was sent. No waveform-level interruption or certified UPS transfer time
was measured.

## Battery-state and recharge observations

Original BF=1 was already present with mains-powered input/output both reporting
115 W, and remained 1 across the input-loss/restoration snapshots. An earlier
main-1.5.1 outage capture also showed BF changing 0→1, then lingering after
restoration. These codes must not serve as instantaneous grid/battery-flow
selectors. The [BMS producer investigation](c1000-battery-phase-firmware.md)
explains the delayed phase indications in the public older BMS images, without
claiming their timing or exact components on the updated station.

The first subsequent public-SDK attempt stopped **before any write**: the
original had reached 99%, BF=2, and upstream power was **837–839 W** while
normal recharge was occurring. Three final checks preserved every setting.
This failed precondition is retained separately. It is consistent with the
charge-phase interpretation, but the meters were not calibrated and no exact
charge rate, efficiency or current direction was independently measured.
The successful repeat subsequently observed BF=2 at 99%, with original
input **241–253 W** versus output **114–119 W** while charging tapered.
On reaching 100%, BF returned to 1 and original input/output matched again.
This supplies versioned transitions without making BF a bypass indicator.

## Limits and retained evidence

The successful Fast flag trial verifies stored preference, fresh readback and
restoration at full SOC. It does **not** establish accelerated charging watts,
reboot persistence, operation without sufficient AC supply, generated-identity
original pairing or C1000X behavior. Native MQTT Fast has its own
[subsequent validation](c1000-native-fast-validation.md). Input removal can clear
the flag. A timeout after transmission can mean the setting changed; inspect
fresh status before retrying. Do not automatically re-enable a cleared flag.

Private scripts, raw BLE notifications, session material, baselines, journals,
failed attempts and results are retained with restricted permissions in ignored
`.solix-private/bms-fast-20261001/`. Public reports contain selected observations;
packet captures and account/device identifiers remain private. See the
[older firmware Fast trace](c1000-fast-status-retention.md) for its separately
reproducible 1.5.9 instruction evidence.
The complete private BLE evidence archive SHA-256 is
`54003692f6d78e53714dc21a2719952b79ba8de7d0a02a4a94fdcb65fff3148a`.
