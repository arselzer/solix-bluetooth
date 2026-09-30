"""Sensors backed by the gateway's reported values."""

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorEntityDescription, SensorStateClass
from homeassistant.const import EntityCategory, PERCENTAGE, UnitOfPower, UnitOfTemperature
from homeassistant.core import callback

from .api import numeric
from .coordinator import SolixConfigEntry
from .entity import SolixEntity

PARALLEL_UPDATES = 0
DESCRIPTIONS = (
    SensorEntityDescription(key="battery_percentage", translation_key="battery", device_class=SensorDeviceClass.BATTERY,
                            native_unit_of_measurement=PERCENTAGE, state_class=SensorStateClass.MEASUREMENT),
    SensorEntityDescription(key="temperature_c", translation_key="temperature", device_class=SensorDeviceClass.TEMPERATURE,
                            native_unit_of_measurement=UnitOfTemperature.CELSIUS, state_class=SensorStateClass.MEASUREMENT),
    *(SensorEntityDescription(key=key, translation_key=key, device_class=SensorDeviceClass.POWER,
                             native_unit_of_measurement=UnitOfPower.WATT, state_class=SensorStateClass.MEASUREMENT)
      for key in ("ac_input_power_w", "ac_output_power_w", "dc_output_power_w", "output_power_w")),
    SensorEntityDescription(key="battery_status", translation_key="battery_status", device_class=SensorDeviceClass.ENUM,
                            options=["idle", "charging", "discharging", "unknown"]),
    SensorEntityDescription(key="active_tariff", translation_key="active_tariff", device_class=SensorDeviceClass.ENUM,
                            options=["none", "peak", "mid_peak", "off_peak", "unknown"]),
    SensorEntityDescription(key="usage_mode", translation_key="usage_mode", device_class=SensorDeviceClass.ENUM,
                            options=["standard", "time_of_use", "self_consumption", "custom", "unknown"]),
    SensorEntityDescription(key="power_flow", translation_key="power_flow", device_class=SensorDeviceClass.ENUM,
                            options=["grid", "battery", "transitioning", "unknown"]),
    *(SensorEntityDescription(key=key, translation_key=key, entity_category=EntityCategory.DIAGNOSTIC,
                             entity_registry_enabled_default=False)
      for key in ("dc_input_power_raw", "controller_error_code", "battery_health_raw")),
)


async def async_setup_entry(hass, entry: SolixConfigEntry, async_add_entities) -> None:
    coordinator = entry.runtime_data
    added = set()

    @callback
    def discover() -> None:
        entities = []
        for name, snapshot in (coordinator.data or {}).items():
            for description in DESCRIPTIONS:
                key = description.key
                present = key in (snapshot if key == "power_flow" else snapshot["metrics"])
                if present and (name, key) not in added:
                    added.add((name, key))
                    entities.append(SolixSensor(coordinator, name, description))
        if entities:
            async_add_entities(entities)

    discover()
    entry.async_on_unload(coordinator.async_add_listener(discover))


class SolixSensor(SolixEntity, SensorEntity):
    def __init__(self, coordinator, name, description: SensorEntityDescription) -> None:
        super().__init__(coordinator, name, description.key)
        self.entity_description = description

    @property
    def native_value(self):
        key = self.entity_description.key
        value = self.snapshot.get("power_flow") if key == "power_flow" else self.snapshot.get("metrics", {}).get(key)
        if self.entity_description.options is not None:
            return value if value in self.entity_description.options else None
        value = numeric(value)
        if key == "battery_percentage" and value is not None and not 0 <= value <= 100:
            return None
        if key.endswith("_power_w") and value is not None and value < 0:
            return None
        return value

    @property
    def available(self) -> bool:
        return super().available and self.native_value is not None
