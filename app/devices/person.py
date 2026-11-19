"""Person 360: one person, assembled live from Incident IQ. Each part is asked
separately; a part that fails is left out with a note, never sinks the page."""

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from django.db.models import Q

from integrations.incidentiq.client import IncidentIQError
from integrations.incidentiq.normalize import normalize_asset, normalize_ticket, normalize_user, normalize_user_fees
from integrations.models import IncidentIQConfig
from reports.models import Device as CopiedDevice, Person as CopiedPerson


def find_people(query, limit=20):
    """People in the nightly copy whose name, email, username or ID matches."""
    query = query.strip()
    if len(query) < 2:
        return []
    return list(
        CopiedPerson.objects.filter(
            Q(email__iexact=query) | Q(username__iexact=query) | Q(school_id__iexact=query) | Q(name__icontains=query)
        ).order_by("-active", "name")[:limit]
    )


def _when(value):
    text = str(value or "").strip().replace("Z", "+00:00")
    try:
        when = datetime.fromisoformat(text)
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


@dataclass
class DeviceRow:
    asset_id: str = ""
    tag: str = ""
    serial: str = ""
    model: str = ""
    assigned: datetime | None = None  # when it was assigned to this person
    current: bool = False  # assigned to them now
    last_signed_in: datetime | None = None  # by this person, last 30 days
    owner: str = ""  # who has it now, when that is someone else

    @property
    def key(self):
        return self.tag or self.serial

    @property
    def whose(self):
        if self.current:
            return "Theirs"
        if self.owner:
            return f"{self.owner}'s"
        return "Was theirs" if self.assigned else "Unassigned"

    @property
    def someone_elses(self):
        return not self.current and bool(self.owner)


@dataclass
class PersonResult:
    user_id: str
    found: bool = False
    message: str = ""
    person: object = None  # integrations.incidentiq.normalize.Person
    active: bool = True
    fees: object = None
    iiq_url: str = ""
    devices: list[DeviceRow] = field(default_factory=list)
    tickets: list = field(default_factory=list)
    open_tickets: int = 0
    total_tickets: int | None = None
    classes: list[dict] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)  # parts Incident IQ did not answer
    took_ms: int = 0

    @property
    def google_login(self):
        return _when(self.person.google_last_login) if self.person else None

    @property
    def assigned(self):
        return [d for d in self.devices if d.current]

    @property
    def signed_in(self):
        return [d for d in self.devices if d.last_signed_in]

    @property
    def not_theirs(self):
        return [d for d in self.signed_in if not d.current]

    @property
    def warnings(self):
        found = [
            f"Signed in to {d.key} on {d.last_signed_in:%b %-d}, which is assigned to {d.owner}."
            for d in self.devices if d.someone_elses and d.last_signed_in
        ]
        if self.fees and self.fees.past_due > 0:
            found.append(f"{self.fees.past_due_display} past due in the Fee Tracker.")
        if self.person and self.person.role.lower() == "student" and self.active and not self.assigned:
            found.append("An active student with no device assigned.")
        return found


def _part(result, name, ask):
    try:
        return ask()
    except IncidentIQError:
        result.missing.append(name)
        return None


def person360(user_id):
    config = IncidentIQConfig.load()
    result = PersonResult(user_id)
    if not config.configured:
        result.message = "Atlas is not connected to Incident IQ."
        return result
    started = time.monotonic()
    client = config.client()
    result.iiq_url = client.base_url
    try:
        raw = client.user(user_id)
    except IncidentIQError as error:
        result.message = str(error)
        return result
    raw = raw.get("Item") if isinstance(raw.get("Item"), dict) else raw
    result.person, _, result.active = normalize_user(raw)
    result.fees = normalize_user_fees(raw)
    result.found = bool(result.person.user_id)

    rows = {}

    def row(serial, **values):
        item = rows.setdefault(serial.upper(), DeviceRow(serial=serial))
        for name, value in values.items():
            if value and not getattr(item, name):
                setattr(item, name, value)
        return item

    for raw_asset in _part(result, "assigned devices", lambda: client.assets_for(user_id)) or []:
        asset = normalize_asset(raw_asset)
        if asset.serial:
            row(asset.serial, asset_id=asset.asset_id, tag=asset.asset_tag, model=asset.model).current = True
    for item in _part(result, "device history", lambda: client.device_history(user_id)) or []:
        if item.get("SerialNumber") and not item.get("IsAssetDeleted"):
            row(str(item["SerialNumber"]), asset_id=str(item.get("AssetId") or ""), tag=str(item.get("AssetTag") or ""),
                model=str(item.get("ModelName") or ""), assigned=_when(item.get("AssignedDateUtc")))
    email = result.person.email.lower()
    for item in _part(result, "Chromebook sign-ins", lambda: client.devices_used(user_id)) or []:
        mine = [u for u in item.get("UsageHistory") or []
                if isinstance(u, dict) and (u.get("UserId") == user_id or str(u.get("Username") or "").lower() == email)]
        stamps = [t for t in (_when(u.get("EndDate")) or _when(u.get("StartDate")) for u in mine) if t]
        if item.get("SerialNumber") and stamps:
            tag = str(item.get("AssetTag") or "").split(" | ")[0]  # some districts annotate "tag | name | school"
            row(str(item["SerialNumber"]), tag=tag, model=str(item.get("Model") or ""), last_signed_in=max(stamps))

    # Who has the devices that are not theirs, from the nightly copy.
    others = [d.serial.upper() for d in rows.values() if not d.current]
    for copied in CopiedDevice.objects.filter(serial__in=others).select_related("owner"):
        if copied.owner and copied.owner_id != user_id:
            rows[copied.serial.upper()].owner = copied.owner.name
    result.devices = sorted(
        rows.values(),
        key=lambda d: (not d.current, -(d.last_signed_in or d.assigned or datetime.min.replace(tzinfo=timezone.utc)).timestamp()),
    )

    tickets = _part(result, "tickets", lambda: client.user_tickets(user_id)) or []
    result.tickets = [normalize_ticket(t) for t in sorted(tickets, key=lambda t: str(t.get("CreatedDate") or ""), reverse=True)[:8]]
    result.open_tickets = sum(1 for t in tickets if not t.get("IsClosed", True))
    stats = _part(result, "ticket counts", lambda: client.ticket_stats(user_id)) or {}
    if isinstance(stats.get("TotalTickets"), int):
        result.total_tickets = stats["TotalTickets"]
    result.classes = [
        {"name": str(c.get("Name") or c.get("CourseName") or ""), "course": str(c.get("CourseName") or ""),
         "place": str(c.get("LocationName") or "")}
        for c in _part(result, "classes", lambda: client.classes(user_id)) or []
        if isinstance(c, dict) and c.get("IsActive", True)
    ]
    result.took_ms = int((time.monotonic() - started) * 1000)
    return result
