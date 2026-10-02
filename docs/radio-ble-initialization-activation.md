# Radio BLE initialization and activation boundaries

This is an **offline, model-specific instruction replay**, not a hardware
recovery test. It extends the [initialized advertising route
audit](radio-ble-advertising-recovery.md) without changing its frozen 50 cases.
The only binary tested is **A1763 C1000 Gen 2, main 1.1.4.9, radio 0.3.3.0**:

```text
c1000-radio-validated.bin
SHA-256 e291ec115f013953e825cb51b9e457a8731889547ab55b3058640e599e8cfec8
```

The original C1000's radio version label does not establish binary equivalence.
Its exact radio image and the C2000 radio image remain unrecovered.

## First normal `0024` enable

Actual native ingress, function `10` dispatch, command `0024`, handler
`4203d056`, and SDK enable helper `42029120` execute with body `a10101`.
Starting the SDK initialized byte `3fc903fe` at 0 reaches previously excluded
initialization `42028fa4`:

| Actual operation | Result or boundary |
| --- | --- |
| Advertising default setter `420379f4` | 100 ms becomes 160 units of 0.625 ms at `3fc82a06` |
| Link default setter `42037ac0` | Minimum/maximum 100/120 ms, latency 1, timeout 4000 ms; stored units `80,96,1,400` |
| RF transmit-power setter `42037c20` | Called with argument 6; physical BLE implementation substituted |
| OS timer registration `4202dc94` | Two 1000 ms records, each with flags `1,1` |
| Advertising timer `3fc8a1b8` | Callback `42028c14`; firmware name `ble adv Timer` |
| Link timer `3fc8a1a0` | Callback `420291f0`; firmware name `ble link Timer` |
| SDK initialized byte | Written to 1 |

This RF setting concerns Bluetooth transmit power; it is not a station charging
or output command. A second normal enable reuses the initialized SDK: no new
timer registrations or RF-setting call, while the advertising timer is started
again. Timer registration succeeds only through the synthetic OS substitute;
real allocation failures and callback scheduling were not tested.

Across synthetic PAL states 0/1/2/3, the actual path neither calls the guarded
configuration/flash/network mutation entries nor changes the complete native
configuration, runtime account, MQTT cache or BLE allowlist snapshots. It makes
no MCU command forward. Physical advertisement building, RF operation,
disconnect and OS timer operations remain recording boundaries. State 3 can
request a BLE disconnect; all four states still acknowledge status 00.
Consequently neither this ACK nor the absence of an executed persistence call
proves physical discoverability, reboot retention or asynchronous safety.

## What writes the application BLE flag

The actual common writer `42043fd8` assigns `APP_BLE` (`3fc90646`) only for
selector 0. Selector 1 assigns Wi-Fi, selector 2 assigns Ethernet, and selectors
3/4 handle status notification/deferral. A direct-branch census of the XIP code
finds nine calls to this writer and verifies their selector instructions.
Only two identified direct callers select BLE:

- `42045640`, inside connection callback `4204562c`, writes the low byte of its
  connection-state argument. Executed value 0/1 cases update the flag to 0/1.
  Connection starts the BLE authorization timer; disconnection stops the
  authorization/status timers and enters a substituted cleanup callback.
- `42048cc2`, during startup, writes 1 only when actual getter `42028ce8` returns
  a nonzero SDK connected byte (`3fc90404`). The zero case skips the write.

`0024` does not call this writer. Advertising enable leaves `APP_BLE` unchanged.
These producers support treating the flag as application connection state,
separate from an advertising-enabled preference. The native `0003` response
still cannot establish physical advertising or successful pairing. The census
enumerates direct branches only; indirect callers and delayed callbacks are
not a whole-program proof. Wireless status emission at `42042b24` is recorded,
not executed through the main controller.

## Normal provisioning activation

Eight cases run actual normal `4025` TLV handling/copy at `420537d8` and the
whole application callback `42049092..4204956c`, using retained/generated
synthetic accounts plus busy/allocation-failure branches. This extends the
[identity separation prefix](ble-native-identity-separation.md). It does not
perform a physical Prime connection or cryptographic handshake.

With the synthetic busy provider false and allocation successful, the actual
native account setter executes. Changing the account causes one recorded
configuration flash write; retaining it causes none in these fixtures. The
application then reaches SSID/name configuration, binding/lifecycle setup,
timezone application, MQTT disconnect/reload/start boundaries, and writes
volatile activation state 5. Busy or allocation failure returns before this
update path. This internal state is not an end-to-end provisioning success ACK.

The actual instructions between those boundaries leave the BLE allowlist and
MQTT identity cache unchanged. However, the invoked configuration/binding and
network lifecycle functions are substituted. Their asynchronous effects,
subsequent cache reload, BLE shutdown policy, cloud success and rollback remain
outside the proof. The optional extra WLAN provider is absent, optional text
is empty, and time/MAC/SSID providers are synthetic. A preserved allowlist in
this replay is therefore insufficient to promise independent BLE recovery
after changing a working native identity.

## Registered callback names matter

Startup also registers `42043864`. Its exact firmware name is
`data_check_is_need_update`, with logs about notifying the MCU of an update or
ignoring commands while updating. **It is not evidence of a successful MQTT
connection or account binding callback.** Eight bounded branches record its
OTA-state, update-request and MCU-notification callees. They leave application
BLE/Wi-Fi flags unchanged before the substituted calls. In particular,
`4202a230` handles persistent `upgrade_file`/`_before_version` state; substituting
that call must not be reported as proving a persistence-free lifecycle.

## Recovery prerequisites and reproduction

Keep generated-ID hardware trials deferred until independent BLE advertisement
and physical IoT-button recovery are available. Preserve the working identity
and fresh native telemetry. The user's absence currently removes that physical
fallback. This replay adds no runtime `0024` command or automatic retry, and
does not open a physical registration window. C2000 AC output stays protected.

```sh
PYTHONPATH=/tmp/solix-analysis-tools:tools/firmware_analysis \
  python3 tools/firmware_analysis/emulate_radio_ble_activation.py \
  --image firmware/c1000_gen2/1.1.4.9/c1000-radio-validated.bin \
  --output-dir /tmp/solix-radio-ble-activation
```

Install the pinned offline dependencies in
`tools/firmware_analysis/requirements.txt` in an isolated environment if needed.
Compare `radio-ble-activation-results.json` and
`radio-ble-activation-manifest.json` with the files under
`tools/firmware_analysis/expected_results/`. The 47 cases use synthetic data;
the manifest hashes the image, source and all six replay dependencies. Optimized
Python is rejected because assertions are required. Raw disassembly and the
publication scan stay in the ignored private folder.
