"""Synthetic blueprint/template checks, not a Home Assistant runtime test."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import importlib.util
import math
from pathlib import Path

from jinja2.nativetypes import NativeEnvironment
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "blueprints/automation/solix_link/opportunistic_charging.yaml"
SPEC = importlib.util.spec_from_file_location("solix_blueprint_api", ROOT / "custom_components/solix_link/api.py")
api = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(api)


@dataclass(frozen=True)
class Input:
    name: str


class BlueprintLoader(yaml.SafeLoader):
    pass


BlueprintLoader.add_constructor("!input", lambda loader, node: Input(loader.construct_scalar(node)))
BLUEPRINT = yaml.load(PATH.read_text(), Loader=BlueprintLoader)


@dataclass
class State:
    state: str
    attributes: dict = field(default_factory=dict)
    last_reported: datetime | None = None
    last_changed: datetime | None = None
    last_updated: datetime | None = None


class States(dict):
    def __call__(self, entity):
        return self[entity].state if isinstance(entity, str) and entity in self else "unknown"

    def __getitem__(self, entity):
        return self.get(entity)


def timestamp(value, default=None):
    if isinstance(value, datetime):
        return value.timestamp()
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value).timestamp()
        except ValueError:
            return default
    return default


def is_number(value):
    try:
        return not isinstance(value, bool) and math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def resolve(value, inputs):
    if isinstance(value, Input):
        return inputs[value.name]
    if isinstance(value, dict):
        return {key: resolve(item, inputs) for key, item in value.items()}
    if isinstance(value, list):
        return [resolve(item, inputs) for item in value]
    return value


class Simulation:
    def __init__(self, model="c1000_gen2"):
        self.clock = datetime(2026, 10, 2, 12, tzinfo=timezone.utc)
        self.model = model
        self.inputs = {key: item["default"] for key, item in BLUEPRINT["blueprint"]["input"].items()
                       if "default" in item}
        self.inputs.update(station="test-station", armed="input_boolean.policy",
                           command_latch="input_boolean.command_latch", price_sensor="sensor.price")
        for key in ("telemetry", "mains", "ac_enabled", "fast", "battery", "power",
                    "reserve", "charge_cap", "usage_mode", "tariff"):
            self.inputs[key] = f"sensor.test_{key}"
        self.states = States()
        self.members = {self.inputs[key] for key in ("telemetry", "mains", "ac_enabled", "fast",
                       "battery", "power", "reserve", "charge_cap", "usage_mode", "tariff")}
        self.writes = []
        self.fail_command = None
        self.after_reserve = None
        for key, value in (("armed", "on"), ("command_latch", "off"), ("mains", "on"),
                           ("ac_enabled", "on"), ("fast", "off"), ("battery", "85"),
                           ("power", "300"), ("reserve", "10"), ("charge_cap", "95"),
                           ("usage_mode", "standard"), ("tariff", "none"), ("price_sensor", "-0.01")):
            self.set(key, value)
        self.set("telemetry", (self.clock-timedelta(seconds=5)).isoformat())
        for key, unit in (("power", "W"), ("reserve", "%"), ("charge_cap", "%"), ("battery", "%")):
            self.states[self.inputs[key]].attributes["unit_of_measurement"] = unit
        self.states[self.inputs["reserve"]].attributes["min"] = 10
        self.env = NativeEnvironment()
        self.env.globals.update(states=self.states, now=lambda: self.clock, as_timestamp=timestamp,
                                is_number=is_number, device_entities=lambda _: self.members,
                                device_attr=lambda _, key: self.model if key == "model" else None,
                                is_state=lambda entity, state: self.states(entity) == state,
                                has_value=lambda entity: self.states(entity) not in ("unknown", "unavailable"),
                                state_attr=lambda entity, key: self.states[entity].attributes.get(key)
                                if self.states[entity] is not None else None)

    def set(self, key, value):
        self.states[self.inputs[key]] = State(str(value), last_reported=self.clock,
            last_changed=self.clock-timedelta(seconds=3600),
            last_updated=self.clock-timedelta(days=1))

    def render(self, value, variables):
        return self.env.from_string(value).render(**variables) if isinstance(value, str) and "{{" in value else value

    def truth(self, value, variables):
        result = self.render(value, variables)
        return result is True or isinstance(result, str) and result.strip() == "True"

    def conditions(self, conditions, variables):
        return all(self.truth(item["value_template"], variables) for item in conditions)

    def run(self):
        variables = resolve(BLUEPRINT["variables"], self.inputs)
        if not self.conditions(BLUEPRINT["conditions"], variables):
            return
        self.actions(resolve(BLUEPRINT["actions"], self.inputs), variables)

    def actions(self, actions, variables):
        for item in actions:
            if "variables" in item:
                variables.update({key: self.render(value, variables) for key, value in item["variables"].items()})
            elif "condition" in item:
                if not self.truth(item["value_template"], variables):
                    return False
            elif "if" in item:
                if self.conditions(item["if"], variables) and self.actions(item["then"], variables) is False:
                    return False
            else:
                entity = item["target"]["entity_id"]
                action = item["action"]
                if action.startswith("input_boolean."):
                    self.states[entity].state = "on" if action.endswith("turn_on") else "off"
                    self.states[entity].last_changed = self.clock
                    continue
                assert action == "number.set_value"
                parameter = "reserve" if entity == self.inputs["reserve"] else "watts"
                command = "set-backup-reserve" if parameter == "reserve" else "set-charge-power"
                value = int(self.render(item["data"]["value"], variables))
                snapshot = {"model": self.model, "protocol": "native_mqtt", "connected": True,
                            "available": True, "last_seen_timestamp": api.time.time(),
                            "controls": ["set-charge-power", "set-backup-reserve"],
                            "metrics": {"ac_charging_power_limit_w": int(self.states(self.inputs["power"])),
                                        "backup_reserve_percentage": int(self.states(self.inputs["reserve"])),
                                        "max_charge_percentage": int(self.states(self.inputs["charge_cap"])),
                                        "min_charge_percentage": 1}}
                api.validate_command(snapshot, {"command": command, parameter: value})
                self.writes.append((command, value))
                if self.fail_command == command:
                    raise RuntimeError("Synthetic unconfirmed command")
                self.states[entity].state = str(value)
                self.set("telemetry", self.clock.isoformat())
                if parameter == "reserve" and self.after_reserve:
                    self.after_reserve()
        return True


@pytest.mark.parametrize("model", ["c1000_gen2", "c2000_gen2"])
def test_low_price_raises_reserve_before_requesting_power_then_no_duplicate(model):
    sim = Simulation(model)
    sim.run()
    assert sim.writes == [("set-backup-reserve", 20), ("set-charge-power", 1000)]
    assert sim.states(sim.inputs["command_latch"]) == "off"
    sim.clock += timedelta(seconds=200)
    sim.set("telemetry", sim.clock.isoformat())
    sim.run()
    assert len(sim.writes) == 2


@pytest.mark.parametrize("key,value", [("mains", "off"), ("ac_enabled", "off"), ("fast", "on"),
    ("battery", "unknown"), ("power", "unavailable"), ("reserve", "nan"), ("charge_cap", "85.5"),
    ("usage_mode", "time_of_use"), ("tariff", "peak"), ("armed", "off"), ("command_latch", "on")])
def test_unavailable_outage_fast_plan_or_disarmed_blocks_all_device_writes(key, value):
    sim = Simulation()
    sim.set(key, value)
    sim.run()
    assert not sim.writes


@pytest.mark.parametrize("age", [31, 90, -6])
def test_station_timestamp_rejects_stale_even_when_ha_state_was_just_reported(age):
    sim = Simulation()
    sim.set("telemetry", (sim.clock-timedelta(seconds=age)).isoformat())
    assert sim.states[sim.inputs["telemetry"]].last_reported == sim.clock
    sim.run()
    assert not sim.writes


def test_old_unchanged_ha_state_timestamp_does_not_reject_fresh_station_report():
    sim = Simulation()
    assert sim.states[sim.inputs["power"]].last_updated < sim.clock-timedelta(hours=12)
    sim.run()
    assert len(sim.writes) == 2


@pytest.mark.parametrize("model", ["c1000", "c300", "unknown"])
def test_original_and_unsupported_models_are_excluded(model):
    sim = Simulation(model)
    sim.run()
    assert not sim.writes


def test_mixing_entities_between_stations_is_rejected():
    sim = Simulation()
    sim.members.remove(sim.inputs["fast"])
    sim.run()
    assert not sim.writes


@pytest.mark.parametrize("mode", ["price", "export", "either"])
def test_selected_opportunity_requires_all_relevant_fresh_signals(mode):
    sim = Simulation()
    sim.inputs.update(signal_mode=mode, export_sensor="sensor.export")
    sim.set("export_sensor", "900")
    sim.states["sensor.export"].attributes["unit_of_measurement"] = "W"
    selected = "export_sensor" if mode == "export" else "price_sensor"
    sim.states[sim.inputs[selected]].last_reported -= timedelta(days=2)
    sim.run()
    assert not sim.writes


@pytest.mark.parametrize("unit", ["kW", "kWh", None])
def test_solar_requires_watts_without_unit_guessing(unit):
    sim = Simulation()
    sim.inputs.update(signal_mode="export", export_sensor="sensor.export")
    sim.set("export_sensor", "900")
    sim.states["sensor.export"].attributes["unit_of_measurement"] = unit
    sim.run()
    assert not sim.writes


def test_positive_export_triggers_and_negative_export_does_not():
    sim = Simulation()
    sim.inputs.update(signal_mode="export", export_sensor="sensor.export")
    sim.set("export_sensor", "900")
    sim.states["sensor.export"].attributes["unit_of_measurement"] = "W"
    sim.run()
    assert sim.writes[-1] == ("set-charge-power", 1000)
    other = Simulation()
    other.inputs.update(signal_mode="export", export_sensor="sensor.export")
    other.set("export_sensor", "-900")
    other.states["sensor.export"].attributes["unit_of_measurement"] = "W"
    other.run()
    assert other.writes == [("set-backup-reserve", 20)]


@pytest.mark.parametrize("model,idle,charging", [("c2000_gen2", 100, 1000), ("c1000_gen2", 300, 1300),
                                              ("c1000_gen2", 0, 1000), ("c1000_gen2", 150, 1000)])
def test_invalid_model_power_limits_do_not_write(model, idle, charging):
    sim = Simulation(model)
    sim.inputs.update(idle_watts=idle, charging_watts=charging)
    sim.run()
    assert not sim.writes


def test_high_reserve_is_never_lowered_and_emergency_soc_has_priority():
    sim = Simulation()
    sim.set("reserve", "50")
    sim.states[sim.inputs["reserve"]].attributes.update(unit_of_measurement="%", min=10)
    sim.set("battery", "40")
    sim.states[sim.inputs["battery"]].attributes["unit_of_measurement"] = "%"
    sim.set("price_sensor", "999")
    sim.run()
    assert sim.writes == [("set-charge-power", 1000)]


@pytest.mark.parametrize("command", ["set-backup-reserve", "set-charge-power"])
def test_failed_commands_latch_and_never_retry_automatically(command):
    sim = Simulation()
    sim.fail_command = command
    with pytest.raises(RuntimeError):
        sim.run()
    assert sim.states(sim.inputs["command_latch"]) == "on"
    writes = sim.writes.copy()
    sim.clock += timedelta(seconds=300)
    sim.set("telemetry", sim.clock.isoformat())
    sim.run()
    assert sim.writes == writes


def test_outage_between_reserve_and_power_stops_with_latch_left_on():
    sim = Simulation()
    sim.after_reserve = lambda: sim.set("mains", "off")
    sim.run()
    assert sim.writes == [("set-backup-reserve", 20)]
    assert sim.states(sim.inputs["command_latch"]) == "on"


@pytest.mark.parametrize("current,price,expected", [(300, 0.03, []), (1000, 0.03, []),
                                                  (1000, 0.06, [("set-charge-power", 300)])])
def test_price_hysteresis_uses_start_when_idle_and_stop_when_active(current, price, expected):
    sim = Simulation()
    sim.states[sim.inputs["power"]].state = str(current)
    sim.states[sim.inputs["reserve"]].state = "20"
    sim.set("price_sensor", price)
    sim.run()
    assert sim.writes == expected


@pytest.mark.parametrize("current,export,expected", [(300, 450, []), (1000, 450, []),
                                                    (1000, 250, [("set-charge-power", 300)])])
def test_export_hysteresis(current, export, expected):
    sim = Simulation()
    sim.inputs.update(signal_mode="export", export_sensor="sensor.export")
    sim.states[sim.inputs["power"]].state = str(current)
    sim.states[sim.inputs["reserve"]].state = "20"
    sim.set("export_sensor", export)
    sim.states["sensor.export"].attributes["unit_of_measurement"] = "W"
    sim.run()
    assert sim.writes == expected


@pytest.mark.parametrize("change", ["age", "disarm", "fast"])
def test_recheck_after_reserve_rejects_new_staleness_disarm_or_fast(change):
    sim = Simulation()

    def changed():
        if change == "age":
            sim.clock += timedelta(seconds=31)
        else:
            sim.set("armed" if change == "disarm" else "fast", "off" if change == "disarm" else "on")
    sim.after_reserve = changed
    sim.run()
    assert sim.writes == [("set-backup-reserve", 20)]
    assert sim.states(sim.inputs["command_latch"]) == "on"


def test_reserve_above_current_charge_cap_blocks_all_changes():
    sim = Simulation()
    sim.inputs["minimum_reserve"] = 100
    sim.run()
    assert not sim.writes


def test_one_helper_cannot_be_used_as_both_enable_and_latch():
    sim = Simulation()
    sim.inputs["armed"] = sim.inputs["command_latch"]
    sim.run()
    assert not sim.writes


def test_station_roles_cannot_reuse_the_same_entity():
    sim = Simulation()
    sim.inputs["mains"] = sim.inputs["ac_enabled"]
    sim.run()
    assert not sim.writes


def test_cooldown_prevents_an_immediate_following_power_change():
    sim = Simulation()
    sim.run()
    sim.clock += timedelta(seconds=10)
    sim.set("telemetry", sim.clock.isoformat())
    sim.set("price_sensor", "999")
    sim.run()
    assert sim.writes == [("set-backup-reserve", 20), ("set-charge-power", 1000)]


def test_blueprint_starts_disabled_and_contains_no_output_or_plan_actions():
    assert BLUEPRINT["initial_state"] is False and BLUEPRINT["mode"] == "single"
    actions = []

    def visit(value):
        if isinstance(value, dict):
            if "action" in value:
                actions.append(value["action"])
            assert "continue_on_error" not in value
            for item in value.values():
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)
    visit(BLUEPRINT["actions"])
    assert set(actions) == {"input_boolean.turn_on", "input_boolean.turn_off", "number.set_value"}
