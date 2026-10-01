# Original C1000 isolated update network

## Purpose and current state

On 2026-09-30 the original C1000 / A1761, main **1.5.1**, offered **1.7.1**
in the Anker app. The app required station Wi-Fi. This experiment provides
internet for the official update without disclosing the home Wi-Fi password
or permitting station access to the home LAN. After a failed first update and
a retry, the user confirmed that the app reports installed **1.7.1**.
Bluetooth independently confirms main **1.7.1** using Prime/AES-GCM.
Three fresh samples match all **11 protected pre-update settings**. The
post-update radio version was not captured during this update. A subsequent
[local MQTT trial](c1000-original-mqtt-followup.md) independently confirmed **0.3.3.0**.
The C2000 and C1000 Gen 2 are outside this experiment.

This temporary internet-enabled network is separate from the tool's normal
[isolated native MQTT AP](isolated-ap-mqtt.md), which has no internet route.
Its private runner is research infrastructure, not a shipped internet-AP mode.

## Isolation and verification

A dedicated adapter supplies WPA2 Wi-Fi on **2.4 GHz, channel 6**, using a
fresh disposable SSID/password. The PHY, hostapd, dnsmasq, firewall and IPv4
forwarding belong to an owned network namespace. Wi-Fi client isolation is
enabled. IPv6 is disabled and dropped.

Internet access uses slirp4netns with its host-loopback and built-in-DNS
access disabled. Namespace rules block private, special and connected host
networks, plus the router's current public address. Outbound station traffic
allows TCP **80/443/8883** and UDP **123**; clients use the AP's DNS service,
which forwards only to public resolvers with DNS-rebinding checks.

Before enabling Wi-Fi, both namespace-originated traffic and a synthetic
client behind the forwarding/NAT path reached public HTTPS successfully.
Each path also exercised explicit deny rules for the HA host, home router,
relay gateway and public router address. Host routes, firewall rules and
forwarding configuration matched the baseline. After startup, packet capture
was confirmed running. This verifies these paths and destinations; it does
not imply that every possible update protocol or destination port is allowed.

### Mount sandbox correction

slirp4netns **1.3.3** makes the mount namespace's root private without applying
that change recursively, then mounts a tiny tmpfs on `/tmp`. On this host,
`/tmp` was a separately shared mount: two failed sandbox attempts propagated
temporary overlays and hid its existing contents. The original files were
not deleted.

Recovery identified the exact owned mount/device trees, audited process
references and mount peers, preserved overlay-only data, privatized the copied
subtrees and removed them with guarded ordinary unmounts. The original `/tmp`
and earlier capture tools became visible again.

