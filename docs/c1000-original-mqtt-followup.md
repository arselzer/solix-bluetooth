# Original C1000: native MQTT after the 1.7.1 update

Later on 2026-10-01, an [independent native DC Smart trial](c1000-native-dc-smart-validation.md)
and public SDK repeat added a seventh control, with DC off and full F8
restoration. A subsequent [native Fast trial and public SDK repeat](c1000-native-fast-validation.md)
added an eighth control, with independent final Bluetooth confirmation.
The six-control measurements below describe the earlier trial.

## What the local trial established

On **2026-10-01**, an original **A1761 C1000**, main **1.7.1** and radio
**0.3.3.0**, completed bootstrap against the isolated local API and broker.
Bluetooth provisioning used the negotiated **Prime/GCM** transport with the
original A1761 Wi-Fi field layout. Both `4824` and `4825` acknowledged success.
The network supplied no station internet route. This updated-original trial
used the **existing account ID** in its local activation profile; acceptance of
a newly generated account ID for this original native MQTT workflow remains
unverified. Locally generated certificates do not establish account-ID
independence.

The API received `get_mqtt_info`, binding/check, `update_info`, DST and
point-switch requests. `update_info` reported both versions. The separate
read-only `4030`/`4830` query corroborated them: its **untyped** A1 and A2 values
were radio and main version strings respectively. Do not interpret the first
ASCII byte as a typed-field discriminator.

The station established **TLS 1.2 with mutual certificate authentication**, then
MQTT 3.1.1 CONNECT, SUBSCRIBE, publications and PING. The broker authenticated
the provisioned client certificate; the station connected using the locally
supplied broker trust material. This was a local authenticated connection,
without forwarding station traffic to Anker. The initial bootstrap/status
trial made no setting writes. A later private native-control trial verified
six settings and restored their baselines, as detailed below.

This supersedes the transport limitation in the earlier
[main 1.5.1/radio 0.1.3.0 Wi-Fi trial](c1000-original-wifi-validation.md).
That earlier certificate candidate was never requested, so its failure did
not establish incompatible encryption or HTTP framing. The update and changed
bootstrap state are confounded; this result does not isolate which fixed the
older radio's failure to request credentials.

## Credentials and framing

The successful response uses the original station's **16-character serial**:

- Client certificate and private key: Base64 of AES-256-CBC, PKCS#7 padded.
- AES key: serial repeated twice, truncated to 32 ASCII bytes.
- IV: first 16 serial bytes.
- Root CA: plain PEM.
- HTTP response: complete JSON with **Content-Length**.

Certificates and keys were generated for the local service. Serial-derived
wrapping is protocol encoding, not a secret unavailable to someone who knows
the serial. Accounts, serials, WLAN details, certificates, private keys and
raw request/publication logs remain in private captures.

`APServiceConfig(model=Model.C1000)` now selects A1761 and requires the observed
16-character alphanumeric serial. Gen 2 profiles retain their 17-character
requirement and existing default. The public credential helper supports both
observed lengths. An independent synthetic OpenSSL vector and local certificate
round trip cover the original envelope; these tests contain no real identity.

An additional offline replay fed the same response structure through the
**retained Gen 2** radio parser using whole, 1024-byte and 511-byte chunks. All
three cases recovered matching key/IV and reached substituted storage/startup
calls. This is supporting parser evidence from another model, not proof that
the original radio binary is identical. The live original connection is the
compatibility evidence.

## Original telemetry has its own opcodes

The full original publication is function `0f`, command **`0405`**. The observed
packet contained 360 payload bytes and 69 TLVs, including the 21-byte type04
F8 block with DC/AC Smart-mode values at offsets 1 and 2. Existing original
telemetry decoding recovered firmware, SOC, AC/DC state, charging-power limit,
timeouts and preferences consistent with the protected baseline.

`0407` is a network-information publication; `0830` is a version response.
Their tags must **not** be passed to the power decoder, because shared tag
numbers can produce plausible but unrelated readings. Original MQTT decoding
accepts only `0405` and successful direct `0840` responses, after product and
serial checks. Gen 2 continues using `0421` and `0900`.

## Why a status read can reply through `0405`

