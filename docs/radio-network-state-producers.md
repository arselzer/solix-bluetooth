# Radio network state producers and MQTT notifications

This **offline instruction replay** explains selected fields of native radio
`10 / 0003` and their event producers. It does not qualify another live setter,
establish cloud binding, or guarantee physical recovery. The exact image is
**A1763 C1000 Gen 2, main 1.1.4.9, radio 0.3.3.0**:

```text
c1000-radio-validated.bin
SHA-256 e291ec115f013953e825cb51b9e457a8731889547ab55b3058640e599e8cfec8
```

Original C1000 and C2000 radio binary equivalence remains unproven. Matching
version labels are insufficient. This extends the [native information-query
audit](radio-native-info-queries.md) and [BLE producer
audit](radio-ble-initialization-activation.md).

## Fields describe different layers

| Native tag | Actual source | Supported interpretation |
| --- | --- | --- |
| A5 | Byte `3fc90645` | Application `eth` status, as named by the firmware log; producer coverage remains limited |
| A6 | `4202bd9a → 42030e8e`, byte `3fc904c5` | Station got-IP state, converted to boolean by `0003` |
| A8 | Pointer getter `4203145c`, buffer `3fc8ad98` | Optional cached IPv4 text; no timestamp or demonstrated freshness guarantee |

The query log explicitly labels its fields `ble`, `wifi`, `eth` and
`net_state`. A6 is a local IP acquisition flag, **not** proof of internet
access, MQTT connection, account binding, or reachability from another host.
Executing raw byte 0/1/255 cases yields A6 value 0/1/1.

The shared application writer `42043fd8` stores A5 for selector 2. Executed
synthetic values 0/1/255 are returned unchanged, with no status emission in
this branch. The prior exact direct-branch census finds nine callers, none
using selector 2. Thus the `eth` name is established, but an active producer or
usable Ethernet capability on this model is not established. The census does
not rule out indirect callers or other writes.

## Got-IP and cached-IP producers

Actual event handler `42031e74` compares the exact firmware `IP_EVENT` and
`WIFI_EVENT` pointers. Its station got-IP event copies address/netmask/gateway
into `3fc904d4/d0/cc` and sets `3fc904c5` to 1 at `420322a2`.

The reconstructed SDK default got-IP callback points to `4202d0d4`. It calls
actual formatter wrapper `42031410`, which returns those three words and
supplies the IP octets to C `sprintf("%d.%d.%d.%d")`. The host formatting
primitive alone is substituted. The destination is the 16-byte A8 buffer.
The query's `4203145c` getter only returns this buffer; it does not refresh it.

| Executed path | Got-IP flag | Cached IPv4 text | Application Wi-Fi flag |
| --- | --- | --- | --- |
| Station got-IP, MQTT disconnected | 1 | Updated | Normal callback leaves it 0 |
| Station got-IP, MQTT connected | 1 | Updated | Normal callback sets it 1 |
| Station STOP | Cleared | Retained in these fixtures | Retained |
| Station DISCONNECTED prefix | Cleared | All 16 bytes cleared | Disconnect callback clears it |
| Disconnect helper `420313a2`, before asynchronous event | Cleared | Retained | Retained |

The disconnect helper calls a substituted physical Wi-Fi operation. The
station-disconnected replay stops at `42031f80`, after clearing fields and
running the SDK/application callback, before configuration retrieval and
reconnect policy. These are bounded observations, not complete teardown tests.

Synthetic got-IP events containing `0.0.0.0` also set A6 to 1. The executed
assignment assumes the event producer supplied valid network data; the replay
does not validate real DHCP or driver behavior. An A8 string alone can therefore
be stale or misleading, including after STOP or the disconnect helper.

## Real MQTT notifications reach application lifecycle

The MQTT-connected byte is separate: **`3fc9036c`**, read through
`4201874c`. The actual successful connection tail sets it to 1, then calls
**`4202d20c`** at `42019b1e`. The actual disconnect routine `42019800` calls
**`4202d1ec`** at `4201984e` before clearing that byte.

Those dispatchers select a primary SDK callback at `3fc90430/34`, or an
application fallback at `3fc90438/3c`. They call one selected callback,
not both. Actual application startup installs:

- `420440f2`, firmware name `wifi_connect_state_notify`.
- `4204429c`, firmware name `wifi_disconnect_state_notify`.

With the synthetic normal application context, connection notification requires
**MQTT state exactly 1 and a nonzero got-IP flag** before setting application
Wi-Fi to 1. It also reaches topic subscription, MCU Wi-Fi notification and
configuration/binding lifecycle boundaries. These callees are recorded,
not completed. The factory/bypass context is absent in this replay.

Disconnect notification clears application Wi-Fi. In the executed MQTT-loss
cases it leaves got-IP and cached text alone. The callback sees the old
MQTT-connected value 1 because the caller clears it afterward. Session buffers
are absent, so optional buffer-reset effects are excluded. Actual final helper
`42043184` clears volatile application notification state; it does not open a
BLE registration window in this executed body.

The XIP direct-call census identifies five calls to the connection dispatcher
and three to the disconnect dispatcher. The replay executes one real success
tail with an explicit synthetic caller register frame, plus the actual
disconnect routine with physical MQTT disconnect/cleanup/notification
substitutes. It does not execute socket connection, TLS, cloud authentication,
or all asynchronous lifecycle callbacks. Synthetic primary callbacks prove
dispatcher precedence only, not their production effects.

## Recovery implications and limits

Application Wi-Fi 0 with got-IP 1 can reflect MQTT loss while local IP state
remains acquired. Got-IP 0 with an A8 address can reflect cached text surviving
an earlier network transition. Neither combination authorizes changing an
identity or proves that BLE is advertising. Physical IoT-button recovery and
independent BLE visibility remain prerequisites for the deferred generated-ID
trial; the user's absence currently removes that physical fallback.

The executed paths preserve complete native configuration, runtime account,
MQTT identity cache and BLE allowlist snapshots at the selected boundaries.
This does **not** establish the behavior of substituted binding/network/MCU
operations or delayed physical policy. C2000 AC output remains protected;
these results must not be generalized to its unrecovered image.

## Reproduce

```sh
PYTHONPATH=/tmp/solix-analysis-tools:tools/firmware_analysis \
  python3 tools/firmware_analysis/emulate_radio_network_producers.py \
  --image firmware/c1000_gen2/1.1.4.9/c1000-radio-validated.bin \
  --output-dir /tmp/solix-radio-network-producers
```

The **31 synthetic cases** cover selector/getter values, IPv4 formatting,
got-IP/STOP/disconnect branches, primary/fallback/absent notifications,
normal callback gates and real MQTT notification tails. Compare
`radio-network-producers-results.json` and
`radio-network-producers-manifest.json` with
`tools/firmware_analysis/expected_results/`. The manifest hashes all seven
replay dependencies, source and image. Optimized Python is rejected because
assertions are required. Use the pinned offline dependencies in
`tools/firmware_analysis/requirements.txt`. Raw evidence and the publication
scan remain in the ignored private folder with restricted permissions.
