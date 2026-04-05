# Anker App Reverse Engineering Notes

Decompiled from `com.anker.charging` (Flutter/Dart app, ARM64 native binary).

## App Structure

- **Package**: `com.anker.charging`
- **Framework**: Flutter (Dart compiled to `libapp.so`)
- **BLE Module**: `ble_zx/` (custom BLE protocol)
- **Device Models**: Each device has its own command/controller classes

## Device Files

### C1000 (A1761, part of A1753 family)
- `ble/device/A1753/a1753_device_commands.dart`
- `ble/device/A1753/a1753_device_controller.dart`
- `ble/device/A1753/a1761_anker_device.dart`

### Solarbank 3 Pro (A17C5)
- `ble/device/A17C5/a17c5_device_commands.dart`
- `ble/device/A17C5/a17c5_anker_device.dart`

### BLE Protocol Core
- `ble_zx/ble_confer/ecdh_confer.dart` — ECDH key exchange
- `ble_zx/ble_confer/secure_ecdh_confer.dart` — Secure ECDH
- `ble_zx/helper/aes_helper.dart` — AES encryption
- `ble_zx/base/assemble_command_util.dart` — Command assembly
- `ble_zx/helper/ble_write_payload_helper.dart` — Payload construction

## Discovered Command Methods

### Send Methods (commands the app sends to devices)
```
sendBleCmd                              — Main BLE command sender
sendDeviceCommand                       — Generic device command
sendDeviceCommandOfAIEmsMode            — AI energy management mode
sendDeviceCommandOfConservepercent      — Conservation percentage (min SoC?)
sendDeviceCommandOfGridRecharging       — Grid recharging control
sendDeviceCommandOfManualBackupPower    — Manual backup power mode
sendDeviceCommandOfMode                 — Mode selection
sendDeviceCommandOfPeckShaving          — Peak shaving
sendDeviceCommandOfTimeslot             — Time slot scheduling
sendDeviceCommandOfTou                  — Time-of-Use pricing
sendDeviceCommandOfTouAndMode           — TOU + mode combined
sendDeviceCommandOfUtilityRatePlan      — Utility rate plan
sendDeviceHomeLoadsData                 — Home load settings
sendDeviceMicroPower                    — Micro inverter power
sendDeviceSelfTest                      — Self test trigger
sendDeviceTacticsTime                   — Tactics/strategy timing
sendEmsStrategyPatternCommand           — EMS strategy pattern
sendHomeLoadData                        — Home load data
sendHomeLoadLoopMode                    — Home load loop mode
sendPeakAndValleyCmd                    — Peak & valley pricing
sendPeakValleyData                      — Peak valley data
sendScreenOffTimeSchedule               — Screen off schedule
sendClockFormatSchedule                 — Clock format
sendDeleteTaskSchedule                  — Delete scheduled task
sendHeatPumpTimePlanCmd                 — Heat pump time plan
sendScreensaverCycleSchedule            — Screensaver cycle
sendScreensaverSwitchSchedule           — Screensaver switch
sendDeviceInfoCmd                       — Device info request
sendReportHeartCmd                      — Heartbeat
sendAutoDisasterData                    — Auto disaster preparedness
sendCmdRequest                          — Generic command request
sendCommandWithParam                    — Command with parameters
```

