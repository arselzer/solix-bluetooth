# Original C1000: Bluetooth bridge charging and bypass test

## What works without station Wi-Fi

On **2026-09-30**, the original **C1000/A1761**, version code **151**, passed
an actual TCP MQTT → Paho client → `MqttBridge` → Bluetooth → fresh telemetry
round trip. The station itself used legacy Bluetooth, with no pairing ID or
Wi-Fi setup. A disposable MQTT 3.1.1 listener ran on laptop loopback only.
This is independent of the Gen 2 isolated AP/native MQTT service.

The supported original-C1000 charging-power domain is **100–1000 W**, in
100 W steps. To use an existing trusted broker:

```sh
solix-link mqtt-bridge --config /path/to/config.json \
  --broker YOUR_BROKER --port 1883
```

For a configured station named `original`, publish a **nonretained** payload:

```text
Topic:   solix_gen2/original/set/ac_charging_power
Payload: {"watts":100}
```

The result topic is `solix_gen2/original/result`. The successful 100 W result was:

```json
{"command":"ac_charging_power","ok":true,"confirmed":{"ac_charging_power_limit_w":100}}
```

The bridge's machine needs Bluetooth range and network access to the broker.
Wi-Fi, charging caps, Time-of-Use and native device MQTT are not prerequisites
for this charging-power control. A timeout still requires inspecting the
station before another attempt. Broker authentication/TLS are configurable;
test payloads and raw device state were retained privately.

## Charging limit versus forced discharge

The test chain was mains → C1000 Gen 2 → original C1000 → expendable HP switch.
Both AC outputs remained enabled throughout. No output-switch command was sent.
The original charging limit changed **1000 → 100 → 1000 W** through MQTT.
Fresh result/readback checks confirmed both writes; three final samples
confirmed restoration and unchanged protected settings on both stations.

| Phase | Original output | Upstream Gen 2 output | Original SOC |
| --- | ---: | ---: | ---: |
| 1000 W baseline | 116 W | 180 W | 100% |
| 100 W limit, four follow-up samples | 115 W | 180–181 W | 100% |
| Restored 1000 W | 115 W | 181 W | 100% |

Thus the charging limit was **below the reported load**, yet the upstream
source continued supplying the chain. It did not establish a switch to
battery-only operation with AC input present. This supports treating the
setting as a charging limit rather than an overall AC-input limiter or
forced-discharge command. The sizeable difference between the two stations'
power readings is unresolved; it must not be converted into a charging rate
or efficiency claim. Their meters were not independently calibrated.

The short, full-battery trial does not exclude a small concurrent battery
current or establish behavior at lower SOC. Original A5 input power and BC
charging-source code remained zero, so these fields cannot independently
prove bypass, missing mains or battery discharge.

## Keeping AC output on while using the battery

The earlier [chain test](c1000-chain-validation.md) removed the original
station's input by disabling only the upstream Gen 2 AC output; the original
kept AC enabled and reported continuing output. This requires removing its
AC input, rather than a verified Gen 1 internal bypass-disable command.

No software-only force-discharge command with AC input present is established
for A1761. The reviewed upstream A1761 MQTT map includes charging power,
timeouts, outputs, light, temperature, fast charge and smart-output modes,
but no mapped reserve, tariff or AC-input-disable control. A missing reference
entry does not prove the firmware lacks an undocumented command. Gen 2 tariff
commands must not be assumed compatible with the original station.

## Evidence and limits

Private scripts, packet captures, MQTT publications, baselines and results are
retained in ignored `.solix-private/c1000-bridge-20260930/`, with owner-only
permissions and an archive/hash manifest. No cloud request or station Wi-Fi
change was made. The C2000 was excluded completely. These observations
verify the bridge transport and stored limits, not charging-rate enforcement
or waveform-level UPS continuity.
