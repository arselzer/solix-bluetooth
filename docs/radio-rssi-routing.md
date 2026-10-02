# Gen 2 radio RSSI: BLE and native MQTT routing

## Scope and result

The public **C1000 Gen 2 A1763 radio 0.3.3.0** image contains a separate RSSI
query reachable through **function `10`, command `0022`**. This audit executes
its BLE and native MQTT routes, reply framing, and native reply JSON/topic
builders in **71 synthetic RISC-V replay cases**. No device was queried.

This extends the [ordinary telemetry audit](gen2-normal-feature-audit.md):
the direct query preserves failure status, unlike the cached A3 quality byte
that can report 100 after an RSSI failure. The subsequent
[C1000 Gen 2 live check](c1000-radio-readback-validation.md) validates its
unavailable response and adds an explicit Python/CLI getter. This offline
audit is not evidence for another model with the same radio version text.
The later [native MQTT trial](c1000-gen2-native-rssi-validation.md) confirms
one `030010/0022` round trip at −42 dBm with fresh protected settings unchanged.
The private operator CLI exposes that explicit read-only query.

## Exact command namespace

Application startup `420487e4..420487f6` registers function `10` with
`4203c9e0`. Its table at `3c147be4` maps `0022` to RSSI handler `4203cb26`.

| Purpose | Function byte | Command |
| --- | --- | --- |
| This radio RSSI query, clear payload | `10` | `0022` |
| This radio RSSI query, GCM-marked payload | `10` | `4022` |
| Existing SDK Prime time/timezone negotiation | `01` | `4022` |
| Existing SDK generic `DATA_REQUEST` helper | `0f` | Depends on controller command |

The function byte is essential. Neither the generic native `_request` helper
nor the existing handshake `4022` produces this RSSI request unchanged.

The replay constructs frames as:

```text
ff 09 <total length LE16> 03 00 10 <command BE16> <payload> <outer XOR>
```

An empty payload, raw A1 source `21`, and source `22` plus a typed FE timestamp
all reach the same handler. The handler does not inspect those fields. This
does not mean that other commands ignore their sources or timestamps.

## BLE route and reply

The actual BLE callback `4203fc9a` passes complete data to the protocol receiver
as port `0`. The replay then executes outer validation, queue consumption,
payload parsing, function dispatch, the radio command table and RSSI handler.
Normal clear and GCM cases are included; negotiated session state and GCM
primitives are explicit synthetic substitutes. Pairing and the BLE stack are
outside this audit.

The RSSI wrapper `42011522` obtains a signed byte from the ESP AP-info result.
On service failure it returns zero. Handler `4203cb26` returns:

| Observation | Plain response body |
| --- | --- |
| Nonzero signed RSSI | `00 a1 04 <signed RSSI LE32>` |
| Zero or AP-info error | `01` |

The `04` after A1 is a **length**, not a typed-value prefix. For example, a
synthetic observation of −70 gives `00 a1 04 ba ff ff ff`.

Response routine `4204fa68` and single-frame builder `4204f846` preserve the
incoming header/function and set response bit `08`: command `0022` becomes
`0822`; `4022` becomes `4822`, with the body encrypted for the marked request.
The outer length and XOR are recomputed by actual firmware instructions.

For these cases, the parser supplies route-kind/detail bytes `0/0` at context
offsets `632/633`. Wrapper `42053ec0` sees the response bit and immediately
uses `4205421a` to look up the original port. Actual startup registration
selects BLE callback `4203fcf0`, which reaches the substituted BLE send boundary
`42028cf2`. The replay observes no controller forwarding or UART send.

## Native MQTT admission and response

The replay also starts at subscription callback `42027f5e`, with a synthetic
initialized connection, configured device serial and account. It executes:

```text
subscription callback 42027f5e
  → receive event 42027c90
  → header unpack 4203b120
  → remote authorization 42026cf6 / identity check 4203b4f4
  → common command17: payload.data base64 decoding
  → registered raw callback 42043a18
  → protocol receiver as port5 → function10/0022 → RSSI handler
```

