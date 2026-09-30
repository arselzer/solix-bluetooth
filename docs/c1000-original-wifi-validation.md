# Original C1000: isolated Wi-Fi and local API observation

## Device and scope

On **2026-09-30**, an original **A1761 C1000** joined a dedicated WPA2 AP
using the SDK's app-derived legacy Bluetooth provisioning. Its local HTTP
update request identified main **`v1.5.1`** and radio **`v0.1.3.0`**. The radio
reported a **16-character serial**, unlike the tested Gen 2 stations' 17.
Raw requests, identifiers, secrets and packet captures remain private.

This proves WLAN association and endpoint replacement. It does **not** prove
original-model direct MQTT, credential decryption, charge caps, reserve or
forced discharge. The station had no internet route; no Anker request was made.

## Successful first trial

The laptop used the existing P-256/AES-CBC session. `4024` used untyped A1–A6;
`4025` used the original-specific A1–A8 layout, including **A5 country `AT`**,
product A1761 and **no C3**. Account ID was newly generated locally, with no
Anker login. See [app recovery](c1000-app-provisioning-investigation.md).

- `4824` returned `00`; hostapd recorded association and DHCP acknowledged a lease.
- `4825` timed out, yet the radio subsequently contacted the configured HTTP API.
- The probe returned only minimal empty-data success responses. It supplied
  no working MQTT credentials, so no TLS/MQTT connection was expected.

Observed POST paths, after removing the radio's doubled leading slash:

| Path | Body fields |
| --- | --- |
| `/equipment/devicemanage/get_mqtt_info` | `device_sn`, `check_code` |
| `/equipment/devicerelation/bind_device` | Above plus `account`, `name`, `time_zone`, `wifi_ssid` |
| `/equipment/devicerelation/check_relate_bind_device` | `device_sn`, `check_code`, `account`, `bt_ble_mac` |
| `/equipment/devicemanage/update_info` | `device_sn`, `check_code`, `parent_sn`, `account`, `main_sw_version`, `sec_sw_version` |
| `/equipment/help/dst` | `device_sn`, `check_code`, `account`, `city` |

Repeated calls are retained. Their presence proves request construction and
delivery, not that our empty response satisfied each radio parser.

## Bootstrap retry and its limit

A second eight-minute isolated trial prepared local certificates and a
**candidate** serial-derived envelope adapted to 16 characters. However,
the station joined Wi-Fi without making an HTTP request to this new listener.
Provisioning and a single activation-only retry returned `4825=26`.
There was no TLS connection. Consequently, the prepared response was **never
consumed**; neither its cipher layout nor HTTP framing was tested.

The error's meaning and radio retry/binding state remain unknown. Do not infer
that original firmware rejected the certificates or that Gen 2 bootstrap works
unchanged. Recovering the original radio's parser or observing a fresh bootstrap
is the next useful step; arbitrary credential variants cannot resolve a trial
that never reaches HTTP.

## Power baseline and packaged support

Both trials recorded and checked three fresh final snapshots: AC on, DC off,
charging power 1000 W, Device Timeout 720 minutes, display timeout 30 seconds,
brightness 2, light off, Celsius, fast charge off and both Smart modes enabled.
No output switch or charging-setting command was sent. Owned AP processes
were stopped; the original retains the isolated WLAN profile without a running
AP. Its earlier WLAN credentials were unavailable, so this is **power-setting
restoration**, not restoration of unknown network credentials.

`wifi-join` and `wifi-setup` now accept `--model c1000` saved devices;
`wifi-setup --country-code AT` selects its original activation field. If no ID
is supplied, the CLI generates and privately saves a reusable local ID.
Printed results contain acknowledgements, not credentials. The production
native AP service still supports **Gen 2 only**. Original C1000 remains usable
through the Bluetooth gateway and BLE-to-MQTT bridge.
