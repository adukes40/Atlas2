"""Looking a device up. Views call this and render what comes back; they
never talk to an outside system themselves."""

import time
from datetime import datetime, timezone
from dataclasses import dataclass, field

from integrations.errors import IntegrationError
from integrations.google.normalize import Chromebook, normalize_chromebook
from integrations.incidentiq.client import IncidentIQError
from integrations.incidentiq.normalize import Device, normalize_asset
from integrations.meraki import normalize as meraki_normalize
from integrations.mist import normalize as mist_normalize
from integrations.models import GoogleConfig, IncidentIQConfig, IncidentIQLocation, MerakiConfig, MistConfig
from integrations.network import Sighting

FOUND, NOT_FOUND, NOT_CONNECTED, ERROR = "found", "not_found", "not_connected", "error"


@dataclass
class Part:
    """What one outside system added to a lookup."""

    name: str = ""
    state: str = NOT_CONNECTED
    message: str = ""


@dataclass
class LookupResult:
    query: str
    state: str
    devices: list[Device] = field(default_factory=list)
    message: str = ""
    took_ms: int = 0
    google: Part = field(default_factory=lambda: Part("Google Workspace"))
    chromebook: Chromebook | None = None
    network: list[Part] = field(default_factory=list)  # Mist, then Meraki
    sightings: list[Sighting] = field(default_factory=list)  # most recent first

    @property
    def device(self):
        """The device, when the lookup matched exactly one."""
        return self.devices[0] if len(self.devices) == 1 else None

    @property
    def sighting(self):
        return self.sightings[0] if self.sightings else None

    @property
    def network_connected(self):
        return any(part.state != NOT_CONNECTED for part in self.network)


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
    result = LookupResult(query, FOUND if devices else NOT_FOUND, devices=devices)
    if result.device:
        _add_google(result)
        _add_network(result)
    result.took_ms = int((time.monotonic() - started) * 1000)
    return result


def _ask(config, part, question):
    """Run one question to an outside system. A failure is noted on the card
    and in Settings; it never sinks the rest of the lookup."""
    try:
        answer = question(config.client())
    except IntegrationError as error:
        config.record_error(str(error))
        part.state, part.message = ERROR, str(error)
        return None
    config.record_ok()
    return answer


def _add_google(result):
    config = GoogleConfig.load()
    if not config.configured or not result.device.serial:
        return
    result.google.state = NOT_FOUND
    raw = _ask(config, result.google, lambda client: client.find_chromebook(result.device.serial))
    if raw:
        result.google.state = FOUND
        result.chromebook = normalize_chromebook(raw)


def _mist(client, macs):
    for mac in macs:
        raw = client.find_client(mac)
        if raw:
            site = client.site_name(raw.get("site_id") or "")
            ap = client.access_point_name(raw.get("last_ap") or "")
            return mist_normalize.normalize_client(raw, site=site, access_point=ap)
    return None


def _meraki(client, macs):
    for mac in macs:
        raw = client.find_client(mac)
        sighting = meraki_normalize.normalize_client(raw) if raw else None
        if sighting:
            return sighting
    return None


def _add_network(result):
    """Mist and Meraki find the device by the MAC addresses Google reports."""
    macs = result.chromebook.macs if result.chromebook else []
    for model, ask in ((MistConfig, _mist), (MerakiConfig, _meraki)):
        config = model.load()
        part = Part(config.name)
        result.network.append(part)
        if not config.configured:
            continue
        part.state = NOT_FOUND
        if not macs:
            part.message = "Atlas finds devices on the network by the MAC address Google Workspace reports."
            continue
        sighting = _ask(config, part, lambda client: ask(client, macs))
        if sighting:
            part.state = FOUND
            result.sightings.append(sighting)
    result.sightings.sort(key=lambda s: s.last_seen or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
