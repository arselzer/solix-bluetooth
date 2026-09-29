# C2000 local binding callback follow-up

## Scope and observed result

Tested on 2026-09-29 with C2000 Gen 2 main firmware **2.1.6.4**. The station
joined the existing isolated access point and used the local API and MQTT
broker. The network namespace had no default route. No official Anker API
request was made.

Small API responses now use `Content-Length` and one complete JSON body. Only
the large `get_mqtt_info` certificate response retains the previously verified
one-byte HTTP chunk workaround. This change addresses the recovered C1000
radio binding callback, which parses each delivered body separately. A single
server write still does not guarantee a single callback on the station.

The C2000 requested, and received local success responses for:

- `get_device_point_switch` and `update_info`;
- `get_mqtt_info` (5,303 response bytes, one-byte chunks);
- `bind_device` and `check_relate_bind_device` (313 response bytes each,
  complete bodies);
- `dst` (97 response bytes, complete body).

Two TLS 1.2 connections supplied client certificates accepted by the local
broker. Native MQTT status, telemetry subscription, and connectivity queries
worked. `0027` reported access point connected / Ethernet disconnected;
`0028` reported server connected. Fourteen decoded power telemetry samples
matched the recorded baseline.

**This confirms transport and ordinary local binding API exchanges. It does
not establish that the main controller's binding gate changed, or that
Time-of-Use discharge now works.** No charging, mode, reserve, schedule, timer,
or output setting was written during this follow-up.

## Reproduction sequence and recovery

1. Establish a Bluetooth session from the HA node while the isolated access
   point is absent. Record telemetry and saved network configuration.
2. Require the baseline: mains present, AC output enabled, Standard mode, no active
   tariff, zero schedule slots, schedule parameter 0, reserve 10%, charging
   limits 90% / 1%, and AC charging power 1,800 W.
3. Keep that session open while starting the isolated access point, API, and
   existing NTP service. Resend the existing `4024` network credentials and
   ascending-tag `4025` local endpoint/service configuration.
4. Disconnect Bluetooth, then start the MQTT broker. Its outgoing command
   allowlist is exclusively `0100`, `0057`, `0027`, and `0028`.
5. Stop the access point and services, then obtain a fresh Bluetooth reading.

The initial attempt sent `4024` while the access point was absent. Its
acknowledgment timed out; the guard aborted before `4025`. The final reading
showed unchanged settings. The corrected sequence received `4024` success.
`4025` itself did not return a timely BLE acknowledgment, but the subsequent
credential and binding requests independently demonstrated API activity.

The first Bluetooth read immediately after the successful MQTT trial could
not discover the station. A delayed read succeeded and independently
confirmed every guarded setting, including AC output on. All temporary
services and the namespace were removed; the Wi-Fi interface was left
administratively down. Private input versions, raw notifications, packet
capture, HTTP/MQTT logs, and failed attempts were retained. Remote temporary
credentials were removed after local archive hashes matched.

## Firmware lead: a controller readiness query

Offline inspection of the recovered **C1000 Gen 2 1.1.4.9 main controller**
found an app-function `0f` status handler:

- Dispatch entry at `0x08032ec8`: command `0089`, handler `0x0800b42c`
  (Thumb function pointer `0x0800b42d`).
- It calls `0x0800d1d8`, the same readiness predicate used by the tariff
  selector. That predicate requires a nonzero binding byte at
  `0x20000770 + 0x3f` and bit 1 in the cached network flags at `+0x46`.
- Reply `A1` is `34` when that predicate passes and `31` otherwise.

The 42-byte reply is structured as follows; byte values are hexadecimal:

| Field | Source or value |
| --- | --- |
| Status | `00` |
| `A1` | `34` if ready; otherwise `31` |
| `A2` | `01` type prefix plus byte at `0x200001eb` |
| `A3` | `01` type prefix plus byte at `0x200001ea` |
| `A4` / `A5` / `A6` | `01 28` / `01 0f` / `01 0f` |
| `FD` | `00` prefix plus 15 bytes from the context at `0x20000770` |

The meaning of `A2`/`A3` and the opaque `FD` value needs confirmation. Adjacent
firmware labels describe an app ID field; do not assume `FD` is a timestamp
or publish its contents.

Twelve offline cases executed the real handler and readiness predicate,
varying the binding byte through 0/1/2 and cached flags through 0/1/2/3.
All matched the rule above. Allocator, memory copy, and response transport
were substituted. The handler wrote only its stack and response buffer;
the replay did not emulate the entire transport stack or real hardware.

### Earlier telemetry already carries a readiness indicator

The recovered C1000 `0100` response handler calls serializer `0x080224a0`.
That serializer uses the same readiness predicate to select the telemetry
`A1` prefix: `34` when ready, `31` otherwise. A separate replay confirmed
this behavior for the same twelve combinations of binding and cached flags.

All 54 retained C2000 native status records examined from the preceding
charging-power, Time-of-Use, and complete-small-response experiments already
have `A1=34`. This includes the unsuccessful all-day Peak experiment. **If
the C2000 uses the same meaning, its controller readiness gate was already
satisfied during that test.** Complete small API replies must therefore not
be described as a demonstrated fix for the missing discharge behavior.
Other gates, including the controller's power-state condition and clock
handling, remain relevant; C2000 firmware equivalence is still an inference.

The retained C2000 Bluetooth baseline before this trial has `A1=31`, as does
the final Bluetooth reading after the access point stopped; live MQTT
readings have `A1=34`. AC output remained on in all three stages. This is
consistent with the recovered readiness semantics, although the comparison
also changes transport and does not inspect C2000 controller memory directly.

### Bounded follow-up and next observation

A separate attempt restarted only the isolated access point and local
services, using the station's saved configuration. The C2000 did not
associate during the four-minute reconnect window. Consequently, no MQTT
connection, API exchange, or `0089` request occurred. There was no additional
provisioning or alternate command attempt. The access point was stopped and
a fresh Bluetooth reading immediately confirmed the full baseline, AC output
on, and `A1=31`. The failed reconnect capture and inputs were retained;
temporary remote files were removed after matching archive hashes.

After a new baseline, compare this status with native radio `0028` while MQTT
is connected. Expected framing, inferred from the dispatch table and existing
app protocol, is native pattern `03 00 0f`, command `0089`, `A1=22`, and
`FE=03` plus Unix seconds in little endian. The expected response is `0889`.
The corresponding encrypted BLE commands would be `4089` / `4889`.

Neither form was sent during this passive follow-up. Its firmware image
has not been recovered, so handler compatibility and interpretation remain
unverified there. This query could distinguish radio MQTT connectivity from
the controller gate without writing internal flags or changing a power mode.

See [local MQTT investigation](local-mqtt-investigation.md) for the preceding
native charging-power and Time-of-Use experiments.

### Subsequent successful native query

The [reconnect and tariff follow-up](c2000-mqtt-reconnect-and-tariff.md) later
recovered native MQTT twice after AP interruptions and successfully sent
`0089` to the C2000. Its `0889` response contained success `00`, `A1=34`,
typed `A2=01 01`, `A3=01 00` and the expected constant fields. The opaque `FD`
identifier remains private. This verifies command compatibility and the
observed ready prefix; it does not establish all field meanings. Longer Peak
trials with more reserve headroom still left the tariff inactive. The verified
local connection is now available through the [Python lab tool](isolated-ap-mqtt.md).
