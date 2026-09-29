# C300 and C300X AC local Bluetooth

## Tested hardware and scope

On 2026-09-29 the laptop discovered **Anker SOLIX C300X**. The user
confirmed that this station has AC sockets. The reference product map calls
this variant **A1723** and C300 AC **A1722**; the tested GATT services did
not expose an independent product-number string. A1722 uses the same
reference telemetry layout but was not separately tested.

The radio identified itself as **ESP32**, firmware **0.0.0.3**, in the
`0829` negotiation response. Telemetry `B1` reported decimal version code
`1049`, rendered **1.0.4.9** following SolixBLE's main-version convention.
That dotted interpretation has not been checked against the app. `B6`
also contained `1049`; other component/version fields remain undecoded.

**C300 DC/A1726 and C300X DC/A1728 have different layouts and are not
covered by this support.** The browser's historical `C300X_PARAMS` table
also contains conflicting labels; it is not this Python decoder's source.

## Connection and packets

The advertisement included service `0000ff09-0000-1000-8000-00805f9b34fb`.
GATT discovery exposed the normal SOLIX service and command/notification
characteristics (`8c850001`, `8c850002`, `8c850003` UUID prefixes).

The existing **legacy** handshake worked:

1. Plain negotiation `0001`, `0003`, `0029`, `0005`, `0021`.
2. Fresh P-256 ECDH; shared-secret halves become the AES-CBC key and IV.
3. Encrypted `4022` completes the connection; the station replied `4822`.
4. Status request **`4040`**, `A1=21`, with the existing typed timestamp.

No account login, owner ID, phone connection, or button confirmation was
needed during this test. The normal session setup supplies its timestamp
and timezone. The initial session was read-only; subsequent authorized
control tests are documented below.

Both status replies **`C840`** and unsolicited telemetry **`C402`** arrived
under pattern `03010f`, fragmented across notifications. Assemble fragments
before AES-CBC decryption. These records contain individual typed TLVs,
unlike the packed Gen 2 fields. Do not send the Gen 2 `4100` subscription.

## Telemetry mapping

| TLV | Python field or meaning |
| --- | --- |
| A2 / A3 | AC/DC timer remaining, seconds |
| A4 | Remaining time in tenths of an hour |
| A5 / A6 | AC input/output watts |
| A7 / A8 / A9 / AA | USB-C1/C2/C3/USB-A1 watts |
| AB / AC | DC output / solar input watts |
| AD / AE | Total input / total output watts |
| B1 | Main firmware decimal version code |
| B7 / C1 | AC/DC output enabled |
| B8 | DC charging status |
| B9 | Signed temperature, Celsius |
| BA | Battery status: 0 idle, 1 discharging, 2 charging |
| BB | Battery percentage |
| BD / BE / BF / C0 | USB-C1/C2/C3/USB-A1 status |
| C5 | Serial number; keep captures private |
| C6 | AC charging power limit, watts |
| C8 | Display timeout, seconds; verified 30 → 60 → 30 |
| CF | Light mode: 0 off, 1 low, 2 medium, 3 high |

USB power is unsigned; use the matching port status for direction. In the
live capture, USB-C2 status was `2` (input), its power was 50–51 W, `AD`
was also 50–51 W, and AC input `A5` was zero. This distinguishes total input
from AC input despite the reference MQTT map's narrower label for `AD`.
Other individual port labels follow the reference BLE mapping and were
not separately exercised with loads.

**Do not expose BC as battery health:** it tracked BB at 49–50 during this
capture. Its meaning remains unresolved. Do not infer mains presence from
AC watts; a reliable `ac_input_connected` field is not identified yet.
Ambiguous settings, component versions, and all unknown fields remain
available in `raw_tlvs`. The decoder rejects incomplete typed scalar values.

## Verified controls

### Display timeout

After the read-only tests, the user authorized investigating C300 controls.
Display timeout was changed **30 → 60 → 30 seconds**, with fresh telemetry
confirming each value in `C8`. AC/DC switches stayed off and the charging
power limit stayed at 330 W. The original display timeout was restored
before disconnecting.

The verified legacy command is **`4046`**, containing:

