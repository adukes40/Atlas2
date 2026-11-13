"""Turns Meraki's answer for one MAC into a Sighting."""

from datetime import datetime, timezone

from ..network import Sighting


def _text(value):
    return str(value).strip() if value not in (None, "") else ""


def _when(value):
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    try:
        when = datetime.fromisoformat(_text(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def normalize_client(raw):
    """The most recent record across networks, or None if there is none."""
    records = [r for r in raw.get("records") or [] if isinstance(r, dict)]
    stamped = [(_when(r.get("lastSeen")), r) for r in records]
    stamped = [(seen, r) for seen, r in stamped if seen]
    if not stamped:
        return None
    last_seen, record = max(stamped, key=lambda pair: pair[0])
    return Sighting(
        source="Meraki",
        place=_text((record.get("network") or {}).get("name")),
        access_point=_text(record.get("recentDeviceName")) or _text(record.get("recentDeviceSerial")),
        ssid=_text(record.get("ssid")),
        ip=_text(record.get("ip")),
        last_seen=last_seen,
    )
