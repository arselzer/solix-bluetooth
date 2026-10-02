# Gen 2 AC-input disable: app action, capability and SDK boundary

## Result and scope

The retained Android ARM64 app contains a direct **battery-only / AC-input
disable** control in its shared IoT power-station UI. Its exact action is
`action_set_ac_params` with parameter `acInputDisableSwitch=0/1`.

This establishes an **app/SDK interface**, not a usable native command. The
retained Dart code delegates the action to a platform SDK; its opcode, TLV,
transport selection, device-side handler and actual Gen 2 capability values
remain unresolved. There is no newly established local input-disable control
to implement or send alongside the existing verified Time-of-Use controls.
The [retained Android SDK inspection](android-sdk-native-boundaries.md)
identifies the protected-bytecode boundary preventing a native mapping here.
Follow-up audits now resolve the actual
[Dart interceptor registration](gen2-dart-action-interceptors.md) and recover
[readable native loader instructions](android-loader-carriers.md); the SDK
action encoder and device readback remain missing.

This is static analysis of the exact retained `libapp.so`, SHA256
`8537b4f8a4da327f9bf298d99e90ad969b89f52ae7d6d68c454e5bf0ec25c070`,
using its Dart 3.11.0 AOT metadata and ARM64 instructions. Twenty-five selected
functions were extracted; **no new CPU emulation cases**, app execution,
network requests or device commands were performed. Decompiler text was used
only for navigation. No firmware-version equivalence follows from this app.

## Exact action path

| Boundary | Address | Finding |
| --- | --- | --- |
| `ACRechargingPowerLogic.onInit` | `035e4364` | Requires an `IotAnkerDevice`; obtains `A1782DeviceInfo` and its update stream |
| `onAcInputDisableSwitchChanged` | `03ccf29c` | Busy guard, optimistic UI update, then debounce |
| Debounced closure | `03ccf3dc` | Converts the requested boolean to integer 0/1 and calls `setACParams` |
| `AKPPSCommand.setACParams` | `02cdaf18` | Adds the named parameter and builds the SDK action arguments |
| `AKIoTKitManagerImpl.handle` | `021c3a94` | Handles action/transaction dispatch, then calls the Flutter SDK wrapper |
| `AkIotKitFlutter.handle` | `021c3d8c` | Platform-channel boundary; native implementation not recovered here |

The selected Dart action arguments, before platform-channel wrapping, are:

```text
method: akiot.device.invoke_action
arguments:
  deviceSn: <private device serial>
  bleMac: <private normalized Bluetooth address>
  id: action_set_ac_params
  param:
    acInputDisableSwitch: 0 or 1
openTransaction: true
```

These are SDK arguments, **not** a native MQTT JSON envelope or a BLE frame.
No cloud API endpoint is called directly by the selected Dart closure. That
does not prove offline operation: BLE/native MQTT/cloud selection and fallback
are inside the unavailable SDK dispatcher. Sending these property names to
the local broker is not an established protocol route.

The manager's `_processParams` (`0221aef0`) folds configured parameter
interceptors; the [follow-up audit](gen2-dart-action-interceptors.md) identifies
the registered transaction-lock interceptor and unchanged switch argument.
The final examined
handoff uses channel `ak_iot_kit_flutter`, method `handle`, with `identifier`
and `param` map entries. No actual frame encoder was recovered on this path.

The same wrapper accepts other optional keys, including `acInputStatus`,
`acTimerValue`, `acRechargePower`, `acFrequency`, `acEnergySavingSwitch`,
`superFastChargeStatus`, and two auxiliary recharge-power keys. The wrapper
does not establish device support for every optional parameter.

### AC-output naming hazard

`acInputStatus` and `acInputDisableSwitch` are **separate** SDK parameters.
The shared PPS transaction configuration explicitly maps `acInputStatus` to
reported **`acOutputStatus`**. This resolves an otherwise misleading name:
it cannot be substituted for a direct input-disable action.

The existing A1763 main 1.1.4.9 audit separately proves that native `0101/A2`
controls the AC-output lifecycle. Do not reuse that output setter, infer an
extra tag from parameter order, or invent a raw request from this app action.
The [normal charging audit](c1000-charging-control-followup.md) established no
input-disable field in the selected `0101`/`0102` handlers. It is a bounded
negative result, not a complete search of all firmware revisions or commands.

## Capability, reported state and model applicability

`A1782DeviceInfo.fromJson` at `0459e968` parses two integer properties:

