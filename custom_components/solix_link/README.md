# SOLIX Link for Home Assistant

Custom integration for a separately running **local SOLIX Link HTTP gateway**.
Home Assistant uses the gateway connection; it does not open a second Bluetooth
connection or contact Anker. This is an unofficial integration.

## Install and configure

1. Run the repository's gateway and verify its `/devices` endpoint. Enable its
   authenticated control option only if you want charging controls.
2. Copy this whole `solix_link` directory into
   `<Home Assistant config>/custom_components/solix_link/`, then restart HA.
3. In **Settings → Devices & services → Add integration**, select **SOLIX Link**.
4. Enter the gateway URL, such as `http://gateway.local:8765`, and its bearer
   token. The token is required for commands. An unauthenticated gateway can
   be monitored with an empty token.

Use the integration's **Reconfigure** action to change its address or token.
Authentication failures start a reauthentication flow. Use HTTPS with a trusted
certificate when the local network is not trusted; tokens travel in an HTTP
Authorization header. Treat HA configuration backups as containing credentials.

## Entities and actions

- Binary sensors: mains present and AC output enabled, when explicit 0/1
  telemetry is available. Unknown/stale values become unavailable. The Gen 2
  mains transition was verified by unplug/replug on C1000; C2000 is checked
  against baseline/reference data without unplugging its server supply.
- Sensors: battery percentage/status, temperature, AC/DC/total output power,
  AC input power, usage mode, active tariff and observed AC power source, when
  reported by the gateway. Power sensors support HA statistics and can feed
  HA's Integral helper; no unverified firmware energy counters are published.
- Numbers: AC charging power limit, charge cap and backup reserve. A number is
  created only for a supported model when telemetry exists and the gateway
  advertises that command. Charging-power ranges are original C1000/A1761
  100–1000 W, C1000 Gen 2 300–1200 W and C2000 Gen 2 300–1800 W, in 100 W
  steps. Original C1000 charging-power changes/restoration passed library and
  HTTP tests on version code 151; other range values and physical enforcement
  remain untested.
  C300 charging power has discrete choices (including 330 W); this integration
  does not expose a charging-power number for it. Reserve limits follow the
  current charge caps; native MQTT supports reserve on both Gen 2 models.
  Original C1000 support adds no charge-cap/reserve controls.
- Selects: C1000 Gen 2 native MQTT temperature display (Celsius/Fahrenheit) and
  lower discharge limit (1%, 5%, 10%, 15%, 20%). Available limits leave at least
  five percentage points below the current backup reserve; selecting a limit
  does not adjust the reserve. The temperature sensor continues to report Celsius.
- Configuration switch: C1000 Gen 2 native MQTT off-grid alert preference.
  Setting storage and readback were verified; actual alert delivery is untested.
- **Return to grid**: C1000 Gen 2 and C2000 Gen 2 native MQTT. The gateway checks actual
  power flow; changing the displayed mode alone is insufficient confirmation.
- Action **`solix_link.set_tou_plan`**: select the HA device and supply `enabled`
  plus up to six whole-hour, non-overlapping periods. Both Gen 2 models are
  supported through native MQTT:

  ```yaml
  action: solix_link.set_tou_plan
  data:
    device_id: YOUR_HOME_ASSISTANT_DEVICE_ID
    enabled: true
    periods:
      - tariff: off_peak
        start_hour: 0
        end_hour: 24
  ```

Tariffs are `peak`, `mid_peak`, `off_peak`. Split overnight intervals at midnight.
Peak may discharge the battery with mains connected. The action replaces the
whole plan. `enabled: false` stores a Standard-mode plan but does not independently
confirm grid supply; use **Return to grid** when that is the intent. Fast charge
must already be off. Only single all-day plans have been exercised on C2000
hardware; clock interpretation and multi-period operation need further validation.

## Availability and failure behavior

One coordinator polls every five seconds while idle; a command pauses polling.
Stale/disconnected stations and
missing metrics become unavailable. Commands re-read the station and require
telemetry no older than 30 seconds, a token and an advertised capability.
Controls are serialized with polling, range-checked and never retried
automatically. A failed or timed-out command triggers a status refresh because
settings may already have changed; a timeout does not cancel or roll back a
device operation. HTTP deadlines include the gateway's worker budget, its
five-second RPC margin and ten seconds for HTTP overhead: Return to grid uses
175 seconds for its 30-second per-phase confirmation setting, schedule changes
135 seconds, and other commands 60 seconds. Connection establishment remains
limited to ten seconds. No AC-output, timer or firmware control is
exposed. Existing gateway safety checks remain authoritative.

Identity uses the original configured gateway endpoint plus its saved station
name, without serial numbers or account IDs. Keep names stable; renaming a
gateway station creates new entities. Reconfiguring the same gateway address
preserves its existing identities. Do not point an existing entry at a different
gateway. New stations/capabilities are discovered during polling.

## Development and verification

Contract tests use synthetic data and an ephemeral loopback HTTP server:

```sh
python3 -m pip install pytest aiohttp
python3 -m pytest home_assistant_tests -q
python3 -m compileall -q custom_components/solix_link
```

Home Assistant itself is not installed in the development environment. These
tests verify HTTP contracts, parsing, privacy filtering, command guards and
translations, but do **not** establish HA runtime/config-flow compatibility.
Before deployment, run HA integration tests/hassfest and verify setup, entity
discovery, reauthentication, reconfigure, unload and reload in a test HA instance.
The component uses `ConfigEntry.runtime_data` and current coordinator APIs;
target current Home Assistant releases.

The implementation follows official guidance for [config flows](https://developers.home-assistant.io/docs/core/integration/config_flow/),
[coordinated fetching](https://developers.home-assistant.io/docs/integration_fetching_data/),
[shared HTTP sessions](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/inject-websession/),
and [runtime data](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/runtime-data/).