### Set Methods (writable parameters)
```
# Power & Output Control
setAcOutput                    — AC output on/off
setACOutputSwitch              — AC output switch
setACOutputCountDown           — AC output countdown timer
setDCOutputSwitch              — DC output switch
setDCOutputCountDown           — DC output countdown timer
setACControlSwitch             — AC control switch
setDCRelaySwitch               — DC relay switch
setACRelaySwitch               — AC relay switch
setAcSwitch                    — AC switch
setAcCountdown                 — AC countdown
setAcCountDownTime             — AC countdown time
setCarSwitch                   — Car charger switch
setCarCountdown                — Car charger countdown
setCarCountDownTime            — Car charger countdown time
setACPortCountdownTask         — AC port countdown task
setACPortTimedTask             — AC port timed task
setDCPortCountdownTask         — DC port countdown task
setDCPortTimedTask             — DC port timed task
setPowerLimit                  — Power limit
setSiteDevicePowerLimit        — Site device power limit
setDevicePowerLimit            — Device power limit
setDeviceMicroPowerLimit       — Micro power limit
setDevicePvPowerLimit          — PV power limit
setPeakPowerLimit              — Peak power limit
setMaxOutputPowerRequest       — Max output power
setPowerOffTimer               — Power off timer
setPowerOnTimer                — Power on timer

# Battery & Charging
setBatterySoc                  — Battery SoC
setBatterySOC                  — Battery SOC
setBatteryReserve              — Battery reserve percentage
setBatteryDischarge            — Battery discharge control
setSoc                         — Set SoC
setCloseSOC                    — Close SoC threshold
setNormalStartSoc              — Normal start SoC
setQuietStartSoc               — Quiet mode start SoC
setSafetySocParams             — Safety SoC parameters
setChargeMode                  — Charge mode
setChargeModeParams            — Charge mode parameters
setChargingMode                — Charging mode
setChargingSchedule            — Charging schedule
setChargingScheduleList        — Charging schedule list
setSuperCharge                 — Super charge mode
setAcChargePower               — AC charge power
setAcChargingPower             — AC charging power
setCycleChargingPower          — Cycle charging power
setGridCharging                — Grid charging on/off
setOnlySmartCharging           — Smart charging only
setAllowHesToCharge            — Allow HES to charge

# Display & UI
setScreenBrightness            — Screen brightness
setScreenOffTime               — Screen off timeout
setScreenTimeout               — Screen timeout
setScreenTime                  — Screen time
setScreenAlwaysOn              — Screen always on
setScreenSaver                 — Screen saver
setScreensaverSwitch           — Screensaver switch
setLcdBrightness               — LCD brightness
setLcdLight                    — LCD light
setBrightness                  — Brightness
setClockDisplayTime            — Clock display time
setClockFormat                 — Clock format (12/24h)
setDeviceScreenTime            — Device screen time
setAcLightMode                 — AC light mode
setLightSwitch                 — Light switch
setAmbientLight                — Ambient light
setAmbientLightSwitch          — Ambient light switch
setAtmosphereLightsHrightness  — Atmosphere lights brightness

# Grid & Energy
setFeedGridSwitch              — Feed-in to grid switch
setFeedGridValue               — Feed-in grid value
setFeedNetworkLimitValue       — Feed network limit value
setGridConnectedInput          — Grid connected input
setGridSensitivity             — Grid sensitivity
setOffGridSwitch               — Off-grid switch
setGreenEnergyPriority         — Green energy priority
setSelfConsumption              — Self consumption mode
setEnergyMode                  — Energy mode
setEMSTactics                  — EMS tactics
setEmsModeN                    — EMS mode
setTOU                         — Time of Use
setTimeOfUse                   — Time of Use
setPeakShaving                 — Peak shaving
setPeakAndValleyPeriods        — Peak & valley periods
setPeakValley                  — Peak valley
setElectricityPrice            — Electricity price
setFixedPrice                  — Fixed price
setDynamicPrice                — Dynamic price
setHomeLoadValue               — Home load value
setHomeLoadWeek                — Home load weekly schedule
setDeviceHomeLoadData          — Device home load data
setSiteHomeLoadDevicePower     — Site home load device power
setBle17c0HomeLoads            — BLE home loads (17C0)
setA17C1PowerLimit800W         — A17C1 power limit 800W
setDeviceFeedGridSwitch        — Device feed grid switch

# Backup & Disaster
setBackPower                   — Backup power
setBackPowerMode               — Backup power mode
setBackPowerTime               — Backup power time
setBackupStrategy              — Backup strategy
setManualBackupPower           — Manual backup power
setDisasterSwitch              — Disaster preparedness switch
setManualDisasterSwitch        — Manual disaster switch
setAutoStrategy                — Auto strategy

# Schedules & Timers
setSchedule                    — Schedule
setTimedShutdownAC             — Timed shutdown AC
setTimedShutdownCancelAC       — Cancel timed shutdown AC
setTimerTurnOff                — Timer turn off
setTimerTurnOn                 — Timer turn on
setTimingCmd                   — Timing command

# Device Settings
setLanguage                    — Device language
setTemperatureUnit             — Temperature unit (C/F)
setDeviceHourFormat            — Hour format
setWorkMode                    — Work mode
setSleepMode                   — Sleep mode
setSleepTime                   — Sleep time
setAutoUpgrade                 — Auto upgrade
setResetRes                    — Factory reset
setBuzzerSwitchReq             — Buzzer switch
setMemoryPortValue             — Memory port value
setPortPriority                — Port priority
setPortRemark                  — Port remark
setDeviceStartup               — Device startup config
```