| Property | Dart field offset in instructions | UI interpretation |
| --- | --- | --- |
| `supportAcInputDisable` | `1a3` | Only value **1** enables the battery-only card |
| `acInputDisableSwitch` | `19f` | Reported state: 0 off, 1 on; state 2 blocks the action |

These are app-object offsets, **not** telemetry offsets, TLV tags or wire values.

`updateRechargingPowerModel` (`035e4760`) converts capability equality to 1
into a UI boolean at state offset `43`, and stores the reported switch as an
unboxed integer at state offset `3b`. Missing reported switch becomes 0 in
this view; missing/other capability values hide the card. Those UI defaults
must not be interpreted as an authoritative firmware readback.

`ACRechargingPowerPage.build` (`03ccc544`) shows the card only for that
capability boolean. The switch draws on only for state 1. State 2 is dimmed;
its click handler (`03ccf200`) displays the `batteryOnlyDisableTips` dialog
and returns **without** calling the setter. The physical reason for state 2
is not established. Preserve it as a distinct unavailable/blocked state;
do not normalize it to off or use it as a restorable boolean baseline.

Both `A1763AppModule.onInit` (`045a5050`, C1000 Gen 2) and
`A1783AppModule.onInit` (`045a57bc`, C2000 Gen 2) register the shared
`/a1782HomePage` and `/a1782SettingPage` family and PPS transaction config.
The shared model name therefore does not confine this page to model A1782.
It also does **not** guarantee support on A1763/A1783: neither model's actual
`supportAcInputDisable` value was found in the selected retained captures.
The action closure contains no additional hardcoded Gen 2 model allowlist.

Original A1761 C1000 belongs to a different device-class family and fails the
page's `IotAnkerDevice` type boundary, as established in the
[original app charging audit](c1000-ota-http-wrapper.md). This action does not
add original-model support. No original/C2000 radio binary or newer A1763
main image was recovered by this task.

## Confirmation and transaction limits

The setter updates the visual state to the requested 0/1 **before** the SDK
action runs. Its Future callbacks log result/error and clear the logic's
busy flag; they do not read physical power telemetry or roll the visual
switch back on an error. The model stream later calls the update function
again. A changed switch appearance is therefore not evidence of battery supply.

`ppsTransactionConfig` (`045a452c`) includes `action_set_ac_params` rules for
output status, frequency, charge powers, Smart and Fast, but does **not**
include `acInputDisableSwitch`. The presence of `openTransaction=true` is not
proof that this property's readback is validated. A deeper SDK layer could
have its own handling; that implementation is outside the retained Dart proof.

Even a genuine reported state 1 would need independent measurements to show
battery discharge with mains still qualified and AC output enabled. It would
not establish transfer latency, reserve behavior, reboot retention, restoration
semantics or applicability to a server-backed C2000.

## Next evidence to obtain

1. Read the legitimate app/SDK device-info result for each Gen 2 and record
   both properties, their firmware versions and fresh physical telemetry.
   This first step requires no setting change.
2. If the **noncritical C1000 Gen 2** reports capability 1 and a normal 0/1
   baseline, a future authorized app capture can identify its actual action
   request and reported response while restoring that baseline. State 2,
   missing properties or an absent UI are reasons to retain the research lead.
3. Recover the SDK serialization or an authentic capture before selecting a
   firmware handler to replay. Then establish field bounds, side effects,
   full restoration and fresh readback before adding a public local setter.

No guessed setter or C2000 trial is proposed. Its AC output remains protected.
For current local battery-first behavior, the existing
[verified C2000 Peak workflow](c2000-corrected-peak-trial.md) remains the
established route; this audit supplies a separate lead to investigate.

## Retained evidence and reproduction

Raw selected instructions, the local extraction helper and hashes are kept in
ignored `.solix-private/gen2-ac-input-app-20261002/`, directories 700/files 600.
The evidence manifest records the exact app, metadata and helper hashes,
selected function addresses, runtime versions and zero live/emulation counts.
No phone identifiers, credentials or app binaries are published by this audit.

The bounded cross-reference search finds 13 consecutive ARM64
`ADD(pool page)/LDR` loads for the action and two property strings. This is
not a complete dataflow or all-reference search. It locates SDK wrappers,
transaction config and the JSON model parser, without discovering a wire
serializer. Locally rerun the retained helper with:

```sh
PYTHONPATH=/tmp/solix-analysis-tools python3 \
  .solix-private/gen2-ac-input-app-20261002/trace_gen2_ac_input.py
```

An independent analysis needs its own matching app copy and AOT metadata.
The addresses, exact strings and SDK boundary above are the reproducible
public findings; private extracted app code remains outside the repository.
