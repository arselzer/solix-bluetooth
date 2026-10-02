# Home Assistant recovery and telemetry freshness

## Offline audit scope

On **2026-10-02**, 21 new contract cases exercised the real config-flow and
coordinator code with scoped Home Assistant doubles. Another 41 cases covered
the real freshness/flag entities, discovery and inherited availability. Inputs
are synthetic. No HA, gateway, Bluetooth, MQTT, SSH or cloud connection was
opened; deployment and station profiles were untouched.

These tests do not execute Home Assistant's own flow helpers, device registry,
state machine or frontend. The existing [HA 2026.7.4 runtime trial](ha-runtime-validation.md)
remains the release-specific deployment evidence. Reauthentication and
reconfiguration still need actual runtime validation before claiming release
compatibility. No newer local HA registry source was available for this audit,
so the existing `DeviceEntry.config_entries` service membership check was not
changed speculatively.

## Configuration and command recovery

The executed component methods preserve these contracts:

- Reconfiguration normalizes the URL, retains an untouched token, permits
  explicit empty-token removal and rejects another configured endpoint without
  mutating the entry.
- Reauthentication replaces only the token and preserves the original endpoint
  identity. Authentication/read errors return forms without saving changes or
  exposing the underlying error text.
- Password selectors do not prefill the saved token. Invalid endpoints fail
  before a gateway read.
- Poll authentication failures map to `ConfigEntryAuthFailed`; other gateway
  failures map to `UpdateFailed`.
- Failed commands trigger one read refresh and never repeat the write. If
  refresh also fails, the target becomes unavailable while peer snapshots
  remain intact. Polling and commands share the same I/O lock.

No concrete config-flow/coordinator defect was found in these cases. HA helper
calls are doubles, so successful contract tests do not prove actual reload,
reauthentication flow startup or registry migration.

## Last telemetry sensor

Every supported station exposes diagnostic **Last telemetry**, entity key and
translation key **`last_seen_timestamp`**, enabled by default. It uses
`SensorDeviceClass.TIMESTAMP`, without a unit or measurement state class.

The native value is a timezone-aware UTC `datetime` converted directly from
the coordinator snapshot's top-level `last_seen_timestamp`. It never substitutes
HA `last_updated`, `last_reported`, a metrics field or the HTTP polling time.
No additional request is made.

Boolean, nonnumeric, nonfinite, unrepresentable and more-than-five-seconds-future
timestamps yield no value. Existing availability checks make disconnected,
unavailable, poll-failed or more-than-90-seconds-stale stations unavailable.
An automation requiring fresher control data must additionally compare this
timestamp with its own **30-second** limit. Read freshness from the device
timestamp rather than the age of an unchanged battery percentage entity.

## C2000 read-only Fast flag

C2000 Gen 2 using native MQTT exposes diagnostic **Fast charging enabled**,
binary-sensor key/translation key **`ac_fast_charge_enabled`**, enabled by
default. Only exact integer 0/1 produces a state. The value is the existing
decoded flag; it does not measure charging rate or imply a validated setter.

Discovery excludes other models/transports, and an existing entity becomes
unavailable if its station profile changes away from C2000 native MQTT.
The C1000 Gen 2 keeps its supported Fast switch without a duplicate binary
sensor. Staleness and station availability continue to gate the read-only flag.

## Account-free setup and stable HA identities

HA identity uses the entry's original gateway identity plus stable station
names. Native account IDs, BLE pairing IDs and serial numbers are excluded.
Changing only a successfully tested native identity therefore requires no HA
entity-ID migration; changing station names creates new entities. Discovery
adds newly reported sensors and advertised controls during polling, with fresh
capability checks before commands.

If an original-C1000 generated-native-ID trial succeeds, SDK onboarding should
offer an explicit **native account** choice while preserving its working Prime
BLE ID. CLI AP initialization/addition already supports a separate
`--account-id-file`; the TUI/line workflow currently defaults the AP identity
to the paired BLE client ID. A future UI should retain the separation, save a
private rollback profile and require fresh bootstrap/reconnect confirmation.
Do not silently re-pair BLE or replace identities used by the working HA
deployment. Native-ID success would not prove generated original Prime pairing.

## Verification

```sh
python3 -m pytest \
  home_assistant_tests/test_recovery_contract.py \
  home_assistant_tests/test_freshness_entities.py -q
```

Use an environment with `pytest` and `aiohttp`; these tests need no installed
HA runtime. The scoped doubles are restored after each test and do not change
the operational custom component package namespace.
