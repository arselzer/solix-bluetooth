# Original C1000: Prime AC output and AC Smart validation

Live tests on **2026-10-01**, original **A1761 main 1.7.1**, using the retained
app pairing identity. Radio 0.3.3.0 was previously reported during bootstrap.
The upstream **C1000 Gen 2 main 1.1.4.9** supplied the original; the original
powered the expendable HP test switch. C2000 was not accessed. No Wi-Fi
provisioning, cloud request or firmware change was involved.

## Commands and guarded sequence

| Operation | Prime BLE command | Typed A2 | Fresh result |
|---|---|---|---|
| AC output off/on | `404a` | `01 00` / `01 01` | D7 = `01 00` / `01 01` |
| AC Normal/Smart | `4077` | `01 00` / `01 01` | F8 AC mode byte = 1 / 2 |

Bodies include A1=`21` and FE=`03` + UTC seconds LE32. Changing a Smart
preference does not send an output-switch command. The experiment switched
AC off explicitly before exercising that preference.

Three complete baselines checked original AC on, DC off, Fast off, zero
output timers, eleven protected settings and the whole 21-byte F8. Its
charging ceiling stayed 1000 W. Upstream AC remained on with mains connected,
Standard mode and no Time-of-Use slots. Seventeen protected upstream settings
and D9/A4 were checked throughout, allowing only dynamic LCD activity.

| Stage | Setting writes | Explicit full snapshots | Result |
|---|---:|---:|---|
| Independent packet prototype | 4 | 20 | AC off, Normal, Smart, AC on; whole baseline restored |
| Public SDK repeat | 4 | 20 | Same sequence through standard methods; whole baseline restored |

Each mode had three held samples while original AC stayed off. Only F8's AC
mode byte at offset 2 changed during preference writes; its unknown tail and
other protected settings remained unchanged. Each output write changed only
the intended output preference. Three final checks restored the whole baseline
in each stage. No setter retry or upstream output command was sent.

## Tool support

Original Prime now has **ten verified SDK controls**, including AC output.
The gateway exposes **nine preferences**, including AC Smart. Native MQTT
remains independently limited to eight; this Bluetooth trial does not validate
native AC Smart or native output switching.

```sh
solix-link set-ac-output --name original --enabled off
solix-link set-ac-power-saving --name original --enabled off  # Normal
solix-link set-ac-power-saving --name original --enabled on   # Smart
solix-link set-ac-output --name original --enabled on
```

Record and restore the actual baseline; this example assumes Smart was
originally enabled. AC output uses the SDK/direct CLI and interactive terminal.
The HTTP gateway and original Prime broker bridge do not expose output
switching. AC Smart uses the existing preference routes, with confirmation
in interactive interfaces.

Both new controls require a fresh typed A2 countdown of exactly
`03 00 00 00 00` before and after the write. Cached zero, missing, malformed
or active countdowns fail. AC Smart additionally requires fresh AC output off
in either direction. Full settings/F8 must match except the requested output
or mode byte. Invalid booleans fail before transmission; a failure after
transmission may leave a changed setting. No automatic write retry is made.

## Limits and retained evidence

The [older Smart-policy replay](c1000-smart-auto-off-policy.md) found inherited
inactivity counters. Enabling Smart does not guarantee a new grace period.
This verifies configuration with AC off, not loaded auto-off timing, physical
thresholds, reboot persistence or C1000X behavior. Output readback is telemetry
evidence, without waveform-level measurement.

Scripts, source snapshots, BLE notifications, negotiated session material,
baselines, journals and results remain owner-only in ignored
`.solix-private/ac-smart-20261001/`. Complete archive SHA-256:
`e73a3b6a051b25164661f9fe6bd064697f12882fa56a5922b6162ff6e5ca0fe2`.
Public fixtures and screenshots use synthetic data.
