# Original C1000: firmware metadata and update precharge scope

Offline investigation dated **2026-10-02**. The retained Android app's ARM64
`libapp.so` has SHA-256
`8537b4f8a4da327f9bf298d99e90ad969b89f52ae7d6d68c454e5bf0ec25c070`.
This is app instruction/object-pool evidence, not a cloud response or firmware
implementation. No request, device command, account change or update was sent.

## A concrete artifact-metadata path

`BaseBleController.firmwareUpdateRequest` at **`0263a178`** calls the shared
`HttpRequestController.checkOTAUpdateWifi` at `023f95ac`, reaching its
implementation **`023f982c`**. The OTA page also uses this helper from
`AnkerOTABaseLogic.onInit` (`03414594`) and `requestOTA` (`0373ade8`). This is
stronger evidence than merely finding an endpoint string in the app.

Actual stores into the interpolation array at `023f9a68..023f9aa0` construct:

```text
/app/ota/<product-code>/ALL/update/<device-version>
```

The original model's getter at `036c2778` returns **`A1761`**. The BLE caller
supplies the device's `deviceVersion` member at object offset `7b`, independently
identified by its getter `028479bc`. This is distinct from the separately stored
`otaMcuVersion` / `otaModuleVersion` members. An empty version becomes `1.0.0.0`.
The exact version text should come from the app's model; prefix/format should
not be guessed from the display.

The helper builds this map and passes it with **`Method.post`** to
`encryptionRequestHttp` at `02264510`:

| Key | Actual source/default |
|---|---|
| `device_mac` | Caller-supplied device identifier |
| `sub_package` | Optional `subPackageJson`, default null |
| `sub_ota_way` | Optional integer, default 0 |
| `force_version` | Optional `forceVersion` converted to 0/1, default 0 |
| `device_type` | Optional `deviceType`, default null |

The request path is relative to the app's HTTP configuration. A further wrapper
trace shows `encryptionRequestHttp` defaults `needEncryption` to true and
forwards it to `requestHttp` (`02264924`); this OTA caller supplies no override.
With the default `useBaseUrl`, `requestHttp` prepends `RequestApi.currentBaseUrl`
(`02264e50`), which resolves environment/country through
`SpUtil.getHostByRegion` (`022650d8`). That resolver checks a cached regional host
before its local fallback. A captured station-side API host therefore cannot
simply be assumed to be the app's OTA host. The lower encryption, authentication
and response processing remain untraced; a plain JSON POST is not established
as an equivalent request. Despite being used for a version check, the endpoint is a POST:
no server-side read-only guarantee is established. Changing `force_version`
or treating this as an install API is unsupported.

## The parsed response contains a download URL

The success closure `023f9c3c` constructs `OtaUpdateModel.fromJson` at
`023f9cb4`. It parses `change_log`, `current_version`, `lastPackage`,
`needUpdate`, `need_rollback`, `children`, `is_pop_up` and `threshold_ver`.
`LastPackage.fromJson` at **`023fa5c8`** reads:

| Field | Model offset | Type checked by the parser |
|---|---:|---|
| `is_forced` | `7` | bool |
| `md5` | `b` | string |
| `product_code` | `f` | string |
| `product_component` | `13` | string |
| `size` | `17` | int |
| `upgrade_scheme` | `1b` | int |
| `url` | `1f` | string |
| `version` | `23` | string |

The independent `LastPackage.toJson` at `023fa388` emits those same keys.
`FirmwareUpdateInfo.fromOtaUpdateModel` at `0280f7b8` subsequently reads the
package's `url`, `md5`, `size` and `version`, supporting their download purpose.
Children use a separate `full_package` object with `file_path`, `file_size`
and `file_md5` (`023fafb0`); do not assume every component uses `lastPackage`.

These are **schema fields**, not a recovered 1.7.1 URL, hash, size or package.
The captured official update remains [TLS-encrypted](c1000-original-update-network.md).
A fresh, authorized app metadata response before an offered update would be
useful: retain the complete package/component metadata privately, then verify
the artifact's product, version, variant and hashes before analysis. A check
on an already current device may return no package. Historic-version access
and exact main/radio contents remain unproved.

## Two tempting routes have narrower scope

**Compatible OTA:** `compatible/get_ota_info` is used by `getThirdOtaInfo`
(`036c7d88`), called through the MicroInverter OTA mixin. Its body contains
`solar_bank_sn` and `solar_sn`. This trace does not establish it as an A1761
firmware metadata route. The related compatible endpoint names alone supply
neither an original-model request schema nor a charging command.

**Update history:** `get_upgrade_record` (`02912048`) sends `type` and
`device_sn`; `check_upgrade_record` (`02913854`) supports `device_sn`,
`device_sns` or neither, plus `type`. Their parsed history models record
versions, dates, descriptions and child records (`029126bc`, `02912c68`);
the examined models contain no package URL/hash. Unparsed server fields are
not ruled out, but history is weaker artifact evidence than `lastPackage`.

## Update precharge does not establish an original charging control

`triggerOtaPreCharge` at `03d19304` invokes the IoT action
**`action_set_ota_charge`** with **`{"otaChargeStatus": 1}`**. The app's
eligibility function `03d18c38` first requires a supported setup mode and an
exact **`IotAnkerDevice` class ID `20fa`**, then reads `otaChargeSupport == 1`.
The retained original C1000 is **`A1761AnkerDevice`, class ID `212a`**, and
fails that exact class check. The setup gate at `03d18d24` additionally limits
the feature to `singleDeviceUse`, `newMainDeviceBuild` or
`mainDeviceJoinEmptyStation`.

No matching original BLE opcode, local MQTT setter, pause argument, reserve
policy or discharge mode is established. Bypassing a UI capability check
would not demonstrate device support or preservation of AC output. This
action is update preparation evidence, not a supported A1761 charging lever.

## Reproduction and boundaries

ARM64 instructions were checked with Capstone and cross-checked against the
already retained AOTopsy snapshot extractor, including the canonical `/`
separator, enum `Method.post`, original class IDs and ordered model fields.
Decompiler pseudocode was used for navigation, not as an instruction replay.
There are no additional claimed emulation cases for this app audit.

Selected disassembly, including the wrapper/base-host follow-up, snapshot
extraction, call-edge inventory and the local
`trace_ota_metadata.py` helper are in the ignored
`.solix-private/original-input-qualification-20261002/` (directories 700,
files 600). Raw app/phone files and authentication identifiers are unpublished.

The installed main **1.7.1** image is still missing. The public main **1.5.9**
[input-qualification replay](c1000-second-input-qualification.md) explains
older qualified source handoff but proves no external bypass/pause control.
For live charging evidence, use the existing [below-full native test plan](c1000-native-charging-test-plan.md)
after the original has useful charging headroom and a load; its present
100%/0 W idle baseline does not justify rate writes.
