"""Turns one Mist client record into a Sighting."""

from datetime import datetime, timezone

from ..network import Sighting


def _text(value):
    return str(value).strip() if value not in (None, "") else ""


def normalize_client(raw, site="", access_point=""):
    try:
        last_seen = datetime.fromtimestamp(float(raw.get("timestamp")), tz=timezone.utc)
    except (TypeError, ValueError, OverflowError):
        last_seen = None
    return Sighting(
        source="Mist",
        place=site,
        access_point=access_point or _text(raw.get("last_ap")),
        ssid=_text(raw.get("last_ssid")),
        ip=_text(raw.get("last_ip")),
        last_seen=last_seen,
        wireless=True,
        vlan=_text(raw.get("last_vlan")),
        policy=_text(raw.get("last_psk_name")),
    )


# The nightly copy of access points and wireless networks.

SECURITY = {"eap": "802.1X", "psk": "Pre-shared key", "open": "Open", "wep": "WEP", "eap192": "802.1X (192-bit)"}
BANDS = {"24": "2.4", "5": "5", "6": "6"}


def _when(value):
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc) if value else None
    except (TypeError, ValueError, OverflowError):
        return None


def normalize_ap(raw, sites=None):
    """An access point from Mist's organization device stats."""
    lldp = raw.get("lldp_stat") if isinstance(raw.get("lldp_stat"), dict) else {}
    port = _text(lldp.get("port_id"))
    port = port[5:] if port.lower().startswith("port ") else port
    try:
        draw = round(float(lldp.get("power_draw")), 1) if lldp.get("power_draw") not in (None, "") else None
    except (TypeError, ValueError):
        draw = None
    clients = raw.get("num_clients")
    return {
        "mac": _text(raw.get("mac")).lower().replace(":", ""), "vendor": "Mist", "kind": "Access point",
        "name": _text(raw.get("name")) or _text(raw.get("mac")), "model": _text(raw.get("model")),
        "serial": _text(raw.get("serial")), "site": (sites or {}).get(_text(raw.get("site_id")), ""),
        "ip": _text(raw.get("ip")),
        "status": {"connected": "Online", "disconnected": "Offline"}.get(_text(raw.get("status")), _text(raw.get("status")).capitalize()),
        "firmware": _text(raw.get("version")), "clients": clients if isinstance(clients, int) else None,
        "last_seen": _when(raw.get("last_seen")), "poe_draw": draw,
        "reported_switch": _text(lldp.get("system_name")), "reported_port": port,
    }


def normalize_wlan(raw, sites=None):
    auth = raw.get("auth") if isinstance(raw.get("auth"), dict) else {}
    kind = _text(auth.get("type"))
    security = SECURITY.get(kind, kind.upper())
    if kind == "psk" and auth.get("multi_psk_only"):
        security = "Personal keys"
    site = _text(raw.get("site_id"))
    return {
        "ssid": _text(raw.get("ssid")), "enabled": bool(raw.get("enabled", True)), "security": security,
        "vlan": _text(raw.get("vlan_id")) if raw.get("vlan_enabled", True) else "",
        "bands": ", ".join(BANDS.get(str(b), str(b)) for b in raw.get("bands") or []) + (" GHz" if raw.get("bands") else ""),
        "scope": (sites or {}).get(site, "") or "Whole district",
    }


def normalize_psk(raw):
    """A personal key's owner, network and role. The key itself is never read."""
    return {
        "name": _text(raw.get("name")), "ssid": _text(raw.get("ssid")), "role": _text(raw.get("role")),
        "vlan": _text(raw.get("vlan_id")), "expires": _when(raw.get("expire_time")),
    }
