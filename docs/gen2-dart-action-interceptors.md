# Gen 2 Dart SDK actions: interceptor registration and native handoff

## Result and scope

The retained app's ordinary AC action path has a concrete transaction-lock
interceptor. Its AC key
aliases are used to build a **separate app-side property lock**, not to rename
outgoing command arguments. A pure `acInputDisableSwitch` action reaches the
Android SDK with that same integer parameter; this audit establishes no native
BLE opcode, MQTT frame, transport choice or device-side input-disable handler.

This extends the [AC-input-disable app audit](gen2-ac-input-disable-app-audit.md)
using ARM64 instructions and Dart 3.11.0 metadata from the same retained
`libapp.so`, SHA-256
`8537b4f8a4da327f9bf298d99e90ad969b89f52ae7d6d68c454e5bf0ec25c070`.
There were **no app executions, CPU emulation cases, network requests or device
commands**. The app image does not establish main/radio firmware equivalence.

## Actual interceptor registration

| Boundary | Address | Instruction evidence |
| --- | --- | --- |
| `registerService` | `049dfaa8` | Creates an empty growable list, constructs `AKIoTKitManagerImpl`, stores the list in manager field `7`, and registers it with the service locator |
| Manager initialization | `049e0304` | Calls `AKIoTKitManagerImpl.init`; initializes the platform SDK and event receiver |
| Interceptor registration | `049e0358` | Appends a newly allocated `TransactionLockEventInterceptor` to that same list |
| Parameter fold | `0221aef0` | Folds the manager's field-`7` list with the command map as accumulator |
| Parameter fold callback | `0221afbc` | Directly calls `TransactionLockEventInterceptor.interceptParams` at `0221afd4` |
| Result fold callback | `0221d02c` | Directly calls the same class's `interceptResult` at `0221d044` |

The bounded direct-`BL` scan of sized functions finds only these registration
calls to the manager/interceptor allocation stubs. The symbol inventory contains
this one named interceptor implementation and its matching closure. This is
stronger evidence than a string search, but does not prove that all indirect
list mutations or allocations in every possible runtime environment are absent.

## What the interceptor changes

`interceptParams` returns the original command map unchanged unless transaction
locking is enabled, the device serial resolves to an `IotAnkerDevice`, capability
negotiation permits locking, and a transaction rule matches the action ID.

`supportGetUpgradeInfo` (`0221cbdc`) accepts reported
`supportTransactionId == 1`. Otherwise it returns the configuration's fallback
boolean. Shared `ppsTransactionConfig` explicitly initializes that fallback to
**false** (`045a503c`–`045a5040`); registering a configuration alone does not
enable transaction support. `supportTransactionId` is separate from the UI's
`supportAcInputDisable` gate.

For matching `akiot.device.invoke_action` requests:

1. It reads each configured input key from the existing nested `param` map.
2. It copies non-null values into a new `lockPropertyMap`, choosing a configured
   report-key alias there when present (`0221b4dc`–`0221b550`).
3. It calls `TransactionLockManager.lockPropertiesForCommand` (`0221b720`).
4. Only a non-null returned ID is added as `param.transactionId`
   (`0221b5dc`–`0221b690`). The original command map is returned.

The shared AC rule aliases **`acInputStatus` → `acOutputStatus`** in that lock
map. It does not rewrite `acInputStatus` in the outgoing request, and supplies
no native tag. Treat this name as the separate AC-output action discussed in
the earlier audit; it is not a substitute for input disable.

The full shared PPS rule initializer (`045a452c`) has no
`acInputDisableSwitch` or `acTimerValue` lock rule. For a command containing only
the input-disable switch and optional owner metadata, the lock map stays empty.
The lock-manager entry returns null immediately for an empty map
(`0221b748`–`0221b770`), so this path adds **no transaction ID** even when
transaction capability is reported. Absence of a lock rule neither rejects the
action nor proves device support.

## Owner metadata and actual serialization

