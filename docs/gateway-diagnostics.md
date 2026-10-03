# Passive gateway diagnostics

GET or HEAD **`/diagnostics`** works with both the Bluetooth gateway and native
AP-service gateway. It uses their existing snapshots; it does not poll stations,
read pairing profiles, start services or send commands. The endpoint requires
the configured bearer token and returns `Cache-Control: no-store`.

The browser dashboard's **Checks** button shows the monitoring report alongside
saved AP checks. It keeps readings and edited settings intact. Older and
Bluetooth-only gateways can show an unavailable AP report without interrupting
monitoring; rejected authentication clears the browser session.

The JSON report contains a schema version, station/available counts and ordinal
station entries with model, transport, validated firmware version, connection
state, report age, freshness limit, availability reason and a fixed next step.
Names, addresses, IDs, arbitrary metrics and raw error text are omitted, making
the report suitable for reviewing before sharing.

| Reason | Meaning |
| --- | --- |
| `ready` | Monitor reports available and telemetry satisfies its transport age limit |
| `disconnected` | Monitor does not report an active connection |
| `awaiting_telemetry` | Connected, but no usable report timestamp exists |
| `stale_telemetry` | Timestamp is at least 30 seconds old for native MQTT or 90 seconds for BLE |
| `clock_skew` | Timestamp is more than five seconds ahead of the gateway clock |
| `unavailable` | Monitor reports unavailable despite a recent timestamp |

This diagnostic cannot upgrade an unavailable monitor to available. A fresh
report is evidence of telemetry availability, not electrical continuity or
permission to send a setting command. Existing command guards remain unchanged.
The native monitor also checks its worker status-file age; an expired file can
therefore appear disconnected even if its last telemetry timestamp looks recent.

Focused tests exercise transport age boundaries, invalid/future timestamps,
redaction, authentication and passive GET/HEAD behavior. For the completed
physical monitoring interval, see the
[13-hour observation](ha-13-hour-observation.md).

For saved certificates, routing and provisioning prerequisites, the native
gateway additionally offers [GET `/setup-check`](ap-service-setup-check.md).
That report likewise sends no station requests; local-file success does not
establish a live connection.
