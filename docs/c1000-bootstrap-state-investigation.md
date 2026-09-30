# Original C1000 bootstrap failure and radio state

## What `4825=26` tells us

The retained app categorizes byte **`0x26` (decimal 38)** as a **server-connection
failure**. Its generic `ConnectErrorMsg.onWifiErrorCode` function at `02ac062c`
selects these translation keys:

| Raw byte | App category |
| --- | --- |
| `21` | `wifiSignalNotFound` |
| `22` | `wrongWifiPassword` |
| `23`, `24` | `routerConnectFailed` |
| `25`, `26`, `27` | `routerServerConnectFailed` |
| Other byte values | `failToDeployWifi` |

`WiFiHelper.parseWifiErrorCode` at `02e14e1c` calls this categorizer. The
activation-reply parser `02423154` preserves the first decrypted payload byte
under `A0`; see [app provisioning recovery](c1000-app-provisioning-investigation.md).
A private **256-case actual ARM64 replay** verifies the numeric category branches.
The translation-key names are identified statically; the replay stops before
translation lookup. It does not execute the complete activation UI callback
chain or the radio's error producer.

This narrows the interpretation of the [retained bootstrap failure](c1000-original-wifi-validation.md).
It does **not** identify whether the underlying problem is endpoint routing,
HTTP parsing, binding state, credential acquisition or another server-stage
condition. The failed trial made no request to its newly prepared credential
listener, so its candidate credential response was not tested. Neither
certificate rejection nor a need for factory reset follows from `0x26`.

## Main-MCU evidence is a different layer

The public original-C1000 image is **A1761 main 1.5.9**, while the tested station
reports **main 1.5.1 / radio 0.1.3.0**. The original radio image is unavailable.
The controller findings below therefore establish an internal interface in
1.5.9, not the installed radio's HTTP/MQTT implementation.

### `0825` is an acknowledgment flag, not an error decoder

The function-`10` table routes `0825` to `0800c2bc`. This handler does not read
its context or payload. It sets **`20000054+0x12 = 1`** unconditionally. All 256
payload bytes, including zero and `0x26`, do the same thing in the replay;
a null context also succeeds because it is never dereferenced.

That flag is consumed by the startup sequence `08028704`, whose waiting state
is 6. A present ACK advances the sequence using its previous stage; it does not
consult the reply's error byte. The routine clears the ACK and reloads its
retry counter to three. Without an ACK, crossing the stored deadline decrements
a remaining retry and returns to the previous stage, or returns to idle when
none remain. These are **MCU/module command retries**, not evidence of an HTTP
retry timer or a way to restart cloud binding.

The ACK after stage four advances to stage five only while the recovered
uptime counter is below 10000 nominal milliseconds; later it returns to idle.
This is a startup-sequence constraint, not a ten-second lifetime for WLAN
credentials. No replay writes these internal states on a real station.

By contrast, `0824` / `0800a9b0` checks the first payload byte: zero becomes a
`5500` configuration marker and every nonzero value becomes `0000`, through
persistence key `00d6`. It still does not provide named error meanings.

### Internal `0025` is not the app's activation request

Builder `080111fc` emits:

```text
function 10, command 0025
A1 length1: argument byte
A2 length1: module-context byte at 20000c6c+9e
```

Six executions verify argument 0/1 and context values 0/1/2. The state-machine
and button callers use these fields for radio enable/mode handling. They do
not contain the app's account, API URL, country or product fields.

The app's actual activation command is **function `0f`, `0025`**, encrypted as
`4025`. Sharing the command number does not make the two payloads interchangeable.
The internal frame is not a proposed BLE recovery packet.

Module event function-`10` / `0027` stores payload byte 2 at `20000054+0x0a` and
replies with zero. In the state-zero branch it marks startup work pending and
clears the two low connection-state bits at `20000c6c+0xc4`. If the existing
permit flag at `20000054+0x0f` is set, it sends internal `0025` with both fields
one. Twelve replay cases cover zero/nonzero states and that permit flag.
This is a module-originated event handler, not an app-facing reconnect command.

## Wi-Fi button and timed radio availability

Two one-shot timers are allocated at `08028578..080285a2`, each with period
**300000 ticks**. Their IDs live at module-context offsets `b7` and `b8`, and
their callbacks are `08011274` and `08007b40`. Timer registration executes in
the replay. The nominal one-millisecond tick is separately established in the
[original Smart-policy analysis](c1000-smart-auto-off-policy.md), giving nominal
five-minute windows. Real scheduling and radio behavior remain separate.

Static tracing distinguishes three button paths:

- **Short click**, logged at `0800657a`: changes radio-related flags, schedules
  state 15, sets its enable argument to one, and restarts a relevant window
  timer according to the existing connection state. State 15 calls the internal
  `0025` builder. This path does not directly change the AC-enable bit 4 in
  `200004fc`.
- **Three-second hold**, `080067cc`: stops both window timers, clears their
  keep-awake flags and connection bits, and takes a different radio-state path.
- **Seven-second hold**, `0800665c` / `0800678c`: schedules state 6. That state
  reaches `0800cd78(1)`, which builds internal function-`10` / `0007`, A1=1.
  The unavailable radio implements its final effect. This is not established
  as a harmless reconnect operation.

The window callbacks can schedule radio-disable work; `08011274` can also
reach a GPIO control path after checking connection and keep-awake state.
They do not constitute an HTTP retry or clear a known bootstrap error. The
callbacks and button paths were traced statically, not replayed as a whole.
A main-MCU instruction trace alone cannot guarantee that a button action has
no downstream output, radio, profile or binding effects on the installed version.

## Recovery implications

Keeping the same isolated AP available and observing a cached-profile retry
requires no station write. If the radio associates, the next useful boundary
is whether it reaches DNS and the configured HTTP listener. A response variant
cannot be evaluated until the radio actually requests that response.

A short Wi-Fi click is a concrete **local wake candidate** supported by the
controller trace, separate from restarting the entire station. Its installed
firmware behavior still requires a bounded, noncritical-device check. Do not
replace that candidate with a three/seven-second hold, a factory reset, an
injected module-state event, or an arbitrary internal frame.

**No validated remote, reset-free recovery command is established here.**
The narrow remaining prerequisite for explaining server error `0x26` is the
original radio parser/state machine or a new capture of an ordinary bootstrap
that reaches the configured API. Gen 2 radio firmware cannot settle that gap.

## Reproduction and limits

```sh
python3 -m pip install -r tools/firmware_analysis/requirements.txt
python3 tools/firmware_analysis/emulate_c1000_network_state.py \
  --output /tmp/c1000-network-state.json
```

The tool accepts `--firmware` and rejects any image whose SHA-256 differs from
`b295ee8613f5c96e70dcc905896df516621cab4dc590bb580eac6b84519911a6`.
**1,165 MCU cases pass:** 768 `0825` payload/seed combinations, one null context,
256 `0824` statuses, six internal serializers, 12 module-state events, 120
startup ACK/retry boundaries and two timer registrations. Exact non-stack RAM
changes are checked for the ACK, builder, module-state and retry cases.

Actual controller instructions perform the parsing, arithmetic, TLV building,
state changes and timer allocation. Only persistence and transport/response
callbacks are captured in Python. The startup wait tests use synthetic uptime
and flags. The [synthetic results](../tools/firmware_analysis/expected_results/c1000-network-state.json)
and adjacent digest manifest contain no station identifiers or captured traffic.

The separate app replay uses the previously documented private app hash and
retains its image, disassembly and analysis scripts privately. This investigation
made no station, SSH, cloud, firmware-flashing, output or configuration requests.
