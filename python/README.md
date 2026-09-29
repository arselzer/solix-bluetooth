# solix-gen2-ble

Async Python monitoring for Anker SOLIX C1000 Gen 2 (A1763) and C2000 Gen 2
(A1783) over local Bluetooth, plus verified C1000 charge-limit, AC charging
power, display timeout, and fast charge switch controls. C2000 upper charge cap,
charging power, and 30/60-second screen timeout are also verified. It uses no cloud account.

Install from this repository (include `server` and/or `mqtt` for the network services):

```bash
pip install './python[server,mqtt]'
```

Pair a Prime station once as described below, then find a nearby unit and
print updates using its saved client ID:

```python
import asyncio
import os
from solix_gen2 import SolixMonitor, discover

async def main():
    devices = await discover()
    if not devices:
        raise RuntimeError("No SOLIX Gen 2 device is advertising")
    async with SolixMonitor(devices[0], owner_user_id=os.environ["SOLIX_CLIENT_ID"]) as monitor:
        while True:
            print(await monitor.wait_for_update(timeout=30))

asyncio.run(main())
```

For a Home Assistant integration, reuse the discovered `BLEDevice` and register
an update callback:

```python
monitor = SolixMonitor(
    ble_device,
    owner_user_id=saved_client_id,
    on_update=lambda metrics: coordinator.async_set_updated_data(metrics),
)
await monitor.connect()
# On unload: await monitor.disconnect()
```

