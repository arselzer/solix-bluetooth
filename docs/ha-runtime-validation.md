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
