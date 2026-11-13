"""Turns one Google Chrome device into the fields Atlas shows."""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from ..network import ago, mac_plain


@dataclass
class Chromebook:
    device_id: str = ""
    status: str = ""
    org_unit: str = ""
    last_sync: datetime | None = None
    os_version: str = ""
    updates_until: date | None = None
    recent_users: list[str] = field(default_factory=list)
    macs: list[str] = field(default_factory=list)  # Wi-Fi first
    last_ip: str = ""
    wan_ip: str = ""
    asset_id: str = ""  # the asset ID typed into Google Admin
    boot_mode: str = ""
    ram_total_gb: float | None = None
    ram_used_pct: int | None = None
    disk_total_gb: float | None = None
    disk_used_pct: int | None = None

    @property
    def updates_state(self):
        """How the auto-update date stands: the original ATLAS called more than
        six months left compliant."""
        if not self.updates_until:
            return ""
        today = date.today()
        if self.updates_until < today:
            return "Expired"
        if self.updates_until < today + timedelta(days=183):
            return "Ending soon"
        return "Supported"

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


def _number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _used(total, free):
    """(total in GB, percent used), or (None, None) if either is unknown."""
    if not total or free is None or not 0 <= free <= total:
        return None, None
    return round(total / 1e9, 1), round(100 * (total - free) / total)


def _ram(raw):
    reports = raw.get("systemRamFreeReports") or []
    latest = reports[-1] if reports and isinstance(reports[-1], dict) else {}
    free = [_number(x) for x in latest.get("systemRamFreeInfo") or []]
    free = [x for x in free if x is not None]
    return _used(_number(raw.get("systemRamTotal")), free[-1] if free else None)


def _disk(raw):
    """The largest volume in the latest report: that is where the user's data lives."""
    reports = raw.get("diskVolumeReports") or []
    latest = reports[-1] if reports and isinstance(reports[-1], dict) else {}
    volumes = [
        (_number(v.get("storageTotal")), _number(v.get("storageFree")))
        for v in latest.get("volumeInfo") or []
        if isinstance(v, dict)
    ]
    volumes = [v for v in volumes if v[0]]
    return _used(*max(volumes)) if volumes else (None, None)


def normalize_chromebook(raw):
    macs = [mac_plain(raw.get(name)) for name in ("macAddress", "ethernetMacAddress")]
    network = (raw.get("lastKnownNetwork") or [{}])[0]
    users = [u.get("email") for u in raw.get("recentUsers") or [] if isinstance(u, dict) and u.get("email")]
    network = network if isinstance(network, dict) else {}
    ram_total, ram_used = _ram(raw)
    disk_total, disk_used = _disk(raw)
    return Chromebook(
        device_id=_text(raw.get("deviceId")),
        # ACTIVE, DISABLED, DEPROVISIONED ... read better as words.
        status=_text(raw.get("status")).replace("_", " ").capitalize(),
        org_unit=_text(raw.get("orgUnitPath")),
        last_sync=_when(raw.get("lastSync")),
        os_version=_text(raw.get("osVersion")),
        updates_until=_day(raw.get("autoUpdateThrough")),
        recent_users=users[:5],
        macs=[mac for mac in macs if mac],
        last_ip=_text(network.get("ipAddress")),
        wan_ip=_text(network.get("wanIpAddress")),
        asset_id=_text(raw.get("annotatedAssetId")),
        boot_mode=_text(raw.get("bootMode")),
        ram_total_gb=ram_total,
        ram_used_pct=ram_used,
        disk_total_gb=disk_total,
        disk_used_pct=disk_used,
    )
