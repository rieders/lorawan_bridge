"""MQTT-Verbindung, Geräteverwaltung und API-Import."""
from __future__ import annotations

import base64
import json
import logging
import ssl
import threading
from typing import Any

import aiohttp
import paho.mqtt.client as mqtt

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    BACKEND_CS4,
    BACKEND_TTN,
    CONF_API_KEY,
    CONF_API_TOKEN,
    CONF_API_URL,
    CONF_APP_ID,
    CONF_BACKEND,
    CONF_FIRE_EVENTS,
    CONF_IMPORT,
    CONF_TENANT,
    CONF_TLS,
    CONF_TOPIC,
    DEFAULT_TTN_TENANT,
    DOMAIN,
    EVENT_UPLINK,
    STORAGE_VERSION,
    default_topic,
    signal_new_device,
    signal_new_field,
    signal_update,
)
from .parsers import PARSERS, Uplink, classify, guess_binary_class, guess_sensor_meta, normalize_eui

_LOGGER = logging.getLogger(__name__)


class ImportError_(Exception):
    """Fehler beim Geräteimport über die API."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


# --------------------------------------------------------------------------- MQTT helpers


def _new_client(client_id: str = "") -> mqtt.Client:
    """paho 1.x und 2.x unterstützen."""
    try:
        return mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)  # type: ignore[attr-defined]
    except AttributeError:
        return mqtt.Client(client_id=client_id)


def _rc_ok(rc: Any) -> bool:
    try:
        return int(getattr(rc, "value", rc)) == 0
    except (TypeError, ValueError):
        return str(rc).lower() == "success"


def _configure(client: mqtt.Client, cfg: dict) -> None:
    if cfg.get(CONF_USERNAME):
        client.username_pw_set(cfg[CONF_USERNAME], cfg.get(CONF_PASSWORD) or None)
    if cfg.get(CONF_TLS):
        client.tls_set_context(ssl.create_default_context())


def test_mqtt_connection(cfg: dict, timeout: float = 10.0) -> str | None:
    """Blockierender Verbindungstest. Gibt Fehlerschlüssel oder None zurück."""
    result: dict[str, Any] = {}
    done = threading.Event()

    def on_connect(client, userdata, flags, rc, *args):
        result["rc"] = rc
        done.set()

    client = _new_client()
    client.on_connect = on_connect
    try:
        _configure(client, cfg)
        client.connect(cfg[CONF_HOST], int(cfg[CONF_PORT]), keepalive=30)
    except (OSError, ssl.SSLError, ValueError) as err:
        _LOGGER.debug("MQTT-Test fehlgeschlagen: %s", err)
        return "cannot_connect"
    client.loop_start()
    try:
        if not done.wait(timeout):
            return "cannot_connect"
        rc = result.get("rc")
        if _rc_ok(rc):
            return None
        code = int(getattr(rc, "value", rc) or 0)
        return "invalid_auth" if code in (4, 5, 134, 135) else "cannot_connect"
    finally:
        client.disconnect()
        client.loop_stop()


# --------------------------------------------------------------------------- API import


async def fetch_devices(hass: HomeAssistant, cfg: dict) -> list[dict[str, Any]]:
    """Geräteliste vom Netzwerkserver holen -> [{key, dev_id, dev_eui, name}]."""
    session = async_get_clientsession(hass)
    backend = cfg[CONF_BACKEND]
    timeout = aiohttp.ClientTimeout(total=20)
    try:
        if backend == BACKEND_TTN:
            url = f"https://{cfg[CONF_HOST]}/api/v3/applications/{cfg[CONF_APP_ID]}/devices"
            headers = {"Authorization": f"Bearer {cfg[CONF_API_KEY]}"}
            params = {"field_mask": "name,description", "limit": "1000"}
            async with session.get(url, headers=headers, params=params, timeout=timeout) as resp:
                if resp.status in (401, 403):
                    raise ImportError_("invalid_auth")
                if resp.status == 404:
                    raise ImportError_("app_not_found")
                resp.raise_for_status()
                data = await resp.json()
            out = []
            for d in data.get("end_devices") or []:
                ids = d.get("ids") or {}
                eui = normalize_eui(ids.get("dev_eui"))
                out.append(
                    {
                        "key": eui or ids.get("device_id"),
                        "dev_id": ids.get("device_id"),
                        "dev_eui": eui,
                        "name": d.get("name") or ids.get("device_id"),
                        "app_id": cfg[CONF_APP_ID],
                    }
                )
            return out

        # ChirpStack v4 (REST-Proxy) bzw. v3 (eingebaute REST-API)
        base = cfg[CONF_API_URL].rstrip("/")
        app_param = "applicationId" if backend == BACKEND_CS4 else "applicationID"
        headers = {"Grpc-Metadata-Authorization": f"Bearer {cfg[CONF_API_TOKEN]}"}
        params = {"limit": "1000", app_param: str(cfg[CONF_APP_ID])}
        async with session.get(f"{base}/api/devices", headers=headers, params=params, timeout=timeout) as resp:
            if resp.status in (401, 403):
                raise ImportError_("invalid_auth")
            resp.raise_for_status()
            data = await resp.json()
        out = []
        for d in data.get("result") or []:
            eui = normalize_eui(d.get("devEui") or d.get("devEUI"))
            if eui:
                out.append(
                    {"key": eui, "dev_id": eui, "dev_eui": eui, "name": d.get("name") or eui, "app_id": str(cfg[CONF_APP_ID])}
                )
        return out
    except ImportError_:
        raise
    except (aiohttp.ClientError, TimeoutError, ValueError) as err:
        _LOGGER.debug("Geräteimport fehlgeschlagen: %s", err)
        raise ImportError_("cannot_connect") from err


# --------------------------------------------------------------------------- Hub


class LoRaWANHub:
    """Hält die MQTT-Verbindung und den Zustand aller Geräte eines Eintrags."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.cfg: dict[str, Any] = {**entry.data, **entry.options}
        self.backend: str = self.cfg[CONF_BACKEND]
        self.devices: dict[str, dict[str, Any]] = {}
        self.connected = False
        self._client: mqtt.Client | None = None
        self._store: Store = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}")
        self.topic = self.cfg.get(CONF_TOPIC) or default_topic(
            self.backend, self.cfg.get(CONF_APP_ID), self.cfg.get(CONF_TENANT)
        )

    # ------------------------------------------------------------- lifecycle

    async def async_load(self) -> None:
        stored = await self._store.async_load() or {}
        self.devices = stored.get("devices", {})
        if self.cfg.get(CONF_IMPORT):
            try:
                imported = await fetch_devices(self.hass, self.cfg)
                for d in imported:
                    if not d["key"]:
                        continue
                    dev = self.devices.setdefault(d["key"], self._empty_device(d["key"]))
                    dev.update({k: v for k, v in d.items() if v and k != "key"})
                _LOGGER.info("%s: %d Geräte importiert", self.entry.title, len(imported))
                self._schedule_save()
            except ImportError_ as err:
                _LOGGER.warning("Geräteimport fehlgeschlagen (%s) – Geräte werden beim ersten Uplink angelegt", err.reason)

    async def async_start(self) -> None:
        await self.hass.async_add_executor_job(self._start_mqtt)

    async def async_stop(self) -> None:
        if self._client:
            await self.hass.async_add_executor_job(self._stop_mqtt)
        await self._store.async_save(self._data())

    def _start_mqtt(self) -> None:
        client = _new_client(f"ha-lorawan-{self.entry.entry_id[:8]}")
        _configure(client, self.cfg)
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message
        client.reconnect_delay_set(min_delay=2, max_delay=120)
        client.connect_async(self.cfg[CONF_HOST], int(self.cfg[CONF_PORT]), keepalive=60)
        client.loop_start()
        self._client = client

    def _stop_mqtt(self) -> None:
        assert self._client
        self._client.disconnect()
        self._client.loop_stop()
        self._client = None

    # ------------------------------------------------------------- MQTT callbacks (Thread)

    def _on_connect(self, client, userdata, flags, rc, *args) -> None:
        if _rc_ok(rc):
            _LOGGER.info("%s: verbunden, abonniere %s", self.entry.title, self.topic)
            client.subscribe(self.topic, qos=0)
            self.connected = True
        else:
            _LOGGER.error("%s: MQTT-Verbindung abgelehnt (%s)", self.entry.title, rc)
            self.connected = False

    def _on_disconnect(self, client, userdata, *args) -> None:
        self.connected = False
        _LOGGER.debug("%s: MQTT getrennt", self.entry.title)

    def _on_message(self, client, userdata, msg) -> None:
        self.hass.loop.call_soon_threadsafe(self._process, msg.topic, msg.payload)

    # ------------------------------------------------------------- processing (Event-Loop)

    @staticmethod
    def _empty_device(key: str) -> dict[str, Any]:
        return {"name": key, "dev_id": None, "dev_eui": None, "app_id": None, "fields": {}, "values": {}, "state": {}}

    @callback
    def _process(self, topic: str, payload: bytes) -> None:
        try:
            data = json.loads(payload)
        except (ValueError, UnicodeDecodeError):
            _LOGGER.debug("Kein JSON auf %s", topic)
            return
        try:
            up = PARSERS[self.backend](data)
        except Exception:  # noqa: BLE001 – fremde Payloads sollen nie die Integration abschießen
            _LOGGER.exception("Uplink auf %s konnte nicht geparst werden", topic)
            return
        if up is None:
            return
        self.handle_uplink(up)

    @callback
    def handle_uplink(self, up: Uplink) -> None:
        entry_id = self.entry.entry_id
        is_new = up.key not in self.devices
        dev = self.devices.setdefault(up.key, self._empty_device(up.key))
        if is_new and up.name:
            dev["name"] = up.name
        for attr in ("dev_id", "dev_eui", "app_id"):
            if getattr(up, attr):
                dev[attr] = getattr(up, attr)

        dev["state"].update(
            {
                "rssi": up.rssi,
                "snr": up.snr,
                "f_cnt": up.f_cnt,
                "f_port": up.f_port,
                "gateway": up.gateway,
                "gateway_count": up.gateway_count,
                "sf": up.sf,
                "payload_hex": up.payload_hex,
                "last_seen": (up.received_at or dt_util.utcnow()).isoformat(),
            }
        )

        new_fields: list[str] = []
        for name, value in up.fields.items():
            kind = classify(value)
            if kind is None:
                continue
            dev["values"][name] = value
            if name not in dev["fields"]:
                meta = guess_sensor_meta(name, value) if kind == "sensor" else {"device_class": guess_binary_class(name)}
                dev["fields"][name] = {"kind": kind, **meta}
                new_fields.append(name)

        if is_new:
            _LOGGER.info("Neues LoRaWAN-Gerät: %s (%s)", dev["name"], up.key)
            async_dispatcher_send(self.hass, signal_new_device(entry_id), up.key)
        for name in new_fields:
            async_dispatcher_send(self.hass, signal_new_field(entry_id), up.key, name)
        async_dispatcher_send(self.hass, signal_update(entry_id, up.key))

        if self.cfg.get(CONF_FIRE_EVENTS, True):
            self.hass.bus.async_fire(
                EVENT_UPLINK,
                {
                    "entry_id": entry_id,
                    "device_key": up.key,
                    "device_name": dev["name"],
                    "dev_eui": up.dev_eui,
                    "f_port": up.f_port,
                    "f_cnt": up.f_cnt,
                    "payload_hex": up.payload_hex,
                    "decoded": up.fields,
                    "rssi": up.rssi,
                    "snr": up.snr,
                    "gateway": up.gateway,
                },
            )
        self._schedule_save()

    # ------------------------------------------------------------- downlink

    async def async_send_downlink(self, key: str, f_port: int, payload: bytes, confirmed: bool) -> None:
        dev = self.devices.get(key)
        if not dev or not self._client:
            raise ValueError("Gerät unbekannt oder keine MQTT-Verbindung")
        b64 = base64.b64encode(payload).decode()
        if self.backend == BACKEND_TTN:
            if not dev.get("dev_id"):
                raise ValueError("TTN device_id unbekannt (noch kein Uplink empfangen)")
            tenant = self.cfg.get(CONF_TENANT) or DEFAULT_TTN_TENANT
            topic = f"v3/{self.cfg[CONF_APP_ID]}@{tenant}/devices/{dev['dev_id']}/down/push"
            body = {"downlinks": [{"f_port": f_port, "frm_payload": b64, "priority": "NORMAL", "confirmed": confirmed}]}
        else:
            app_id = dev.get("app_id") or self.cfg.get(CONF_APP_ID)
            if not app_id:
                raise ValueError("ChirpStack applicationId unbekannt (noch kein Uplink empfangen)")
            topic = f"application/{app_id}/device/{dev['dev_eui']}/command/down"
            body = {"confirmed": confirmed, "fPort": f_port, "data": b64}
            if self.backend == BACKEND_CS4:
                body["devEui"] = dev["dev_eui"]
        info = await self.hass.async_add_executor_job(self._client.publish, topic, json.dumps(body), 1)
        _LOGGER.debug("Downlink an %s gesendet (mid=%s)", topic, getattr(info, "mid", "?"))

    # ------------------------------------------------------------- storage

    def _data(self) -> dict[str, Any]:
        return {"devices": self.devices}

    @callback
    def _schedule_save(self) -> None:
        self._store.async_delay_save(self._data, 30)

    @callback
    def remove_device(self, key: str) -> None:
        self.devices.pop(key, None)
        self._schedule_save()

    async def async_remove_store(self) -> None:
        await self._store.async_remove()

