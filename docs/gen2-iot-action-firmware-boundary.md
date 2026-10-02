# Named IoT actions and the Gen 2 firmware boundary

## Result and scope

The retained Anker app advertises SDK action `action_set_ac_params` with
property `acInputDisableSwitch`. This audit does **not** recover its binary
encoding or establish that C1000 Gen 2 implements it. Instead, **48 synthetic
instruction cases** establish where that SDK action would have to become a
normal station command. Adding those names to the existing MQTT envelope does
not produce AC-input control.

Exact images inspected:

| Image | Version | Bytes | SHA-256 |
| --- | --- | ---: | --- |
| A1763 main | 1.1.4.9 | 198,656 | `21ffb746c1e07ecaa9817fa7017807585a00bedbca3f136c650129bb52a4a0c9` |
| A1763 radio | 0.3.3.0 | 1,482,800 | `e291ec115f013953e825cb51b9e457a8731889547ab55b3058640e599e8cfec8` |

All replay data is synthetic. No device, network, account, SDK implementation,
controller setter, flash or power electronics ran. Original C1000 and C2000
radio binaries remain unrecovered; matching version text is insufficient to
apply this result to them.

## Where named actions stop

The [app audit](gen2-ac-input-disable-app-audit.md) proves this upstream shape:

```text
Flutter channel: ak_iot_kit_flutter
Flutter method: handle
arguments.identifier: akiot.device.invoke_action
arguments.param.id: action_set_ac_params
arguments.param.param: {acInputDisableSwitch: 0|1}
```

The SDK implementation between that platform call and transport remains
the missing link. Those are app/SDK arguments, not an established station
MQTT payload. The Dart call enables the `openTransaction` action option; that
does not establish a corresponding serialized SDK field. The
[interceptor audit](gen2-dart-action-interceptors.md) records the exact channel
boundary and which app transformations run before it. Exact ASCII counts for `action_set_ac_params`,
`acInputDisableSwitch` and `supportAcInputDisable` are zero in both pinned
images. This only excludes a literal occurrence; numeric mapping, compressed
data, SDK translations and other firmware versions remain possible.

The station's reviewed native route is:

```text
MQTT subscription 42027f5e → envelope admission 42027c90
  → header/identity checks → numeric head dispatcher 42026f4e
  → head 17 branch 42027364: JSON key data → base64 decode
  → registered raw callback 42043a18
```

Four synthetic RSSI-reference cases add an action ID, its 0/1 property or flat
capability fields beside valid `data`. Every case performs the same RSSI query
and returns the same function-10 `0822` response. Four controller-query cases
preserve function `0f`, command `0100`, body `a10122` at forwarding wrapper
`420439cc`, regardless of added action/property values or an unknown action ID.
That wrapper is substituted before any controller transmission or handler.

The actual JSON lookups never request `id`, `param`, `acInputDisableSwitch` or
`supportAcInputDisable`. Named-action-only, property-only, a synthetic SDK-like
argument fixture, and nonstring-`data` fixtures fail before the raw callback. This is a bounded
head-17 finding, not an audit of every SDK or cloud endpoint.

## A malformed JSON fallback can forward

Base64-encoding the SDK action JSON as `payload.data` is unsafe as a discovery
shortcut. Radio callback `42043a18` extracts a normalized binary opcode before
generic outer-frame validation. Opcodes above `003f` enter the controller
forwarding branch directly; the prior [ingress audit](radio-ingress-failure-audit.md)
documents that boundary separately.

In this new synthetic fixture, the JSON bytes select an accidental function,
opcode and declared body length. The declared length exceeds the available
decoded bytes. The replay records the first 32 available body bytes and the
arguments at `420439cc`; it neither reads the claimed extent nor executes the
wrapper. No main firmware runs. Successful JSON admission is therefore not a
reason to treat an arbitrary `data` value as an action request.

## Other normal heads are separate operations

Thirty-five selector cases execute only the actual numeric selection
instructions. Commands 0–31 plus 32, 255 and 65535 stop before their selected
branch or return body. The selection has these branch entries:

