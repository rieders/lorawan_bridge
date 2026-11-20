"""Konstanten für die LoRaWAN Bridge."""
from __future__ import annotations

DOMAIN = "lorawan_bridge"
STORAGE_VERSION = 1

CONF_BACKEND = "backend"
CONF_TLS = "tls"
CONF_APP_ID = "application_id"
CONF_TENANT = "tenant"
CONF_API_KEY = "api_key"
CONF_API_URL = "api_url"
CONF_API_TOKEN = "api_token"
CONF_IMPORT = "import_devices"
CONF_TOPIC = "topic"
CONF_FIRE_EVENTS = "fire_events"

BACKEND_TTN = "ttn"
BACKEND_CS4 = "chirpstack_v4"
BACKEND_CS3 = "chirpstack_v3"

BACKEND_NAMES = {
    BACKEND_TTN: "The Things Network",
    BACKEND_CS4: "ChirpStack v4",
    BACKEND_CS3: "ChirpStack v3",
}

DEFAULT_TTN_HOST = "eu1.cloud.thethings.network"
DEFAULT_TTN_TENANT = "ttn"

EVENT_UPLINK = f"{DOMAIN}_uplink"

SERVICE_DOWNLINK = "send_downlink"


def signal_new_device(entry_id: str) -> str:
    return f"{DOMAIN}_new_device_{entry_id}"


def signal_new_field(entry_id: str) -> str:
    return f"{DOMAIN}_new_field_{entry_id}"


def signal_update(entry_id: str, key: str) -> str:
    return f"{DOMAIN}_update_{entry_id}_{key}"


def default_topic(backend: str, app_id: str | None, tenant: str | None) -> str:
    """Standard-Uplink-Topic je Backend."""
    if backend == BACKEND_TTN:
        return f"v3/{app_id}@{tenant or DEFAULT_TTN_TENANT}/devices/+/up"
    app = app_id or "+"
    if backend == BACKEND_CS4:
        return f"application/{app}/device/+/event/up"
    return f"application/{app}/device/+/rx"
