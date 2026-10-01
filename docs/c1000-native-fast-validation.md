# Original C1000: native MQTT Fast validation

Live tests on **2026-10-01**, original **A1761 main 1.7.1 / radio 0.3.3.0**,
using its existing app account and pairing identity. The station used the
owned isolated AP/native MQTT endpoint on the HA node. No route to the LAN
or internet, cloud request, firmware update, or C2000 connection was involved.

## Independently verified native command

Native **`005e`** writes A1=`22`, A2=`01 00` or `01 01`, and FE=`03` + UTC
seconds LE32. Its nominal response is **`085e`**, with no response alias.
Original E5=`01 value` supplies the fresh retained-flag readback. This is
separate evidence from the [earlier Prime Bluetooth trial](c1000-prime-fast-validation.md).

The experiment required three complete fresh baselines: SOC **100%**, AC
enabled, DC disabled, Fast off, zero AC/DC countdowns, all eleven protected
preferences and the whole **21-byte F8**. Original charging power was
**1000 W**; independent upstream C1000 Gen 2 baseline checks showed its AC
input/output connected/enabled, SOC 100%, Standard mode and a small test load.

| Trial | Setting writes | Explicit complete snapshots | Result |
|---|---:|---:|---|
| Native prototype | 2 | 20 | Off → on, four held samples, off; whole baseline restored |
| Public SDK repeat | 2 | 16 | Same round trip without packet overrides; whole baseline restored |
| Independent final Prime BLE audit | 0 | 3 | All eleven original preferences and complete F8 matched the initial BLE baseline |

The final held samples were about **14.8/15.3 seconds** after each enable
attempt. Each setter was published once; no setting retry or output-switch
command was sent. The prototype checked two complete status confirmations per
write. The SDK performs its own fresh baseline plus **two complete status
confirmations** per setter, additional to its sixteen journal snapshots.
Unrelated preferences and every F8 byte remained unchanged throughout.

Both AP cleanups preserved host routes, firewall and forwarding state,
removed the namespace, and returned the dedicated adapter without addresses.
The existing same-SSID profile was reused. Initial startup failure was saved
separately: a missing copied response file stopped the worker before any
station setting write; correcting the local deployment file allowed the tests.

## Tool support

Original Prime BLE and native MQTT now have **eight independently verified
controls**. Fast is available through `NativeMqttCommands.fast_charge(bool)`,
`LocalMqttServer.set_fast_charge_enabled(bool)`, the AP service, its HTTP gateway,
CLI, terminal/browser controls and the Home Assistant gateway switch:

```sh
solix-link ap-service-set-fast-charge \
  --directory /path/to/private/ap-service --name original --enabled on
# Restore the recorded original preference:
solix-link ap-service-set-fast-charge \
  --directory /path/to/private/ap-service --name original --enabled off
```

Controls require explicit `--allow-control`. The native model uses its own
opcode and eleven-setting/F8 guard, rather than the Gen 2 settings structure.
An optional ACK cannot replace fresh telemetry; a negative response remains
a failure even if a later success ACK arrives. Retained, wrong-device, partial
and unrelated-command reports cannot satisfy a fresh status request. Original
mains/tariff fields are not invented to satisfy Gen 2 UI checks. C2000 Fast
and original output-switch/AC Smart commands remain excluded.

## Charging-rate and persistence limits

This verifies the Fast **flag**, transport, retention during the short trial,
and restoration at full SOC. It does not establish faster charging watts,
safe source capacity, reboot persistence or generated-identity original setup.
Use an adequate AC supply; the earlier BLE input-loss trial showed that removing
input clears the flag. A timeout after sending can mean the setting changed;
inspect fresh status before another attempt. Do not automatically re-enable it.

The [separate 1.5.9 controller-to-DSP replay](c1000-fast-current-limits.md)
shows two reasons to avoid assuming that Fast means a fixed high input rate:
its current allowance depends on cached charging stage and temperature, and
it can bypass the saved normal charging ceiling. Those paths still need
independent physical confirmation on installed 1.7.1.
The separate [normal charging-rate trial](c1000-charging-rate-validation.md)
retained successful setting restoration but found no stable below-full
comparison window; it supplies no Fast-rate validation.

## Retained evidence and verification

Owner-only profiles, certificates, native publications, packet captures, BLE
notifications, scripts, baselines and journals remain in ignored
`.solix-private/native-fast-20261001/`. Failed startup and successful data were
retained separately. Complete private archive SHA-256:
`b0d39bd3aacb3d64aa0fe00afb4f836d86e9d88eb2a886ae595cf9808596eda6`.
Public examples and tests use synthetic identities only.

Packet tests verify the original opcode, strict boolean domain, UTC type and
absence of output fields/aliases. Actual loopback TLS tests cover both Fast
directions, absent ACKs, ignored writes, protected-setting/F8 changes and sticky
negative responses; each test requires a single setter publish. Other models
retain their existing controls and rejection rules.
