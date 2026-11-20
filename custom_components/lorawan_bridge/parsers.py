"""Uplink-Parser für TTN v3 und ChirpStack v3/v4 (ohne HA-Abhängigkeiten)."""
from __future__ import annotations

import base64
import binascii
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class Uplink:
    """Normalisierter Uplink, unabhängig vom Netzwerkserver."""

    key: str
    dev_id: str | None = None
    dev_eui: str | None = None
    name: str | None = None
    app_id: str | None = None
    f_port: int | None = None
    f_cnt: int | None = None
    fields: dict[str, Any] = field(default_factory=dict)
    rssi: float | None = None
    snr: float | None = None
    gateway: str | None = None
    gateway_count: int = 0
    sf: str | None = None
    received_at: datetime | None = None
    payload_hex: str | None = None
    labels: dict[str, str] = field(default_factory=dict)


# --------------------------------------------------------------------------- helpers


def normalize_eui(value: Any) -> str | None:
    """DevEUI als 16-stelliges Hex (klein). ChirpStack v3 liefert teils Base64."""
    if not value or not isinstance(value, str):
        return None
    v = value.strip().replace(":", "").replace("-", "")
    if re.fullmatch(r"[0-9a-fA-F]{16}", v):
        return v.lower()
    try:
        raw = base64.b64decode(value, validate=True)
        if len(raw) == 8:
            return raw.hex()
    except (binascii.Error, ValueError):
        pass
    return v.lower()


def b64_to_hex(value: Any) -> str | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return base64.b64decode(value).hex()
    except (binascii.Error, ValueError):
        return None


def parse_time(value: Any) -> datetime | None:
    """ISO-Zeit inkl. Nanosekunden (TTN) robust parsen."""
    if not isinstance(value, str) or not value:
        return None
    s = value.strip().replace("Z", "+00:00")
    s = re.sub(r"(\.\d{6})\d+", r"\1", s)
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


_VALUE_KEYS = ("value", "wert", "val", "v", "reading", "messwert")
_UNIT_KEYS = ("unit", "einheit", "unit_of_measurement", "uom", "u")
UNIT_SUFFIX = "__unit"


def _value_unit(obj: dict) -> tuple[Any, str] | None:
    """{"value": 12.3, "unit": "m3"} -> (12.3, "m3")."""
    low = {str(k).lower(): v for k, v in obj.items()}
    val = next((low[k] for k in _VALUE_KEYS if k in low), None)
    unit = next((low[k] for k in _UNIT_KEYS if k in low), None)
    if isinstance(val, (int, float)) and not isinstance(val, bool) and isinstance(unit, str):
        return val, unit
    return None


def _clean(key: str) -> str:
    key = (key.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue")
           .replace("Ä", "Ae").replace("Ö", "Oe").replace("Ü", "Ue").replace("ß", "ss"))
    return re.sub(r"[^0-9a-zA-Z_]+", "_", key).strip("_")


def flatten(obj: Any, prefix: str = "", labels: dict[str, str] | None = None, lprefix: str = "") -> dict[str, Any]:
    """Verschachtelte Decoder-Ausgabe zu flachen Feldern: {"a":{"b":1}} -> {"a_b":1}.

    Objekte der Form {"value": x, "unit": "m3"} werden zu einem Feld; die Einheit
    landet unter "<feld>__unit" (wird nicht als eigene Entity angelegt).
    """
    out: dict[str, Any] = {}
    if isinstance(obj, dict) and prefix:
        vu = _value_unit(obj)
        if vu:
            if labels is not None:
                labels[prefix] = lprefix
            out[prefix] = vu[0]
            out[prefix + UNIT_SUFFIX] = vu[1]
            return out
    if isinstance(obj, dict):
        for k, v in obj.items():
            key = _clean(str(k))
            lab = re.sub(r"[_\s]+", " ", str(k)).strip()
            out.update(flatten(v, f"{prefix}_{key}" if prefix else key, labels,
                               f"{lprefix} {lab}" if lprefix else lab))
    elif isinstance(obj, list):
        if len(obj) <= 16 and all(not isinstance(i, (dict, list)) for i in obj):
            for i, v in enumerate(obj):
                out[f"{prefix}_{i}"] = v
                if labels is not None:
                    labels[f"{prefix}_{i}"] = f"{lprefix} {i + 1}" if len(obj) > 1 else lprefix
        else:
            for i, v in enumerate(obj[:16]):
                out.update(flatten(v, f"{prefix}_{i}", labels, f"{lprefix} {i + 1}"))
    elif obj is not None and prefix:
        out[prefix] = obj
        if labels is not None:
            labels[prefix] = lprefix
    return out


