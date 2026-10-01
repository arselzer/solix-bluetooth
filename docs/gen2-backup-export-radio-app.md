# Gen 2 backup export: radio and app follow-up

## Result

This offline follow-up does **not establish a complete local export of the
manual and three automatic disaster-preparation records**. It resolves several
additional candidates, including the app's opaque `BackupMind` parser, without
inventing a device query. The existing [complete-restoration prerequisite](gen2-persistent-plan-followup.md#requirement-for-a-reversible-backup-trial)
still applies.

The new evidence includes **15 radio instruction-replay cases** and **12
controller diagnostic-callback cases**. These investigate different paths from
the earlier 24 ordinary-status cases and 19 direct getter callers. No station,
cloud, Bluetooth, SSH, Wi-Fi configuration or output command was used.

## Radio “device parameters” and “backup” candidates

Addresses in this section apply to the published **C1000 Gen 2 radio 0.3.3.0**.

| Candidate | Actual path | Finding |
| --- | --- | --- |
| `base_get_device_param` | Builder `420224d8` | Fixed request for point `20001`, the [analytics enable switch](energy-report-lifecycle.md#analytics-enable-switch) |
| `aws_get_mqtt_device_param_info` | `42026918`, initializer `420265fa`, validator `42026878` | Reads/reconstructs the `0x154`-byte `mqtt_client_file` connection record |
| MQTT file “backup block” | Fallback read `420148f6`, normal write `42014ee6` | Storage redundancy for that connection record; not the controller's disaster-plan records |

The actual point-switch builder produces this shape with synthetic input:

```json
{"account":"synthetic","base_get_device_param":[{"destination":"user","param_names":["20001"]}]}
```

Its parameter name is constructed as a constant in the instructions. The
replay covers two synthetic account lengths, three rejected pointer/input
conditions and six JSON allocation/printing failures. It makes no file calls.

Four file-recovery cases cover normal-file success, valid fallback, unreadable
fallback and invalid fallback magic. The latter paths repair/reinitialize the
normal file through a substituted write boundary. This internal routine is
therefore also unsuitable as evidence of a passive configuration-export API.
The replay executes the initializer and validator; file I/O, identity getters,
JSON, allocation, libc and logging are explicit host substitutes.

## The app's BackupMind publication is an energy-report lead

The retained app's charging opcode-map initializer `02420104` associates
`postPbFormatBackupMindData` with **`0490`**. Its A1753-family response branch
at `03b75a9c..03b75aec` calls `PPSDeviceCommands.parsePBBackupMindData`
(`03b75fac`). That parser Base64-encodes opaque A2 bytes and decodes A3 as a
filename. It does not decode four saved disaster records or define a request
to fetch them. Model-family inheritance alone does not establish A1763 support.

Here the controller provides the missing connection: C1000 Gen 2 main
**1.1.4.9** wrapper `08011ae8` uses the same report callback and filename,
`charging_pps_series_c_0009`, with two routes:

| Readiness getter `0800d1d8` | A1 | Function/opcode |
| --- | --- | --- |
| False | `31` | `0f/0490`, send at `08011b90..08011b96` |
| True | `1a` | `0f/0401`, send at `08011b76..08011b7c` |

The existing [energy lifecycle](energy-report-lifecycle.md#trigger-retry-and-completion)
links this wrapper to builder `0802db30`. Thus this particular `0490` producer
is an alternate energy/diagnostic publication, not a newly discovered backup
export. A2 uses a **two-byte little-endian length including its type byte**;
it must not be parsed with the ordinary short status-TLV length convention.
This is producer/dispatch evidence, not authorization to transmit `0490` as a
query. Other producers or model implementations are not ruled out.

The separate TLV parser `03b45d74` is called from the A1790 response branch
`03b418e8..03b41924`, associated with `postA17b1DeviceInfoReq`. It retains an
opaque Base64 payload. That branch supplies no demonstrated A1763 request or
complete saved-record schema either.

## `SETTING_MSG_DESC` contains tracking data and has side effects

The energy builder installs callback `08016bb0` with the name
`SETTING_MSG_DESC` at `0802ddc4..0802ddc8`. This was an important unresolved
boundary in the earlier energy arithmetic replay.

The actual branch `0801701c` calls getter `0801a010`, which returns the
**tracking block at `20002028`**. It serializes cumulative counters and up to
ten 25-byte session records as repeated protobuf field **24**, using descriptor
`08034044`. Session deserializer `080097ae` reads a flag mask, a 16-bit value
and up to four byte-sized counter deltas. This is not a raw saved-settings dump.
The complete backup records live separately at `20001ea9`.

After traversing the sessions, `080171c2` calls **`0800ff40`**. That routine
clears the ten session validity bytes, lengths and 20-byte encoded bodies,
resets tracking metadata and requests persistence at `08015c4c`. This occurs
during report construction; the isolated callback has received no transport
acknowledgement.

The new 12-case replay executes the callback, session decoder, protobuf
encoder, getter and clearing instructions. It varies a valid change session,
a zero-change session and a truncated session, with output capacities 0, 2,
64 and 1024 bytes. Findings are bounded to these cases:

- The entire `0x190`-byte saved configuration stays unchanged. Instruction
  memory-read instrumentation records no access to the 38-byte backup block.
- With adequate space, the callback clears session tracking and requests
  persistence. This does not clear saved disaster plans.
- Some failed nested encodings still lead to clearing. For example, a valid
  session with capacity 64 returns success and clears tracking after the
  second submessage encoder returns failure. A zero-capacity tag failure
  returns early without clearing.

Logging and persistence scheduling are substituted; no flash backend runs.
Other energy-report callbacks and the whole report's delivery/error policy
are outside this isolated replay. Consequently this is neither proof of a
physical data-loss event nor proof that all analytics reporting is passive.
Do not trigger a diagnostic report as a supposedly harmless backup export.

## Other app schemas do not establish the missing route

| App candidate | Bounded finding |
| --- | --- |
| `AKIotBasicCommand.fetchKVConfig`, `026fa2a0` | `akiot.product.fetch_kvconfig` with `pnCode`, `key`, `sku`: product configuration, not a proven device-memory export |
| `A17C1DeviceController.getDeviceFactorySetting`, `0262a75c` | A17C1-specific command builder; no equivalent A1763 handler established |
| `DisasterPreparednessPlans.toJson`, `0277fc70` | Serializes `soc`, `start_time`, `end_time`, `type` |
| `ElectricityStrategy`, `0277f950` / `0278323c` | Contains plan list and manual/automatic-enable fields; reviewed direct callers are `A5101DeviceCommandModel` serialization/parsing |

The richer A5101 JSON shape is a useful schema lead, but neither the four
record positions nor raw switch values have been linked to a C1000 Gen 2
local response. Cloud state or another model's serializer cannot substitute
for a complete backup of this controller's saved bytes.

## Missing prerequisite and next useful evidence

A safe reversible backup-control trial still needs either:

1. An established A1763 request/response or existing passive report containing
   every record's maximum, start/end and both saved switches, including dormant
   records, with freshness and serialization demonstrated; or
2. Another independently verified complete saved-state backup.

Useful further offline work is a concrete A1763 schema/producer for such a
message, or a vendor format definition for an otherwise unidentified existing
publication. A parser name, inactive D9 status, energy point-switch reply,
tracking history, factory query from another model or cloud plan list is
insufficient. No new raw query or backup write is proposed here. Ordinary
`0100` remains the separately audited route for tariff, reserve and reported
UTC described in [the status-query investigation](gen2-backup-query-investigation.md).

## Reproduction and input identity

```sh
python3 -m pip install -r tools/firmware_analysis/requirements.txt
python3 tools/firmware_analysis/emulate_radio_config_candidates.py \
  --output /tmp/radio-config-candidates-results.json
python3 tools/firmware_analysis/emulate_settings_report_candidate.py \
  --output /tmp/settings-report-candidate-results.json
cmp /tmp/radio-config-candidates-results.json \
  tools/firmware_analysis/expected_results/radio-config-candidates-results.json
cmp /tmp/settings-report-candidate-results.json \
  tools/firmware_analysis/expected_results/settings-report-candidate-results.json
```

Both use Unicorn **2.1.4**, enforce the input hash and accept
`--firmware-dir` or `SOLIX_FIRMWARE_DIR`. The controller callback tool reuses
the published energy harness and `replay_io.py`; it overrides the named
callback and substitutes only the reached logging/persistence boundaries.
Expected results and manifests contain synthetic data only.

| Input | SHA-256 |
| --- | --- |
| C1000 Gen 2 main 1.1.4.9, `MainMcu-decoded.bin`, 198656 bytes | `21ffb746c1e07ecaa9817fa7017807585a00bedbca3f136c650129bb52a4a0c9` |
| Radio 0.3.3.0, `c1000-radio-validated.bin`, 1482800 bytes | `e291ec115f013953e825cb51b9e457a8731889547ab55b3058640e599e8cfec8` |
| Retained ARM64 Flutter app `libapp.so`, analyzed locally only | `8537b4f8a4da327f9bf298d99e90ad969b89f52ae7d6d68c454e5bf0ec25c070` |

App address findings are static, not executed app behavior. Raw app material,
disassembly and metadata remain private and are unnecessary for the published
synthetic replays. No C2000 controller or original C1000 equivalence is inferred.
