"""Shared station identity and availability."""

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import device_id, snapshot_available
from .const import DOMAIN
from .coordinator import SolixCoordinator


class SolixEntity(CoordinatorEntity[SolixCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, coordinator: SolixCoordinator, name: str, key: str) -> None:
        super().__init__(coordinator, context=name)
        self.station_name = name
        identity = device_id(coordinator.endpoint_id, name)
        snapshot = coordinator.data[name]
        self._attr_unique_id = f"{identity}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, identity)}, name=name, manufacturer="Anker",
            model=snapshot["model"], sw_version=snapshot["metrics"].get("software_version"),
        )

    @property
    def snapshot(self) -> dict:
        return (self.coordinator.data or {}).get(self.station_name, {})

    @property
    def available(self) -> bool:
        return super().available and snapshot_available(self.snapshot)

    def control_available(self, command: str) -> bool:
        return (super().available and bool(self.coordinator.client.token)
                and snapshot_available(self.snapshot, 30)
                and command in self.snapshot.get("controls", []))
