# C1000 Gen 2: native radio application-state validation

## Scope and result

On **2026-10-02**, one read-only native MQTT query succeeded on **A1763
C1000 Gen 2, main 1.1.4.9 / radio 0.3.3.0**. It returned:

| Selected field | Raw value |
| --- | --- |
| `bluetooth_application_state` | 0 |
| `wifi_application_state` | 1 |

These are radio application bytes, not a physical advertising test or an
account-free setup result. The [initialization/producer replay](radio-ble-initialization-activation.md)
finds connection-state writers for the BLE byte; advertising enable leaves it
unchanged. No `0024` BLE-enable command was sent.

## Exact transport and matching

The request uses **pattern `030010`, opcode `0003`, empty body**. It contains
neither the controller source tag A1=`22` nor an inner timestamp. The existing
native JSON-string envelope retains the configured account/serial and head
`cmd=17`, `cmd_status=2`. Reply matching requires **`030010 / 0803`**, the
configured device identity and an active serialized request. Controller
`DATA_RESPONSE` packets continue through their existing handling.

The radio response cannot acknowledge a controller request or refresh controller
telemetry. Retained, mismatched and unsolicited radio packets are excluded.
Replies do not echo the request sequence/session in the executed firmware
builders; exact pattern/opcode matching cannot distinguish a delayed identical
reply from a new one. This trial used one query, without retries.

The decoder requires success status, complete nonduplicated raw TLVs and
exactly one byte each for A1/A2. It returns only those two integers. The
[query-provider audit](radio-native-info-queries.md) identifies private configured
network text in A4 and unresolved optional cached text in A8; neither enters
the returned result, HA diagnostics or public fixtures.

## Baseline, confirmation and wire audit

The SDK acquired complete fresh controller settings before and after the
query. The full A4 record was unchanged apart from naturally changing display
activity; D9 was unchanged apart from its leading runtime bytes. Outputs and
mains-presence matched, and output countdowns did not increase.

Three subsequent gateway samples over approximately 15 seconds showed all
three stations fresh, with AC enabled and protected settings unchanged.
The bounded private wire capture contained:

| Station | Requests | Setting writes |
| --- | --- | --- |
| C1000 Gen 2 | Five `0100` status requests; one `0003` query | 0 |
| Original C1000 | Two `0040` status requests | 0 |
| C2000 Gen 2 | Three `0100` status requests | 0 |

No identity, network, pairing, charging or output command was sent. Reported
output state is not an independent electrical-continuity measurement. Raw
MQTT responses and operational profiles remain in ignored private storage.

## Operator access and guards

```sh
solix-link ap-service-wireless-state \
  --directory /path/to/private/ap-service --name c1000_gen2
```

`NativeMqttCommands.wireless_state()` builds the frame;
`LocalMqttServer.wireless_state()` performs fresh baseline/confirmation and
returns the selected fields. The local Unix-socket command works with controls
disabled and rejects setter arguments. It is not an HTTP/HA control capability.

The guarded operation supports C1000 Gen 2 only and requires fresh exact
main/radio versions **1.1.4.9 / 0.3.3.0** before sending the radio request.
Original/C2000 support is unverified; matching radio version labels alone are
insufficient. Function `10/0002` is excluded: its providers can initialize
Wi-Fi or return stale scratch with success status.

Focused tests cover framing, namespace correlation, freshness isolation,
malformed/private fields, firmware/model checks and protected-state changes.
The full release gate passed **2,433 Python/HA tests**. Deployment details are
in [HA runtime validation](ha-runtime-validation.md#read-only-native-radio-query-update).
