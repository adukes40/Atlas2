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
    )
