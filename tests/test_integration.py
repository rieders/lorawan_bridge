import json
from unittest.mock import patch

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr, entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.lorawan_bridge.const import DOMAIN, EVENT_UPLINK

TTN_UP = {
    "end_device_ids": {"device_id": "garten-sensor", "application_ids": {"application_id": "myapp"}, "dev_eui": "70B3D57ED0051234"},
    "received_at": "2026-11-20T06:00:00.123456789Z",
    "uplink_message": {"f_port": 2, "f_cnt": 42, "frm_payload": "AQI=",
        "decoded_payload": {"temperature": 4.5, "humidity": 88, "battery": 3.61, "door_open": True, "status": "ok"},
        "rx_metadata": [{"gateway_ids": {"gateway_id": "gw1"}, "rssi": -80, "snr": 9}],
        "settings": {"data_rate": {"lora": {"spreading_factor": 7}}}},
}
DATA = {"backend": "ttn", "host": "eu1.cloud.thethings.network", "port": 8883, "tls": True,
        "application_id": "myapp", "tenant": "ttn", "api_key": "NNSXS.x", "username": "myapp@ttn",
        "password": "NNSXS.x", "import_devices": True}


async def test_config_flow_ttn(hass):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.MENU
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "ttn"})
    assert result["step_id"] == "ttn"
    with patch("custom_components.lorawan_bridge.config_flow.test_mqtt_connection", return_value=None), \
         patch("custom_components.lorawan_bridge.config_flow.fetch_devices", return_value=[]), \
         patch("custom_components.lorawan_bridge.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {
            "host": "eu1.cloud.thethings.network", "application_id": "myapp", "api_key": "NNSXS.x",
            "tenant": "ttn", "import_devices": True})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["username"] == "myapp@ttn"


async def test_import_and_uplink(hass):
    entry = MockConfigEntry(domain=DOMAIN, data=DATA, title="TTN: myapp")
    entry.add_to_hass(hass)
    imported = [{"key": "a84041000181c3e1", "dev_id": "lht65", "dev_eui": "a84041000181c3e1", "name": "LHT65 Keller", "app_id": "myapp"}]
    events = []
    hass.bus.async_listen(EVENT_UPLINK, lambda e: events.append(e))
    with patch("custom_components.lorawan_bridge.hub.LoRaWANHub._start_mqtt"), \
         patch("custom_components.lorawan_bridge.hub.fetch_devices", return_value=imported):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    devreg = dr.async_get(hass)
    assert devreg.async_get_device({(DOMAIN, f"{entry.entry_id}:a84041000181c3e1")}).name == "LHT65 Keller"

    hub = entry.runtime_data
    hub._process("v3/myapp@ttn/devices/garten-sensor/up", json.dumps(TTN_UP).encode())
    await hass.async_block_till_done()

    ents = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    print(sorted(e.entity_id for e in ents))
    t = hass.states.get("sensor.garten_sensor_temperature")
    assert t.state == "4.5" and t.attributes["unit_of_measurement"] == "°C"
    assert hass.states.get("binary_sensor.garten_sensor_door_open").state == "on"
    assert hass.states.get("sensor.garten_sensor_rssi").state == "-80"
    assert hass.states.get("sensor.garten_sensor_status").state == "ok"
    assert hass.states.get("sensor.garten_sensor_zuletzt_gesehen").state.startswith("2026-11-20T06:00:00")
    assert events and events[0].data["decoded"]["temperature"] == 4.5

    # zweiter Uplink aktualisiert Werte
    up2 = json.loads(json.dumps(TTN_UP)); up2["uplink_message"]["decoded_payload"]["temperature"] = 5.0
    hub._process("t", json.dumps(up2).encode()); await hass.async_block_till_done()
    assert hass.states.get("sensor.garten_sensor_temperature").state == "5.0"

    # Reload: Entitäten kommen aus dem Speicher zurück
    with patch("custom_components.lorawan_bridge.hub.LoRaWANHub._start_mqtt"), \
         patch("custom_components.lorawan_bridge.hub.LoRaWANHub._stop_mqtt"), \
         patch("custom_components.lorawan_bridge.hub.fetch_devices", return_value=imported):
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
    assert hass.states.get("sensor.garten_sensor_temperature").state == "5.0"

    # Downlink-Service
    class FakeClient:
        published = []
        def publish(self, topic, payload, qos): self.published.append((topic, json.loads(payload)))
    entry.runtime_data._client = FakeClient()
    dev = devreg.async_get_device({(DOMAIN, f"{entry.entry_id}:70b3d57ed0051234")})
    await hass.services.async_call(DOMAIN, "send_downlink", {"device_id": dev.id, "f_port": 3, "payload": "01 FF"}, blocking=True)
    topic, body = FakeClient.published[0]
    assert topic == "v3/myapp@ttn/devices/garten-sensor/down/push"
    assert body["downlinks"][0]["frm_payload"] == "Af8="
    entry.runtime_data._client = None


