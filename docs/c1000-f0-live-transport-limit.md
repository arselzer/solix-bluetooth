# Original C1000: unsuccessful F0 transport probe

## Observed result

On **2026-10-01**, the original C1000 **A1761, installed main 1.7.1** accepted
normal Prime Bluetooth monitoring from the HA node. Two complete baseline
snapshots showed AC on, DC off, AC Smart on and inactive output timers. Its
input and output readings were **114–121 W**. The upstream C1000 Gen 2
**A1763, main 1.1.4.9** was monitored without changing settings.

One attempted F0 diagnostic request returned no diagnostic notification. The
following full-status request timed out. After reconnecting, complete telemetry
showed the original's AC output **off**, with input/output at zero. This was an
unexpected change during the probe; no output-off command was sent. The cause
has not been proved.

The recorded original settings and complete 21-byte F8 flags matched their
baseline except for AC output. One previously validated SDK AC-on command
restored that state. Three subsequent complete paired snapshots confirmed the
whole original settings/F8 baseline and unchanged upstream Gen 2 settings/D9/A4
(allowing the known LCD-status byte to vary). AC was on and input/output reached
**52 W** in the last two snapshots. Those changing load readings are observations,
not a claim that the switch's operating state was restored.

**C2000 was not connected or changed.** No charging, Wi-Fi, cloud, firmware or
other preference writes were made. The test load on the original was authorized
as expendable. No further diagnostic requests were sent.

## The transport mistake and bounded failure mechanism

The [offline F0 analysis](c1000-f0-diagnostic-tunnel.md) proves a getter inside
public **main 1.5.9**, reached through function `0c`, command `0000`, with a
specific plaintext envelope. It does not prove how installed **1.7.1** or the
original's radio transports that envelope.

The live attempt used host pattern `03000c`, command **`0000`**, and an
**AES-GCM encrypted** payload. That omitted the `0x4000` encryption marker used
by the established Prime BLE commands. The private request builder encrypted
the body without setting that marker. Successful instruction replay of the
plaintext MCU packet was insufficient to justify this transport combination.

The public **Gen 2 radio 0.3.3.0** parser provides static corroboration: it checks
`packet[7] & 0x40` and otherwise dispatches the original payload pointer without
decryption. This is another model's radio image; original-radio forwarding
remains unproved.

The older original MCU tunnel trusts a 16-bit embedded length but increments
its copy-loop index as an 8-bit value. Lengths above **255** do not terminate in
the bounded replay. Treating this attempt's ciphertext as plaintext would
produce a length above 255. That gives a concrete **conditional failure
mechanism**, not proof that the station entered that loop or reset. Watchdog
behavior, radio forwarding and a plaintext installed-1.7.1 main image remain
unresolved. No ciphertext or session key is published here.

The correctly framed F0 handler also cancels an existing **30-minute upgrade-mode
reset timer**; it must not be treated as a passive sensor or used during OTA.
There is no public SDK, CLI, HTTP, browser or Home Assistant F0 control.

## Retained evidence and next work

Raw notifications, private request/session material, six complete paired
snapshots, failed reconnect evidence, restoration records and installed runtime
hashes are retained in the ignored, restricted
`.solix-private/query-recovery-20261001/` directory. All publication evidence
above is derived from that capture; the synthetic firmware results are separate.

Before another probe, establish original-radio routing and encryption handling,
validate the installed firmware's tunnel, and bound malformed lengths before
they reach a device. The GPIO's physical meaning is also still unknown. The
current result is a documented transport limit, not a verified GPIO sensor.

The subsequent [BLE framing guard](ble-encryption-framing.md) rejects this
inconsistent encrypted header in the Python library and Web Bluetooth app.
It does not establish diagnostic support or make a corrected F0 request safe.