Actual startup fragment `42048500..42048542` and registration routine
`42028022` install the raw callback. Its `0022` command is below the `003f`
cutoff, so it takes the normal radio parser path rather than the higher-command
controller-forwarding branch described in the
[factory routing audit](radio-factory-routing.md).

The existing native envelope structure applies: head `cmd:17`,
`cmd_status:2`, and a JSON-string payload containing `device_sn`, `account_id`
and base64 `data`. Matching identifiers are required. Missing, different or
prefix-only payload identifiers reject before the RSSI observation. Missing
required header fields and selected nonrequest `cmd_status` values also reject.

Important limits of that finding:

- The selected branch reads `sess_id`, `client_id`, `msg_seq`, `cmd`,
  `cmd_status`, `timestamp` and `version`. A different client label is accepted;
  the checked account/serial come from the **payload**.
- `sign_code` is not read on this branch. This is not an audit of MQTT TLS,
  broker authentication, subscription permissions or session establishment.
- An older timestamp logs an old-message warning but still returns admission
  code `3`, updates its saved request timestamp and reaches RSSI. This is not a
  controller RTC write or proof that every native command ignores time.
- An account mismatch enters identity-refresh providers before rejection.
  Those providers are substituted in this offline test. **Do not use invalid
  account probes as supposedly side-effect-free live queries.**

The reply follows the actual port-5 callback `4203c98a`, wrapper `420206b4`,
message packer `4201c17a`, JSON header/body builders and topic selector. At the
substituted final publish boundary it is:

```text
topic: dt/anker_power/A1763/<configured serial>/param_info
head: cmd=16, cmd_status=1
payload: JSON string containing data=<base64 full response frame>, sn, pn
```

The embedded frame is function `10` / `0822` for the normal clear native
request. It is not an ordinary function-`0f` controller telemetry frame; a
future client needs explicit response correlation and decoding for this shape.
The subsequent SDK getter handles `030010` / `4822` separately from controller
responses; the generic function-`0f` helpers cannot be reused unchanged.
Neither a broker round trip nor delivery to a real client was tested by this
offline replay; the linked later hardware trial covers one native request.

## Coverage, reproduction and limits

| Cases | Coverage |
| ---: | --- |
| 48 | BLE/native × clear/GCM × four observations/results × three request bodies |
| 15 | Native required-header and payload-identity rejections |
| 5 | Native metadata behavior, including old timestamp and unused sign field |
| 2 | Three successive changing RSSI observations on each transport |
| 1 | Uninitialized native subscription path rejects before observation |

Each successive query in the repeat cases invokes AP-info again; the handler
does not return the controller's cached A3 quality byte. This proves the
software call path, not radio measurement accuracy or refresh timing.

```sh
python3 -m pip install -r tools/firmware_analysis/requirements.txt
python3 tools/firmware_analysis/emulate_radio_rssi_routes.py \
  --output /tmp/solix-radio-rssi-routes
```

`SOLIX_FIRMWARE_DIR` or `--image` selects an external copy. The tool enforces
the 1,482,800-byte image and SHA-256
`e291ec115f013953e825cb51b9e457a8731889547ab55b3058640e599e8cfec8`.
Synthetic results, exact dependency hashes and runtime versions are in
`tools/firmware_analysis/expected_results/radio-rssi-routes-*.json`.

OS services, cJSON/libc/base64 primitives, keys/GCM, AP-info, identity-refresh
providers, time and final transports are substitutes. Unexpected code paths
and firmware writes outside the registry, bookkeeping, synthetic allocation
and stack ranges fail the replay. Generic malformed-frame and crypto-failure
matrices are in the [ingress audit](radio-ingress-failure-audit.md). No private captures, real
identifiers, radio operations, station settings or shared runtime files were
used or changed.