Before interception, `_buildDeviceParams` (`0220c3a4`) builds the outer
`deviceSn`/`id` map and optional `bleMac`, then merges owner metadata into the
action's nested parameter map. `buildParamsWithOwnerId` (`0220c4d4`) looks up the
device by nonempty serial, or by Bluetooth address when no usable serial was
given. A nonempty cached owner value is added as **`ownerUid`**; missing/empty
owner data adds nothing. This is app-cache behavior, not proof that the device
accepts an anonymous action or that an account is required by every transport.

The resulting bridge request is structurally:

```text
channel: ak_iot_kit_flutter
method: handle
arguments:
  identifier: akiot.device.invoke_action
  param:
    deviceSn: <private device serial>
    bleMac: <optional private Bluetooth address>
    id: action_set_ac_params
    param:
      acInputDisableSwitch: 0 or 1
      ownerUid: <optional private cached owner value>
```

This describes the app/SDK boundary, not a broker publish or BLE command.
`AkIotKitFlutter.handle` (`021c3d8c`) sends this map via a channel object whose
codec is **`StandardMethodCodec`** (`021c3e10`). `MethodChannel.invokeMethod`
(`048a348c`) calls `_invokeMethod` (`0326ce64`), which constructs a `MethodCall`,
encodes it and sends it through the binary messenger. The selected codec's
`encodeMethodCall` (`048a309c`) writes the method and arguments separately using
`StandardMessageCodec`, then returns the write buffer. Its map/string/integer
serialization is Flutter IPC; it supplies no Anker opcode, TLV tag, native
envelope or device authentication algorithm.

Both `A1763AppModule.onInit` (`045a5050`) and `A1783AppModule.onInit`
(`045a57bc`) install the shared PPS configuration and shared IoT pages. This
is an actual product-code registration: the builder (`04544874`) copies the
transaction configuration from builder field `4f` into config field `3b`, then
`register` (`04544808`) stores the config in the shared product-code map. The
interceptor's `getTransactionConfig` (`0221cb94`) resolves that map through
`getForProductCode` (`0220bf54`) and returns field `3b`. These are Dart object
offsets, not device protocol fields.

This establishes static app routing for C1000 Gen 2 and C2000 Gen 2. Actual capability
values, normal input-disable readback and physical behavior on either device
remain unverified; it does not establish original C1000 support.

## Failure handling is not a restoration guarantee

The result interceptor immediately returns successful SDK results. On a failure
with transaction locking enabled, it reads **top-level** `deviceSn` and
`transactionId` (`0221d144`–`0221d1a8`); both must be non-null before
`unLockForFail` is called. That lower helper contains app-property unlock and
original-info restoration calls. These are app state operations, not a reverse
device write or a fresh physical-state check.

On the examined ordinary action path, the parameter interceptor inserts the ID
under **`param.transactionId`**, while the manager's result fold passes the
whole processed command map. No selected wrapper lifts that nested ID to the
top level. Accordingly, these instructions do not guarantee the failure
rollback path is reached for that construction. A pure input-disable switch
also has no generated transaction ID. This does not rule out other callers
providing top-level metadata, and is not a universal SDK defect claim.

Do not use a completed Future, an optimistic UI switch or transaction-lock
metadata as the baseline/confirmation for a reversible charging experiment.
An independently decoded, fresh device report remains necessary.

## Reproduction and next useful boundary

The restricted extraction helper is retained locally at
`.solix-private/gen2-dart-action-interceptors-20261002/trace_dart_action_interceptors.py`.
Run it only with the exact app and matching retained AOT metadata available:

```sh
PYTHONPATH=/tmp/solix-analysis-tools python3 \
  .solix-private/gen2-dart-action-interceptors-20261002/trace_dart_action_interceptors.py
```

Its manifest records 37 unique selected functions, exact input/evidence hashes,
29 checked instructions and the bounded direct-call scan; these instruction
checks are **not CPU replay cases**. Raw app disassembly and metadata remain
private with directory mode `700` and file mode `600`. No credentials or
retained app binaries are published with this report.

The remaining useful mapper is the Android implementation of
`ak_iot_kit_flutter`/`handle` for `akiot.device.invoke_action`. The
[native SDK inspection](android-sdk-native-boundaries.md) documents the
protected-loader boundary in the retained APK. Recover an actual action mapper
or a correlated official-app command plus fresh reported state before extending
the native library. There is no new input-disable setter to send from this
static Dart result.
