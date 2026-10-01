# Gen 2 radio clock and connection-status routing

## Result and scope

**42 synthetic instruction cases** establish BLE routes to the radio's clock
and connection-status handlers in the public **C1000 Gen 2 A1763 radio
0.3.3.0** image. Both require **function `10`**. The MQTT raw-data callback
takes a different branch for these command numbers and does not invoke either
radio handler.

This extends the [106 direct-handler cases](radio-status-features.md) and
reuses the [RSSI routing harness](radio-rssi-routing.md). It does not add an
SDK getter, access hardware, or establish original C1000/C2000 compatibility.
BLE session keys and cipher state are synthetic established-session fixtures;
pairing and real transport delivery are not tested.

## Exact BLE namespace and replies

| Radio query | Function | Clear command → reply | GCM-marked command → reply |
| --- | --- | --- | --- |
| Radio time and offset | `10` | `004a` → `084a` | `404a` → `484a` |
| Binding/BLE/MQTT status | `10` | `004b` → `084b` | `404b` → `484b` |

Frames use `ff09`, little-endian total length, header `03 00 10`, big-endian
command, payload and outer XOR. The function byte is essential: the SDK's
generic controller `DATA_REQUEST` is `03 00 0f`, while Prime negotiation is
`03 00 01`. Reusing those helpers unchanged would select another namespace.
Controller commands with the same numeric value are not these radio queries.

For both reviewed handlers, an empty payload, raw A1 source `21`, source `22`
with FE timestamp, and an unrelated raw TLV all produce the same observed
reply. The handlers do not consume those input fields. This does not establish
that arbitrary payloads or neighboring commands are safe.

The actual executed route is:

```text
BLE callback 4203fc9a → receiver 4204d14e → queue processor 4204d30e
  → payload parser 4204ffbe → function dispatch 4204feee
  → function 10 handler 4203c9e0 → command table at 3c147be4
  → 004a: 4203fac0 or 004b: 4203da2c
  → response 4204fa68 / frame 4204f846
  → response-port selection 42053ec0 / 4205421a
  → registered BLE wrapper 4203fcf0 → substituted send 42028cf2
```

Application registration and response framing execute actual instructions.
The reply preserves the incoming function/header, sets response bit `08`,
recomputes length/XOR and returns to BLE port `0`. A marked request produces
an encrypted response. None of these BLE cases calls a controller-forwarding
wrapper or UART send builder. Feeding `084a`/`084b` response opcodes back as
requests finds no matching radio query entry and produces no handler reply.

## Handler semantics and side effects survive routing

The field values are **raw TLVs**, without typed-value prefixes:

- **`004b`:** status `00`, A1 length 1 binding observation; A2 length 1 equals
  1 only for stored BLE state 3; A3 length 1 copies cached MQTT byte
  `3fc9036c`. The bound/connected synthetic example is
  `00 a1 01 01 a2 01 01 a3 01 01`. Optional binding-provider output and unknown
  MQTT values remain unnormalized. The query does not ping the broker or
  refresh Wi-Fi association.
- **`004a`:** status `00`, A1 length 4 radio epoch; A2 length 4 negated signed
  timezone offset, both little-endian. Literal timezone `GMT0` still takes
  the sentinel path yielding A2 `+1`. Epoch 0 and `ffffffff` still receive
  successful outer status. Separate time samples can straddle a transition.

The clock route can update the two **volatile transition cache** regions when
their cached year is stale. The replay allows only those six-byte regions in
that case and checks the observed global changes. Other permitted changes
are radio command/repeat bookkeeping and fragment cleanup. Settings and
stored binding/BLE/MQTT inputs remain unchanged. This is not a main-controller
RTC query, tariff-clock synchronization guarantee, or strictly RAM-write-free
handler.

The detailed getter addresses, sentinel interpretation and cache arithmetic
remain documented in [radio status features](radio-status-features.md).

## MQTT is not the BLE route

The native raw callback `42043a18` compares the normalized command with
`003f` **before generic radio parsing**. Both `004a` and `004b` exceed that
cutoff. In eight synthetic cases, varying A1 `21`/`22` and clear/GCM markers:

- The radio executes the branch to forwarding wrapper **`420439cc`**.
- That wrapper receives function `10`, normalized command `004a` or `004b`,
  and the original payload unchanged.
- No clock/status handler executes. No time service is called, and even
  marked ciphertext is not decrypted on this branch.

**The replay captures and substitutes wrapper entry `420439cc`. It does not
execute its send builder, UART transport, any controller firmware, or any
controller interpretation of that frame.** Its evidence is the radio's route
selection, not a successful alternative query or a safe controller request.

These cases begin at the raw callback after native envelope extraction.
The separate RSSI suite covers native JSON admission; it cannot make this
higher-command branch behave like RSSI `0022`. No alternative MQTT route to
these radio handlers is established here, and a live native `004a`/`004b`
probe is not proposed.

## Coverage and reproduction

| Cases | Coverage |
| ---: | --- |
| 16 | Two BLE queries × clear/GCM × four benign payload fixtures |
| 4 | Stored/optional binding inputs and BLE/MQTT status edge values |
| 10 | GMT0 sentinel, invalid epochs, transition sampling and stale-year cache |
| 8 | MQTT higher-command branch, captured before its forwarding wrapper executes |
| 4 | Incoming response opcodes do not invoke the query handlers |

```sh
python3 -m pip install -r tools/firmware_analysis/requirements.txt
python3 tools/firmware_analysis/emulate_radio_status_routes.py \
  --output /tmp/solix-radio-status-routes
```

`SOLIX_FIRMWARE_DIR` or `--image` can select an external copy. The script
requires the 1,482,800-byte radio image with SHA-256
`e291ec115f013953e825cb51b9e457a8731889547ab55b3058640e599e8cfec8`.
Results and source/runtime manifests are under
`tools/firmware_analysis/expected_results/radio-status-routes-*.json`.

OS services, libc, keys/GCM primitives, time/calendar observations, an optional
binding provider and the final BLE send are host substitutes. Every fixture is
synthetic. No private identifiers, phone captures, physical measurements,
network services or persistent backends are used. The subsequent
[live readback check](c1000-radio-readback-validation.md) observes both replies
on the C1000 Gen 2. The SDK now handles function-10 RSSI separately; clock/status
getters still need dedicated decoding and are not added by this route suite.
