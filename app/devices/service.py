"""Looking a device up. Views call this and render what comes back; they
never talk to an outside system themselves."""

import time
from dataclasses import dataclass, field

from integrations.incidentiq.client import IncidentIQError
from integrations.incidentiq.normalize import Device, normalize_asset
from integrations.models import IncidentIQConfig, IncidentIQLocation

FOUND, NOT_FOUND, NOT_CONNECTED, ERROR = "found", "not_found", "not_connected", "error"


@dataclass
class LookupResult:
    query: str
    state: str
    devices: list[Device] = field(default_factory=list)
    message: str = ""
    took_ms: int = 0

    @property
    def device(self):
        """The device, when the lookup matched exactly one."""
        return self.devices[0] if len(self.devices) == 1 else None


def _school_name(client, location_id):
    """The name of the student's school. Asked once, then remembered. A failure
    here must not sink a lookup that otherwise worked."""
    if not location_id:
        return ""
    known = IncidentIQLocation.objects.filter(pk=location_id).first()
    if known:
        return known.name
    try:
        name = client.location_name(location_id)
    except IncidentIQError:
        return ""
    if name:
        IncidentIQLocation.objects.update_or_create(pk=location_id, defaults={"name": name[:200]})
    return name or ""


def lookup(query):
    config = IncidentIQConfig.load()
    if not config.configured:
        return LookupResult(query, NOT_CONNECTED)

    started = time.monotonic()
    try:
        client = config.client()
        devices = [normalize_asset(raw) for raw in client.find_assets(query)]
        for device in devices:
            if device.owner:
                device.owner.location = _school_name(client, device.owner.location_id)
    except IncidentIQError as error:
        config.record_error(str(error))
        return LookupResult(query, ERROR, message=str(error))

    config.record_ok()
    took_ms = int((time.monotonic() - started) * 1000)
    return LookupResult(query, FOUND if devices else NOT_FOUND, devices=devices, took_ms=took_ms)
