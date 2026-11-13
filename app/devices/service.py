"""Looking a device up. Views call this and render what comes back; they
never talk to an outside system themselves."""

import time
from datetime import datetime, timezone
from dataclasses import dataclass, field

from integrations.errors import IntegrationError
from integrations.google.normalize import Chromebook, normalize_chromebook
from integrations.incidentiq.client import IncidentIQError
from integrations.incidentiq.normalize import Device, normalize_asset, normalize_ticket
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
    iiq_url: str = ""  # the Incident IQ site, for links to the device and person
    tickets_message: str = ""  # why open tickets could not be listed
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

    @property
    def conflicts(self):
        """Where Incident IQ and Google disagree, as sentences for a person.
        The same checks the original ATLAS ran, plus the asset ID in Google."""
        device, book, found = self.device, self.chromebook, []
        if not device:
            return found
        if device.asset_tag and device.asset_tag == device.serial:
            found.append("The asset tag in Incident IQ is the serial number, so a real tag was never entered.")
        if not book:
            return found
        if book.asset_id and device.asset_tag and book.asset_id != device.asset_tag:
            found.append(f"Google has asset ID {book.asset_id}, but Incident IQ has asset tag {device.asset_tag}.")
        email = (device.owner.email if device.owner else "").lower()
        if email and book.recent_users and book.recent_users[0].lower() != email:
            found.append(
                f"Incident IQ assigns it to {email}, but the last sign-in Google saw was {book.recent_users[0]}."
            )
        return found


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
    result = LookupResult(query, FOUND if devices else NOT_FOUND, devices=devices, iiq_url=client.base_url)
    if result.device:
        _add_tickets(client, result.device, result)
        _add_google(result)
        _add_network(result)
    result.took_ms = int((time.monotonic() - started) * 1000)
    return result


def _add_tickets(client, device, result):
    """List the open tickets. Only asked when Incident IQ says there are some;
    a failure leaves the count showing, never sinks the lookup."""
    if not device.open_tickets or not device.asset_id:
        return
    try:
        device.tickets = [normalize_ticket(raw) for raw in client.open_tickets(device.asset_id)]
    except IncidentIQError as error:
        result.tickets_message = str(error)


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
