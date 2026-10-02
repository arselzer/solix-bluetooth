# Native radio queries: MAC, region and private network text

## Scope and useful result

This audit covers the exact **A1763 C1000 Gen 2 radio 0.3.3.0** image distributed
with main firmware **1.1.4.9**. Twenty-three synthetic instruction replays
resolve fields and failure behavior of normal function `10` queries `0002`
and `0003`, plus their contrast with `0022`. No station or network was used.
There is no equivalent binary evidence for C2000 Gen 2 or original C1000.

`0002` supplies interface MAC text and an optional country/region string. It
does **not** supply a pairing/account ID or additional power controls. It can
return uninitialized scratch with status `00`, and its Wi-Fi branch can call
initialization. It is unsuitable as a general public read-only getter.

`0003` contains private configuration/network text alongside application
flags. A narrow monitoring adapter should expose only its raw A1/A2 bytes;
retain an entire response privately if needed for research. Use the existing
[dedicated RSSI getter](radio-rssi-routing.md) for RSSI failure handling.

## Namespace and native route

Startup registers function `10` to `4203c9e0`; the table at `3c147be4` contains:

| Function / command | Actual handler | Relevant behavior |
| --- | --- | --- |
| `10 / 0002` | `4203cece` | Raw selector chooses BT or Wi-Fi MAC; Wi-Fi also returns region |
| `10 / 0003` | `4203cc10` | Application flags, RSSI and optional private text |
| `10 / 0022` | `4203cb26` | Dedicated signed RSSI query with failure status |
| `0f /` these opcodes | `42045792` | Separate dispatcher; not the radio query table |

Native JSON admission and port-5 protocol dispatch are the actual instruction
paths already described in the [71-case RSSI audit](radio-rssi-routing.md).
They require matching configured account and serial. This audit does not
establish account-free admission, MQTT authentication or broker connectivity.
Queries also update ordinary native request/reply bookkeeping.

The clear request is:

```text
ff 09 <total length LE16> 03 00 10 <opcode BE16> <raw body> <XOR>
```

Replies preserve pattern `03 00 10`, set opcode bit `0800`, and return through
`dt/anker_power/A1763/<configured serial>/param_info`. The native envelope has
head `cmd=16`, `cmd_status=1` and a JSON-string payload containing base64 of
the full frame. Body fields here are **raw TLVs**, without typed-value prefixes.

In a single synthetic connection, requests with sequences 101/203/307 produce
independently generated reply sequences 0/1/2 and different session labels.
The replay's time/session primitives are deterministic substitutes; these
literal values are not predictions for a real station. The executed builders
do not echo the supplied request sequence/session. Correlate by configured
device, exact pattern and response opcode; serialize concurrent requests for
the same opcode and discard stale queued replies. These inner frames contain
no transaction ID that distinguishes two identical pending queries.

The raw callback's local cutoff is `003f`. A handler in the function table
does not establish native-local reachability above that cutoff, for example
`004b`; higher opcodes take the forwarding route. Commands `0001`, `0005`,
`0023`, `0024` and `0025` are setters, not additional readback probes. This is
a targeted query audit, not an exhaustive classification of every table entry.

## `0002`: selector, error paths and country cache

Use raw body `a1 01 00` for BT; `a1 01 01` selects Wi-Fi. The handler checks
A1 presence but does not check its length or boolean range. Any nonzero first
byte selects Wi-Fi, including 2/255. A zero-length A1 followed by A2 consumes
the next tag byte as selector `a2` in the replay. Missing A1 returns body `01`.

Do not use the controller helper that prepends source A1=`22`, or prepend a
typed boolean byte: either selects the nonzero Wi-Fi branch. A future private
probe must require one A1 containing exactly one byte, value 0 or 1.

| Branch | Normal raw fields after status `00` | Failure / effect |
| --- | --- | --- |
| Selector 0 | A1: 12 uppercase hex ASCII bytes of BT MAC | Provider error skips encoding; emits 12 uninitialized scratch bytes with success |
| Nonzero | A1: 12 uppercase hex ASCII bytes of Wi-Fi station MAC; A2: 4 bytes | MAC provider error is ignored; six stale raw scratch bytes are hex-encoded with success |

The BT path is `42028d8a → 4203701e → 420a4cc0` with interface type 2. The Wi-Fi
wrapper `42030eb2` uses the same MAC-service boundary with type 0, and calls
**Wi-Fi initialization `42030a06`** first when SDK byte `3fc9049c` is zero.
The full initializer is substituted; no physical or delayed effects are proven.

