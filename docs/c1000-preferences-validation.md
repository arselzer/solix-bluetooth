# Original C1000 preferences: physical validation

## Device and method

On 2026-09-30, an original A1761 with B3 version code **151** (reference
format **1.5.1**) powered the expendable HP-switch load from its AC output.
Its AC input was supplied by C1000 Gen 2. Only original-model legacy BLE
commands were sent; no C2000 or output-switch command was used.

The baseline was battery 100%, AC on, DC off, charging limit 1000 W,
Device Timeout 720 minutes, display timeout 30 seconds, brightness 2,
light off, Celsius, fast charge off and both outputs in Smart mode.
Every preference setter obtained a fresh baseline and required fresh
readback of the changed preference and ten protected configuration/output
fields. Each change was followed by a two-second delay and another complete
status request. Three final requests matched the original baseline.

## Confirmed settings

All requests contain A1=`21`, A2=`01` plus the value, and typed FE Unix seconds.

| Preference | Legacy command | Trial | Confirming field |
| --- | --- | --- | --- |
| Temperature unit | `4050` | Celsius → Fahrenheit → Celsius | DD=`0100` / `0101` |
| Fast charge | `405e` | Off → on → off | E5=`0100` / `0101` |
| AC Smart mode | `4077` | Smart → Normal → Smart | F8 AC byte, 2 / 1 / 2 |
| DC Smart mode | `4076` | Smart → Normal → Smart | F8 DC byte, 2 / 1 / 2 |

F8 has type byte `01`, then **DC mode**, then **AC mode**. Thus both Smart
is `010202`, AC Normal is `010201`, and DC Normal is `010102`.
Command values are **0=Normal, 1=Smart**. The public upstream reference
instead describes inverted command values. An initial reference-based trial
failed protected readback, demonstrating the discrepancy; the corrected
builder passed both cycles. All observations, including that failure and
restoration, are retained privately.

Temperature telemetry BD remained **23°C** after changing the display
preference to Fahrenheit. `temperature_c` therefore stays Celsius.
AC load was 115–116 W; AC stayed on and DC stayed off throughout.

## Limits and interfaces

The fast-charge trial establishes flag acceptance and retention at full
SOC, not actual charging power. Smart-mode trials establish setting control,
not low-load shutdown timing or thresholds. Enabling Smart can later turn
an output off; the terminal and browser interfaces require confirmation.

SDK, CLI, line menu, TUI, BLE-to-MQTT bridge, authenticated HTTP gateway and
prepared HA selectors/config switches expose these original-only controls.
Use `set-temperature-unit --unit celsius|fahrenheit`,
`set-fast-charge --enabled on|off`, and
`set-ac-power-saving` / `set-dc-power-saving --enabled on|off`.
No station Wi-Fi is required. Later [Wi-Fi provisioning succeeded](c1000-original-wifi-validation.md);
direct original-model TLS/MQTT bootstrap remains unresolved.

Synthetic packet, freshness, failure and UI tests supplement these physical
checks. Raw packets, identifiers and complete configuration are only in
ignored owner-only capture directories; this document contains sanitized
observations. Firmware evidence has its own [version-specific limits](../firmware/c1000_original/1.5.9/README.md).
