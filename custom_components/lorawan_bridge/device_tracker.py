"""Kartenposition für LoRaWAN-Geräte mit GPS (Lat/Lon im Decoder)."""
from __future__ import annotations

from typing import Any

from homeassistant.components.device_tracker import SourceType, TrackerEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import signal_new_field
from .entity import LoRaWANEntity
from .hub import LoRaWANHub
from .parsers import find_gps_groups


def _num(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback) -> None:
    hub: LoRaWANHub = entry.runtime_data
    created: set[tuple[str, str]] = set()

    @callback
    def check(key: str, _field: str | None = None) -> None:
        dev = hub.devices.get(key)
        if not dev:
            return
        new = []
        for group in find_gps_groups(dev["fields"]):
            if (key, group["id"]) not in created:
                created.add((key, group["id"]))
                new.append(LoRaWANTracker(hub, key, group))
        if new:
            async_add_entities(new)

    for key in list(hub.devices):
        check(key)
    entry.async_on_unload(async_dispatcher_connect(hass, signal_new_field(entry.entry_id), check))


class LoRaWANTracker(LoRaWANEntity, TrackerEntity):
    _attr_icon = "mdi:map-marker-radius"

    def __init__(self, hub: LoRaWANHub, key: str, group: dict[str, str | None]) -> None:
        super().__init__(hub, key, f"gps_{group['id']}")
        self.group = group
        self._attr_name = "Position" if group["id"] == "gps" else f"Position {group['id']}"

    @property
    def source_type(self) -> SourceType:
        return SourceType.GPS

    def _coords(self) -> tuple[float | None, float | None]:
        v = self.dev["values"]
        lat, lon = _num(v.get(self.group["lat"])), _num(v.get(self.group["lon"]))
        if lat is None or lon is None or (lat == 0 and lon == 0) or not (-90 <= lat <= 90 and -180 <= lon <= 180):
            return None, None  # kein GPS-Fix
        return lat, lon

    @property
    def latitude(self) -> float | None:
        return self._coords()[0]

    @property
    def longitude(self) -> float | None:
        return self._coords()[1]

    @property
    def location_accuracy(self) -> float:
        acc = _num(self.dev["values"].get(self.group["acc"])) if self.group["acc"] else None
        if acc is not None and self.group["acc"] and "hdop" in self.group["acc"].lower():
            acc *= 5  # grobe Umrechnung HDOP -> Meter
        return acc if acc is not None else 0

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.group["alt"]:
            return {"altitude": self.dev["values"].get(self.group["alt"])}
        return None
