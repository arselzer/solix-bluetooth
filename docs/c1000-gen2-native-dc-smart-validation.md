# C1000 Gen 2 native DC Smart validation

## Version and command

Live trial on **2026-10-02**, A1763 main **1.1.4.9**, radio **0.3.3.0**,
using the installed SOLIX Link gateway and Home Assistant **2026.7.4**.
The native `0102` request carries source A1=`22`, typed A4=`01 <0|1>`
and the normal FD timestamp. No output A2 or countdown A3 field is included.

The CLI, terminal/browser UI, HTTP API and HA expose `set-dc-power-saving`.
C2000 and Gen 2 BLE do not advertise this capability. This is a saved DC
power-saving preference; enabling it does not turn the DC output on.

## Guards and confirmation

Both directions require a fresh main-1.1.4.9 report, exact saved Smart flag,
DC output off and inactive AC/DC countdowns. Unknown fields or other firmware
versions decline before sending a setting command. The worker first requests
full A4/D9 and validates the baseline, then confirms two new reports.

Only A4 byte 13 may change; runtime display activity at byte 22 may vary within
its Boolean domain. Other A4 bytes, D9 settings, mains and output states must
match. Timeout or unexpected mutation fails the command without automatic
retry or rollback. An uncertain result requires fresh status before restoration.

## Physical trial

The actual HA `switch.turn_on` / `switch.turn_off` services performed
**OFF → ON → OFF**. The native worker confirmed each setting through fresh
telemetry. Periodic gateway snapshots during the trial showed all three AC
outputs enabled, with C1000 Gen 2 DC output off. Every protected setting on
the original C1000, Gen 2 C1000 and C2000 matched the baseline afterward.
No C2000 setting command was sent.

The baseline was 100% SOC, DC off, no active countdown and Standard mode.
This verifies storage, readback and restoration. It does not verify automatic
low-load shutoff, a new inactivity grace period, relay timing or electrical
continuity. Raw captures and authentication evidence remain private.

## Offline coverage

Twenty-six native tests cover exact framing, strict Boolean input, firmware/DC/
timer guards, two-sample confirmation, ignored ACKs, protected changes and
restoration. Seventeen HA contract cases cover capability and freshness guards.
The browser suite includes confirmed changes and refusal with DC active.
