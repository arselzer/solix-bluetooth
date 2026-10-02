# Three-station Home Assistant runtime validation

Live trial on **2026-10-02** with Home Assistant **2026.7.4**, Ubuntu
**26.04** and Python **3.14.4**. This is physical deployment evidence beyond
the standalone HA contract tests. The gateway package is the verified
`solix_link-0.1.0` wheel; the integration uses the normal authenticated
configuration flow and one shared HTTP coordinator.

## Stations and discovery

| Station | Main firmware | Native controls | Registered / available HA entities |
| --- | --- | ---: | ---: |
| Original C1000, A1761 | 1.7.1 | 9 | 15 / 14 |
| C1000 Gen 2, A1763 | 1.1.4.9 | 13 | 32 / 23 |
| C2000 Gen 2, A1783 | 2.1.6.4 | 5 | 20 / 15 |

These are initial-deployment counts. The later freshness/DC Smart update below
adds entities without replacing the existing integration entry or station profiles.

All three stations simultaneously connected to one isolated **2.4 GHz** AP,
with separate client certificates, telemetry and command queues. Disabled
diagnostics and unavailable derived values account for the remaining entities;
their presence is not a station connection failure. New entities appeared
during ordinary polling, without reloading the integration for discovery.

The C2000 automatically reused its existing network configuration. Only the
two C1000s received Wi-Fi/API/timezone provisioning, through their saved Prime
Bluetooth identities. The original replied `4824=00`, with `4825` timing out;
the Gen 2 replied `4824=00` and `4825=00a10400000000`. Both subsequently
completed local TLS/MQTT activation. A configuration timeout alone is therefore
insufficient to diagnose provisioning failure.

Both C1000 Bluetooth sessions disconnected during activation. Post-activation
checks used fresh native telemetry; a subsequent Gen 2 direct BLE probe could
not find its advertisement and sent **no setting command**. This observation
does not establish a permanent BLE lockout or a universal pairing requirement.

## HA command and restoration

The original C1000's actual HA `select.select_option` service changed its
screen timeout **30 → 60 → 30 seconds**. The gateway confirmed the temporary
value and restoration through native telemetry. All recorded output, charging,
timeout, display and Smart settings across the three stations matched their
baselines afterward. The native setter independently guards the original's
complete F8 and eleven preferences.

All three AC outputs remained enabled in recorded snapshots. No C2000
provisioning, charging or output command was sent in this deployment trial.
These are reported output states, not independent waveform measurements.

## Service and integration recovery

Both dedicated systemd services are enabled at boot:

```sh
systemctl status solix-link-ap.service solix-link-gateway.service
```

Restarting only the HTTP gateway recovered all three available snapshots in
approximately **1.01 seconds after `systemctl` returned**. Restarting the AP
and gateway together recovered all three in approximately **32.07 seconds
after `systemctl` returned**. Neither phase reprovisioned a station or sent a
settings command. All protected settings matched the pre-restart baseline.

Reloading only the SOLIX Link HA integration succeeded. Its entry returned to
`loaded`, with the same device IDs, entity IDs and available-entity counts.
Six additional samples confirmed all three were available and their report
timestamps advanced. These recovery times exclude service-command execution
and are not guaranteed outage durations.

## Passive report continuity

Owner-only copies of the three MQTT logs were decoded locally at **08:40 UTC
on 2026-10-02**, without restarting a service or sending an additional station
command. The latest sessions began during the deliberate AP recovery trial.

| Station | Reports in latest session | Observed session | Median report gap |
| --- | ---: | ---: | ---: |
| C2000 Gen 2 | 615 | 53.50 minutes | 5.235 seconds |
| Original C1000 | 594 | 53.42 minutes | 5.400 seconds |
| C1000 Gen 2 | 609 | 53.23 minutes | 5.234 seconds |

No further TLS connection appeared in those sessions. All decoded protected
settings stayed constant, including enabled AC outputs and inactive countdowns.
The original retained its **720-minute** device timeout; both Gen 2 stations
reported **Never**. This observation does not prove behavior at the original's
12-hour idle boundary.

The C1000 Gen 2's largest gap, **15.069 seconds**, separated its initial status
publication from the first requested status. Request timestamps match the
gateway's intentional 15-second startup grace after subscription; this is not
evidence of a later reconnect. The other maximum gaps were 5.241 seconds
(C2000) and 6.667 seconds (original). MQTT envelope identities and packet
checksums were validated before decoding; no malformed record was encountered.

The HP test switch was disconnected by this point. The original reported
100% SOC and 0 W input/output, so these logs provide no below-full charging-rate
test. Report continuity and logical output states do not measure relay timing,
electrical continuity, long-term availability or durable energy totals.

## Deployment and remaining checks

An allowlisted diagnostics platform was then installed. HA was restarted to
load it, and once more for failed-setup handling. The actual authenticated diagnostics download returned
all three fresh stations and their expected firmware versions. Gateway URL/token
were absent from the complete response. The integration payload omits configured
names, entry/device IDs, serials, account identities and raw captures; it reads
only the coordinator cache. HA adds standard system metadata around that payload.
Twenty-one privacy/staleness tests passed, including unavailable setup without
a coordinator; the full standalone HA suite is now **520 tests**. No gateway
wheel or station configuration changed for diagnostics.

HA runs in a host-network Docker container; its actual configuration mount,
rather than the Compose-file directory, contains the custom component. The
separate privileged AP worker owns a dedicated Wi-Fi adapter in a network
namespace with no LAN interface or default route. The authenticated HTTP
gateway and bundled browser dashboard are reachable from the host LAN.
Unauthenticated station requests return 401; no output-switch API is exposed.