def _best_rx(rx_list: list[dict], gw_key, rssi_key="rssi", snr_keys=("snr",)):
    if not isinstance(rx_list, list) or not rx_list:
        return None, None, None, 0
    best = max(rx_list, key=lambda r: r.get(rssi_key) if isinstance(r.get(rssi_key), (int, float)) else -999)
    snr = next((best.get(k) for k in snr_keys if best.get(k) is not None), None)
    return best.get(rssi_key), snr, gw_key(best), len(rx_list)


# --------------------------------------------------------------------------- parsers


def parse_ttn(data: dict) -> Uplink | None:
    ids = data.get("end_device_ids") or {}
    up = data.get("uplink_message")
    if not isinstance(up, dict):
        return None  # join/ack/etc.
    dev_eui = normalize_eui(ids.get("dev_eui"))
    dev_id = ids.get("device_id")
    key = dev_eui or dev_id
    if not key:
        return None
    rssi, snr, gw, cnt = _best_rx(
        up.get("rx_metadata") or [],
        lambda r: (r.get("gateway_ids") or {}).get("gateway_id"),
        "rssi",
        ("snr",),
    )
    if rssi is None and up.get("rx_metadata"):
        rssi, snr, gw, cnt = _best_rx(
            up["rx_metadata"], lambda r: (r.get("gateway_ids") or {}).get("gateway_id"), "channel_rssi"
        )
    sf_raw = (((up.get("settings") or {}).get("data_rate") or {}).get("lora") or {}).get("spreading_factor")
    return Uplink(
        key=key,
        dev_id=dev_id,
        dev_eui=dev_eui,
        name=dev_id,
        app_id=(ids.get("application_ids") or {}).get("application_id"),
        f_port=up.get("f_port"),
        f_cnt=up.get("f_cnt"),
        fields=flatten(up.get("decoded_payload") or {}, labels=(labels := {})),
        labels=labels,
        rssi=rssi,
        snr=snr,
        gateway=gw,
        gateway_count=cnt,
        sf=f"SF{sf_raw}" if sf_raw else None,
        received_at=parse_time(data.get("received_at") or up.get("received_at")),
        payload_hex=b64_to_hex(up.get("frm_payload")),
    )


def parse_cs4(data: dict) -> Uplink | None:
    info = data.get("deviceInfo") or {}
    dev_eui = normalize_eui(info.get("devEui"))
    if not dev_eui:
        return None
    rssi, snr, gw, cnt = _best_rx(data.get("rxInfo") or [], lambda r: r.get("gatewayId"))
    sf_raw = (((data.get("txInfo") or {}).get("modulation") or {}).get("lora") or {}).get("spreadingFactor")
    return Uplink(
        key=dev_eui,
        dev_id=dev_eui,
        dev_eui=dev_eui,
        name=info.get("deviceName"),
        app_id=info.get("applicationId"),
        f_port=data.get("fPort"),
        f_cnt=data.get("fCnt"),
        fields=flatten(data.get("object") or {}, labels=(labels := {})),
        labels=labels,
        rssi=rssi,
        snr=snr,
        gateway=gw,
        gateway_count=cnt,
        sf=f"SF{sf_raw}" if sf_raw else None,
        received_at=parse_time(data.get("time")),
        payload_hex=b64_to_hex(data.get("data")),
    )


