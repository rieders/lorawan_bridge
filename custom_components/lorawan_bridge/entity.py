"""Gemeinsame Basis-Entity."""
from __future__ import annotations

from typing import Any

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity

from .const import BACKEND_NAMES, DOMAIN, signal_update
from .hub import LoRaWANHub


class LoRaWANEntity(Entity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, hub: LoRaWANHub, key: str, suffix: str) -> None:
        self.hub = hub
        self.key = key
        dev = hub.devices[key]
        self._attr_unique_id = f"{hub.entry.entry_id}_{key}_{suffix}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{hub.entry.entry_id}:{key}")},
            name=dev.get("name") or key,
            manufacturer=BACKEND_NAMES.get(hub.backend, "LoRaWAN"),
            model="LoRaWAN End Device",
            serial_number=dev.get("dev_eui"),
        )

    @property
    def dev(self) -> dict[str, Any]:
        return self.hub.devices.get(self.key) or {"fields": {}, "values": {}, "state": {}}

    @property
    def available(self) -> bool:
        return self.key in self.hub.devices

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, signal_update(self.hub.entry.entry_id, self.key), self._handle_update
            )
        )

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()
