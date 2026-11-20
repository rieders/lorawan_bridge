import json
from unittest.mock import patch
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry
from custom_components.lorawan_bridge.const import DOMAIN
from .test_integration import DATA, TTN_UP

async def test_enabled_diag_names(hass):
    entry = MockConfigEntry(domain=DOMAIN, data={**DATA, "import_devices": False}, title="TTN")
    entry.add_to_hass(hass)
    with patch("custom_components.lorawan_bridge.hub.LoRaWANHub._start_mqtt"):
        await hass.config_entries.async_setup(entry.entry_id); await hass.async_block_till_done()
        entry.runtime_data._process("t", json.dumps(TTN_UP).encode()); await hass.async_block_till_done()
        reg = er.async_get(hass)
        for eid in ("sensor.garten_sensor_fport", "sensor.garten_sensor_rohdaten"):
            e = reg.async_get(eid); print(eid, e.disabled_by, e.original_name)
            reg.async_update_entity(eid, disabled_by=None)
        with patch("custom_components.lorawan_bridge.hub.LoRaWANHub._stop_mqtt"):
            await hass.config_entries.async_reload(entry.entry_id); await hass.async_block_till_done()
        for eid in ("sensor.garten_sensor_fport", "sensor.garten_sensor_rohdaten", "sensor.garten_sensor_gateway"):
            s = hass.states.get(eid); print(eid, s.name if s else None, reg.async_get(eid).original_name)


async def test_rohdaten_kurz(hass):
    entry = MockConfigEntry(domain=DOMAIN, data={**DATA, "import_devices": False}, title="TTN")
    entry.add_to_hass(hass)
    with patch("custom_components.lorawan_bridge.hub.LoRaWANHub._start_mqtt"):
        await hass.config_entries.async_setup(entry.entry_id); await hass.async_block_till_done()
        up = json.loads(json.dumps(TTN_UP))
        up["uplink_message"]["frm_payload"] = "eAwTYCcBAEJsPzxMExRRAAAMeIAHMDAEbSgBUjuCIGwRNgv9DwAAAgL9FwBQ"
        entry.runtime_data._process("t", json.dumps(up).encode()); await hass.async_block_till_done()
        reg = er.async_get(hass)
        reg.async_update_entity("sensor.garten_sensor_rohdaten", disabled_by=None)
        with patch("custom_components.lorawan_bridge.hub.LoRaWANHub._stop_mqtt"):
            await hass.config_entries.async_reload(entry.entry_id); await hass.async_block_till_done()
        s = hass.states.get("sensor.garten_sensor_rohdaten")
        print(s.state, s.attributes)
        assert s.state == "78 0C 13 60 27 01 … (45 Byte)"
        assert s.attributes["bytes"] == 45 and s.attributes["hex"].startswith("78 0C 13")