### Parse Methods (response types the device sends)
```
parseDeviceAllInfo             — Full device info
parseDeviceInfoData            — Device info data
parseBatteryStateData          — Battery state
parseBatteryTemperatureInfo    — Battery temperature
parseBatteryValueData          — Battery values
parseChargeStateInfo           — Charge state
parseChargeModeInfo            — Charge mode
parseChargeOptimizationInfo    — Charge optimization
parseACTimedTaskInfo           — AC timed task
parseCountDownInfo             — Countdown info
parseCountDownTime             — Countdown time
parseCountDownTimeAC           — AC countdown time
parseCountDownTimeDC           — DC countdown time
parseSwitchStateInfo           — Switch state
parseSwitchStateInfoAC         — AC switch state
parseSwitchStateInfoDC         — DC switch state
parseScreenBrightnessInfo      — Screen brightness
parseScreenOffTimeInfo         — Screen off time
parseScreensaverInformationInfo — Screensaver info
parseAlwaysOnDisplayInfo       — Always-on display
parseInputPowerData            — Input power
parseOutputPowerData           — Output power
parseErrorCodeData             — Error codes
parseFaultInfo                 — Fault info
parseVoltageProtectionData     — Voltage protection
parseAntiBackFlowData          — Anti-backflow data
parsePeakAndValleyRes          — Peak & valley response
parseUtilityPlanData           — Utility plan data
parseTlvData                   — TLV data parser
parseCommonData                — Common data parser
parseCommonDataWithDataType    — Data with type byte
parseCapabilitiesNegotiationResponse — Capabilities negotiation
```

## Key Solarbank 3 (A17C5) Features
Based on asset/image names and methods:
- Grid feed-in control (`setFeedGridSwitch`, `setFeedGridValue`, `setFeedNetworkLimitValue`)
- Off-grid mode (`setOffGridSwitch`, `imgA17C5SettingsOffGrid`)
- AI EMS mode (`sendDeviceCommandOfAIEmsMode`, `ai_mode_condition`)
- Home load management (`setHomeLoadValue`, `setHomeLoadWeek`)
- Peak shaving (`setPeakShaving`, `sendDeviceCommandOfPeckShaving`)
- Time-of-Use scheduling (`setTOU`, `sendDeviceCommandOfTou`)
- Power export limit (`Export Limit`, `softwarePowerExportLimitInput`)
- Self-consumption mode (`setSelfConsumption`)
- Third-party PV support (`setThreePvInstallSwitch`)
- Sleep time control (`sleep_time_ic.png`)
- Remaining power management (`remaining_add_device.png`)
- Smart mode (`icl_station_settings_mode_smart.png`)

## Key C1000 (A1753/A1761) Features
- AC/DC output toggle (`setAcSwitch`, `setDCOutputSwitch`)
- Countdown timers (`setAcCountDownTime`, `setDCPortCountdownTask`)
- Timed tasks (`setACPortTimedTask`, `setDCPortTimedTask`)
- Screen settings (`setScreenBrightness`, `setScreenTimeout`)
- Light modes (`setAcLightMode`, `setAmbientLight`)
- Boost mode (`a1753SetBoost`)
- Ultra-fast charge (`a1753UltraFastCharge`)
- Unit timeout (`a1753SetUnitTimeout`)
- Memory port (`setMemoryPortValue`)
- Port priority (`setPortPriority`)
- Car charger mode (`setCarSwitch`, `setCarCountdown`)

## Protocol Details
- BLE write via: `akiot.ble.write_characteristic`
- Payload helper: `BleWritePayloadHelper`
- Command assembly: `assemble_command_util.dart`
- TLV parsing: `parseTlvData`, `parseCommonDataWithDataType`
- Debug logging: `[decodeResponse] decryptedPayload ===`

## Analytics Tracking Events (action_* and APP_*)

These directly correspond to BLE commands/features:

