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
also depends on existing task state; flash recovery, task execution, certificate
parsing for TLS, and the actual connection remain outside the replay's proof.

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
- `.solix-private/isolated-ap/http-chunk-20260929/`: probe sources, packet capture,
  API/NTP logs, before/after telemetry, response validation, and result summary.

The next discriminating checks are the C1000 hardware comparison when reachable,
the radio's persisted app/account/model fields and initialization errors, and
the TLS credential-loading path before socket creation. Recover C2000 firmware
before assigning it the C1000 startup behavior. Preserve the response framing
and credential checks when testing another hypothesis so results stay comparable.