- `A1 = 21` (one-byte selector).
- `A2 = 02 <seconds:uint16 little-endian>`.
- `FE = 03 <Unix seconds:uint32 little-endian>`.

The Python control should accept only the verified 30/60-second choices.
This is a display timeout, not the device's power-off timer. Generic C300
command access remains restricted to status requests.

### AC output, light, and charging power

The user then explicitly requested these controls. Each was tested
separately, with fresh telemetry confirming both its changed value and
restoration before moving to the next test:

| Setting | BLE command | Typed A2 payload | Verified sequence | Readback |
| --- | --- | --- | --- | --- |
| AC output | `404A` | `01 <0 or 1>` | Off → on → off | B7 |
| Light bar | `404F` | `01 <mode>` | Off → low → off | CF |
| AC charging power | `4044` | `02 <watts:uint16 LE>` | 330 → 300 → 330 W | C6 |

Each command also carries `A1=21` and `FE=03 <Unix seconds:uint32 LE>`.
The corresponding acknowledgements are `484A`, `484F`, and `4844`;
telemetry confirmation establishes the setting change rather than the
acknowledgement alone. Final state: AC off, DC off, light off, charging
limit 330 W, display timeout 30 seconds. No other station was involved.

Reference-supported light modes are 0/1/2/3 (off/low/medium/high), and AC
charging limits are 100/200/300/330 W. Only off/low and 300/330 W were
exercised here. The station was charging through USB-C2, so this verifies
the stored **AC power limit**, not enforcement of that limit under AC
charging. No AC load was used to measure output voltage or transfer time.

An upper battery-percentage cap has **not been identified** for C300.
The C300-specific reference command maps expose charge power in watts;
they do not establish a state-of-charge cap. In particular, `CE` remains
unidentified and was not used as a charging limit.

### Brightness remains unresolved

A follow-up read confirmed `CF=01 00` and `CD=01 02`, with the display
timeout restored to 30 seconds. SolixBLE reads `CF` for the light bar;
the MQTT map calls it display brightness. Both values fit a brightness
enumeration, so the initial read alone could not resolve the conflict.
The later `404F` light test changed `CF` from 0 to 1 and back, while `CD`
stayed 2: **CF is the light bar**, not display brightness, on this unit.
**No `404C` write was sent** because its original brightness setting is
still unidentified. `CD` remains raw without a semantic label.
The reference `404C` payload is `A1=21`, `A2=01 <level>`, and the typed
timestamp, but an independently confirmed brightness baseline is needed
before testing it.

## Live verification and remaining work

A 55-second read-only session yielded **17 telemetry updates**. Battery
charge increased from 49% to 50%; temperature was 28–29 °C. AC and DC
outputs remained off, total output stayed zero, and the charging-power
limit stayed 330 W. Private logs retain advertisements, GATT layout, every
transmitted/received packet, decrypted records, and both original and
corrected decodings. Synthetic tests use invented identifiers. A subsequent
test using the integrated public `SolixMonitor` API completed two separate
connect/status-request/disconnect cycles, delivering four fresh updates;
automatic legacy selection and reconnection both worked.

The final public-API smoke check reapplied the existing 30-second timeout
with `SolixMonitor.set_display_timeout(30)`. It returned after a new
telemetry revision containing that field; the timeout stayed 30 seconds
and AC/DC/light/charging-limit baselines were unchanged. This exercises
the client's fresh-readback confirmation path without another setting
cycle. The connection was closed after the check.

Monitoring, display timeout, AC output, light mode, and AC charging power
have verified BLE paths. DC output, display on/off, display brightness,
state-of-charge caps, native MQTT provisioning, reliable mains-presence
detection, DC variants, and confirmation of ambiguous settings require
separate investigation. This capture establishes BLE monitoring; it does
not establish UPS transfer behavior or a local MQTT connection for C300.

## References

Protocol facts were compared with the local source revisions of
[SolixBLE C300](https://github.com/kb1ibt/SolixBLE/blob/03bf48f/SolixBLE/devices/c300.py)
and [anker-solix-api's MQTT map](https://github.com/thomluther/anker-solix-api/blob/c2f8769/src/anker_solix_api/mqttmap.py).
The Python decoder is independently implemented with synthetic fixtures;
differences and live evidence are recorded above.
