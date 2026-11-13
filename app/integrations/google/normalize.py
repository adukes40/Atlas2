"""Turns one Google Chrome device into the fields Atlas shows."""

from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from ..network import ago, mac_plain


@dataclass
class Chromebook:
    status: str = ""
    org_unit: str = ""
    last_sync: datetime | None = None
    os_version: str = ""
    updates_until: date | None = None
    recent_users: list[str] = field(default_factory=list)
    macs: list[str] = field(default_factory=list)  # Wi-Fi first
    last_ip: str = ""

    @property
    def last_sync_ago(self):
        return ago(self.last_sync)

    @property
    def active(self):
        return self.status == "Active"


def _text(value):
    return str(value).strip() if value not in (None, "") else ""


def _when(value):
    try:
        when = datetime.fromisoformat(_text(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def _day(value):
    try:
        return date.fromisoformat(_text(value))
    except ValueError:
        return None


def normalize_chromebook(raw):
    macs = [mac_plain(raw.get(name)) for name in ("macAddress", "ethernetMacAddress")]
    network = (raw.get("lastKnownNetwork") or [{}])[0]
    users = [u.get("email") for u in raw.get("recentUsers") or [] if isinstance(u, dict) and u.get("email")]
    return Chromebook(
        # ACTIVE, DISABLED, DEPROVISIONED ... read better as words.
        status=_text(raw.get("status")).replace("_", " ").capitalize(),
        org_unit=_text(raw.get("orgUnitPath")),
        last_sync=_when(raw.get("lastSync")),
        os_version=_text(raw.get("osVersion")),
        updates_until=_day(raw.get("autoUpdateThrough")),
        recent_users=users[:5],
        macs=[mac for mac in macs if mac],
        last_ip=_text(network.get("ipAddress")) if isinstance(network, dict) else "",
    )