The replay deliberately seeds only synthetic stale bytes. It obtains
`STALEHEX0001` for a failing BT provider and `02AABBCCDDEE` for failing Wi-Fi.
The real prologue clears the response buffer but neither selected scratch
area. Actual stale content and provider failure frequency remain unknown.
Hex syntax and status `00` cannot establish a valid MAC observation.

The Wi-Fi A2 comes from wrapper `420114c0` and cached country getter
`420328a4`. On a cold cache it creates/takes a mutex, reads the 84-byte
`wifi_country_code` record and copies a record with magic `55aaaa55` into
volatile cache `3fc8adc4`. It checks the record magic after reading; this replay
models read failure by leaving the zeroed record unchanged. An invalid record
falls back to ASCII `01`. No file write is observed on this selected path.

The getter formats at most seven country-string bytes, but the query transmits
four and forces byte 2 to zero. Thus synthetic `ABCD` becomes `41 42 00 44`,
not a clean four-byte string. Its exact region-code vocabulary is unproven;
do not interpret it as an owner ID, ISO-country guarantee or UI locale.

## `0003`: fields and privacy

This resolves a schema limit in the
[50-case advertising audit](radio-ble-advertising-recovery.md), whose optional
text provider was substituted. No BLE-enable cases are repeated here.

| Raw tag | Executed meaning / limit |
| --- | --- |
| A1 / A2 | Application bytes `3fc90646/47`; BLE/Wi-Fi flags, not physical link or MQTT health |
| A3 | Signed RSSI byte; zero also represents observation failure, while outer status stays `00` |
| A4 | Up to 32 bytes from the start of a Wi-Fi configuration record: configured SSID/network text; empty on provider error |
| A5 | Application byte `3fc90645`; finer semantics unproven |
| A6 | Boolean from status getter `4202bd9a → 42030e8e`, reading `3fc904c5`; finer semantics unproven |
| A8 | Optional cached text at `3fc8ad98`; producer meaning and freshness unproven |

Actual wrapper `42030ee2` obtains a 180-byte configuration record through
`42030b8a`, measures at most its first 32 bytes and copies only those to A4.
The provider consults both SSID and password portions and has fallback paths;
its internals are outside this replay. A4 cannot prove current association.
No password appears in the selected serialized replies, but network names
are private and should not enter public metrics or diagnostics.

A8 uses a raw string length reduced to one byte. This audit tests short
synthetic text only; it does not establish safety for corrupt/unbounded strings.
The small A6 and diagnostic-bitmap getters execute actual read instructions;
the diagnostic bitmap only controls logging here, not an extra response tag.
The later [31-case producer audit](radio-network-state-producers.md) resolves
A5's firmware `eth` label, A6 got-IP state and A8 cached IPv4 text. It also
qualifies when that text survives network transitions. Those producers were
outside this query-provider replay; the public query still omits private text.

For synthetic −70 dBm, `0003` returns A3=`ba` with status `00`, while `0022`
returns A1=`ba ff ff ff` with status `00`. Dedicated `0022` returns status `01`
on zero/service failure, as established by its earlier replay.

## Reproduce and limits

```sh
python3 -m pip install -r tools/firmware_analysis/requirements.txt
python3 tools/firmware_analysis/emulate_radio_native_info_queries.py \
  --output /tmp/solix-radio-native-info
```

Use `--image` or `SOLIX_FIRMWARE_DIR` for an external copy. The tool rejects
optimized Python, wrong image size or SHA. It requires the 1,482,800-byte image
with SHA256 `e291ec115f013953e825cb51b9e457a8731889547ab55b3058640e599e8cfec8`.
Compare both JSON files with the corresponding
`tools/firmware_analysis/expected_results/radio-native-info-*.json` fixtures.

Coverage is 15 MAC/region selector, error, initialization and malformed-body
cases; 4 actual network-text serializer cases; 1 mixed native sequence; and
3 namespace cases stopped at the substituted separate function-`0f` dispatcher.
The latter prove boundary selection, not that a controller command is inert
or produces no reply after that boundary.

Native admission, query handlers, selected wrappers, TLV/framing and JSON/topic
builders execute real instructions. OS/libc/JSON/base64, established crypto,
MAC/AP/config/file providers, full Wi-Fi initialization, time/session values and
final publishing remain substitutes. No MCU builder/forwarding is observed
in the selected function-`10` cases, and protected radio identity regions stay
unchanged. Those checks do not prove physical output behavior or delayed SDK
policy. No live queries, private identities, credentials or phone captures are
included. This audit changes no runtime files or public control capabilities.
