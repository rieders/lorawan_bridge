"""LoRaWAN Bridge – LoRaWAN-Geräte aus TTN oder ChirpStack in Home Assistant."""
from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN, SERVICE_DOWNLINK
from .hub import LoRaWANHub

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.DEVICE_TRACKER]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

type LoRaWANConfigEntry = ConfigEntry[LoRaWANHub]

DOWNLINK_SCHEMA = vol.Schema(
    {
        vol.Required("device_id"): cv.string,
        vol.Required("f_port"): vol.All(vol.Coerce(int), vol.Range(min=1, max=223)),
        vol.Required("payload"): cv.string,
        vol.Optional("confirmed", default=False): cv.boolean,
    }
)


def _split_identifier(device: dr.DeviceEntry) -> tuple[str, str] | None:
    for domain, ident in device.identifiers:
        if domain == DOMAIN and ":" in ident:
            entry_id, key = ident.split(":", 1)
            return entry_id, key
    return None


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Domain-weiter Downlink-Service."""

    async def handle_downlink(call: ServiceCall) -> None:
        device = dr.async_get(hass).async_get(call.data["device_id"])
        ids = _split_identifier(device) if device else None
        if not ids:
            raise HomeAssistantError("Kein LoRaWAN-Bridge-Gerät ausgewählt")
        entry = hass.config_entries.async_get_entry(ids[0])
        if not entry or not hasattr(entry, "runtime_data"):
            raise HomeAssistantError("Integration nicht geladen")
        raw = call.data["payload"].replace(" ", "")
        try:
            payload = bytes.fromhex(raw)
        except ValueError as err:
            raise HomeAssistantError("Payload muss Hex sein, z. B. 01FF") from err
        try:
            await entry.runtime_data.async_send_downlink(ids[1], call.data["f_port"], payload, call.data["confirmed"])
        except ValueError as err:
            raise HomeAssistantError(str(err)) from err

    hass.services.async_register(DOMAIN, SERVICE_DOWNLINK, handle_downlink, schema=DOWNLINK_SCHEMA)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: LoRaWANConfigEntry) -> bool:
    hub = LoRaWANHub(hass, entry)
    await hub.async_load()
    entry.runtime_data = hub
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await hub.async_start()
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: LoRaWANConfigEntry) -> bool:
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok and getattr(entry, "runtime_data", None):
        await entry.runtime_data.async_stop()
    return ok


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Gespeicherte Gerätedaten beim Löschen der Integration entfernen."""
    from homeassistant.helpers.storage import Store

    from .const import STORAGE_VERSION

    await Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}").async_remove()


async def _async_reload(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: LoRaWANConfigEntry, device: dr.DeviceEntry
) -> bool:
    """Erlaubt 'Gerät löschen' in der UI."""
    ids = _split_identifier(device)
    if ids and ids[0] == entry.entry_id:
        entry.runtime_data.remove_device(ids[1])
    return True