| Numeric head | Selected branch |
| --- | --- |
| 2 / 3 / 4 | `42027182` / `42027242` / `42027270` |
| 5 / 6 / 9 | `4202748a` / `42026fa6` / `42027282` |
| 12 / 15 / 17 | `42027406` / `42027042` / `42027364` |
| 25 / 26 / 27 | `42027524` / `42027534` / `420270fc` |
| 28 / 31 | `420278a0` / `4202791a` |

Heads 10 and 11 select the shared success return `4202724e`; other tested
values select error return setup `42026f92`. This does not establish admission
or safety for another head. Selected bodies contain reset, WLAN, OTA and
device-list operations; they are not executed by these selector tests.

Static review identifies `trans` for heads 2/9/15, `data` for head 17, OTA
`ota_type`/`sub_dev_sn` for head 6, WLAN `ssid`/`pwd` and `timeid`/`timezone` for
head 26, and `update_ts` plus a base64 `device_list` for heads 27/31. The latter
are device-registry list parsing, not a discovered charging-property resolver.
Their callbacks and downstream asynchronous effects are outside this replay.

## Fixed binary namespaces and existing parameters

The tool records all **17** entries of the installed ordinary main function-0f
table at `08032e60` and all **42** entries of radio function-10 table
`3c147be4`. The main set is:

```text
0100 0101 0102 0103 0051 0057 0059 005e 0063
0064 0065 0066 0072 0089 0090 0091 0092
```

This inventory contains handlers, not names for every supported field. The
already executed [charging-control audit](c1000-charging-control-followup.md)
establishes `0101` A4 as charge watts, A7 as Fast, and `0102` A2 as car/DC output.
Those fields provide no demonstrated AC-input-disable or direct-pause setter.
No neighboring TLV is inferred here, and none of those setters is replayed.

The radio's structured HTTP parameter paths also have specific purposes:

- Builder `420224d8` requests only `base_get_device_param` point `20001`;
  the [backup-query audit](gen2-backup-export-radio-app.md) already executes it.
- Point callback `4201f34c` validates that exact name and string 0/1; its
  business path emits the [analytics-enable command](energy-report-lifecycle.md)
  function `10` / `0014`. It is not arbitrary device-property dispatch.
- Update acknowledgment `4201f136` checks the response and frees its JSON.
  Adjacent callback `4201f1a4` reads `data.timezone` and applies radio time-zone
  handling. Neither reviewed body resolves a named charging action.

These selected paths do not exclude another protocol namespace, table installer,
firmware version or indirect translation. Function `0f`, `10` and `0c` are
different namespaces; a diagnostic member or similarly named field must not
be substituted for a normal SDK action.

## Next evidence and reproduction

The useful next evidence is an exact SDK action encoder/registry or a retained
legitimate capability/info response for A1763. That must identify the command,
function, field type, guard and readback before a charging action can be
considered. The app's shared A1782 capability parser and optimistic UI are not
A1763 capability or physical-current evidence. Existing verified caps, tariffs
and charging-power controls remain separate from this unresolved direct action.

```sh
python3 -m pip install -r tools/firmware_analysis/requirements.txt
python3 tools/firmware_analysis/audit_gen2_iot_action_boundary.py \
  --output-dir /tmp/solix-iot-action-boundary
```

`SOLIX_FIRMWARE_DIR`, `--radio-image` and `--main-image` select external copies.
Both sizes and hashes are mandatory, and optimized Python is rejected.
Expected results and dependency/runtime manifests are in
`tools/firmware_analysis/expected_results/gen2-iot-action-boundary-*.json`.
Actual head-17 admission, base64 routing, radio RSSI handler and reply framing
execute; cJSON/libc/base64, synthetic identities/session state, AP-info,
crypto, queues/allocation and final transports are substitutes. Main firmware
is inspected as data only. Protected configuration/binding/allowlist regions
must remain unchanged, and an unexpected instruction or global write fails.
