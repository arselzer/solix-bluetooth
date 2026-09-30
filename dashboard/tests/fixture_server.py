"""Synthetic fixtures only. No device or private configuration imports."""
import asyncio, os, time
from types import SimpleNamespace
import uvicorn
from solix_link.server import create_app
from solix_link.commands import NATIVE_COMMANDS, NATIVE_C1000_COMMANDS

class Demo:
    def __init__(self):
        self.devices = {name: SimpleNamespace(timezone_name="Europe/Vienna") for name in ("Office · C1000 Gen 2", "Server · C2000 Gen 2")}
        self.commands = []
        self.overrides = {name: {} for name in self.devices}
        self.connected = True
        self.available = True
        self.readonly = False
        self.last_seen = None
    async def start(self): pass
    async def stop(self): pass
    def supported_commands(self, name):
        if self.readonly: return []
        return list(NATIVE_COMMANDS) + (list(NATIVE_C1000_COMMANDS) if name.startswith("Office") else [])
    def snapshot(self, name):
        c1000 = name.startswith("Office")
        now=time.time(); phase=now / 10
        metrics = {"battery_percentage": 91 if c1000 else 88, "battery_status": "discharging" if c1000 else "idle",
                   "ac_input_power_w": 120 if c1000 else 490, "ac_output_power_w": 310 if c1000 else 490,
                   "total_input_power_w": 120 if c1000 else 490, "total_output_power_w": 310 if c1000 else 490,
                   "ac_input_connected": 1, "ac_output_enabled": 1, "dc_output_enabled": 0,
                   "ac_charging_power_limit_w": 1200 if c1000 else 1800, "max_charge_percentage": 100,
                   "min_charge_percentage": 1, "backup_reserve_percentage": 10,
                   "ac_fast_charge_enabled": 0, "temperature_c": 28,
                   "temperature_unit_fahrenheit": 0, "ac_off_grid_alert_enabled": 0,
                   "usage_mode": "time_of_use" if c1000 else "standard", "active_tariff": "peak" if c1000 else "none",
                   "tou_schedule_slot_count": 2 if c1000 else 0, "device_timeout_minutes": 0,
                   "software_version": "1.1.4.9" if c1000 else "2.1.6.4", "software_version_module": "0.3.3.0"}
        metrics.update(self.overrides[name])
        return {"name":name,"model":"c1000_gen2" if c1000 else "c2000_gen2","protocol":"native_mqtt",
                "connected":self.connected,"available":self.available,"last_seen_timestamp":self.last_seen or now - 2,
                "metrics":metrics,"power_flow":"battery" if c1000 else "grid"}
    def snapshots(self): return [self.snapshot(name) for name in self.devices]
    async def command(self,name,command,**values):
        self.commands.append({"name":name,"command":command,**values})
        key={"set-charge-power":"ac_charging_power_limit_w","set-charge-cap":"max_charge_percentage",
             "set-backup-reserve":"backup_reserve_percentage", "set-discharge-floor":"min_charge_percentage","set-temperature-unit":"temperature_unit_fahrenheit",
             "set-off-grid-alert":"ac_off_grid_alert_enabled"}.get(command)
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