`monitor.metrics` contains the latest decoded values. `monitor.raw_tlvs` keeps
the original parameter bytes for further model decoding. C1000 Gen 2 metric
offsets follow the [SolixBLE C1000G2 implementation](https://github.com/flip-dots/SolixBLE/blob/main/SolixBLE/devices/c1000g2.py).

For Wi-Fi/MQTT troubleshooting, query radio diagnostics on an existing Prime
connection with `await monitor.network_diagnostics()`, or run:

```bash
solix-gen2 network-diagnostics --name ups
```

The result contains `http_error_code`, `wifi_error_code`, `ble_disconnect_code`,
`mqtt_error_code`, `system_reboot_code`, and `sdk_reset_code`. This query was
tested on C2000 main firmware 2.1.6.4 and replayed against C1000 radio v0.3.3.0;
the C1000 hardware query remains untested. It changes no power/network settings.
Preserve the first result: reset codes may become 255 on later reads. Zero
errors do **not** prove a connection, and these are not battery/inverter faults.
Run it separately from Wi-Fi provisioning, which shares the response queue.
See [native MQTT findings](../docs/local-mqtt-investigation.md).

For UPS monitoring, `ac_input_connected` is 1 while the mains lead is present
and 0 when it is absent, even if AC output remains on. `battery_status` is
`idle`, `charging`, `discharging`, or `unknown`; `battery_discharging` is a
numeric 0/1 for Prometheus. `time_remaining_minutes` is the device's estimate
to full or empty while charging or discharging, and 0 while idle. Check
availability before using these values for alerts. The C1000 input field was
confirmed by a live unplug/replug test; the C2000 field was checked read-only
while connected, but its outage transition was not tested. C2000 telemetry also
includes the main/controller/inverter/BMS/wireless software versions, AC input
frequency, configured AC charging-power limit, and expansion-battery count when
their corresponding raw blocks are present. These were decoded from six live
read-only C2000 samples; no alarm or fault code has been identified. The C2000
also reports AC/DC output timer countdowns, AC/DC power-saving flags, device
timeout, fast-charge state, and output-port memory from its `A4` settings block.
Their offsets follow the published C2000 map and match a retained live snapshot;
their transitions have not been independently tested on this station. A timer
countdown of zero means no active timer in the observed baseline. The C2000
display timeout was independently changed 30→60→30 seconds with AC output on.
Its `D9` block also reports `usage_mode`, `active_tariff`,
`backup_reserve_percentage`, `tou_schedule_parameter`, and
`tou_schedule_slot_count`. The saved live baseline decodes to `standard`,
`none`, 10%, and zero slots. These read-only fields help distinguish grid
bypass from a Time-of-Use Peak period. Guarded C2000 `4090` writes changed and
restored the reserve, usage mode, and one-slot Peak and Off-Peak schedules,
each confirmed in telemetry with AC output on. A confirmed all-day Peak slot
still left the active tariff at `none` and the battery idle for 78 seconds, so
schedule storage is not yet a verified charging/discharging control. See the
[discharge-mode field notes](../docs/gen2-protocol.md#battery-discharge-while-ac-output-stays-enabled).
Anker's [C2000 app guide](https://lp.ankerjapan.com/hubfs/aoos/manual/A1783Guide.pdf)
specifies Wi-Fi for Time-of-Use. A second guarded Peak test with the correct
`Europe/Vienna` Bluetooth timezone still did not activate a tariff; it restored
the original settings with AC output on. The C2000 subsequently joined an
isolated Wi-Fi AP via Bluetooth and sent four setup requests to a local API
recorder. Wi-Fi association and a local NTP reply still did not activate Peak;
the empty API responses did not start MQTT. The C2000's isolated SSID may remain
saved while that AP is off. A later local API probe returned generated MQTT
certificates and binding acknowledgements; the C2000 then requested an unbind
and never looked up or connected to the local broker. Repeating setup with the
real app account ID from private phone logs produced the same unbind.
Time-of-Use activation over isolated Wi-Fi remains unverified.
The [firmware analysis](../docs/firmware-findings.md) now traces the C1000's
binding and network-readiness requirements and its different schedule layout.
Those findings are not yet verified controls; do not reuse a C2000 schedule
payload on C1000. Native device MQTT remains experimental; the supported local
MQTT bridge uses BLE to communicate with each station.
C1000 Gen 2 firmware 1.1.4.3 uses legacy AES-CBC. After updating to 1.1.4.9,
the same unit switched to Prime AES-GCM and required button pairing with a
generated client ID. C2000 Gen 2 also uses Prime. Prime telemetry and the
read-only `4100` subscription were verified live on both units. C1000 firmware
1.1.4.9 also supports verified AC/DC output, charge-limit, and charging-power
commands. Both devices must be available over BLE near the machine running Home Assistant, or
through a compatible Bluetooth adapter or proxy.

## Pairing a Prime Gen 2 station

Both tested Prime stations accepted a generated 40-character hexadecimal ID after one
short press of the station's **main power button**. Each returned `09` before the press,
accepted the same ID when registration was retried on the same BLE connection,
and accepted it again on a later connection without another press. An Anker
account ID was unnecessary for these tested units.

`SolixMonitor` generates an ID when one is not supplied. Start `connect()` as a
task, wait for `pairing_required`, press the main power button once, then call
`confirm_pairing()`:

```python
import asyncio
from solix_gen2 import SolixMonitor

monitor = SolixMonitor(c2000_device)  # Use protocol="legacy" for C1000 firmware 1.1.4.3.
connect_task = asyncio.create_task(monitor.connect(timeout=120))
await monitor.pairing_required.wait()
await asyncio.to_thread(input, "Press the station's main power button once, then Enter: ")
await monitor.confirm_pairing()
await connect_task
print(monitor.owner_user_id)  # Save this ID in your Home Assistant config.
# Later connections: SolixMonitor(c2000_device, owner_user_id=saved_id)
```

The library keeps this ID in the monitor object, but does not save it to disk.
The `owner_user_id` name is retained for compatibility with existing SOLIX
tools; on both tested Prime stations, it acts as a locally paired client ID. If you lose
it, you may need to pair again. [Anker's setup guide](https://salesforce-knowledge-download.s3.us-west-2.amazonaws.com/000032532/en_US/000032532.pdf)
also shows a short main power button press to confirm a new connection. Do not
hold the button or press the separate AC output button.

## CLI and network server

The package installs a `solix-gen2` command. Pair each Prime station once and
save both in a config file:

```bash
solix-gen2 scan
solix-gen2 pair --name c2000 --address AA:BB:CC:DD:EE:01 --timezone Europe/Vienna
solix-gen2 pair --name c1000 --address AA:BB:CC:DD:EE:02 --model c1000_gen2
solix-gen2 monitor
solix-gen2 serve --host 0.0.0.0 --port 8765
```

`pair` prompts for one short main button press if needed and writes the
generated ID to `~/.config/solix-gen2/config.json` with owner-only permissions.
`--timezone` saves the station's IANA timezone for the Prime handshake; it is
useful when the HA host runs in UTC but the station is elsewhere. The Python
constructor accepts `timezone_name="Europe/Vienna"` for the same purpose. An
existing device can be updated with `solix-gen2 add` and its saved client ID.
Use `--config /path/to/config.json` on `pair`, `add`, `monitor`, or `serve` to
choose another path. This workspace already has a working, ignored config at
`.solix-private/config.json`; copy it to the Home Assistant node with private
file permissions to avoid pairing again. The monitor service and HTTP server
never send setting or AC/DC output commands.

For a C1000 still on firmware 1.1.4.3, use:

```bash
solix-gen2 add --name c1000 --address AA:BB:CC:DD:EE:02 --model c1000_gen2 --protocol legacy
```

### Verified Gen 2 settings

On the tested C1000 firmware 1.1.4.9, the Python client and CLI can set the
charging upper limit, discharge lower limit, AC charging power, display
timeout, and fast charge switch. Each write waits for telemetry to confirm the
result. The C1000 can set both SoC limits and fast charge; the C2000 can set
only its upper charge cap at 80–100% in 5% steps, leaving its lower limit
untouched. The C2000 also supports AC charging power at 300–1800 W in 100 W
steps and screen timeout at 30 or 60 seconds. Its output controls remain blocked.

```bash
solix-gen2 set-limits --name c1000 --upper 90 --lower 1
solix-gen2 set-charge-power --name c1000 --watts 1000
solix-gen2 set-display-timeout --name c1000 --seconds 60
solix-gen2 set-fast-charge --name c1000 --enabled on
solix-gen2 set-display-timeout --name c2000 --seconds 60
solix-gen2 set-charge-power --name c2000 --watts 1700
solix-gen2 set-charge-cap --name c2000 --upper 95
```

Use `--config /path/to/config.json` if the saved device uses a nondefault
config. On C1000 this implementation allows upper 80–100% in 5% steps, lower
1%, 5%, 10%, 15%, or 20%, and 300–1200 W in 100 W steps. Upper 80%/95%/100% and
charging power 300/1000/1200 W were independently exercised from the laptop. The direct Python methods are
`await monitor.set_charge_limits(90, 1)` and
`await monitor.set_ac_charging_power(1000)` (also on C2000), plus
`await monitor.set_display_timeout(60)` (also on C2000) and
`await monitor.set_fast_charge_enabled(True)`. For C2000 use
`await monitor.set_charge_cap(95)` to change only its upper limit. The latest telemetry includes
`max_charge_percentage`, `min_charge_percentage`, and
`ac_charging_power_limit_w`, `display_timeout_seconds`, and
`ac_fast_charge_enabled`. Stop a running BLE monitor/server before invoking
a separate CLI control process if the station allows only one Bluetooth client.
The live check changed 100%→95%→100%, 1200→1000→1200 W,
display timeout 30→60→30 seconds, and fast charge off→on→off; AC stayed on and
DC stayed off. The C2000 display timeout was changed 30→60→30 seconds while
its AC output stayed on at approximately 393 W. C2000 AC charging power was
changed 1800→1700→1800 W and 1800→300→1800 W; battery stayed at its 90% cap,
mains remained present, and AC output stayed on even as its load rose to about
1 kW. Intermediate 100 W steps are inferred from the shared packet format and
were not all tried on this unit. Because the battery was at its cap, the test
confirms the setting value, not the actual charge rate. Fast charge was tested
while the C1000 battery was full, so its actual rate was also not measured.
On the C2000, a separate live test set charging power to 500 W and raised its
upper cap 90→95%. AC input rose to 923–927 W while AC output stayed at
401–411 W, and the station reported charging. Restoring the 90% cap and
1800 W power limit returned it to idle with AC input and output both 396 W.
The lower discharge limit remained 1% throughout.
See [field notes](../docs/gen2-protocol.md) for other app-observed
commands that are not yet exposed as controls.

The charge upper limit is not a local Time-of-Use switch: on the tested C1000,
lowering it from 100% to 80% while the battery was full left the AC input
supplying the AC output and the battery idle. It was restored to 100%. The
minimum verified AC charging-power limit is 300 W. With a 771 W AC load,
setting that limit to 300 W still left the grid supplying the entire load
and the full battery idle; the limit was restored to 1200 W. Neither setting
redirected AC loads to the battery. The tested app required Wi-Fi to open
Time-of-Use mode. Local Wi-Fi provisioning is now available experimentally as
described below. The C2000 Time-of-Use mode selector is verified over BLE, but
Peak scheduling and battery discharge with mains connected remain unverified.

### Local MQTT bridge

`mqtt-bridge` connects to the stations over Bluetooth and publishes their
telemetry to a broker on the same node or LAN. The stations themselves do not
connect to this broker. This offers MQTT monitoring and the verified C1000
settings while direct station-to-MQTT binding remains under investigation.

```bash
solix-gen2 mqtt-bridge --config /path/to/config.json \
  --broker 127.0.0.1 --port 1883
```

For a broker requiring authentication, add `--username NAME` and
`--password-file /path/to/owner-only-password-file`. Use `--ca-file` to enable
TLS with a trusted broker CA. Keep broker command topics restricted to trusted
clients. Run either `mqtt-bridge` or `serve` for a given station: both own a BLE
connection, and these stations may reject a second client.

| Topic | Payload |
| --- | --- |
| `solix_gen2/bridge/availability` | Retained `online` or `offline`; MQTT last will marks an unexpected bridge exit offline |
| `solix_gen2/c1000/state` | Retained JSON with `name`, `model`, `available`, `last_seen`, and `metrics`; excludes the BLE address and pairing ID |
| `solix_gen2/c1000/availability` | Retained `online` or `offline` for station telemetry |
| `solix_gen2/c1000/result` | Nonretained JSON confirmation or error for the last setting command |

Publish JSON to the following **nonretained** command topics, using your
configured C1000 name in place of `c1000`:

```text
solix_gen2/c1000/set/charge_limits      {"upper":95,"lower":1}
solix_gen2/c1000/set/ac_charging_power  {"watts":1000}
solix_gen2/c1000/set/display_timeout   {"seconds":60}
solix_gen2/c1000/set/fast_charge       {"enabled":false}
solix_gen2/c2000/set/ac_charging_power  {"watts":1700}
solix_gen2/c2000/set/charge_cap         {"upper":95}
solix_gen2/c2000/set/display_timeout   {"seconds":60}
```

The bridge checks types and the library's verified value ranges, uses its
existing BLE connection, and waits for telemetry confirmation before
publishing `result`. It ignores retained commands replayed at subscription,
reports a full command queue instead of silently dropping a write, and clears
queued commands if the broker connection drops.
The C2000 subscribes only to its verified charge-cap, charging-power, and screen-timeout
topics; there are no AC/DC output commands. A live
C2000-only test on the HA node published its status to a disposable loopback
MQTT listener, with AC output on. An idempotent 1800 W C2000 charging-power
command sent through the loopback broker returned a confirmed result. This
was followed by an idempotent 90% charge-cap command that confirmed both the
upper 90% and unchanged lower 1% limits. These checks confirm the bridge path,
not a direct MQTT connection from the station. Availability
consumers should check both the bridge and station availability topics, since
the last retained station state remains visible when the bridge goes offline.
The bridge was exercised on the HA node against a disposable loopback broker:
live C1000 telemetry arrived, an idempotent 100%/1% charge-limit command was
confirmed, and the broker last will marked the stopped bridge offline. It has
not been installed as a persistent service on the HA node.

### Controlling C2000 charging through MQTT

The C2000 was also tested with a complete charging cycle through the local
MQTT bridge. Starting at 90% battery and a 90% cap, publish nonretained
commands in this order, checking `solix_gen2/c2000/result` after each one:

```text
solix_gen2/c2000/set/ac_charging_power  {"watts":500}
solix_gen2/c2000/set/charge_cap         {"upper":95}
```

The bridge confirmed both writes. The station then reported `charging`, with
872 W AC input and 318 W AC output; AC output remained on. To stop charging at
the original 90% cap and restore the original charging-power limit, publish:

```text
solix_gen2/c2000/set/charge_cap         {"upper":90}
solix_gen2/c2000/set/ac_charging_power  {"watts":1800}
```

Both restore commands were confirmed. A separate read-only BLE check showed
90% battery, `idle`, AC input and output both 355 W, upper/lower limits 90%/1%,
and AC output still enabled. The 500 W setting limits charging, while total AC
input also includes the AC load supplied to the servers. The charge cap decides
whether mains charging may resume at the current battery level; lowering it
does not force the battery to supply AC loads. This is a local BLE-to-MQTT
bridge: direct MQTT from the station remains unverified.

### Experimental Gen 2 Wi-Fi join and C1000 API setup

The C1000 Gen 2 on Prime firmware 1.1.4.9 accepted Wi-Fi credentials and an
API endpoint sent directly from this library over Bluetooth. It joined a
locally isolated WPA2 AP and made HTTP requests using the generated BLE client
ID; the Anker account ID from phone logs was unnecessary. The C2000 Gen 2
also accepted `4024` credentials (`4824=00`) and joined an isolated AP with
DHCP. Provisioning an AP does **not** make cloud mode, Time-of-Use, or network
control available by itself.

Save the AP passphrase in an owner-only file. `wifi-join` sends only the AP
credentials; both tested models associated and obtained DHCP without an API URL:

```bash
chmod 600 /path/to/wifi-password
solix-gen2 wifi-join --name c1000 --ssid 'YourSSID' \
  --password-file /path/to/wifi-password
solix-gen2 wifi-join --name c2000 --ssid 'YourSSID' \
  --password-file /path/to/wifi-password
```

`wifi-setup` is currently C1000-only. It also supplies an API endpoint and timezone, so the station can
attempt its network binding calls:

```bash
solix-gen2 wifi-setup --name c1000 --ssid 'YourSSID' \
  --password-file /path/to/wifi-password \
  --api-url 'http://192.168.50.1/' --allow-http \
  --posix-timezone 'CET-1CEST,M3.5.0,M10.5.0/3' \
  --iana-timezone 'Europe/Vienna'
```

The URL above is an example for an isolated local API server; replace it with
your server's reachable address. An HTTPS URL requires a certificate trusted
by the station. The test station rejected a self-signed certificate with TLS
`unknown_ca`. If `--password-file` is omitted, the CLI prompts without showing
the passphrase. The configured client ID is used as the Wi-Fi binding account
field unless `--account-id` is supplied. `wifi-setup` returns the BLE reply
bytes, `4824=00` and `4825=26` on the tested C1000. Their complete meanings
are not known; verify association and API traffic separately.

Python callers can use `await monitor.join_wifi(...)` or
`await monitor.send_wifi_provisioning(...)` with the same parameters. The
observed endpoint sequence is documented in the
[field notes](../docs/gen2-protocol.md). The HTTP monitoring server remains
read-only and does not run an Anker API emulator.

For offline protocol research, `solix_gen2.mqtt_credentials` provides
`encrypt_device_credential(device_serial, pem_bytes)` and
`decrypt_device_credential(device_serial, base64_text)`. These implement the
device endpoint's serial-derived AES-256-CBC envelope, verified against a
saved C1000 response and a synthetic OpenSSL vector. They perform no network
requests and support the observed 17-character serial format. They do not
complete station binding; native station-to-MQTT setup remains unverified.
See the [credential and firmware findings](../docs/gen2-protocol.md#device-mqtt-credential-envelope).

The read-only server provides:

| Endpoint | Content |
| --- | --- |
| `/health` | Availability summary; HTTP 503 when no station is reporting |
| `/devices` | JSON status for all configured stations |
| `/devices/c2000` | JSON status and latest metrics for one station |
| `/events` | Server-sent events with snapshots and live updates |
| `/metrics` | Prometheus numeric metrics and availability |

The default bind address is `127.0.0.1`. Use `--host 0.0.0.0` to let other
machines on your network read it. Set `SOLIX_HTTP_TOKEN` to require a Bearer
token on every endpoint; Home Assistant can send it in an `Authorization`
header. The service reconnects BLE automatically and marks readings
unavailable when the station stops reporting.

For Home Assistant on the same node, this [RESTful sensor configuration](https://www.home-assistant.io/integrations/rest/)
polls one endpoint for battery and AC output power:

```yaml
rest:
  - resource: http://127.0.0.1:8765/devices/c2000
    scan_interval: 10
    sensor:
      - name: C2000 Battery
        unique_id: solix_c2000_battery
        value_template: "{{ value_json.metrics.battery_percentage }}"
        availability: "{{ value_json.available }}"
        unit_of_measurement: "%"
        device_class: battery
        state_class: measurement
      - name: C2000 AC Output
        unique_id: solix_c2000_ac_output
        value_template: "{{ value_json.metrics.ac_output_power_w }}"
        availability: "{{ value_json.available }}"
        unit_of_measurement: W
        device_class: power
        state_class: measurement
      - name: C2000 Time Remaining
        unique_id: solix_c2000_time_remaining
        value_template: "{{ value_json.metrics.time_remaining_minutes }}"
        availability: "{{ value_json.available and value_json.metrics.battery_status in ['charging', 'discharging'] }}"
        unit_of_measurement: min
        state_class: measurement
    binary_sensor:
      - name: C2000 Mains Lost
        unique_id: solix_c2000_mains_lost
        value_template: "{{ value_json.metrics.ac_input_connected == 0 }}"
        availability: "{{ value_json.available and value_json.metrics.ac_input_connected is defined }}"
```

The binary sensor turns on when the station reports its AC input absent. The
REST server also publishes `solix_gen2_ac_input_connected` and
`solix_gen2_battery_discharging` as Prometheus metrics. The time remaining
reading is an estimate from the station and may change sharply with load.

For a direct custom Home Assistant integration, use `SolixMonitor` callbacks
or `MonitorService.subscribe()` and call `disconnect()` / `stop()` when the
entry unloads. `available`, `last_seen`, and `error` in each status support HA
entity availability. The HTTP service can run as a systemd service on a Linux
HA node using the same `solix-gen2 serve --config ...` command.
