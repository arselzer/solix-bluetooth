# Native local MQTT investigation

## Status and scope

**Native station MQTT has not connected to the local broker.** The working
Python [MQTT bridge](../python/README.md) uses Bluetooth to communicate with the
station. The investigation here concerns the station's own Wi-Fi MQTT client,
which may satisfy the network-readiness gate for Time-of-Use operation.

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

The next discriminating checks are the C1000 hardware comparison when reachable,
the radio's persisted app/account/model fields, worker/task state, and actual
credential storage. Recover C2000 firmware
before assigning it the C1000 startup behavior. Preserve the response framing
and credential checks when testing another hypothesis so results stay comparable.
