# LoRaWAN Bridge für Home Assistant

Holt LoRaWAN-Geräte aus **The Things Network (TTN v3)**, **ChirpStack v4** (auch Helium-LNS auf ChirpStack-Basis) oder **ChirpStack v3** in Home Assistant – per MQTT-Push, ohne Polling.

## Funktionen
- **Geräteimport per API** (TTN: immer möglich, ChirpStack: mit API-URL + Token) – alle Geräte der Application erscheinen sofort.
- **Automatisches Anlegen** neuer Geräte und Sensoren beim ersten Uplink.
- Jedes Feld aus dem Payload-Decoder wird zu einer Entity: Zahlen/Text → `sensor`, `true/false` → `binary_sensor`. Verschachtelte Werte werden flach gemacht (`gps.lat` → `gps_lat`).
- Objekte wie `{"value": 12.3, "unit": "m3"}` werden zu einem Sensor mit Einheit (z. B. Wasserzähler in m³, nutzbar im Energie-Dashboard unter Wasser).
- Gerätetyp und Einheit werden über den Feldnamen erkannt (temperature, humidity, battery, BatV, pressure, co2, lux, pm25, …).
- Diagnose je Gerät: Zuletzt gesehen, RSSI, SNR, Gateway, Spreading Factor, Frame-Zähler (FPort/Rohdaten deaktiviert).
- Event `lorawan_bridge_uplink` mit Rohdaten und decodierten Werten für Automationen.
- Service `lorawan_bridge.send_downlink` (Gerät, FPort, Hex-Payload, confirmed).
- **Kartenpunkt** (`device_tracker`) für Geräte mit GPS: Felder wie `latitude`/`longitude`, `lat`/`lon` oder `gps_lat`/`gps_lng` werden erkannt, optional mit `hdop`/`accuracy` und `altitude`. Position 0/0 gilt als „kein Fix“.
- Werte überstehen Neustarts (werden in `.storage` gesichert).

## Installation über den Marketplace (ab HA 2026.11)
1. *Einstellungen → Marketplace → ⋮ → Benutzerdefinierte Repositories*.
2. URL dieses Repositorys eintragen, Typ **Integration**.
3. „LoRaWAN Bridge“ installieren, ggf. über *Einstellungen → System → Reparaturen* neu starten.
4. *Einstellungen → Geräte & Dienste → Integration hinzufügen → „LoRaWAN Bridge“*.

Mit HACS (ältere HA-Versionen) funktioniert es genauso über „Benutzerdefinierte Repositories“.

## Manuelle Installation
Ordner `custom_components/lorawan_bridge` nach `/config/custom_components/` kopieren und Home Assistant neu starten.

Benötigt Home Assistant ≥ 2024.12.

## TTN einrichten
1. TTN-Konsole → *Applications → deine App → API keys → Add API key*.
2. Rechte: **Read application traffic**, **Write downlink application traffic**, **View devices in application** (für den Import).
3. In HA: Cluster (z. B. `eu1.cloud.thethings.network`), Application ID, API-Key. Tenant bleibt `ttn`.

Wichtig: In TTN muss ein **Payload Formatter** (Uplink) hinterlegt sein, sonst gibt es nur Diagnose + Rohdaten. Für viele Geräte gibt es den fertig im Device Repository.

## ChirpStack einrichten
- MQTT-Broker von ChirpStack (meist Mosquitto, Port 1883 bzw. 8883 mit TLS).
- Application ID leer lassen = alle Applications.
- Codec im **Device Profile** hinterlegen, damit `object` befüllt wird.
- Import (optional): v4 braucht den REST-Proxy `chirpstack-rest-api` (z. B. `http://chirpstack:8090`) und einen API-Key aus *API Keys*; v3 nutzt die eingebaute REST-API (`http://chirpstack:8080`).
- Topic abweichend? → Optionen der Integration.

## Downlink-Beispiel
```yaml
action: lorawan_bridge.send_downlink
data:
  device_id: 1234abcd...   # Gerät im UI auswählen
  f_port: 2
  payload: "01FF"
  confirmed: false
```

## Automation auf jeden Uplink
```yaml
trigger:
  - platform: event
    event_type: lorawan_bridge_uplink
    event_data:
      device_name: garten-sensor
action:
  - action: logbook.log
    data:
      name: LoRaWAN
      message: "{{ trigger.event.data.decoded }}"
```

## Tests
`pip install pytest-homeassistant-custom-component && pytest tests -o asyncio_mode=auto`