def parse_cs3(data: dict) -> Uplink | None:
    dev_eui = normalize_eui(data.get("devEUI"))
    if not dev_eui:
        return None
    rx = data.get("rxInfo") or []
    rssi, snr, gw, cnt = _best_rx(rx, lambda r: normalize_eui(r.get("gatewayID")), "rssi", ("loRaSNR", "snr"))
    sf_raw = (((data.get("txInfo") or {}).get("loRaModulationInfo") or {}).get("spreadingFactor"))
    return Uplink(
        key=dev_eui,
        dev_id=dev_eui,
        dev_eui=dev_eui,
        name=data.get("deviceName"),
        app_id=str(data["applicationID"]) if data.get("applicationID") is not None else None,
        f_port=data.get("fPort"),
        f_cnt=data.get("fCnt"),
        fields=flatten(data.get("object") or {}, labels=(labels := {})),
        labels=labels,
        rssi=rssi,
        snr=snr,
        gateway=gw,
        gateway_count=cnt,
        sf=f"SF{sf_raw}" if sf_raw else None,
        received_at=parse_time((rx[0] or {}).get("time") if rx else None),
        payload_hex=b64_to_hex(data.get("data")),
    )


PARSERS = {"ttn": parse_ttn, "chirpstack_v4": parse_cs4, "chirpstack_v3": parse_cs3}


# --------------------------------------------------------------------------- heuristics


def classify(value: Any) -> str | None:
    """Entity-Typ für einen Feldwert: 'binary', 'sensor' oder None (ignorieren)."""
    if isinstance(value, bool):
        return "binary"
    if isinstance(value, (int, float, str)):
        return "sensor"
    return None


_SENSOR_RULES: list[tuple[str, str, str | None]] = [
    # (Regex auf Feldnamen, device_class, Einheit)
    (r"(^|_)(dew_?point)", "temperature", "°C"),
    (r"temp", "temperature", "°C"),
    (r"(soil_?)?(moist|moisture|vwc)", "moisture", "%"),
    (r"(^|_)(hum|humidity|rh)($|_)", "humidity", "%"),
    (r"humid", "humidity", "%"),
    (r"(mv$|_mv_|millivolt)", "voltage", "mV"),
    (r"(volt|_v$|^v$|batv|bat_v|vbat|vdd)", "voltage", "V"),
    (r"(bat|battery)", "battery", "%"),
    (r"(press|baro|hpa)", "atmospheric_pressure", "hPa"),
    (r"co2", "carbon_dioxide", "ppm"),
    (r"(tvoc|voc)", "volatile_organic_compounds_parts", "ppb"),
    (r"pm_?2_?5", "pm25", "µg/m³"),
    (r"pm_?10", "pm10", "µg/m³"),
    (r"pm_?1($|_)", "pm1", "µg/m³"),
    (r"(lux|illum|light|(^|_)lx($|_)|^ill($|_))", "illuminance", "lx"),
    (r"wind_?speed", "wind_speed", "m/s"),
    (r"(rain|precip)", "precipitation", "mm"),
    (r"((^|_)(current|amp|amps)($|_)|_a$)", "current", "A"),
    (r"(energy|kwh)", "energy", "kWh"),
    (r"(power|watt)", "power", "W"),
    (r"(distance|level_mm)", "distance", "mm"),
    (r"(rssi)", "signal_strength", "dBm"),
]


_UNIT_MAP = {
    "m3": "m³", "m^3": "m³", "m³": "m³", "cbm": "m³",
    "l": "L", "liter": "L", "litre": "L",
    "c": "°C", "°c": "°C", "degc": "°C",
    "kwh": "kWh", "wh": "Wh", "w": "W", "kw": "kW", "v": "V", "mv": "mV", "a": "A",
    "%": "%", "hpa": "hPa", "pa": "Pa", "lux": "lx", "lx": "lx", "ppm": "ppm",
}
_UNIT_CLASS = {
    "m³": ("water", "total_increasing"), "L": ("water", "total_increasing"),
    "°C": ("temperature", "measurement"), "kWh": ("energy", "total_increasing"),
    "Wh": ("energy", "total_increasing"), "W": ("power", "measurement"),
    "kW": ("power", "measurement"), "V": ("voltage", "measurement"),
    "mV": ("voltage", "measurement"), "A": ("current", "measurement"),
    "hPa": ("atmospheric_pressure", "measurement"), "Pa": ("atmospheric_pressure", "measurement"),
    "lx": ("illuminance", "measurement"), "ppm": ("carbon_dioxide", "measurement"),
}