### Device Control Actions
```
action_set_ac_params                    — AC parameter settings
action_set_dc_port_switch               — DC port on/off
action_set_dc_port_countdown            — DC port countdown timer
action_set_backup_mode                  — Backup power mode
action_set_backup_strategy              — Backup strategy
action_set_schedule                     — Schedule/timer
action_set_screen_off_time              — Screen off timeout
action_set_screen_saver_params          — Screen saver settings
action_set_lcd_backlight_brightness     — LCD brightness
action_set_sleep_mode                   — Sleep mode
action_set_ambient_light_switch         — Ambient light
action_set_temperature_type             — Temperature unit (C/F)
action_set_language_type                — Device language
action_set_system_params                — System parameters
action_set_tou_system_params            — TOU system parameters
action_set_electricity_price_and_unit   — Electricity price
action_set_power_limit_country_code     — Power limit by country
action_set_connection_sensitivity       — Grid connection sensitivity
action_set_bms_params                   — BMS parameters
action_set_system_self_check            — System self check
action_set_distribution_box             — Distribution box config
action_set_third_party_pv              — Third party PV config
action_set_charging_schedule            — Charging schedule
action_set_charging_protocol            — Charging protocol
action_set_cycle_charging_power         — Cycle charging power
action_set_car_charger_params           — Car charger parameters
action_set_oil_machine_params           — Oil generator params
action_set_standby_oil_machine_params   — Standby generator params
action_set_gyroscope_switch             — Gyroscope switch
action_set_screen_orientation           — Screen orientation
action_set_laboratory_function_switch   — Lab function toggle
action_set_offline_port_switch          — Offline port switch
action_set_offline_port_switch_memory   — Offline port memory
action_set_custom_branch                — Custom branch
action_set_biggest_frequency            — Biggest frequency setting
action_set_standby_rated_power          — Standby rated power
action_set_protocol_switch              — Protocol switch
action_set_tomato_time                  — Pomodoro timer
action_control_charging                 — Charging control
action_delete_task                      — Delete scheduled task
action_device_reset                     — Factory reset
action_disconnect_function              — Disconnect function
action_restart_device                   — Restart device
action_start_self_check                 — Start self check
```

### PPS (Portable Power Station) Specific
```
APP_PPS_ac_output_button_click          — AC output button
APP_PPS_ac_output_switch_click          — AC output switch
APP_PPS_dc_output_switch_click          — DC output switch
APP_PPS_cart_port_button_click          — Car port button
APP_PPS_set_ac_countdown                — AC countdown
APP_PPS_set_dc_countdown                — DC countdown
APP_PPS_super_charge_button_click       — Super charge
APP_PPS_end_super_charge                — End super charge
APP_PPS_charging_and_discharging_limit_enter — Charge/discharge limits
APP_PPS_charging_discharging_limits_set — Set charge/discharge limits
APP_PPS_output_memory_button_click      — Output memory
APP_PPS_screen_language_set             — Screen language
```

### Generator Specific
```
APP_Gen_set_ac_output_mode              — AC output mode
APP_Gen_set_dc_output_mode              — DC output mode
APP_Gen_set_ac_countdown                — AC countdown
APP_Gen_set_lpg_monitoring_mode         — LPG monitoring
APP_Gen_start-stop_switch               — Start/stop switch
APP_Gen_click_light_brightness          — Light brightness
```

### EMS/Energy Management
```
APP_ems_mode_set                        — EMS mode setting
APP_FIRST_EMS_MODE                      — First EMS mode
APP_DEVICE_EMS_BUTTON_POWER_SET_CLICK   — EMS power setting
APP_DEVICE_EMS_MODEL_SETUP              — EMS model setup
APP_DEVICE_EMS_SWITCH_MODE              — EMS switch mode
APP_DEVICE_EMS_SWITCH_MODE_CHOOSE       — EMS mode choice
APP_DEVICE_EMS_SINGLE_MODE              — Single EMS mode
APP_DEVICE_EMS_OFFLINE_LEARNING         — Offline learning
APP_DEVICE_EMS_LEARN_RESULT             — Learning result
APP_DEVICE_EMS_RAPID_BATTERY_CHARG      — Rapid battery charge
APP_DEVICE_EMS_PV_POWE                  — PV power setting
APP_GRID_CHARGING_SETTING_PARAMETER     — Grid charging params
```

### Energy Deploy/Schedule
```
APP_ENERGY_DEPLOY_CLICK                 — Energy deploy
APP_ENERGY_DEPLOY_ENTER_CLICK           — Enter energy deploy
APP_ENERGY_DEPLOY_EDIT_POWER_CLICK      — Edit deploy power
APP_ENERGY_DEPLOY_MODIFICATION_TIME_CLICK — Modify deploy time
APP_ENERGY_DEPLOY_DELETE_CLICK          — Delete deploy
APP_ENERGY_DEPLOY_TIME_CAR_CLICK        — Deploy time car
APP_ENERGY_DEPLOY_NUMBER                — Deploy number
```