The read-only request is **`0040`**, source A1=`22`, with a normal typed FE
UTC-seconds timestamp. Its nominal response is `0840`. In the first live
attempt, a full `0405` arrived approximately **0.489 seconds after the request**,
but waiting exclusively for `0840` timed out.

The public original **MCU 1.5.9** image explains a deferred route:

| Address | Behavior |
| --- | --- |
| `0800adb0` | Handles `0040`, prepares success and full telemetry |
| `0800cb78` | True when module byte `20000c6c+a7` is nonzero and byte `+c4` bit1 is set |
| `0800ae00` | With that gate true, stores report state `49` and pending byte `20000054+0c=1`; skips direct reply |
| `0800ef8e` / `0800f06c` | Report scheduler dispatches state `49`; pending byte forces the full report |
| `0800f0e0` | Calls the same full telemetry serializer `08009164` |
| `0800f0fe` / `0800f104` | Sends function `0f`, command `0405`, through `08012b38` |
| `0800f108..0800f110` | Clears pending bookkeeping and advances to state `50` |

The actual prefix builder `0800a8b4` emits A1=`34` for this deferred report.
Consequently, `34` does not distinguish a request-triggered report from an
unsolicited one. Changing the request source from `22` to `21` does not change
the routing gate.

**24 synthetic instruction-replay cases** vary both gate inputs and request
source. Gate-off cases return the direct success body; gate-on cases execute
the complete report scheduler and send `0405`, then clear pending state. Full
measurement serialization, status-difference detection, clock/activity getters
and transport are explicit substitutes. The routing, gate and prefix branches
execute actual firmware instructions. Installed 1.7.1 code is unavailable, so
the older code remains an explanation consistent with the observed timing.

`NativeMqttRequest.response_aliases` marks **only original `0040` status** with
`("0405",)`; the primary response remains `0840`. A receiver must validate
identity and full telemetry, and accept an alias only after sending the pending
read. An unsolicited report can race with a request, so this establishes fresh
status availability, not a transaction identifier absent from the report.
Setting acknowledgements have no such alias.

A subsequent **read-only live check received three full status frames**, each
approximately 0.4 seconds after its request, using this alias and protected
original telemetry checks. An initial charging-setting attempt received no
`0844` acknowledgement; that timeout alone did not establish whether the
setting changed. The subsequent successful control trial sent each write once
and requested another full status to establish its result. The status alias
must not be reused as a write acknowledgement.

## Original setting builders and readback

The native builders use the same validated typed A2 fields as the original
Bluetooth controls, with source A1=`22` and an FE seconds timestamp. On main
**1.7.1/radio 0.3.3.0**, the private native MQTT trial verified these complete
round trips:

| Setting | Native command | Supported SDK values | Live change and restoration |
| --- | --- | --- | --- |
| AC charging-power limit | `0044` | 100–1000 W, steps of 100 | 1000 → 900 → 1000 W |
| Device Timeout | `0045` | 0, 30, 60, 120, 240, 360, 720, 1440 minutes | 720 → 0 (Never) → 720 minutes |
| Display timeout | `0046` | 20, 30, 60, 300, 1800 seconds | 30 → 60 → 30 seconds |
| Display brightness | `004c` | 0–3 | 2 → 1 → 2 |
| Light mode | `004f` | 0–4 | 0 → 1 → 0 |
| Temperature unit | `0050` | Boolean: Celsius/Fahrenheit | Celsius → Fahrenheit → Celsius |

The trial made **12 writes** and collected **18 fresh full status snapshots**,
including three final baseline checks. Each write was followed by an explicit
`0040` status request and an independently decoded full `0405` report. All
11 protected baseline settings and the raw F8 block matched at completion:
AC on, DC off, charging limit 1000 W, Device Timeout 720 minutes, display timeout
30 seconds, brightness 2, light off, Celsius, fast charge off, and both Smart
modes enabled. **AC output remained on.** No AC/DC output command was sent.

The packaged **APService/LocalMqttServer** subsequently repeated all six round
trips using an actual original-model profile, without prototype patches. Each
setter required a fresh full baseline and **two** fresh confirmation reports;
all 11 settings and the complete F8 block were protected. A separate Never
hold and server restart brought this stage to **14 writes**. Three final full
samples matched the original baseline, with AC output on throughout. The
server sent no AC/DC-output command and made no automatic write retry.
After AP cleanup, an independent Prime Bluetooth audit supplied three fresh
full samples matching all 11 original settings and F8 bytes. Host routes,
firewall and forwarding matched their pre-test state, the owned namespace
was removed, and the dedicated adapter was returned.