This trial retains existing identities. Generated native identities for the
original C1000/C2000, full host or station power-cycle persistence, long-term
availability, reauthentication/reconfiguration and charging automations remain
separate checks. Original mains/battery-source observations remain unknown
where the firmware provides no validated field. Session charts are not
persistent energy accounting.

Private deployment scripts, baselines, authentication/config backups, native
logs and complete runtime results remain owner-only in ignored local evidence
and the node's restricted runtime directory. Public documentation contains
no account identifiers, station serials, certificates or bearer tokens.

## Freshness, DC Smart and charging blueprint update

A separately built wheel, SHA-256
`1aee184878e4d163d30259afd4d5ad1fa526ce78f9979eb9fc07e0a6d33716ed`,
was installed with no dependency changes. Runtime and component backups were
retained first. One AP/gateway restart recovered all three fresh snapshots
in **22.04 seconds after the recovery check began**, preserving every recorded
output, timer, charging and preference baseline. No station was reprovisioned.

After restarting HA to load the component update:

| Station | Native controls | Registered / available HA entities |
| --- | ---: | ---: |
| Original C1000 | 9 | 16 / 15 |
| C1000 Gen 2 | 14 | 34 / 25 |
| C2000 Gen 2 | 5 | 22 / 17 |

All three enabled **Last telemetry** timestamp sensors were fresh. The C2000's
new read-only **Fast charging enabled** binary sensor reported off. The actual
Gen 2 DC Smart HA switch passed **OFF→ON→OFF**, with native two-report confirmation,
all sampled AC outputs enabled and complete protected-settings restoration.
See [command guards and limits](c1000-gen2-native-dc-smart-validation.md).

The optional [opportunistic charging blueprint](home-assistant-charging-automation.md)
passed the installed HA **2026.7.4** blueprint schema, input substitution and
expanded automation trigger/condition/action validation. It is copied into HA's
blueprint directory, with no automation instance or helper created and no policy
activated. Fifty synthetic cases cover policy gates, hysteresis and failed-write
latching. The complete Python/HA contract suite passed **2,214 tests**, and
**19 browser scenarios** passed against synthetic station data.

No station advertised over BLE during the recovery scan, and the user was away.
The original/C2000 generated-native-ID trial is therefore deferred: an independent
working BLE recovery path must be demonstrated before changing a live identity.
The original remains at its recorded **720-minute** timeout; both Gen 2 stations
retain **Never**. No new charging-rate claim follows from these idle/full-SOC tests.

A bounded **13-hour read-only observation** now samples the authenticated gateway
every 30 seconds. It records report ages, availability and protected-setting
changes in restricted node-local JSONL, with an updated summary. It sends no
commands, performs no recovery and stops after its deadline. The result is
pending; these API samples cannot prove electrical continuity or continuous
availability between samples. Existing full MQTT logs remain retained separately.

## Clock, AC Smart and disabled solar-policy update

The subsequent clock/AC Smart runtime wheel had SHA-256
`df0b19b1d6bc510a9db54b940ed90992f7260727f0d875220cca871bef3db7b3`.
It was installed after retaining code/configuration backups, preserving all
station identities. The [bounded C1000 Gen 2 trial](c1000-gen2-clock-ac-smart-validation.md)
confirmed both saved clock-brightness flags and AC Smart, then restored all
protected settings. Its private AC-output setup/restoration affected only the
noncritical C1000 Gen 2; original/C2000 received status requests only.

The final wheel adds the line-menu entries and corrected browser requirement
text, SHA-256
`806085309f31157d1e892bc74deb57faaf9f9aac2ee48ab20b2156dbdd271a41`.
Only the HTTP gateway needed a second reload for its asset map; the AP sessions
and station profiles were retained. Fresh checks showed all three AC outputs on.

| Station | Native controls | Registered / available HA entities |
| --- | ---: | ---: |
| Original C1000 | 9 | 16 / 15 |
| C1000 Gen 2 | 16 | 37 / 25 |
| C2000 Gen 2 | 5 | 22 / 17 |

Both optional Gen 2 clock selects are registered **disabled by default**.
AC Smart was discovered during the SDK trial's safe AC-off interval and is
unavailable after AC restoration. Live select-service writes remain untested;
the native worker was exercised and HA has forty select contract cases.

The revised charging blueprint passed the installed HA 2026.7.4 schema again.
Normal authenticated HA APIs created two dedicated off helpers and saved a
**disabled C1000 Gen 2 solar charging instance**. No policy was activated or
station charging setting changed. Its candidate export sensor reports W, but
export sign remains unconfirmed. Defaults and activation prerequisites are in
the [charging policy guide](home-assistant-charging-automation.md).

The policy verifies all ten exact roles and native transport on one HA device,
rechecks guards in HA's manual Run path, and recalculates the opportunity after
a reserve action. Eighty-seven actual-template tests cover these conditions.
Final release checks passed **2,362 Python/HA tests** and **20 synthetic browser
scenarios**; regenerated screenshots contain synthetic station data only.
Independent 92-case Gen 2 and 131-case original firmware replays matched both
complete result/manifest pairs. Their limits are documented separately.

The bounded observation remains pending. Deliberate service upgrades and the
AC Smart trial are recorded as interventions, so resulting gaps or setting
changes must not be treated as spontaneous failures. The original remains at
100% SOC/0 W without a test load; no actual charging-rate or newly supported
forced-discharge behavior is claimed. Generated original/C2000 native identities
still require a demonstrated independent recovery path before testing.
