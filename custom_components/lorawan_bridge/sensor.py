"""Sensoren: Messwerte aus dem Decoder + Funk-Diagnose."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import SIGNAL_STRENGTH_DECIBELS, SIGNAL_STRENGTH_DECIBELS_MILLIWATT, EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import signal_new_device, signal_new_field
from .entity import LoRaWANEntity
from .hub import LoRaWANHub
from .parsers import gps_fields, pretty_name


@dataclass(frozen=True, kw_only=True)
class DiagDescription(SensorEntityDescription):
    value_fn: Callable[[dict[str, Any]], Any]


def _short_hex(value: str | None, keep: int = 6) -> str | None:
    """'780c13…' -> '78 0C 13 60 27 01 … (45 Byte)' – passt in eine Zeile."""
    if not value:
        return None
    data = [value[i:i + 2].upper() for i in range(0, len(value), 2)]
    head = " ".join(data[:keep])
    return f"{head} … ({len(data)} Byte)" if len(data) > keep else head


def _ts(state: dict) -> Any:
    return dt_util.parse_datetime(state["last_seen"]) if state.get("last_seen") else None


DIAGNOSTICS: tuple[DiagDescription, ...] = (
    DiagDescription(
        key="last_seen",
        name="Zuletzt gesehen",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=_ts,
    ),
    DiagDescription(
        key="rssi",
        name="RSSI",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda s: s.get("rssi"),
    ),
    DiagDescription(
        key="snr",
        name="SNR",
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda s: s.get("snr"),
    ),
    DiagDescription(
        key="gateway",
        name="Gateway",
        icon="mdi:router-wireless",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda s: s.get("gateway"),
    ),
    DiagDescription(
        key="sf",
        name="Spreading Factor",
        icon="mdi:radio-tower",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda s: s.get("sf"),
    ),
    DiagDescription(
        key="f_cnt",
        name="Frame-Zähler",
        icon="mdi:counter",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda s: s.get("f_cnt"),
    ),
    DiagDescription(
        key="f_port",
        name="FPort",
        icon="mdi:numeric",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda s: s.get("f_port"),
    ),
    DiagDescription(
        key="payload_hex",
        name="Rohdaten",
        icon="mdi:hexadecimal",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda s: _short_hex(s.get("payload_hex")),
    ),
)


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback) -> None:
    hub: LoRaWANHub = entry.runtime_data

    @callback
    def add_device(key: str) -> None:
        async_add_entities(LoRaWANDiagSensor(hub, key, d) for d in DIAGNOSTICS)

    @callback
    def add_field(key: str, field: str) -> None:
        if hub.devices[key]["fields"][field]["kind"] == "sensor":
            async_add_entities([LoRaWANValueSensor(hub, key, field)])

    for key, dev in list(hub.devices.items()):
        add_device(key)
        for field in dev["fields"]:
            add_field(key, field)

    entry.async_on_unload(async_dispatcher_connect(hass, signal_new_device(entry.entry_id), add_device))
    entry.async_on_unload(async_dispatcher_connect(hass, signal_new_field(entry.entry_id), add_field))


class LoRaWANDiagSensor(LoRaWANEntity, SensorEntity):
    entity_description: DiagDescription

    def __init__(self, hub: LoRaWANHub, key: str, description: DiagDescription) -> None:
        super().__init__(hub, key, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.dev["state"])

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.entity_description.key == "payload_hex":
            raw = self.dev["state"].get("payload_hex")
            if not raw:
                return None
            return {
                "hex": " ".join(raw[i:i + 2].upper() for i in range(0, len(raw), 2)),
                "bytes": len(raw) // 2,
            }
        if self.entity_description.key != "last_seen":
            return None
        d, s = self.dev, self.dev["state"]
        return {
            "dev_eui": d.get("dev_eui"),
            "device_id": d.get("dev_id"),
            "application_id": d.get("app_id"),
            "gateways_received": s.get("gateway_count"),
        }


class LoRaWANValueSensor(LoRaWANEntity, SensorEntity):
    def __init__(self, hub: LoRaWANHub, key: str, field: str) -> None:
        super().__init__(hub, key, f"v_{field}")
        self.field = field
        meta = hub.devices[key]["fields"][field]
        self._attr_name = pretty_name(field, hub.devices[key]["fields"][field].get("label"))
        if field in gps_fields(hub.devices[key]["fields"]):
            self._attr_entity_registry_enabled_default = False  # steckt im Kartenpunkt
        self._numeric = meta.get("state_class") is not None
        if meta.get("device_class"):
            try:
                self._attr_device_class = SensorDeviceClass(meta["device_class"])
            except ValueError:
                pass
        if meta.get("unit"):
            self._attr_native_unit_of_measurement = meta["unit"]
        if meta.get("state_class"):
            self._attr_state_class = SensorStateClass(meta["state_class"])

    @property
    def native_value(self) -> Any:
        value = self.dev["values"].get(self.field)
        if not self._numeric:
            return str(value)[:255] if value is not None else None
        try:
            return float(value) if not isinstance(value, int) else value
        except (TypeError, ValueError):
            return None
