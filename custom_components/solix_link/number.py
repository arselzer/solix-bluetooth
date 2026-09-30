"""Only gateway-enabled, model-validated charging settings."""

import math

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberEntityDescription
from homeassistant.const import EntityCategory, PERCENTAGE, UnitOfPower
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError

from .api import CHARGE_CAP_MODELS, numeric, POWER_MAXIMUM, POWER_MINIMUM, reserve_supported
from .coordinator import SolixConfigEntry
from .entity import SolixEntity

PARALLEL_UPDATES = 0
SETTINGS = {
    "ac_charging_power_limit_w": ("set-charge-power", "watts"),
    "max_charge_percentage": ("set-charge-cap", "upper"),
    "backup_reserve_percentage": ("set-backup-reserve", "reserve"),
}
DESCRIPTIONS = (
    NumberEntityDescription(key="ac_charging_power_limit_w", translation_key="charging_power",
                            device_class=NumberDeviceClass.POWER, native_unit_of_measurement=UnitOfPower.WATT,
                            entity_category=EntityCategory.CONFIG, native_min_value=300, native_step=100),
    NumberEntityDescription(key="max_charge_percentage", translation_key="charge_cap",
                            native_unit_of_measurement=PERCENTAGE, entity_category=EntityCategory.CONFIG,
                            native_min_value=80, native_max_value=100, native_step=5),
    NumberEntityDescription(key="backup_reserve_percentage", translation_key="backup_reserve",
                            native_unit_of_measurement=PERCENTAGE, entity_category=EntityCategory.CONFIG,
                            native_min_value=5, native_max_value=100, native_step=5),
)


async def async_setup_entry(hass, entry: SolixConfigEntry, async_add_entities) -> None:
    coordinator = entry.runtime_data
    added = set()

    @callback
    def discover() -> None:
        entities = []
        if not coordinator.client.token:
            return
        for name, snapshot in (coordinator.data or {}).items():
            if snapshot["model"] not in POWER_MAXIMUM:
                continue
            for description in DESCRIPTIONS:
                key = description.key
                if (name, key) in added or SETTINGS[key][0] not in snapshot["controls"]:
                    continue
                if key == "max_charge_percentage" and snapshot["model"] not in CHARGE_CAP_MODELS:
                    continue
                if key == "backup_reserve_percentage" and not reserve_supported(snapshot):
                    continue
                if numeric(snapshot["metrics"].get(key)) is not None:
                    added.add((name, key))
                    entities.append(SolixNumber(coordinator, name, description))
        if entities:
            async_add_entities(entities)

    discover()
    entry.async_on_unload(coordinator.async_add_listener(discover))


class SolixNumber(SolixEntity, NumberEntity):
    def __init__(self, coordinator, name, description: NumberEntityDescription) -> None:
        super().__init__(coordinator, name, description.key)
        self.entity_description = description
        self.command, self.parameter = SETTINGS[description.key]

    @property
    def native_value(self):
        return numeric(self.snapshot.get("metrics", {}).get(self.entity_description.key))

    @property
    def native_min_value(self) -> float:
        if self.parameter == "watts":
            return POWER_MINIMUM.get(self.snapshot.get("model"), 300)
        if self.parameter == "reserve":
            lower = numeric(self.snapshot.get("metrics", {}).get("min_charge_percentage"))
            return max(5, math.ceil((lower + 5) / 5) * 5) if lower is not None else 5
        if self.parameter == "upper" and reserve_supported(self.snapshot):
            reserve = numeric(self.snapshot.get("metrics", {}).get("backup_reserve_percentage"))
            return max(80, math.ceil(reserve / 5) * 5) if reserve is not None else 80
        return self.entity_description.native_min_value

    @property
    def native_max_value(self) -> float:
        if self.parameter == "watts":
            return POWER_MAXIMUM.get(self.snapshot.get("model"), 300)
        if self.parameter == "reserve":
            upper = numeric(self.snapshot.get("metrics", {}).get("max_charge_percentage"))
            return min(100, math.floor(upper / 5) * 5) if upper is not None else 100
        return self.entity_description.native_max_value

    @property
    def available(self) -> bool:
        metrics = self.snapshot.get("metrics", {})
        if self.parameter == "reserve" and any(numeric(metrics.get(k)) is None
                                                for k in ("min_charge_percentage", "max_charge_percentage")):
            return False
        if self.parameter == "upper" and reserve_supported(self.snapshot) and numeric(metrics.get("backup_reserve_percentage")) is None:
            return False
        return (self.control_available(self.command) and self.native_value is not None
                and self.native_min_value <= self.native_max_value)

    async def async_set_native_value(self, value: float) -> None:
        if numeric(value) is None or not float(value).is_integer():
            raise HomeAssistantError("Use a whole-number setting")
        await self.coordinator.async_command(self.station_name, {"command": self.command, self.parameter: int(value)})
