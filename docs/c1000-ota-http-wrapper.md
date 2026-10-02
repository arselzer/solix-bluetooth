# Original C1000: app HTTP processing and firmware capture plan

Offline follow-up dated **2026-10-02**, using only the retained Android ARM64
`libapp.so`, SHA-256
`8537b4f8a4da327f9bf298d99e90ad969b89f52ae7d6d68c454e5bf0ec25c070`.
This extends the [OTA metadata caller trace](c1000-firmware-metadata-followup.md).
No network request, credential extraction, hardware connection or device write
was performed. The installed original C1000 main **1.7.1** image remains missing;
app behavior does not establish its firmware implementation.

## The encrypted wrapper has two transport paths

`HttpEncryptionDecorator.request` at **`04868ff4`** selects its native SDK path
when `AkIotKitFlutter.isLogin` returns true, `needEncryption` is true, and its
URL/base-host string predicate succeeds (`048694f0..04869550`). The OTA caller
retains the default `needEncryption = true`. This is a conditional static path;
the production phone's selected branch was not observed in this investigation.

`_handlerHttpBySdk` at **`04869874`** normalizes the request map and removes
`currentBaseUrl + "/"` from the URL (`04869bb4..04869bf0`). It passes this
envelope to `AkIotKitFlutter.handle` (`021c3d8c`):

```text
identifier: akiot.cloud_api
param: {path: <relative path>, method: POST, param: <body>, header: <headers>}
```

The Flutter method channel is **`ak_iot_kit_flutter`**, method **`handle`**
(`021c3e28`). The native implementation below this boundary supplies the SDK's
wire processing. Its actual signing, encryption, session authentication and
accepted cloud request format are not established by this retained Dart image.
The envelope is an internal SDK argument, not a documented cloud HTTP body.
The [retained APK/native-library inventory](android-sdk-native-boundaries.md)
locates a protected clear-bytecode boundary and selected crypto primitives;
it still does not recover this SDK dispatcher or OTA wire format.

If the SDK conditions fail, `04869700` calls `HttpRequestImpl.request`
(`0486cee4`) through Dart/Dio. In this examined branch, request data reaches
Dio without an intervening Dart AES transformation. The configured
interceptors are duplicate-request cancellation and network observability
(`04868a00`). This does not prove that a hand-built JSON POST is equivalent to
the normal logged-in SDK request or that an anonymous update check will work.

## Authentication is app context, not BLE registration

The Dart fallback's `_headerToken` at **`0486d610`** builds client headers such
as `Model-Type`, `Timezone`, `Os-Type`, `Os-Version`, `Phone-Model`,
`App-Version`, `Country`, `Language`, `App-Name: anker_power`,
`X-Auth-Token` and an empty `X-App-Key`. `StringUtil.httpHeaderToken`
(`02935fb4`) reads the cached user model's token, or takes a separate default
token branch when that model is absent. That branch is not proof of anonymous
OTA access.

There are alternative extra-header branches: an MD5-derived `gtoken` from a
non-null cached model string, or `X-Terminal-ID` and `X-Auth-TS`
(`0486d8c0..0486d9fc`). Caller headers are merged separately. These are app
account/client fields; they are not the generated 40-character local BLE owner
ID or the previously captured station-side API authentication context.
No token or account identifier was read or copied during this audit.

An examined ECDH/AES helper is insufficient to implement OTA encryption:
the direct call inventory reaches `EncryptionUtilImpl.commonHeaders`
(`04016fbc`) and request AES encryption (`0401767c`) from
`exchangeKey` (`04016d54`), whose direct caller is the GDPR consent flow.
Those helpers are not on the established OTA request chain. Indirect uses
elsewhere are not exhaustively excluded.

## Capture the business response after SDK processing

The SDK success closure at **`0486aa38`** constructs `IoTAKResult`, checks its
success enum, then reads **`payload["data"]`** as a string (`0486ab9c`). A
non-empty string is JSON-decoded at **`0486ac18`** and passed to the upper
success callback. An absent/empty string supplies an empty map instead.
The shared wrapper success closure (`02266374`) forwards that argument, and
the OTA closure **`023f9c3c`** passes it directly to
**`OtaUpdateModel.fromJson` at `023f9c74`**.

This gives two precise prospective capture points: the SDK's plaintext
`payload["data"]` string, or the map immediately before the OTA model parser.
They avoid guessing the native encrypted wire envelope. Availability of
authorized instrumentation on the user's production phone remains unproved.
Ordinary Bluetooth HCI and station-side TLS packet captures do not reveal
this app-to-cloud plaintext response.

The Dart/Dio branch differs: `_handleResponse` (`0486c2dc`) constructs a
`Result` with `code`, `msg`, `signature` and `data`. `_handler200`
(`0486c680`) requires HTTP 200 and `code == 0`, optionally validates a
non-null signature, then passes `Result.data` upward. Its examined validator
(`0486c99c`) uses `data["server_public_key"]` or an empty string, request
timestamp/nonce, and a native HMAC helper. It does not serialize the complete
OTA metadata map for that check. The preimage builder (`04017200`) forms
`timestamp + "+" + nonce + "+" + supplied_data`; the native HMAC algorithm
is outside this Dart implementation. This is not evidence of firmware
artifact authentication or of SDK response processing.

