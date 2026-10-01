# Original C1000: native MQTT DC Smart validation

Live follow-up on 2026-10-01 used **A1761 main 1.7.1/radio 0.3.3.0** and the
existing paired app ID. The isolated 2.4 GHz AP had no LAN or internet route.
AC powered an expendable switch; DC stayed off. No C2000 command was sent.

## Packet and independent transport evidence

Native **`0076`**, A1=`22`, A2=`01 00` selects Normal; A2=`01 01` restores
Smart. FE carries the ordinary typed UTC-seconds timestamp. The nominal reply
is `0876`; a write has no status-response alias. In full F8, only byte 1
changes **2 → 1 → 2**, preserving the other twenty bytes exactly.

The earlier [Prime Bluetooth result](c1000-prime-dc-smart-validation.md) did
not enable this native route. An independent native prototype sent **two
writes** and recorded **15 complete fresh snapshots**. A second trial using
the public `LocalMqttServer.set_dc_power_saving_enabled` sent two writes and
recorded **11 explicit snapshots**, in addition to each setter's fresh
baseline and two confirmation reads. Both trials restored all eleven protected
settings and the whole original F8, with three final baseline samples each.
An independent final Bluetooth check supplied three more matching samples.

Across the combined native capture, commands were **43 `0040` status requests
and four `0076` writes**. Responses included five direct `0840`, 38 deferred
`0405`, and one `0876`. This illustrates why confirmation must tolerate an
absent setter ACK and still require fresh full status. No AC/DC-output-switch
command was sent. AC remained enabled and DC disabled in every guarded sample.

## SDK, server and interfaces

`NativeMqttCommands.dc_power_saving(enabled)` accepts a strict boolean only
for the original C1000. The server requires control permission, reads a fresh
complete baseline, rejects DC-on before a write in either direction, and
allows exactly the expected F8 mode-byte change. It publishes once, checks
any negative ACK, and confirms two complete fresh reports. Confirmation
failure can leave a changed setting; automatic setting retries are excluded.

This trial brought the original model to **seven independently verified native controls**,
including DC Smart. AP service, CLI, terminal, browser and HA expose the same
boolean command; Gen 2 and original AC Smart remain excluded.
The [subsequent native Fast trial](c1000-native-fast-validation.md) brings
the independently verified native whitelist to eight.

```sh
solix-link ap-service-set-dc-power-saving \
  --directory /path/to/private/ap-service --enabled off
```

The service must already be running with `--allow-control`. Smart can inherit
an inactivity counter, so enabling it does not guarantee a new grace period.
The tests validate configuration with DC off, not powered-DC shutdown timing.

## Reconnect and retained evidence

The prototype needed same-profile Bluetooth provisioning, which returned
successful `4824`/`4825` responses. An early SDK-repeat attempt saw neither
native telemetry nor Bluetooth advertising within its short wait and sent
**zero setting writes**. Allowing a longer AP reconnect window let the repeat
connect automatically without another Bluetooth join. This does not establish
a maximum reconnect delay or long-term idle availability.

Both successful runs removed their AP namespace, left the dedicated adapter
addressless and confirmed host routes, firewall and forwarding unchanged.
Baseline, captures, the failed attempt, SDK records and final Bluetooth
records remain owner-only in the ignored private folder. Generated-ID setup
and station power-cycle persistence were not tested.
