# Gen 2 ordinary telemetry audit: Wi-Fi quality and payload capacity

## Scope and result

This offline audit uses the published C1000 Gen 2 main **1.1.4.9** and radio
**0.3.3.0** images. It adds **288 synthetic instruction replay cases**. It does
not change a device, add a control, or establish C2000 or original C1000 behavior.

The useful finding is a limitation on a possible Wi-Fi-quality sensor:
**controller telemetry can report 100 after the radio fails to obtain RSSI**.
The byte is also retained in incremental reports. A separate radio handler
returns signed RSSI with an explicit failure result, making it a better
candidate for future monitoring once its external transport is verified.

All A3/A4/A7 offsets below include the leading `04` type byte.

## A3 byte 8: transformed cached Wi-Fi RSSI

The provenance is established across both images, rather than inferred from a
field name:

1. Main `08030d80` queues internal function `10`, command `0003`, with builder
   `08012ebc` and completion callback `0801ae44`. The replay checks the actual
   queued descriptor; the builder statically sends the same function/command.
2. Radio dispatch table `3c147be4` maps command `0003` to `4203cc10`.
   That handler calls `42011522`, which calls the ESP AP-info service at
   `420ebe0a`. On success it returns the signed byte at AP-record offset `2c`.
   The firmware's own error log identifies the service as
   `esp_wifi_sta_get_ap_info`.
3. The radio handler puts that byte in raw A3 of its status response. Main
   callback `0801ae44` stores it at `20000786`; getter `0801b03c` reads it signed.
4. Full A3 serializer `0801a2a8`, specifically `0801a326..0801a33a`, produces:

   ```text
   quality = min((((signed_cached_rssi + 100) & 127) * 2), 100)
   ```

For example, −100, −80, −70 and −50 produce 0, 40, 60 and 100. This is a
quantized, capped quality byte, **not raw dBm**. The shift/mask happens before
the clamp; values outside the normal range wrap. All 256 signed inputs were
executed rather than assuming a conventional clamp at both ends.

### Failure and freshness matter

On an AP-info error, `42011522` returns **0**. Handler `4203cc10` still emits a
successful outer response with A3 value 0. The main callback accepts it and
the serializer converts it to **100**. Both error results exercised in the
replay follow this path. A cold zero-filled cache produces 100 as well.

A rejected callback or a response without A3 leaves the old cached RSSI
unchanged. Serializer mode 3 preserves byte 8 from the previous telemetry
buffer even if the cached RSSI has changed. Consequently, a new packet or a
recent gateway `last_seen_timestamp` does not establish this field's freshness.

Do not expose this byte as an unqualified signal-strength or connectivity
sensor. It cannot establish association, MQTT connectivity, or a valid RSSI
measurement by itself. No new runtime metric was added by this audit.

The similarly named radio handler `4203da2c` is command **004b**, not 0003. It
uses a different radio status getter; confusing these dispatch entries would
give the wrong provenance for the controller's cached RSSI.

## Separate RSSI handler: candidate, not an implemented request

The same radio table maps command **0022** to `4203cb26`. It calls the actual
RSSI wrapper above and replies:

| Getter result | Handler response body |
| --- | --- |
| Nonzero signed RSSI | Status `00`, raw A1 length 4, signed little-endian RSSI |
| Zero, including AP-info failure | Status `01`, no RSSI field |

Five actual-handler cases cover valid negative RSSI, zero, and service failure.
The handler does not parse an input payload. Its exercised calls are the RSSI
observation, logging, serialization and substituted response transport.

This audit establishes the **radio dispatch entry**. The later
[RSSI route audit](radio-rssi-routing.md) executes function **`10`** / command
`0022` through BLE and native MQTT admission, handler and reply serialization
using synthetic inputs. It does not add a runtime command or establish live
behavior on other models. In particular, function `10` / encrypted `4022`
is distinct from Prime **negotiation** on function **`01`** / `4022`, which
handles time and timezone information. Native session establishment and the
physical observation still require separate evidence.

## A3 bytes 9–10: internal transfer payload capacity

Main dispatch-table entry at `080338d8` maps internal command `004e` to
`0800bec8`. The handler reads an untyped A1 little-endian 16-bit MTU report and
stores at `200007b8`:

```text
capacity = (reported_mtu - 46) & 0xfffc
```

Getter `080182e4` returns that word. A3 serialization writes it to `[9:11]`,
including in mode 3. Ten cases execute the real parser, handler and serializer:
MTU 64 → 16, 247 → 200, 256 → 208, and 517 → 468. The subtraction wraps for
synthetic MTUs below 46; the handler itself does not reject those values.

This is not a charging limit, battery capacity, current ATT MTU, or a universal
maximum for application commands. Static consumers include the bulk-update
response at `0802c1d6..0802c1e4`, whose firmware log calls the value `MTU`.
Those update paths were not executed. The capacity can remain cached after its
producing report; reading it does not prove a currently active BLE connection.

## Other ordinary fields checked statically

These observations are from the actual serializers; they are not additional
execution cases in the 288-case count.

- **A4 serializer `0801a018`:** full/settings branches set byte 19 and bytes
  27–31 to zero. Byte 24 mirrors the upper cap, 25 the lower cap, and 26 the
  already documented saved language byte. At byte 32 the low nibble is rebuilt
  with the off-grid alert in bit 1, while the upper nibble is retained from the
  old buffer. Byte 33 is retained too. Stack initialization does not make these
  retained bytes fresh: the serializer subsequently copies the prior buffer.
- **A7 serializer `08017ee0`:** `[5:7]` calls `0801a724(5)`, the same AC input
  power getter used by A6 `[3:5]`. Mode 3 retains this power word. It is a
  duplicate power reading, not an extra AC mode or SurgePad control.

These limited paths do not establish whether SurgePad or another control
exists elsewhere. They do rule out assigning those meanings to these bytes
without additional evidence.

## Reproduce and review limits

```sh
python3 -m pip install -r tools/firmware_analysis/requirements.txt
python3 tools/firmware_analysis/emulate_normal_feature_audit.py \
  --output /tmp/solix-normal-feature-audit
```

`--firmware-dir` or `SOLIX_FIRMWARE_DIR` selects external copies of the same
images. Both exact hashes are enforced; the main helper also checks its size.
Synthetic results and the dependency/hash manifest are under
`tools/firmware_analysis/expected_results/normal-feature-audit-*.json`.

| Cases | Executed evidence |
| ---: | --- |
| 10 | Radio RSSI wrapper and wireless-status serialization → main parser/callback → A3 |
| 5 | Separate radio RSSI reply, including failure status |
| 256 | Every signed cached byte through actual A3 serialization |
| 4 | Incremental A3 retains previous quality |
| 2 | Rejected or missing-RSSI callback preserves cache |
| 10 | Internal MTU report, stored capacity and A3 mirror |
| 1 | Actual main request descriptor plus exact radio/main table assertions |

The ESP AP-info result, optional network strings/state providers, libc,
logging, queue and response transports are host substitutes. The main callback
starts with synthetic readiness/reconnect flags cleared, so its separate
binding/reconnect side-effect branches are **not** characterized here. The
controller getters, TLV parser, selected callbacks and serializers execute real
ARM instructions; the radio wrappers and serializers execute real RISC-V
instructions. No RF accuracy, polling interval, physical connection, firmware
update, or live request was tested. Results contain synthetic data only.
