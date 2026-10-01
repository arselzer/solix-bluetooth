# Radio status features: MQTT flag and clock readback

## Result and scope

Two additional internal radio handlers provide useful monitoring candidates:
**`004b` reports binding/BLE/MQTT state**, and **`004a` reports radio time and
timezone offset**. The MQTT field is more specific than the handler's own
“wifi” log label. The clock response has sentinel and sampling behavior that
a future receiver must preserve rather than treating every successful reply
as a valid synchronized clock.

**106 synthetic instruction cases pass.** These execute the actual handlers,
state getters, timezone selector and raw TLV serializer. They establish
neither a ready-to-send external request nor live device behavior. No
hardware, network, credentials, output settings or firmware were changed.

Input is the published **A1763 C1000 Gen 2 radio 0.3.3.0**:
`firmware/c1000_gen2/1.1.4.9/c1000-radio-validated.bin`, SHA-256:

```text
e291ec115f013953e825cb51b9e457a8731889547ab55b3058640e599e8cfec8
```

Original C1000 or C2000 equivalence is not established by a matching version
string. Command numbers below belong to the internal radio table at
`3c147be4`; they must not be confused with ordinary controller AC/DC commands.

## Candidate inventory

The tool extracts all **42 command/callback pairs** from that table. It only
executes the two reviewed handlers. Relevant neighboring candidates are:

| Command | Callback | Audit disposition |
| --- | --- | --- |
| `0003` | `4203cc10` | Previously traced [wireless/RSSI report](gen2-normal-feature-audit.md); not repeated |
| `0022` | `4203cb26` | Previously traced signed RSSI getter; external route investigated separately |
| `001f` | `4203d1ba` | Identity/configuration path also copies module-information fields; not selected as a passive status query |
| `0031` | `4203ca9e` | Constant successful response; no useful state field |
| `0032` | `4203d56e` | Stored identity/SN response candidate; unnecessary private identity exposure for this task |
| `003c` | `4203ed3c` | Previously documented private configuration readback; includes encrypted Wi-Fi password |
| **`004a`** | **`4203fac0`** | Radio epoch and negated timezone offset; new replay below |
| **`004b`** | **`4203da2c`** | Binding state, BLE predicate and cached MQTT flag; new replay below |
| `0064` | `4203cab8` | Static OTA-status-byte candidate; producer semantics and external route remain unreviewed |

This inventory is not a getter allowlist. Other entries include setters,
pairing/provisioning, update callbacks and other state-changing operations.

## `004b`: the third field is MQTT state

Handler `4203da2c` creates a response with leading status `00`, followed by
ordinary **raw TLVs**, with no type byte inside their values:

| Tag | Length | Actual source and mapping |
| --- | --- | --- |
| `A1` | 1 | Binding getter `4202beba`. If callback pointer `3fc8aad0` is zero, returns boolean of stored byte `3fc8ab70`; otherwise calls that provider |
| `A2` | 1 | `42028d8e` → `420379ea` reads BLE state byte `3fc90534`; reply is **1 only when state equals 3** |
| `A3` | 1 | `4201874c` reads cached byte **`3fc9036c`** into a word; handler serializes its low byte unchanged |

For synthetic bound/BLE-state-3/MQTT-1 input, the body is:

```text
00 a1 01 01 a2 01 01 a3 01 01
```

Although the log calls A3 “wifi,” its producer is the MQTT implementation:

- `aws_mqtt_connect_once` stores **1** at `42019ad4`, beside its connection
  success log, and again at `42019b16` after its completion path.
- Its failure path stores the error word at `3fc90358` and clears the flag
  at **`420199fc`**.
- MQTT cleanup clears the flag at **`42018a24`**. Additional disconnect
  helpers clear it at `420197f8` and `42019bc4`.

These producer links are static instruction evidence; network connection and
cleanup routines are not executed by this replay. The getter reads cached
software state. It does not perform a broker ping, prove an active subscription,
refresh AP association, expose an IP address or supply a failure reason.

The 78 status cases cover stored binding values 0/1/255, BLE states
0/1/2/3/4/255, MQTT bytes 0/1/2/255, optional binding-provider results and
response-transport failure. Unexpected MQTT values are forwarded with
status `00`; callers must not normalize every nonzero raw byte into confirmed
connectivity. An optional binding provider can also return an unnormalized
byte. All global state remains unchanged in these cases. The transport
failure cases show one response attempt, with no handler-level retry.

## `004a`: radio time and timezone selection

Handler `4203fac0` replies with status `00` and:

| Tag | Length | Value |
| --- | --- | --- |
| `A1` | 4 | Little-endian current time from `42035dce` → time service `42115e56` |
| `A2` | 4 | Little-endian **negation** of signed timezone getter `420365f0` |

