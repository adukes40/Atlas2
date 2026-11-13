from dataclasses import dataclass
from datetime import datetime

from django.utils.timesince import timesince


def ago(when):
    """ "3 hours ago", to one unit, or "" if unknown."""
    return f"{timesince(when, depth=1)} ago" if when else ""


@dataclass
class Sighting:
    """Where Mist or Meraki last saw a device on the network."""

    source: str
    place: str = ""  # Mist site or Meraki network
    access_point: str = ""
    ssid: str = ""
    ip: str = ""
    last_seen: datetime | None = None

    @property
    def last_seen_ago(self):
        return ago(self.last_seen)


def mac_plain(mac):
    """aabbccddeeff, or "" if it is not a MAC address."""
    digits = "".join(c for c in str(mac or "").lower() if c in "0123456789abcdef")
    return digits if len(digits) == 12 else ""