These domains are serializer validation, not proof that every value was tested
on native MQTT. Device Timeout zero means the known Never setting; it does not
guarantee continuous radio availability. Brightness zero can turn off the
display. No output switch is included in these setting packets.

A separate actual-instruction check of the original MCU's common
acknowledgement helper `08007718` found that the same `0800cb78` network gate
suppresses direct write acknowledgements. Thus a timeout waiting for the
nominal `0844`/other write reply cannot safely be treated as a failed write or
as permission to retry. Send once, inspect a negative acknowledgement if one
arrives, then request fresh full status and confirm the exact setting plus all
protected fields. The successful trial used this send-once/readback workflow;
it did not infer success from a missing acknowledgement or retry a timed-out
write. Original write requests retain their nominal response-command metadata
for recognizing an explicit reply, with no status aliases added.

## Normal AP-service CLI check

A separate standard `ap-service-run` check, using the saved original profile,
joined through `--provision` with the known original Prime Wi-Fi layout.
Its initial fresh status used **direct `0840`**, followed by periodic and
request-triggered `0405` reports. A later cached-profile run verified
`ap-service-set-light --mode low` and `--mode off` through the owner-only Unix
socket and real CLI parser. Both writes passed the server's fresh protected
readback, restoring the baseline with AC output on. The temporary worker/AP
then stopped; host routes, firewall and forwarding were unchanged and the
owned namespace was removed.

One cached AP attempt immediately after the independent BLE audit produced
no fresh connection within its 60-second run; same-profile BLE provisioning
recovered it. This observation does not identify the radio's cause or prove
that every BLE session requires provisioning. The successful short server
restart below did not require it. Raw failures and successful retries remain
in the private evidence bundle.

## Never and server recovery

With Device Timeout set to Never, a **30-second hold** preserved the protected
settings. Only the local AP service was stopped and restarted; the isolated
AP remained running. The station reconnected in **2.256 seconds**, retained
Never, and supplied a fresh full status without another Bluetooth join or
activation. Device Timeout was then restored to **720 minutes**. Three final
samples confirmed all 11 original settings and F8 bytes.

This tests local-service recovery and a brief hold under the existing load.
It does not establish idle availability over days, behavior across station
power-off, or recovery from every radio fault. Previous cached-profile AP
restarts also joined successfully; those were separate from this timed trial.

## Bounded reporting window

The original `0057` request uses A2 type01 boolean and **A3 type02 uint16
seconds**, unlike the Gen 2 uint32 duration. The SDK bounds requests to
1–120 seconds. MCU handler `0800b2a8` stores the duration, asks timer helper
`080104d4` for `seconds × 1000`, and restarts its timer through `080104a0`.
Callback `080078a4` clears the report flag and stops that timer.

**30 synthetic cases** exercised the actual handler, period/start/stop helpers
and callback. If a timer is already running, the period setter refuses to
replace its duration and the handler ignores that return code; restarting can
therefore retain the previous period. The parsed TLV lookup and acknowledgement
are substituted. No wall-clock waiting or radio transmission was emulated,
and original native streaming still requires live validation.

## Reproduction and remaining limits

Controller input:
`firmware/c1000_original/1.5.9/MainMcu-decoded.bin`, SHA-256:

```text
b295ee8613f5c96e70dcc905896df516621cab4dc590bb580eac6b84519911a6
```

The private evidence bundle retains the scripts, inputs and hashes for the
24 status-routing cases, 30 timer cases, 12 app serialization/framing cases,
and three radio-parser cases. App replay uses actual shared serializer
instructions with synthetic VM containers/providers and dummy encrypted bytes;
it does not replay the app's real cryptography or expose app data.

Focused public synthetic tests cover original identity/telemetry routing,
status alias isolation, request fields, original power bounds, Gen 2 control
refusal, credential encoding, and profile loading. Original charge-cap,
reserve, Time-of-Use and readiness builders remain explicitly unavailable.
Do not infer their support from Gen 2 firmware or from original MQTT bootstrap.
