# Readable Android SDK assets: scope and mapping boundary

## Result

Static inspection of the retained Android app found a readable React Native
client for chargers and power banks, but no station action-to-packet descriptor.
This narrows the SDK investigation to the native bridge and its implementation;
it does not establish that the native SDK lacks station controls.

No JavaScript or native app code was executed. No device, account, network or
firmware state changed. App binaries and extracted source remain private.

## Exact inputs

| Static input | SHA-256 |
| --- | --- |
| `base.apk` | `27986f94a992f3aac1189be746d310553e99909ed81dfbb1abd5880f71662110` |
| `split_flutter_assets_pack.apk` | `f882ad71c38b2120c805c7695162de624ae2ba5bd959f6055f8d4b84afe696cf` |
| `assets/rn_bundle_android_charging.zip` → `index.charging.bundle` | `924ea0a580ca778534db4c871b40138483d9ed7f5fa5f126f1fedc1aa043c78e` |

The bundle is 4,576,017 bytes, with 2,999 Metro modules. Module IDs and source
line numbers below identify this exact build, not a stable SDK interface.

## Product scope

Module 769 (line 774) has per-port power chart limits for `A1903`, `A1341`,
`A110A`, `A110B` and `A110G`. Their ports are USB-C, USB-A and charging-base
contacts. Module 794 (line 799) contains a 14-entry product-code lookup,
including `B403 → A1341`, `B404 → A110A`, `B406 → A110B`, `B407 → A1903` and
`B40A → A110G`.

The bundle contains no exact `A1761`, `A1763`, `A1782`, `action_set_ac_params`,
`acInputDisableSwitch`, `supportAcInputDisable`, `setACParams`, `chargePower`
or `charge_power` literals. This applies to the bundle only: the Flutter app's
station implementations and the native SDK are separate artifacts. The lookup
and UI tables are evidence of the implemented charging-product pages, not proof
that the native bridge rejects every other product.

## Action and property boundary

All 18 `akiot.device.invoke_action` occurrences are in module 775 (line 780).
They use 16 unique action IDs covering DC-port switches/countdowns/timers,
screen settings/images, charging protocol, device reset, port telemetry
heartbeat, and Bluetooth device grouping.

The module calls `NativeModules.AKIoTKitReactNative.handle` with a string
identifier and a structured object. Device selectors are `deviceSn`, `bleMac`
and `pnCode`; action requests carry an `id` and, where required, a `param`
object. The JavaScript does not map these action names to station command
numbers, TLV fields or MQTT packet bytes.

Its device-information read similarly forwards
`akiot.device.read_property` / `prop_read_device_info`. Returned fields such as
`version` and `supportFunction` are already decoded native payloads; module
2497's `changeDataByLocal` is an identity function. Module 777 (line 782)
consumes `AKIoTEvent` identifiers and decoded payloads, including device
notifications and connection events.

The bundle's byte-buffer helpers do not supply the missing mapper: the
`writeUInt16` methods are in a general Buffer implementation (module 915),
while the application-specific `Uint8Array` use converts screen images for
image transfer (module 2921). Charging-product capability bits and action
parameters must not be applied to the C1000/C2000 by analogy.

## Other asset candidates

The audit inventoried 985 base-APK assets and 4,011 asset-pack entries. It scanned
148 clear text files, 206 text entries within 102 nested ZIPs, and 469 other
nonvisual assets. Static API-key content, visual/font assets and the protected
IJM/native carriers were excluded; native loader analysis is a separate task.

No additional exact station action or charging-field literals above appeared.
The only clear-text station-model hit was `A1782` in a Lottie animation, whose
root fields describe animation layers, assets and timing.

`AssetManifest.bin` was decoded completely: 4,445 path entries occupying
623,883 bytes, with variant fields only `asset` and `dpr`. Its A1761/A1763/A1782
matches are asset paths, not protocol descriptors. All 35 Compose `.cvr`
resource files were decoded as resource data (28,406 entries); none contained
the selected SDK/station mapping literals. The Unity metadata `propertyMap`
match belongs to `MaterialPointerPropertyMap` and glTF animation helpers.

These are bounded negative results for these exact assets and literal names.
They do not rule out renamed, encrypted, generated or downloaded descriptors,
or mappings inside the native SDK. The next useful evidence is the native
`handle` implementation or an authorized capture of its decoded action request
and resulting wire packet for an actual station caller.