def normalize_unit(unit: Any) -> str | None:
    if not isinstance(unit, str) or not unit.strip():
        return None
    u = unit.strip()
    return _UNIT_MAP.get(u.lower(), u)


def guess_sensor_meta(name: str, value: Any, unit: Any = None) -> dict[str, Any]:
    """device_class/Einheit/state_class anhand von Feldname, Wert und ggf. Einheit raten."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return {"device_class": None, "unit": None, "state_class": None}
    n = name.lower()
    u = normalize_unit(unit)
    if u:
        dc, sc = _UNIT_CLASS.get(u, (None, "measurement"))
        if dc == "water" and re.search(r"(flow|durchfluss|rate)", n):
            dc, sc = None, "measurement"
        return {"device_class": dc, "unit": u, "state_class": sc}
    if re.search(r"(time|stamp|epoch|date|datum|zeit|counter|count$|_id$|^id$|nummer|number|serial|"
                 r"version|fw|firmware|status|code|mode|modus|typ|type)", n):
        return {"device_class": None, "unit": None, "state_class": None}
    if re.search(r"(volum|wasser|water|zaehler|meter_reading)", n):
        return {"device_class": "water", "unit": "m³", "state_class": "total_increasing"}
    for pattern, dc, unit in _SENSOR_RULES:
        if re.search(pattern, n):
            if dc == "battery" and isinstance(value, (int, float)) and 0 < value <= 5:
                dc, unit = "voltage", "V"  # "battery": 3.6 -> Volt
            if dc == "atmospheric_pressure" and value > 2000:
                unit = "Pa"
            if dc == "voltage" and value > 100 and "bat" in n:
                unit = "mV"
            state_class = "total_increasing" if dc == "energy" else "measurement"
            if dc == "precipitation" and ("rate" in n or "intens" in n):
                dc, unit = "precipitation_intensity", "mm/h"
            return {"device_class": dc, "unit": unit, "state_class": state_class}
    return {"device_class": None, "unit": None, "state_class": "measurement"}


_BINARY_RULES = [
    (r"(leak|water|flood)", "moisture"),
    (r"(door|window|contact|open)", "opening"),
    (r"(motion|pir|occup|presence)", "motion"),
    (r"(bat|battery).*low|low.*bat", "battery"),
    (r"(alarm|tamper)", "problem"),
    (r"(button|press)", None),
]


def guess_binary_class(name: str) -> str | None:
    n = name.lower()
    for pattern, dc in _BINARY_RULES:
        if re.search(pattern, n):
            return dc
    return None


def pretty_name(field_name: str, label: str | None = None) -> str:
    if label:
        return label[:1].upper() + label[1:]
    s = field_name.replace("_", " ").strip()
    return s[:1].upper() + s[1:] if s else field_name


# --------------------------------------------------------------------------- GPS

_LAT = re.compile(r"^(?P<p>.*?)(?:_)?(lat|latitude)$", re.I)
_LON_SUFFIXES = ("lon", "lng", "long", "longitude")
_ACC_SUFFIXES = ("accuracy", "acc", "hdop")
_ALT_SUFFIXES = ("alt", "altitude")


def find_gps_groups(field_names) -> list[dict[str, str | None]]:
    """Lat/Lon-Paare mit gleichem Präfix finden: gps_lat + gps_lon, latitude + longitude, …"""
    names = {n.lower(): n for n in field_names}
    groups = []
    for low, orig in names.items():
        m = _LAT.match(low)
        if not m:
            continue
        prefix = m.group("p").rstrip("_")
        def pick(suffixes):
            for s in suffixes:
                for cand in (f"{prefix}_{s}", f"{prefix}{s}") if prefix else (s,):
                    if cand in names:
                        return names[cand]
            return None
        lon = pick(_LON_SUFFIXES)
        if lon:
            groups.append({"id": prefix or "gps", "lat": orig, "lon": lon,
                           "acc": pick(_ACC_SUFFIXES), "alt": pick(_ALT_SUFFIXES)})
    return groups


def gps_fields(field_names) -> set[str]:
    out: set[str] = set()
    for g in find_gps_groups(field_names):
        out.update(v for k, v in g.items() if k in ("lat", "lon") and v)
    return out