## Built-in logging has concrete limits

The SDK request-info capture is enabled only when `serverEnv` is **QA or CI**
(`04869a0c..04869a64`); the success closure also checks that flag before
recording the response. `RequestContainer.addRequest` (`046f4ba4`) keeps the
newest **100 entries** in a RAM list and notifies listeners. No persistence
path was found in that function. Production request capture cannot be assumed
from the presence of these classes.

The upper success closure does prepare a plaintext log message containing
the URL and response (`02266410..02266468`). `isLogResponse` defaults true
(`02264b98`); its false branch truncates the response to 200 characters.
However, `Dev._exe` (`021bc3f8`) has an early global logging gate, and its
output passes through additional logger filtering/batching. Neither an
available production log nor a saved full OTA response is established.
Prove logging availability with a fresh authorized capture before relying on
it; changing app environment or debug gates is not part of this plan.

## A bounded acquisition plan for main 1.7.1

1. When the phone is available, record the original device's app-reported
   product, device-version text, main/radio versions and app version privately.
   Open an ordinary firmware-information check with installation unstarted;
   do not fabricate `force_version`, downgrade or component selection.
2. Start an app-local plaintext capture before that check. First verify whether
   the normal success log exposes the full result; otherwise the known SDK
   success/OTA parser boundary is an instrumentation lead requiring its own
   authorized, feasible method. TLS interception alone is insufficient if
   native request/response processing still hides the business payload.
3. Retain the **complete** `OtaUpdateModel` data: `lastPackage` plus `children`,
   product/component, version, size, URL and hashes. Children can use
   `full_package.file_path/file_size/file_md5`; do not extract just one URL.
   Keep tokens, device identifiers, signed URLs and raw captures private.
4. If an authorized response supplies a matching artifact, retrieve that
   exact URL separately, preserve the original package and verify its byte
   length and supplied hash. Confirm original **A1761**, component and hardware
   variant before treating it as main 1.7.1. MD5 checks byte consistency; it
   does not prove a package's signing/authenticity or make local installation
   supported. No firmware update is needed to analyze a downloaded artifact.

The already-current device may return no package. Access to historical 1.7.1
metadata is still unknown. The app's history models do not supply a proved
download URL, and changing the known old S3 object's version text is not an
acquisition method. This task has not recovered an artifact or made a cloud
request.

## Ordinary charging actions with original-model scope

The retained **`A1761AnkerDevice`, class ID `212a`**, inherits the
`A1753AnkerDevice` / `A1771AnkerDevice` family and its BLE controllers.
Following actual callers separates existing original controls from unrelated
IoT names:

| Examined app route | Actual result and boundary |
|---|---|
| `A1753homeLogic.setSuperCharge` (`03c53a04`) → `A1771AnkerDevice.setSuperCharge` (`03c53b6c`) → controller (`03c53c4c`) | One byte 0/1 on **`005e`**, the existing Fast setting. The same flow emits **`APP_DEVICE_CHARGE_SWITCH_CLICK`**; the analytics name does not establish physical charging enable/pause. |
| Inherited `A1771AnkerDevice.setAcChargePower` (`046415a0`) | Two-byte watt value via `CmdUtil.intToList2`, opcode **`0044`**, consistent with the existing native original charging-power control. No additional bypass/source switch appears in this setter. |
| `A1781settingLogic.gotoChargeDischargeLimitsPage` (`03c65900`) | Its field-access branches require **`A1790AnkerDevice`**, not the original class. Inheritance of the surrounding settings controller does not establish an A1761 percentage-limit setter. |
| `ACRechargingPowerLogic.onInit` (`035e4364`) and `onAcInputDisableSwitchChanged` (`03ccf29c`) | The page requires **`IotAnkerDevice`** (`035e4528`), which the original class is not. Its battery-only/AC-input-disable action name is not an established original route. |

These bounded caller checks found no new normal A1761 charging-pause or
battery-forcing command that preserves AC output. They do not exhaust every
possible firmware feature. Existing [native below-full testing](c1000-native-charging-test-plan.md)
remains useful once the original has charging headroom and a load. Its latest
100% / 0 W idle baseline provides no meaningful charge-rate trial.

## Reproduction

Capstone disassembly was checked against the retained AOTopsy object-pool and
class metadata; the SDK path, SDK success parser, OTA closure, header builder,
signature validator and model-specific charging paths were also independently
extracted with the existing local AOTopsy tool. Opcode arrays resolve to
`[0,94]` and `[0,68]`, respectively. Decompiler pseudocode is navigation only;
there are no additional claimed CPU emulation cases.

The offline extraction helper, selected disassembly, call inventory and
evidence manifest remain in ignored
`.solix-private/original-http-wrapper-20261002/`, with directories 700/files
600. The prior [1,064-case source-qualification replay](c1000-second-input-qualification.md)
was not repeated or changed. Firmware conclusions from main 1.5.9 remain
explicitly bounded to that image until main 1.7.1 is acquired.
