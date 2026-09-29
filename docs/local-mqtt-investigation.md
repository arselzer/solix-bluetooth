# Native local MQTT investigation

## Status and scope

**Native local MQTT works on the C2000 Gen 2 (main 2.1.6.4).** On 2026-09-29,
correcting provisioning TLV order produced TLS, MQTT subscription, and live
power telemetry on the isolated HA-node network. The station subsequently
reconnected without Bluetooth and answered MQTT status/telemetry requests.
Those initial tests preserved AC output and charging settings. A subsequent
native MQTT trial verified radio connectivity queries and charging-power
changes **1,800 → 1,700 → 1,800 W**, with AC output continuously reported on.
Native Time-of-Use mode/reserve/schedule writes also worked, but an all-day
Peak slot still did not activate a tariff or battery discharge. Original
settings were restored and checked again over Bluetooth.

The packaged Python [MQTT bridge](../python/README.md#local-mqtt-bridge) uses
Bluetooth; native station MQTT currently requires the private lab API/broker
setup. Public native telemetry decoding and request builders are available. Earlier failed trials
below are retained as evidence; the [TLV-order correction](#provisioning-order-fix)
supersedes the empty-service-ID investigation.

On 2026-09-29 we replayed code from the retained **C1000 Gen 2 main update
1.1.4.9, radio application v0.3.3.0**. We then tested an HTTP framing change on
the **C2000 Gen 2**, whose main firmware reports **2.1.6.4**. Its firmware has
not been recovered; C1000 code behavior is not proof of C2000 compatibility.
See [firmware findings](firmware-findings.md) for image integrity and scheduling
gates, and [protocol observations](gen2-protocol.md) for earlier lab trials.

## Credential acceptance and storage

The C1000 response callback at `0x4201c6e0` reads `data.certificate_id`,
`endpoint_addr`, `certificate_pem`, `private_key`, and `aws_root_ca1_pem`.
Certificate and private-key fields use the documented serial-derived
Base64/AES-CBC envelope; the root CA is **plain PEM**.

The marker check at `0x4201b6de` requires a nonempty buffer shorter than
2,048 bytes. It accepts certificate BEGIN/END markers and either PKCS#8
`PRIVATE KEY` or PKCS#1 `RSA PRIVATE KEY` markers. This is a format check;
full cryptographic validation occurs later.

Storage imposes tighter limits. `_aws_mqtt_write_by_type` at `0x42025034`
allocates these slots, and `0x42024fc6` requires a NUL terminator within the
slot and an appropriate END marker:

| Field | Storage slot | Maximum PEM bytes, reserving the terminator |
| --- | ---: | ---: |
| Client certificate | 1,536 | 1,535 |
| Root CA | 1,536 | 1,535 |
| Private key | 2,048 | 2,047 |

Keep generated credentials within these limits; passing the earlier parser
check alone is insufficient. Both retained response fixtures fit. Their
certificate/private-key pairs were also parsed with `cryptography` and matched.
The lab response was successfully decrypted using the serial in the C2000's
actual request. That rules out a mismatched fixture serial in this trial.

### Embedded TLS parser replay

A second offline harness executes the radio's actual mbedTLS certificate
parser (`0x420da982`) and private-key parser (`0x420cef3c`). Both retained
fixtures pass all three parsers: root CA, client certificate, and private key.
The credential getters (`0x4202536a`, `0x420255ec`, `0x4202586e`) also return
the expected PEM bytes and **include the terminating NUL in their lengths**.

There are 24 asserted cases: two fixtures × three fields × direct parsing,
getter readback, missing NUL, and malformed DER inside valid PEM markers.
The 12 valid cases succeed; all 12 malformed cases fail. Heap allocation,
ROM libc/arithmetic, and flash reads are host substitutes. Flash transforms
are omitted symmetrically. The ROM arithmetic addresses were checked against
[Espressif's ESP32-C3 symbol map](https://github.com/espressif/esp-idf/blob/master/components/esp_rom/esp32c3/ld/esp32c3.rom.libgcc.ld).
This checks parsing, not TLS certificate-chain verification or a handshake,
and does not execute C2000 firmware or prove its physical flash contents.

The TLS connector loads and parses credentials **before** calling
`mbedtls_net_connect` (`0x420c2d4c`). In the recovered C1000 implementation:

| Failure before DNS/TCP | Connector return code |
| --- | ---: |
| Random-generator seeding | -16 |
| Missing/unparseable root CA | -19 |
| Missing/unparseable client certificate | -20 |
| Missing/unparseable private key | -21 |

Consequently, absence of broker traffic alone cannot distinguish a startup
gate from a credential-loading failure. The replay eliminates rejection of
these PEM fixtures by the recovered parser, under the stated assumptions.

## HTTP receive behavior

The header parser at `0x42039bdc` looks for `HTTP/`, then parses the numeric
status. Offline execution accepted equivalent HTTP/1.0 and HTTP/1.1 responses.
It handles `Content-Length` or `Transfer-Encoding: chunked`; changing only the
HTTP version is not supported as a fix by this evidence.

The reader at `0x420394f2` has different plain TCP and TLS paths:

- TLS accumulates positive reads until the requested length or a stop condition.
- Plain TCP uses nonblocking receives. After receiving some bytes, an `EAGAIN`
  returns that partial count immediately (`0x42039802`). If no bytes have arrived,
  it waits and retries instead.
- The caller requires the entire requested block. A short positive read exits
  at `0x42017bfc`–`0x42017c1e`, without delivering that block to the JSON callback.

This makes plain HTTP sensitive to a receive gap within a requested body block.
TCP packet segmentation alone does not prove that such a gap occurred in the
application. The earlier capture's 5,303-byte response was sent in body segments
of 1,440 + 1,440 + 1,440 + 983 bytes; all were TCP-acknowledged. Application-level
parsing success cannot be inferred from those acknowledgments.

### Offline replay results

The replay executes original RISC-V instructions with Unicorn 2.1.4. Host
substitutes provide libc, cJSON access, AES/Base64, allocation, flash I/O, socket
reads, and operating-system entry points. Header/body parsing, accumulation,
credential checks, storage validation, and MQTT startup checks run as firmware
instructions. Flash encryption and the MQTT worker/network stack are not run.

| Experiment | Result |
| --- | --- |
| Official and generated lab response, whole or split into 1,024/511-byte callbacks | All six reach MQTT initialization |
| Both responses through storage validation and startup checks | Reach MQTT worker entry point with synthetic identity fields |
| TLS reads split into 1/17/511/1,460/4,096-byte pieces | Reassemble the requested body |
| Plain TCP: 1,460 bytes, then `EAGAIN`, with more data pending | Returns 1,460 of the requested 2,048 bytes |
| Actual fixed-length body loop with a gap after its first or second TCP segment | Exits before completing JSON delivery |
| HTTP chunks of 1,024 bytes with a gap inside a chunk | Also fails |
| HTTP chunks of **one byte** with the same injected gap | Delivers all JSON and reaches MQTT worker entry point |

One-byte chunks are a **diagnostic workaround**, with substantial framing and
callback overhead. A 5,303-byte body occupies 31,823 wire bytes in this form.
Each body read requests one byte, avoiding a positive short count. The terminating
zero chunk invokes callback flag `2`; data chunks use flag `1`. Fixed-length
delivery uses flag `0`, with the remaining total length as the third argument.

## Parsing success does not prove MQTT startup

The response callback sets its status to zero before calling
`aws_mqtt_param_init` (`0x42028484`) and `anker_mqtt_init` (`0x420280a4`).
It does not check their return values. Initialization requires additional
stored configuration:

| Field | Getter | Offset in MQTT parameter structure |
| --- | --- | --- |
| Device app/service ID | `0x4201232e` | `+0x04` |
| Model/product number | `0x42012416` | `+0x24` |
| Device serial | `0x42012450` | `+0x34` |
| Account ID | `0x420122f4` | `+0x54` |

Diagnostic format strings identify the first and last fields as
`device_app_id` and `account_id`, respectively. The app/service ID is distinct
from the owner's account ID. The recovered subscription format is
`cmd/{app_id}/{model}/{serial}/req`; this does not yet establish command payloads.

With an empty app ID, model, or account ID and no saved configuration to recover,
offline initialization returns `0x90004` and never reaches worker startup, while
the response callback still reports status zero. These are reproduced failure
conditions, **not evidence that the live C2000 is missing those values**. Startup
also depends on existing task state; physical flash recovery, task execution,
and the actual connection remain outside the replay's proof. TLS parsing is
covered separately above.

## Bluetooth network diagnostics

Function ID **`0x0f`**, request **`4020`**, response **`4820`** exposes radio
error state. This is separate from the negotiation function ID `0x01` and
from battery/inverter fault reporting. The tested request contains an `A1`
timestamp TLV. A successful reply starts with `00`, followed by:

| TLV | Meaning | Encoding |
| --- | --- | --- |
| A1 | System reboot code | One byte |
| A2 | SDK reset code | One byte |
| A3 | HTTP error code | Signed 32-bit little-endian |
| A4 | Wi-Fi error code | Signed 32-bit little-endian |
| A5 | BLE disconnect code | Signed 32-bit little-endian |
| A6 | MQTT error code | Signed 32-bit little-endian |

The C1000 handler is selected at `0x42046548` and assembles its reply at
`0x420472be`. Four offline executions checked zero and negative MQTT codes.
The handler clears its reporting copies after replying. Reboot/reset codes
became `255` on repeated C2000 reads; preserve the first sample. It reloads
the four error words from their sources on the next request. The MQTT source
at `0x3fc90358` is not cleared by this handler. Other reset-code meanings and
model differences remain unverified.

The Python API provides `await monitor.network_diagnostics()`; the CLI provides
`solix-gen2 network-diagnostics --name ups`. These query radio diagnostics
without changing power/network settings. Do not infer connectivity from zero
codes or use these values as UPS battery faults.

### C2000 comparison with diagnostic sampling

Nine reads on 2026-09-29 succeeded: one baseline, four while the isolated AP
was available without provisioning, and four during a fresh guarded Wi-Fi
setup. The passive trial produced no station traffic. The active trial joined
successfully, requested MQTT information, then binding/check/DST data, and
used local NTP. No MQTT listener event or TCP/8883 packet appeared. The only
captured DNS question was unrelated to the broker.

At approximately 0, 10, 40 and 90 seconds after `4025`, HTTP, Wi-Fi, and MQTT
error codes were all zero; BLE disconnect code was `531`, left uninterpreted.
This is **not evidence of MQTT success**. It is consistent with a failure
before a recorded connection error, but cannot identify the failing stage.
The local API logged `unbind_device` about 2.1 seconds after BLE disconnect,
matching the earlier provisioning-cleanup behavior; this was not a cloud call.

Before/after checks preserved AC input/output on, Standard mode, no tariff,
zero schedule slots, 90%/1% caps, and 1,800 W charging limit. Both trials ended
with AP services stopped and temporary HA credentials removed after archive
hash verification. Only the active trial resent Wi-Fi provisioning; neither
sent output, charging, or schedule controls.

## C2000 hardware comparison

The HA node could see C2000 advertising; a second scan using marketing-name
aliases still found no C1000. The guarded C2000 test required AC input/output on,
Standard mode, no active tariff, and zero schedule slots before provisioning.
It sent only the Wi-Fi setup sequence, without charging, scheduling, or output
control commands. The AP had a separate network namespace and no internet route.

The local responder served HTTP/1.1 with one-byte chunks and the existing lab
credential fixture. Results:

- Wi-Fi join acknowledged `00`; the four expected local API endpoints were hit.
- Packet reassembly recovered the complete 5,303-byte MQTT response, identical
  to the fixture. The binding and DST responses were also chunked.
- There was **no broker DNS query, TCP/8883 packet, or MQTT listener event**.
- `4825` did not arrive during 100 seconds of waiting; BLE stayed connected for
  another 10 seconds before the final telemetry check and disconnect.
- Battery remained 90%, caps 90%/1%, configured charging power 1,800 W, Standard
  mode, no active tariff, and zero slots. AC input and output remained enabled.

The workaround therefore did **not** resolve native MQTT on this C2000.
Changing framing is not a demonstrated charging-control solution. The AP was
stopped, its namespace removed, and temporary HA-node credentials deleted after
the private archive's SHA-256 matched locally. The station may retain the
previously used offline lab SSID and local API endpoint.

## Configuration and connectivity readback

Additional radio queries were traced in the C1000 image and exercised on C2000
on 2026-09-29. Requests used an `A1` timestamp; replies start with status `00`.
These are research probes, not additional public Python methods yet.

| Function / request → response | Reply TLVs | Evidence and caveats |
| --- | --- | --- |
| `10/403c` → `483c` | A1 SSID, A2 encrypted Wi-Fi password, A3 API base URL, A4 app/service ID | Handler `0x4203ed3c`; **contains private configuration** |
| `0f/4027` → `4827` | A1 AP connected, A2 Ethernet connected, one byte each | Handler branch `0x42046554`; Ethernet is a constant zero in this C1000 image |
| `0f/4028` → `4828` | A1 server connected, one byte | Getter `0x42043b84`; C1000 computes AP connected **and** MQTT connected |

`4027` here is in function `0x0f`, distinct from owner registration in function
`0x01`. The `4028` getter starts the existing server-status polling timer: it
does not write network credentials or power settings, but is not side-effect
free. A false server result cannot distinguish missing Wi-Fi from missing MQTT.

The `10/403c` password has an additional firmware encryption envelope inside the
ordinary encrypted BLE reply. Its research receiver handles function `0x10`
and fragmented responses explicitly; the public session decoder currently
handles normal app replies in function `0x0f`. Do not log decoded configuration
to a public issue or expose this query in the read-only monitoring HTTP API.

Nine offline query executions checked synthetic configuration and all four
AP/MQTT state combinations. These execute the original handlers with host
substitutes for configuration access, password transformation, TLV output, and
timer creation; they do not validate physical flash contents or encryption.

### Earlier trial: C2000 app ID remains empty during activation

A standalone read after the previous setup cleanup returned empty SSID,
password, and app/service ID, with the local API URL still present. A second
guarded trial then sampled before Wi-Fi join, after join, and approximately
3, 15, and 50 seconds after `4025`:

- SSID and encrypted password appeared after join; AP status became `1`.
- The API URL remained correct, but app/service ID **remained empty** throughout.
- Server status stayed `0`; HTTP, Wi-Fi, and MQTT error words stayed zero.
- The local API received MQTT-info, bind, binding-check, and DST requests, then
  unbind after BLE disconnect. There was no TCP/8883 traffic or listener event.

This is evidence of an empty **readback field during setup**, not merely its
cleanup afterward. It supports investigating missing initialization data, but
does not prove that C2000 uses the same field for MQTT startup or explain why
it is empty. Its firmware has not been recovered.

The activation field mapping was checked independently in C1000 code. Three
offline executions ran parser `0x420537d8`, adapter `0x42052e48`, and the start
of activation at `0x42049092`, stopping at the configuration setter. `A6` reaches
`device_app_id`; substituting `A5` or omitting the service field leaves it empty.
This agrees with the reconstructed C1000 field mapping, but the host TLV-lookup
substitute did not test actual wire order. It does **not** validate the C2000
parser. A different command, `0f/4038`, uses `A5` for the service ID;
its layout must not be substituted into `4025`. No `4038` write was sent.

The configuration readback is also distinct from `10/403b`, labeled
`charge_set_upgrade_info` in the recovered image. That command has upgrade and
Wi-Fi side effects and was not used as a generic configuration setter.

Both live probes kept AC input/output enabled, Standard mode, no active tariff,
zero slots, 90%/1% caps, and a 1,800 W charging limit. They sent no charging,
output, scheduling, or firmware-update command. The AP was stopped, Wi-Fi
returned administratively down, and temporary HA copies were deleted after
archive hash verification. No request went to Anker's official API.

## Provisioning order fix

The recovered C1000 byte parser at `0x4204f9a6` scans candidate TLV tags in
ascending order. After consuming a tag, it never returns to smaller tags.
Our `4025` builder emitted `A1 A2 A3 A4 C3 A6 A7 A8`, so the parser skipped
the service ID, model, and IANA timezone following `C3`. Correct order is:

```text
A1 A2 A3 A4 A6 A7 A8 C3
```

Three additional offline executions run the actual byte parser/getter and
activation path, instead of substituting TLV lookup. The old order leaves the
service ID empty; sorted fields reach the setter with `anker_power`; omitting
the opaque `C3` field also succeeds. Its meaning remains unknown. The original
phone plaintext was reconstructed from inferred keystream bytes, so neither
`C3` nor its apparent placement is a verified requirement of the official app.
The Python builder preserves that field at the end and tests ascending order.

Changing only field order on C2000 made configuration readback report the
11-byte service ID. About 30 seconds after `4025`, the station established
TLS 1.2, sent MQTT CONNECT, subscribed, and published telemetry. The local API
also saw `/equipment/agreement/get_device_point_switch` and
`/equipment/devicemanage/update_info`. Early server-status reads at roughly
3 and 15 seconds were still zero, before the successful MQTT connection.

BLE disconnected before the final in-session check; its cause is unproven.
A fresh BLE session subsequently confirmed saved configuration and unchanged
AC input/output, Standard mode, no tariff, zero slots, 90%/1% caps, and 1,800 W
charging limit. The API used the prior one-byte HTTP chunks and lab-generated
credentials. This trial establishes the order fix without showing whether
the HTTP workaround is still necessary on this C2000.

## Native telemetry and read requests

A second trial restarted the same isolated AP/API/broker **without Bluetooth
or provisioning**. The station reconnected after about 80 seconds in this
observation, using saved credentials, and did not repeat MQTT-info/bind calls.
All decoded samples retained the baseline power settings. The AP namespace
had no default route; no official API request was made.

| MQTT topic | Observed purpose |
| --- | --- |
| `cmd/anker_power/A1783/{serial}/req` | Station command subscription |
| `dt/anker_power/A1783/{serial}/param_info` | Power-station telemetry |
| `dt/anker_power/A1783/{serial}/state_info` | Radio/network information |

Messages have an outer JSON `head` and a **JSON string** in `payload`. Telemetry
payloads contain `pn`, `sn`, and Base64 `data`. The decoded data is an ordinary
SOLIX packet with pattern `03010f`, command `0421`, and the existing TLV/XOR
framing. It is not encrypted with the BLE session key. MQTT transport is TLS.
`state_info.battery` reported 100 while the station reported 90%: use the `A5`
power telemetry field for UPS charge, not that radio field. Raw TLVs can
contain serial numbers and must remain private.

Two fixed read requests were sent only after confirming the safe baseline:

| Request → response | Request TLVs | Result |
| --- | --- | --- |
| `0100` → `0900` | `A1=22`, `FE=03` + Unix seconds LE32 | Status `00` followed by full telemetry TLVs |
| `0057` → `0857`, then `0421` | `A1=22`, `A2=0101`, `A3=03` + duration LE32, `FE` as above | Acknowledgment, then fresh telemetry roughly every three seconds; requested 60 seconds |

The outgoing SOLIX pattern is `03000f`. The independently built JSON envelope
uses head `cmd=17`, `cmd_status=2`, `sign_code=1`, version `1.0.0.1`, client/session
IDs, sequence, seed, timestamp, `device_pn`, and `device_sn`; the inner JSON
string carries `device_sn`, `account_id`, and Base64 `data`. The test used the
privately retained account ID. Whether an arbitrary/local account ID works
for native MQTT is **not established**. This is a verified envelope, not a
claim that every field is required. No output, charge, or schedule write was sent.

`solix_gen2.decode_mqtt_telemetry` decodes `0421` reports and successful `0900`
status replies, checks framing/checksum, and optionally filters the serial.
It does not connect, provision, or determine freshness. The lab listener is
a minimal MQTT protocol probe, not a production broker or supported service.

### Client certificate verification

The first two trials did not request a client certificate (`CERT_NONE` on the
server). A third restart loaded only the generated lab CA and required a
client certificate (`CERT_REQUIRED`). TLS 1.2 succeeded with a peer certificate
present, followed by MQTT subscription, status response, and the requested
telemetry stream. Fifteen readings, including the status reply, decoded with
unchanged baseline settings. This verifies a usable saved client certificate
and key under the lab CA; negative tests for station-side hostname/CA checks
were not performed. The broker used only lab-issued TLS credentials.

All temporary AP/API/NTP/broker services were stopped afterward. Wi-Fi returned
to the HA host's normal namespace, administratively down, and copied lab
credentials were removed after the private archive hash matched locally.

## Connectivity queries and native charging-power control

A follow-up trial on the same C2000/main 2.1.6.4 queried the radio through its
native MQTT command topic, using the ordinary `03000f` packet pattern:

| Request → reply | Payload and result |
| --- | --- |
| `0027` → `0827` | Request `A1` is raw Unix seconds LE32; reply `00a10101a20100`: AP connected, Ethernet disconnected |
| `0028` → `0828` | Same timestamp field; reply `00a10101`: server connected |
| `0101` → `0901` | `A1=22`, `A4=02` + charging watts LE16, `FD=00` + ASCII Unix milliseconds; successful acknowledgment and telemetry confirmation |

The native radio query IDs are the low 12 bits of the previously tested BLE
`4027`/`4028` requests. Their `A1` timestamp differs from the application target
marker used in power commands. The server-state query can start the firmware's
status polling timer, as described above; it does not change power settings.

The charging trial first resent the current 1,800 W value, changed to 1,700 W,
waited five seconds, and restored 1,800 W. Each write received `0901` success
and a fresh `0100`/`0900` readback with the requested limit. Its only setting
field was `A4`: no AC output switch, timer, or mode field was sent. Battery
remained at its 90% cap and idle, so this proves native **setpoint control**,
not measurement of charging current at that setpoint. AC input/output remained
enabled, mode Standard, tariff none, and caps 90%/1% throughout.

`NativeMqttCommands` builds status, telemetry-stream, and charging-power
requests for C2000. Publish them with `retain=False` and confirm changes from
fresh telemetry; construction or MQTT delivery alone is not confirmation.
The builders are separate from connection/provisioning and the existing BLE
bridge. The public charging range matches the verified BLE range; native
hardware changes tested here were 1,700 and 1,800 W.

### Bluetooth coexistence and recovery

An attempted BLE readiness query could not discover the C2000 while MQTT was
connected, so the first guarded control attempt sent no charging write. After
the AP stopped, a fresh BLE session succeeded and confirmed unchanged power
settings. This is an observed availability change, not proof that the firmware
always excludes concurrent BLE/Wi-Fi operation. Recovery scripts now stop the
lab AP before attempting Bluetooth restoration if native MQTT fails.

One earlier harness error also stopped before sending commands: a method named
`request` collided with `socketserver.BaseRequestHandler.request`, the socket
attribute. That method was renamed and synthetic outgoing frames checked before
retrying. Both interrupted attempts and their captures were retained privately.

## Time-of-Use with MQTT connected

After the charging-power trial, a separate fixed sequence used native `0090`
and confirmed `0890` acknowledgments plus fresh status replies:

1. Verify AP/server status both `1`, Standard mode, tariff none, battery 90%
   and idle, reserve 10%, empty schedule, caps 90%/1%, and charging limit 1,800 W.
2. Set reserve **10 → 85%** (`A5=0155`).
3. Set mode **Standard → Time-of-Use → Standard** (`A2=0101/0100`) with no slots.
4. Install one Peak slot covering **00:00–24:00** in Time-of-Use mode.
5. Restore Standard, clear the schedule, and restore reserve **85 → 10%**.

All `0090` requests use `A1=22` and `FD=00` plus ASCII Unix milliseconds.
The Peak fields were `A2=0101`, `A3=0100`, `A4=0100`, `A6=0104`,
`A7=0401010018`; `D9[7:11]` confirmed count/slot `01010018`. The baseline
schedule parameter was **0**, so the clear request restored `A6=0100` and
`A7=0400`. An initial precheck expecting parameter 4 stopped without writing;
the retry and both restoration paths were corrected to the actual baseline.

**Result:** 12 Peak-period samples spanning 25.8 seconds still reported tariff
`none`, battery `idle`, and mains present. AC output stayed enabled, supplying
approximately 881–1,573 W during those samples. This establishes native
schedule storage and restoration, **not working scheduled discharge**. The
native radio reported a connected server before the writes, so missing MQTT
alone no longer explains the inactive tariff on this C2000.

Final native readback and a fresh BLE session after AP shutdown confirmed
Standard, no tariff, reserve 10%, parameter 0, zero slots, caps 90%/1%, charging
limit 1,800 W, and AC output on. The complete sequence, including the failed
precheck, is retained privately. No AC output-control request was sent.

### Remaining binding/HTTP question

The C1000 bind-response callback at `0x4201c31c` parses each delivered body
buffer directly; it does not have the MQTT-credential callback's accumulator.
An offline five-case replay compared a whole fixed-length body, one whole HTTP
chunk, one-byte chunks, a two-chunk split, and invalid JSON. The complete bodies
reached the binding-success notification. Splitting a JSON object into two
incomplete pieces did not. One-byte delivery is more subtle: the standalone
digit `0` also reached a success notification, because the original common
checker returns zero while writing a missing-code error through its separate
output parameter. The callback tests the return value, then returns the error.

This uses host cJSON/libc/OS substitutes and NUL-terminated callback buffers;
it does not establish the actual C2000 binding state. In particular, it does
**not** prove that one-byte chunks prevented binding. It does show why the
large MQTT-response workaround must not be assumed suitable for every endpoint.
The subsequent [whole-body binding follow-up](c2000-binding-followup.md)
successfully used complete small binding/DST replies. It did not establish a
new readiness transition: all 54 retained C2000 status records, including the
earlier unsuccessful Peak trial, already contained A1=`34`, the ready value
in the recovered C1000 status builder. Clock propagation, the separate
power-state gate, and C2000-specific schedule semantics remain unresolved;
internal binding flags were not forced.

## Retained evidence and next checks

Private artifacts remain ignored and must not be published:

- `.solix-private/firmware-analysis/emulate_mqtt.py` and
  `mqtt-emulation-results.json`: 29 offline replay cases. Run with Unicorn and
  `cryptography` available, plus the retained radio image and response fixtures.
- `.solix-private/firmware-analysis/mqtt-http-packet-metadata.json`: earlier
  response segmentation metadata, without payloads.
- `emulate_tls.py` / `tls-emulation-results.json` in the same directory:
  24 embedded-parser/getter checks, including negative controls.
- `emulate_diagnostics.py` / `diagnostics-emulation-results.json`: four
  diagnostic-handler executions; `diagnostic-live-c2000-20260929.json` retains
  the first hardware reading and its power-state baseline.
- `.solix-private/isolated-ap/http-chunk-20260929/`: probe sources, packet capture,
  API/NTP logs, before/after telemetry, response validation, and result summary.
- `.solix-private/isolated-ap/diagnostic-mqtt-20260929/`: passive and active
  comparison archives, sampled diagnostics, packet captures, and settings checks.
- `emulate_network_readback.py` / `network-readback-emulation-results.json`:
  nine configuration/connectivity query checks; `probe_network_readback.py` and
  `network-readback-ha-results.tar.gz` retain the standalone C2000 readback.
- `emulate_activation.py` / `activation-emulation-results.json`: three synthetic
  C1000 activation-field checks, with OS, identity, and TLV-lookup substitutes.
- `.solix-private/isolated-ap/config-readback-20260929/`: guarded active readbacks,
  full notification capture, API/NTP logs, packet capture, and baseline checks.
- `emulate_activation_wire.py` / `activation-wire-emulation-results.json`:
  three actual byte-parser executions exposing the ordering error.
- `.solix-private/isolated-ap/ordered-activation-20260929/`: corrected setup,
  TLS/MQTT capture, and fresh BLE verification archives.
- `.solix-private/isolated-ap/native-mqtt-20260929/`: passive reconnect,
  MQTT status/stream requests, raw envelopes, and unchanged-setting checks.
- `.solix-private/isolated-ap/native-mtls-20260929/`: repeated passive trial
  with client-certificate verification, 15 decoded readings, and packet/API logs.
- `.solix-private/isolated-ap/native-control-20260929/`: interrupted attempts,
  BLE recovery readback, native connectivity replies, and confirmed charging
  limit changes/restoration; packet captures and inputs accompany every attempt.
- `.solix-private/isolated-ap/native-tou-20260929/`: guarded native schedule
  trial, inactive-tariff observations, full restoration and final BLE readback.
- `emulate_binding_chunks.py` / `binding-chunks-emulation-results.json`: five
  bind-callback delivery cases, including the unexpected scalar-JSON behavior.
- `.solix-private/isolated-ap/whole-small-replies-20260929/`: complete small
  API replies, controller-readiness investigation, and recovery captures.
- `emulate_telemetry_ready_prefix.py` and `emulate_mains_field.py`: twelve
  status-prefix and 32 mains/output-field cases with the recovered C1000 code.

Continue tariff investigation through the separate power gate, controller
clock, and C2000 schedule semantics, with baseline/restore checks and AC
output on. The [binding follow-up](c2000-binding-followup.md) records the
completed small-response trial and limits of readiness interpretation.
The Python tool now packages local credential/bootstrap handling, isolated AP
management, NTP and a native MQTT TLS endpoint; see
[isolated AP/MQTT setup](isolated-ap-mqtt.md). Subsequent successful reconnects,
the live `0089` query and longer unsuccessful Peak trials are recorded in
[reconnect and tariff findings](c2000-mqtt-reconnect-and-tariff.md).
Repeat on C1000 when reachable; its native connection remains unverified.
Separate experiments can minimize HTTP framing and account-ID requirements.