The timezone getter selects one of two 40-byte rules at `3fc8af2c`. Each
rule's offset is at `+1c`; cached transition epoch is at `+20`, and cached
year at `+24`. Selector `42035fe0` handles both an ordinary ordered season
window and one crossing the year boundary. Boundary comparisons are exercised
immediately before, at and after each transition.

### Sentinel and freshness behavior

Four findings limit how this response can be used:

1. If the stored timezone text at `3fc82f24` is exactly **`GMT0`**, getter
   `420365f0` returns **`-1`**. The query handler negates it, so **A2 becomes
   `+1`**. This is a sentinel path, not evidence of a real one-second zone.
   Equal zero offsets with another synthetic timezone text instead return 0.
2. Time service results **0** and **`ffffffff`** are copied to A1 with status
   `00`. The handler itself does not validate synchronization or service
   failure. A successful response is not a valid-clock guarantee.
3. The handler reads time once for A1, then the timezone getter normally reads
   it again. A replay returning time 99 first and 100 second, at a synthetic
   transition of 100, reports the earlier epoch with the later offset. This
   is a non-atomic snapshot.
4. With different seasonal offsets and a stale cached year, selector
   `42035fe0` calls rule updater `42035e30`, which refreshes the two volatile
   transition epochs/years. The replay executes that arithmetic and checks
   the six-byte cache region in each rule. No settings, connection state or
   persistence backend is changed. The query is therefore not strictly free
   of RAM side effects.

The 28 clock cases cover equal positive/negative/zero offsets, invalid time
values, the GMT0 sentinel, both season-window orderings and exact boundaries,
two different time samples, stale-year cache refresh and response failure.
Rules are seeded directly. The refresh case uses the simple day-number rule;
timezone configuration parsing and every calendar-rule form are not replayed.

This query concerns the **radio clock**. It does not read the main controller's
RTC or its persisted offset, so it cannot alone prove the tariff scheduler's
local time. The existing [controller clock-path investigation](gen2-schedule-clock-audit.md#clock-path-in-the-recovered-firmware)
also uses a different internal time-sync packet and sentinel conversion;
the query's A2 must not be substituted into that write path by assumption.

## Transport and implementation prerequisites

The subsequent [route audit](radio-status-routing.md) executes both BLE paths;
a [C1000 Gen 2 live check](c1000-radio-readback-validation.md) records their
responses with protected settings unchanged. Both handlers pass the body to
response helper `4204fa68`. This direct-handler replay replaces
that final transport and supplies an opaque synthetic context. It does not
execute inbound authentication, routing, encryption, outer framing or response
correlation. No new SDK/CLI command or Home Assistant sensor is added here.

The [existing MQTT routing evidence](radio-factory-routing.md#mqtt-differs-from-ble)
sets a concrete limit: raw callback `42043a18` sends normalized commands
**above `003f`** through `420439cc` directly to the controller builder,
bypassing radio dispatch. Thus `004a` and `004b` cannot automatically use the
native raw-data route investigated for RSSI command `0022`. These handler
replays do not establish another external route to the radio getters.

A future implementation needs the exact external function/command route,
bounded response validation and model/firmware evidence. Status monitoring
should preserve unknown enum bytes; clock monitoring must separate valid time,
timezone sentinel and radio-versus-controller time. The SDK's existing
[network diagnostics](local-mqtt-investigation.md) are a separate interface.

## Reproduce

Verified analysis dependency: Unicorn 2.1.4.

```sh
python3 -m pip install -r tools/firmware_analysis/requirements.txt
python3 \
  tools/firmware_analysis/emulate_radio_status_features.py \
  --image firmware/c1000_gen2/1.1.4.9/c1000-radio-validated.bin \
  --output /tmp/radio-status-features-results.json \
  --manifest /tmp/radio-status-features-manifest.json
```

Checked-in [results](../tools/firmware_analysis/expected_results/radio-status-features-results.json)
and [manifest](../tools/firmware_analysis/expected_results/radio-status-features-manifest.json)
contain only firmware constants and synthetic observations. The script requires
assertions, verifies the image hash and records source/results hashes.

Actual RISC-V instructions execute the handlers, binding fallback, BLE/MQTT
getters, clock wrapper, offset selector, transition arithmetic and serializers
`4204ddb2` / `4204dd4c`. Host substitutes are ROM memory/string primitives,
time/year observations, optional binding-provider callback, logging and final
transport. Strict write guards allow stack and, only in the stale-year case,
the identified volatile cache fields. No peripheral or persistent backend runs.