The corrected runner wraps **only the slirp process** in
`unshare --mount --propagation private -- ...` before enabling slirp's own
sandbox. Subsequent checks retained the original host `/tmp` mount. Do not
change host mount propagation or weaken the host firewall as a workaround.
See the [slirp sandbox source](https://github.com/rootless-containers/slirp4netns/blob/v1.3.3/sandbox.c)
and [slirp options](https://github.com/rootless-containers/slirp4netns/blob/v1.3.3/slirp4netns.1.md).

The host's tcpdump AppArmor profile also refused save files inside a hidden
home directory. Captures instead use an owned, restricted `/tmp` directory
and are copied into private persistent storage. No security profile was changed.

## First official-app Wi-Fi attempt

The app reported failure and suggested checking internet/ports, restarting
or using a hotspot. Packet evidence separates that generic message from the
actual completed steps:

- Three Wi-Fi associations and DHCP acknowledgements occurred.
- All five observed DNS queries succeeded; three NTP replies were delivered.
- No outbound **8838, 8883 or 5353** attempt appeared in this capture.
- An initial HTTPS SYN received SYN-ACK but no final client ACK.
- A later connection completed TCP and sent a TLS 1.2 ClientHello to
  `ankerpower-api-eu.anker.com`.
- The server selected the offered `TLS_ECDHE_RSA_WITH_AES_128_GCM_SHA256`
  cipher and delivered its certificate/key-exchange flight. The station
  acknowledged every server byte, then sent no ClientKeyExchange, TLS alert
  or application data. The server closed about 71 seconds later. After each
  brief traffic burst, the station also stopped answering the gateway's ARP
  probes until a later join/DHCP cycle.

The delivered chain was `*.anker.com` → Amazon RSA 2048 M01 → Amazon Root CA 1
→ Starfield Services Root CA G2. The leaf was valid **2026-09-15–2027-03-31**;
the intermediate and cross-signed root were also within their validity windows.

The first NTP reply arrived roughly **40 ms after** the TLS server flight.
Its transmitted time matched capture wall time, but zero request timestamps
and receipt of a reply do not prove the station applied it before validating
the certificate. Certificate/time handling and startup state are candidates,
not established causes; the ARP silence also permits broader radio teardown
or a stall. There is no evidence that the earlier candidate local MQTT CAs or
credentials were installed: those response fixtures were never requested.
The app also reported failure on a 2.4 GHz mobile-data hotspot. That comparison
does not provide a hotspot packet capture, but reduces the likelihood that this
AP's filtering explains the error. A separate, reversible trial delayed only
TCP source-port-443 replies on AP WLAN egress by two seconds, leaving NTP
unchanged. **Wi-Fi setup succeeded in this trial.**

The retained working capture has five successful HTTPS streams and two
mutually authenticated cloud TLS connections on port 8883. Both sides sent
certificates/key-exchange material, CCS, encrypted handshake records and
bidirectional application records. These support completed TLS sessions;
Finished contents and MQTT CONNECT/SUBACK/messages remain encrypted.
The final broker connection remained active through the capture's end.

The successful HTTPS session used the **same leaf certificate and cipher** as
the failed session. Its ClientKeyExchange preceded the first newly observed
NTP reply by about **397 ms**. Thus the trial does not demonstrate that a new
NTP reply had to arrive before certificate processing: an earlier retry may
already have set time, or the delay affected another condition. A single
successful delayed retry is not proof of a universal workaround.

One later retry also attempted direct DNS to public `8.8.8.8:53`, received no
reply under the policy, then successfully fell back to the AP resolver. No
additional destination port was opened to obtain the working connection.

## Bluetooth setup correlation

Full HCI captures show eight official-app setup sequences before the working
retry. Activation `4025` received encrypted `4825` replies within roughly
0.4–1.0 seconds, before the later HTTPS connection in the correlated AP
attempts. Those replies therefore are not proof of finished cloud setup.
Their repeated ciphertext identifies a repeated response category, not its
numeric status.

The app's opcode map identifies **`4035` / `4835` as exitConnectReq/Res**, not
a status query. In the second AP attempt it followed activation by about
14 seconds; the TLS stall preceded that exit command. Bluetooth telemetry
continued and no BLE disconnect occurred during the captured failed attempts.
These findings neither prove a certificate failure nor recover an encrypted
status code.

An intermittent USB transport loss stopped the initial logcat recorder, while
phone-side HCI logging continued. A reconnecting recorder was started and its
fresh heartbeat/file growth checked **before** cueing the firmware update.

## Capture limits and retention

Full phone logcat, Bluetooth HCI bugreports, network packets, certificates,
credentials and original metadata remain in ignored, restricted private storage.
The final bugreport contains **25,184** records in the current full HCI log;
the previous rotated log is retained too. The update network and its shaping
worker were stopped after capture. Host routing, firewall and forwarding
matched the baseline, the owned namespace was removed, and the dedicated
adapter was returned to its original down state without addresses or routes.
The current HCI file runs through 19:18:51 UTC, but the last SOLIX Bluetooth
frame is at 18:44:44 UTC, before the update. A remote-user disconnect follows.
No BLE OTA opcode or post-update SOLIX telemetry is present in that capture;
continued phone logging is not proof that the station used Bluetooth for OTA.
Phone logcat separately records **675 incoming AWS IoT MQTT-topic messages**
for the original-model product from 18:47:10 to 19:36:32 UTC: 625 `param_info`
and 50 `state_info`. Topic identifiers stay private. These logs support cloud
telemetry reception after the BLE disconnect, but contain no decoded payload,
firmware version or independently verified output state.
The [app OTA audit](c1000-app-ota-capture-investigation.md) explains conditional
URL logging, internal file storage and encrypted BLE chunks: none guarantees
non-root recovery of a plaintext firmware image. Installation in this trial
used the official app.

## Official update and retry

The app offered a single Update button. The first attempt progressed beyond
0% but failed. It established TLS connections to
`public-aiot-fra-prod.s3.dualstack.eu-central-1.amazonaws.com`, the public vendor
bucket also used by the retained original-C1000 1.5.9 package. The object path,
HTTP response and firmware bytes remained encrypted.

The initial two-second reply delay also affected the S3 download. A retained
interval delivered about **2 kB/s**. That delay is a plausible contributor to
the failed update, but the capture does not establish the cause: a new
association/DHCP cycle also occurred before the old download socket reset.
The largest failed-attempt stream had **897,298 normally acknowledged TCP
bytes**, including TLS overhead. This is neither an image size nor evidence
of a successful installation. Reset acknowledgement numbers must not be
counted as normal delivery, and the older 1.5.9 package length is not a
completion threshold for 1.7.1.

Before Retry, the delay filters were restricted to the two observed Anker API
addresses. The S3 addresses were checked to be separate and were excluded.
The user reported normal progress and completion, then checked **v1.7.1** in
the app. No additional destination ports or LAN access were enabled.

The final capture shows an earlier S3 stream receiving **1,390,410 transport
bytes**, all normally acknowledged along with the server FIN. Its final
25-second interval accelerated to about **37 kB/s**. A later S3 stream
delivered **433,922 transport bytes** before the client closed, including all
captured TLS application records; a 31-byte encrypted TLS alert followed that
close. The later stream took about **3.25 seconds**, roughly **133 kB/s** with
handshake overhead. These measurements support improved transport after the
filter change, not a recovered image or a known component size. A fresh cloud
session remained active through the end of the retained capture.

## Post-update Bluetooth boundary

The station advertises and the HA node completes the Bluetooth link, fresh
GATT discovery and notification-CCC write, but legacy hello receives no SOLIX
notification. Checks with phone Bluetooth off, a user button press and a later
user restart did not establish telemetry. Those initial attempts did not
establish output/settings preservation or the radio version. No setting write
was sent during those checks.

The saved app's pre-update negotiation is legacy, with a 40-byte ASCII-hex
client identifier instead of the library's fixed 36-byte UUID. A private trial
using that captured identifier also failed. Its 58-byte hello matches the
app's header, command, TLV order and identifier; only timestamp/checksum differ.
The HA capture confirms a valid checksum and transmitted ATT Write Command.
Client/station MTUs are 517/256, so this packet fits.

The newly discovered SOLIX handles differ from the pre-update phone capture;
the library writes the new command handle and enables the correct CCC.
The app previously waited about 3.4 seconds after enabling notifications.
A separate four-second-delay trial also failed. These negative comparisons
do not establish that legacy support was removed or that an account ID is
required. A fresh official-app Bluetooth capture is the next useful reference.
The user subsequently reported that the official app connected after pressing
the station's **IoT button**, with the temporary AP off. This differs from the
earlier unspecified button press. Legacy attempts with the normal and captured
app identifiers still failed after the same IoT-button action and phone release.

### Encrypted hello resolves monitoring

A bounded **Prime/AES-GCM `4001` hello** returned `4801`, which authenticated
successfully with the known bootstrap cipher. This first probe sent no pairing
or registration request. A subsequent full Prime session using the existing
captured app identifier completed ECDH and registration, then delivered original
C1000 `c840`/`c402` telemetry through its **`4040`** status path.

Main B3 reported **171 / 1.7.1**. Its F8 value changed from type01/length3 to
type04/length21. The latter matches the actual public main 1.5.9 serializer:
DC and AC modes remain at offsets 1 and 2, with 1=Normal and 2=Smart. The decoder
now accepts both exact layouts and retains the rest as raw bytes. This corrected
the baseline wait, which had been missing only the two Smart-mode fields.

Three fresh full samples confirm AC on, DC off, 1000 W charging limit, 720-minute
Device Timeout, 30-second display timeout, brightness 2, light off, Celsius,
fast charge off and both Smart preferences enabled. All match the original
baseline. No C2000 control was involved.

### Control round trips

Six writes on the original C1000 verify these Prime controls:

| Setting | Trial and restoration |
| --- | --- |
| Display brightness | 2 → 1 → 2 |
| AC charging-power limit | 1000 → 900 → 1000 W |
| Device Timeout | 720 minutes → 0/Never → 720 minutes |

The existing original-model MCU command bodies, including the typed FE timestamp,
work with GCM encryption. Fresh reads confirm each change and restoration;
three final samples after each trial match all 11 settings. AC remained on in
all 14 recorded trial snapshots, and the entire expanded F8 value stayed
identical. These full-battery tests verify stored limits, not charging-rate
enforcement. Never is the saved timeout value, not proof that every independent
radio/sleep path is disabled. Other original Prime controls remain unvalidated.

The updated SDK then repeated all three round trips without prototype patches:
six more writes and ten snapshots, including three fresh final samples. Across
both stages, **12 writes and 24 snapshots** retain AC on and the identical full
F8 value; final reads match all eleven baseline settings. The SDK requires those
settings and F8 freshly before and after writes, rejects unvalidated Prime
controls before transport, and reports uncertain outcomes without retrying.
This test used the retained app identifier; generated-ID registration on the
original C1000 remains unverified.

### App security selection

The retained app's inherited original-model hello builder supports encrypted
`4001`. Its scanner enables security when **capability bit 2 is set** and the
cached per-product cloud encryption mode is nonzero; a missing mode returns -1
and also passes that second condition. No firmware-version threshold was found
in this branch. The native scanner's mapping of on-air bytes to capability
remains unverified, so the tool offers explicit Prime selection and preserves
the legacy default that worked on 1.5.1.

Private actual-code replay covers 1,280 selector cases, four hello/framing
cases and four complete F8 serializer cases. Original secure app hellos include
A2 of length 40 or 0, whereas the existing Prime client hello contains timestamp
only. The timestamp-only form worked on this unit; that does not establish
universal acceptance on every original firmware/radio combination.

## Local updater groundwork

A local installer is not implemented. The useful next steps are:

1. Recover a public vendor image and metadata, preserve its original bytes and
   hashes, and identify model, voltage variant and component versions.
2. Identify update initiation, transfer, acknowledgements, completion and
   reboot/version queries from this capture and the app implementation.
3. Distinguish checksums from authenticated image signatures, and determine
   whether Wi-Fi performs the download or carries a phone-supplied transfer.
4. Build offline inspection and compatibility checks before testing a local
   transfer on the expendable original C1000.

Do not substitute a different model's image or count encrypted network bytes
as a verified firmware file. Any later updater must report unsupported formats
and incomplete transfers explicitly; this experiment does not validate flashing
the C2000 that supplies production loads.

## October 1 continuation

Additional Prime BLE SDK round trips verified screen timeout **30→60→30 s**,
light **off→low→off**, and **Celsius→Fahrenheit→Celsius**. Full fresh settings
and the 21-byte F8 block matched at restoration. The same six-control subset
(including the three September 30 controls) then passed isolated native MQTT.
See [native MQTT and route behavior](c1000-original-mqtt-followup.md).
