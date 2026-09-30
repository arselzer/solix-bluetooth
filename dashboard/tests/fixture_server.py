"""Synthetic fixtures only. No device or private configuration imports."""
import asyncio, os, time
from types import SimpleNamespace
import uvicorn
from solix_link.server import create_app
from solix_link.commands import NATIVE_COMMANDS, NATIVE_C1000_COMMANDS

class Demo:
    def __init__(self):
        self.devices = {name: SimpleNamespace(timezone_name="Europe/Vienna") for name in ("Office · C1000 Gen 2", "Server · C2000 Gen 2", "Spare · C1000", "Updated · C1000")}
        self.commands = []
        self.overrides = {name: {} for name in self.devices}
        self.connected = True
        self.available = True
        self.readonly = False
        self.last_seen = None
        self.standard = False
        self.mains = True
    async def start(self): pass
    async def stop(self): pass
    def supported_commands(self, name):
        if self.readonly: return []
        if name.startswith("Updated"): return ["set-charge-power", "set-display-brightness", "set-device-timeout"]
        if name.startswith("Spare"): return ["set-charge-power", "set-device-timeout", "set-temperature-unit", "set-fast-charge", "set-ac-power-saving", "set-dc-power-saving"]
        return list(NATIVE_COMMANDS) + (list(NATIVE_C1000_COMMANDS) if name.startswith("Office") else [])
    def snapshot(self, name):
        c1000 = name.startswith("Office")
        updated = name.startswith("Updated")
        original = name.startswith("Spare") or updated
        now=time.time(); phase=now / 10
        metrics = {"battery_percentage": 91 if c1000 else 88, "battery_status": "discharging" if c1000 else "idle",
                   "ac_input_power_w": 120 if c1000 else 490, "ac_output_power_w": 310 if c1000 else 490,
                   "total_input_power_w": 120 if c1000 else 490, "total_output_power_w": 310 if c1000 else 490,
                   "ac_input_connected": int(self.mains), "ac_output_enabled": 1, "dc_output_enabled": 0,
                   "ac_charging_power_limit_w": 800 if original else 1200 if c1000 else 1800, "max_charge_percentage": 100,
                   "min_charge_percentage": 1, "backup_reserve_percentage": 10,
                   "ac_fast_charge_enabled": 0, "temperature_c": 28,
                   "temperature_unit_fahrenheit": 0, "ac_off_grid_alert_enabled": 0,
                   "ac_power_saving_mode_enabled": 0, "dc_power_saving_mode_enabled": 0,
                   "usage_mode": "time_of_use" if c1000 and not self.standard else "standard", "active_tariff": "peak" if c1000 and not self.standard else "none",
                   "tou_schedule_slot_count": 2 if c1000 else 0, "device_timeout_minutes": 0,
                   "software_version": "code 151" if original else "1.1.4.9" if c1000 else "2.1.6.4", "software_version_module": "0.3.3.0"}
        if c1000:
            metrics.update(display_brightness=1, display_timeout_seconds=30, port_memory_enabled=1)
        if original:
            metrics.update(display_brightness=2, device_timeout_minutes=720 if updated else 0)
            if updated: metrics.update(software_version="1.7.1", ac_charging_power_limit_w=1000)
            for key in ("max_charge_percentage", "min_charge_percentage", "backup_reserve_percentage", "usage_mode",
                        "active_tariff", "tou_schedule_slot_count", "software_version_module", "ac_input_connected",
                        "battery_status", "ac_off_grid_alert_enabled"):
                metrics.pop(key)
        metrics.update(self.overrides[name])
        return {"name":name,"model":"c1000" if original else "c1000_gen2" if c1000 else "c2000_gen2","protocol":"prime" if updated else "legacy" if original else "native_mqtt",
                "connected":self.connected,"available":self.available,"last_seen_timestamp":self.last_seen or now - 2,
                "metrics":metrics,"power_flow":"unknown" if original else "battery" if c1000 else "grid"}
    def snapshots(self): return [self.snapshot(name) for name in self.devices]
    async def command(self,name,command,**values):
        self.commands.append({"name":name,"command":command,**values})
        key={"set-charge-power":"ac_charging_power_limit_w","set-charge-cap":"max_charge_percentage",
             "set-backup-reserve":"backup_reserve_percentage", "set-discharge-floor":"min_charge_percentage","set-temperature-unit":"temperature_unit_fahrenheit",
             "set-off-grid-alert":"ac_off_grid_alert_enabled", "set-device-timeout":"device_timeout_minutes",
             "set-fast-charge":"ac_fast_charge_enabled", "set-ac-power-saving":"ac_power_saving_mode_enabled",
             "set-dc-power-saving":"dc_power_saving_mode_enabled"}.get(command)
        key = {"set-display-brightness": "display_brightness", "set-display-timeout": "display_timeout_seconds",
               "set-port-memory": "port_memory_enabled"}.get(command, key)
        if key: self.overrides[name][key]=int(next(iter(values.values())))
        return self.snapshot(name)
    def subscribe(self): return asyncio.Queue()
    def unsubscribe(self,queue): pass

service=Demo()
app=create_app(service,token="demo-token-not-secret",allow_control=True,web_ui=True)
@app.post("/fixture")
async def fixture(body:dict):
    for key,value in body.items(): setattr(service,key,value)
    return {"ok":True}
@app.get("/fixture")
async def recorded(): return {"synthetic_fixture":True,"commands":service.commands}
uvicorn.run(app,host="127.0.0.1",port=int(os.environ["SOLIX_TEST_PORT"]),log_level="warning")