async def test_gps_tracker(hass):
    entry = MockConfigEntry(domain=DOMAIN, data={**DATA, "import_devices": False}, title="TTN")
    entry.add_to_hass(hass)
    with patch("custom_components.lorawan_bridge.hub.LoRaWANHub._start_mqtt"):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    up = json.loads(json.dumps(TTN_UP))
    up["end_device_ids"]["device_id"] = "feldwetter"
    up["end_device_ids"]["dev_eui"] = "0011223344556677"
    up["uplink_message"]["decoded_payload"] = {"temperature": 3.0, "gps": {"latitude": 50.83, "longitude": 10.95, "hdop": 1.2, "altitude": 290}}
    entry.runtime_data._process("t", json.dumps(up).encode()); await hass.async_block_till_done()
    st = hass.states.get("device_tracker.feldwetter_position")
    assert st and st.attributes["latitude"] == 50.83 and st.attributes["longitude"] == 10.95
    assert st.attributes["gps_accuracy"] == 6.0 and st.attributes["altitude"] == 290
    assert er.async_get(hass).async_get("sensor.feldwetter_gps_latitude").disabled_by is not None
    # kein Fix -> unbekannt
    up["uplink_message"]["decoded_payload"]["gps"] = {"latitude": 0, "longitude": 0}
    entry.runtime_data._process("t", json.dumps(up).encode()); await hass.async_block_till_done()
    assert hass.states.get("device_tracker.feldwetter_position").state == "unknown"


async def test_wasseruhr(hass):
    entry = MockConfigEntry(domain=DOMAIN, data={**DATA, "application_id": "wasseruhrgarten", "import_devices": False}, title="TTN")
    entry.add_to_hass(hass)
    with patch("custom_components.lorawan_bridge.hub.LoRaWANHub._start_mqtt"):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    up = {
        "end_device_ids": {"device_id": "eui-f80df10000017f64", "dev_eui": "F80DF10000017F64",
                           "application_ids": {"application_id": "wasseruhrgarten"}},
        "uplink_message": {"f_port": 1, "f_cnt": 4, "frm_payload": "AQ==", "decoded_payload": {
            "Datum und Zeit": "20.11.26 14:40", "Datum und Zeit valide": True,
            "Fabrikationsnummer": 30300780, "Fehlermeldungen": ["Keine Zugriffsrechte vorhanden"],
            "Installationsdatum": "17.06.24", "Sommerzeit": False, "Version": 2000000,
            "Volumen": {"value": 123.456, "unit": "m3"},
            "Volumen zum Stichtag": {"Wert": 120.0, "Einheit": "m³"},
            "Volumen zur vollen Stunde - 1": {"value": 123.4, "unit": "m³"},
        }},
    }
    entry.runtime_data._process("t", json.dumps(up).encode()); await hass.async_block_till_done()
    names = {s.entity_id: (s.name, s.state, s.attributes.get("unit_of_measurement"), s.attributes.get("device_class"), s.attributes.get("state_class"))
             for s in hass.states.async_all() if "eui_f80df" in s.entity_id}
    for k, v in sorted(names.items()):
        print(k, v)
    vol = hass.states.get("sensor.eui_f80df10000017f64_volumen")
    assert vol.state == "123.456" and vol.attributes["unit_of_measurement"] == "m³"
    assert vol.attributes["device_class"] == "water" and vol.attributes["state_class"] == "total_increasing"
    fab = hass.states.get("sensor.eui_f80df10000017f64_fabrikationsnummer")
    assert fab.state == "30300780" and "state_class" not in fab.attributes
    assert hass.states.get("binary_sensor.eui_f80df10000017f64_datum_und_zeit_valide").state == "on"
    assert hass.states.get("sensor.eui_f80df10000017f64_fehlermeldungen").state == "Keine Zugriffsrechte vorhanden"
    assert not any("unit" in e for e in names)