### Debug Log Strings
```
"Assembled protocol command data from tlv: "     — TLV command assembly debug
"Sending command to set manual backup mode: "     — Backup mode debug
"getDeviceCommandSuccess---conserve_percent> "    — Min SoC success
"getDeviceCommandSuccess---diesel?.mode> "        — Diesel mode success
```

### Protocol Infrastructure
```
commonOpcodeMap          — Common opcode mapping
chargingOpcodeMap        — Charging-specific opcode mapping
OPCODE_KEY               — Opcode key constant
FEATURE_OPCODE_KEY       — Feature opcode key
ZXOpcodeType             — Opcode type enum
BleCommandController     — BLE command controller
BleWritePayloadHelper    — Payload construction helper
```

## BLE Command Scan Results

### Solarbank 3 E2700 Pro (A17C5) — Full Scan 0x4040-0x40FF

| Command | Response | Likely Feature |
|---------|----------|---------------|
| `0x4040` | Full telemetry (495B via c840 3-fragment) | Status request |
| `0x4041` | Full telemetry (494B via c405 3-fragment) | Status request (alt) |
| `0x4045` | ACK `00a10131` | Unknown write cmd |
| `0x4046` | ACK | Unknown write cmd |
| `0x4047` | ACK | Unknown write cmd |
| `0x4048` | ACK | Unknown write cmd |
| `0x4049` | ACK | Unknown write cmd |
| `0x4050` | `01a10131` or `00a10131` | Boolean toggle (feed-in?) |
| `0x4051` | ACK | Unknown write cmd |
| `0x4057` | `01a10131` | Boolean toggle (off-grid?) |
| `0x405c` | ACK | Unknown write cmd |
| `0x405e` | `01a10131` | Boolean toggle (self-consumption?) |
| `0x405f` | ACK | Unknown write cmd |
| `0x4061` | 12B: `00a10131a2020100a3020100` | Config query (a1,a2,a3) |
| `0x4062` | Double ACK | Unknown |
| `0x4072` | ACK | Unknown write cmd |
| `0x4073` | ACK | Unknown write cmd |
| `0x4080` | ACK | Unknown write cmd |
| `0x4081` | `01a10131` | Boolean toggle |
| `0x4082` | ACK | Unknown write cmd |
| `0x409a` | `01a10131` | Boolean toggle |
| `0x4074`-`0x40FF` | No response (except above) | Not implemented |

**Total responding commands: ~20 out of 192 scanned**

Commands returning `01a10131` (boolean true): 0x4050, 0x4057, 0x405e, 0x4081, 0x409a
These likely correspond to: setFeedGridSwitch, setOffGridSwitch, setSelfConsumption, setGreenEnergyPriority, or similar boolean settings.

### C1000 (A1761) — Partial Scan 0x4000-0x4039

| Command | Response | Identified Feature |
|---------|----------|-------------------|
| `0x4000`-`0x4022` | No response | Not implemented |
| `0x4023` | `01` (1B) | Protocol info query |
| `0x4024` | `04` (1B) | Port count query |
| `0x4025` | `04` (1B) | Port count query |
| `0x4029` | `00a10105` TLV a1=5 | Device type query |
| `0x402e` | `01` (1B) | Unknown query |
| `0x402f` | `04` (1B) | Unknown query |
| `0x4030` | 19B: firmware versions "v0.1.3.0", "v1.5.1" | Firmware version query |
| `0x4031`-`0x4039` | No response (disconnected at 0x4039) | |
| `0x4040` | Full telemetry (312B via c840 2-fragment) | Status request |
| `0x4041` | Partial (18B: a1, a2, a3) | Partial status |
| `0x4042`-`0x404c` | ACK only | Write commands (need payloads) |

### Key Insight

Both devices use `a10121` as a generic query/status payload. Write commands need
proper TLV payloads with the target param ID and value. The `01a10131` responses
on Solarbank indicate boolean features that are currently enabled — sending
with a value payload (`a10121a2020100` for OFF, `a10121a2020101` for ON) might
toggle these features.

### Next Steps
1. Try write payloads on the "boolean true" commands (0x4050, 0x4057, 0x405e)
2. Try setting output_limit via 0x4050 with value payload
3. Use Frida to intercept the app's actual command payloads
4. Test during daytime with solar production for meaningful telemetry values
