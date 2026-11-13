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
    wireless: bool = False
    vlan: str = ""
    policy: str = ""  # Mist: the PSK or policy name the device joined with
    # Meraki only: the switch and port it was last seen through, for the Connection card
    switch_mac: str = ""
    switch_port: str = ""
    network_id: str = ""
    client_id: str = ""

    @property
    def last_seen_ago(self):
        return ago(self.last_seen)


@dataclass
class Connection:
    """The Meraki switch port a device (or its access point) is plugged into."""

    switch: str = ""
    port: str = ""
    mode: str = ""  # Trunk or Access
    vlan: str = ""  # access VLAN, or native VLAN on a trunk
    allowed_vlans: str = ""
    voice_vlan: str = ""
    poe: bool = False
    policy: str = ""  # Meraki group policy, or its device policy (Normal, Blocked ...)


def mac_plain(mac):
    """aabbccddeeff, or "" if it is not a MAC address."""
    digits = "".join(c for c in str(mac or "").lower() if c in "0123456789abcdef")
    return digits if len(digits) == 12 else ""
