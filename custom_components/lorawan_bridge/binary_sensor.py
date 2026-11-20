"""Binärsensoren für boolesche Decoder-Felder."""
from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import signal_new_field
from .entity import LoRaWANEntity
from .hub import LoRaWANHub
from .parsers import pretty_name


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback) -> None:
    hub: LoRaWANHub = entry.runtime_data

    @callback
    def add_field(key: str, field: str) -> None:
        if hub.devices[key]["fields"][field]["kind"] == "binary":
            async_add_entities([LoRaWANBinarySensor(hub, key, field)])

    for key, dev in list(hub.devices.items()):
        for field in dev["fields"]:
            add_field(key, field)

    entry.async_on_unload(async_dispatcher_connect(hass, signal_new_field(entry.entry_id), add_field))


class LoRaWANBinarySensor(LoRaWANEntity, BinarySensorEntity):
    def __init__(self, hub: LoRaWANHub, key: str, field: str) -> None:
        super().__init__(hub, key, f"b_{field}")
        self.field = field
        self._attr_name = pretty_name(field)
        dc = hub.devices[key]["fields"][field].get("device_class")
        if dc:
            try:
                self._attr_device_class = BinarySensorDeviceClass(dc)
            except ValueError:
                pass

    @property
    def is_on(self) -> bool | None:
        value = self.dev["values"].get(self.field)
        return None if value is None else bool(value)
