# Multiple stations on one isolated AP

One AP service can register up to **eight original C1000 or C1000/C2000 Gen 2 stations** on the
same dedicated Wi-Fi adapter, SSID and isolated subnet. Each station keeps its
own model, pairing identity, client certificate, telemetry, command queue and
settings guards. BLE-only models can still share the ordinary `serve` gateway;
they do not join this native MQTT service.

## Register and provision

Use [the read-only setup checker](ap-service-setup-check.md) before provisioning
or when diagnosing saved profiles; it is available in CLI, terminal and HTTP.

Keep the existing primary `ap_service.json` and certificates. Stop the AP worker
before adding a station; restart the HTTP gateway after changing profiles.
Registration creates keys and configuration, and sends no device command.

```sh
# Both names already exist in the paired BLE configuration.
solix-link ap-service-add --directory /path/to/private/shared-ap \
  --name c1000 --serial-file /path/to/private/c1000-serial --config /path/to/config.json

# Start the AP and provision only the selected station over its saved BLE pairing.
sudo /path/to/venv/bin/solix-link ap-service-run \
  --directory /path/to/private/shared-ap --config /path/to/config.json \
  --provision --name c1000 --allow-control
```

Repeat provisioning for another registered station when needed. Each invocation
starts the same AP; already configured stations reconnect using saved settings.
Future runs omit `--provision`. Joining changes only the selected station's
Wi-Fi/API configuration. It does not switch AC output or install firmware.
The original station's identity/certificates are retained when adding a peer.
C1000 Gen 2 generated-identity MQTT is verified. Original C1000 and C2000
generated-identity MQTT remain unverified; use their already working identities
or `--account-id-file` if needed.

## Terminal, browser and API

```sh
solix-link tui --config /path/to/config.json --ap-service-directory /path/to/private/shared-ap
solix-link ap-service-status --directory /path/to/private/shared-ap
solix-link ap-service-set-charge-power --directory /path/to/private/shared-ap --name c1000 --watts 1000
```

The fixed terminal dashboard lists each native station separately. **Add to AP**
registers a connected, saved and paired supported Prime BLE station on a stopped AP,
using its fresh serial and saved identity. Provision it with the command above.
The line-based interactive fallback can also add to an existing AP profile and
select a station. Registration cannot replace an existing name or serial.

Run `ap-service-serve --web-ui` with a token and optional `--allow-control`.
The Vue selector, GET `/devices`, SSE updates and Prometheus metrics include
every registered station. GET `/devices/{name}` and POST
`/devices/{name}/commands` target that named station. CLI/private socket writes
require an explicit name when several stations are registered; an ambiguous
write fails before sending a command. Status without a name returns all devices.
Both worker and HTTP gateway must enable controls.

Run socket clients and the HTTP gateway as the AP worker's owner. A worker
started with `sudo` creates a root-owned socket with mode 0600; use
`sudo /path/to/venv/bin/solix-link ...` for those clients too, and explicitly
pass the token environment when starting the gateway. Keep the private files
and socket permissions restricted.

## Isolation and stored files

```text
shared-ap/
  ap_service.json       # primary station and shared AP settings
  ca-key.pem            # authority key, retained only at the root
  status.json           # primary station
  control.sock          # one owner-only command socket
  devices/c1000/
    ap_service.json
    client.pem          # unique station certificate
    client-key.pem
    mqtt-response.json
    status.json
    mqtt-events.jsonl
```

All profiles use the same AP settings and CA/server certificate. TLS peers are
routed by their exact registered client-certificate fingerprint. Reusing an
MQTT client ID or requesting another station's topic does not select a peer.
API bootstrap routes by the observed `device-sn` header or JSON identity,
rejects conflicting identities, and retains model-specific credential framing.
Freshness, reconnect and pending acknowledgements are tracked per station.
No internet route or cloud bridge is added.

The code is tested with **two simultaneous simulated TLS MQTT stations of
different models**, including independent charging writes, cross-topic refusal,
named socket/HTTP controls, model capabilities, terminal registration and stale
peer readings. A [physical three-station trial](ha-runtime-validation.md) on
2026-10-02 verified original C1000/main 1.7.1, C1000 Gen 2/main 1.1.4.9 and
C2000 Gen 2/main 2.1.6.4 simultaneously, including automatic HA discovery,
a restored original-C1000 HA setting and shared AP/gateway restart recovery.
Long-term operation and station/host power-cycle persistence remain untested.
