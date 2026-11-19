"""Turns one Incident IQ asset into the fields Atlas shows."""

import json
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

NO_ID = "00000000-0000-0000-0000-000000000000"


@dataclass
class Person:
    user_id: str = ""
    name: str = ""
    email: str = ""
    school_id: str = ""
    role: str = ""
    grade: str = ""
    homeroom: str = ""
    location_id: str = ""
    location: str = ""  # filled in by the lookup service
    username: str = ""
    created: str = ""  # when Incident IQ added them, ISO
    google_ou: str = ""
    google_last_login: str = ""  # YYYY-MM-DD, from Incident IQ's Google sync


@dataclass
class Charge:
    """One unpaid charge from the Fee Tracker."""

    label: str = ""
    amount: Decimal = Decimal("0")
    balance: Decimal = Decimal("0")
    past_due: Decimal = Decimal("0")
    date: str = ""

    @property
    def balance_display(self):
        return f"${self.balance:,.2f}"

    @property
    def past_due_display(self):
        return f"${self.past_due:,.2f}"


@dataclass
class Fees:
    balance: Decimal = Decimal("0")
    past_due: Decimal = Decimal("0")
    charges: list[Charge] = field(default_factory=list)

    @property
    def owed(self):
        return self.balance > 0

    @property
    def balance_display(self):
        return f"${self.balance:,.2f}"

    @property
    def past_due_display(self):
        return f"${self.past_due:,.2f}"


@dataclass
class Ticket:
    ticket_id: str = ""
    number: str = ""
    subject: str = ""
    status: str = ""
    opened: str = ""  # YYYY-MM-DD
    closed: bool = False


@dataclass
class Device:
    asset_id: str = ""
    asset_tag: str = ""
    serial: str = ""
    model: str = ""
    category: str = ""
    status: str = ""
    location: str = ""
    open_tickets: int = 0
    owner: Person | None = None
    fees: Fees = field(default_factory=Fees)
    tickets: list[Ticket] = field(default_factory=list)  # filled in by the lookup service

    @property
    def key(self):
        """What to look this device up by again."""
        return self.asset_tag or self.serial

    @property
    def label(self):
        return (self.owner.name if self.owner and self.owner.name else "") or self.model or self.key


def _text(value):
    return str(value).strip() if value not in (None, "") else ""


def _money(value):
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("0")


LABEL_KEYS = ("Description", "Name", "FeeName", "FeeType", "Title", "Reason")


def _fees(raw):
    """Fees, or None if there is no Fee Tracker field. They are not a normal
    Incident IQ field: the fee app stores them on the person as a custom field whose value is a JSON list of charges, each with a
    Balance. Find that field by its shape, so no district-specific ID is needed.
    The same charge can be listed twice; count it once."""
    for custom in raw.get("CustomFieldValues") or []:
        value = custom.get("Value") if isinstance(custom, dict) else None
        if not isinstance(value, str) or "Balance" not in value:
            continue
        try:
            charges = json.loads(value)
        except ValueError:
            continue
        if not isinstance(charges, list):
            continue
        seen, fees = set(), Fees()
        for charge in charges:
            if not isinstance(charge, dict) or "Balance" not in charge:
                continue
            balance = _money(charge.get("Balance"))
            if balance <= 0:
                continue
            identity = (str(charge.get("Amount")), str(charge.get("Balance")), str(charge.get("LastActivityDate")))
            if identity in seen:
                continue
            seen.add(identity)
            past_due = max(_money(charge.get("PastDueAmount")), Decimal("0"))
            fees.balance += balance
            fees.past_due += past_due
            fees.charges.append(
                Charge(
                    # shortcut: the Fee Tracker's name for a charge is not documented; first of these that is set.
                    label=next((_text(charge.get(k)) for k in LABEL_KEYS if _text(charge.get(k))), "Charge"),
                    amount=_money(charge.get("Amount")),
                    balance=balance,
                    past_due=past_due,
                    date=_text(charge.get("LastActivityDate"))[:10],
                )
            )
        return fees
    return None


def normalize_asset(raw):
    model = raw.get("Model") or {}
    owner = raw.get("Owner") or {}
    location = raw.get("Location") or {}

    person = None
    if _text(owner.get("UserId")) not in ("", NO_ID) or _text(owner.get("FullName")):
        location_id = _text(owner.get("LocationId"))
        user_id = _text(owner.get("UserId"))
        person = Person(
            user_id="" if user_id == NO_ID else user_id,
            name=_text(owner.get("FullName")),
            email=_text(owner.get("Email")),
            school_id=_text(owner.get("SchoolIdNumber")),
            role=_text(owner.get("RoleName")),
            grade=_text(owner.get("Grade")),
            homeroom=_text(owner.get("Homeroom")),
            location_id="" if location_id == NO_ID else location_id,
        )

    try:
        open_tickets = max(int(raw.get("OpenTickets") or 0), 0)
    except (TypeError, ValueError):
        open_tickets = 0

    return Device(
        asset_id=_text(raw.get("AssetId")),
        asset_tag=_text(raw.get("AssetTag")),
        serial=_text(raw.get("SerialNumber")),
        model=_text(model.get("Name")),
        category=_text((model.get("Category") or {}).get("Name")),
        status=_text((raw.get("Status") or {}).get("Name")),
        location=_text(location.get("Name")),
        open_tickets=open_tickets,
        owner=person,
    )


def normalize_ticket(raw):
    return Ticket(
        ticket_id=_text(raw.get("TicketId")),
        number=_text(raw.get("TicketNumber")),
        subject=_text(raw.get("Subject")),
        status=_text((raw.get("WorkflowStep") or {}).get("StepName")),
        opened=_text(raw.get("CreatedDate"))[:10],
        closed=raw.get("IsClosed") is True,
    )


def normalize_user_fees(raw):
    """The Fee Tracker charges on one person, or None if they have no fee record."""
    user = raw.get("Item") if isinstance(raw.get("Item"), dict) else raw
    return _fees(user)


def normalize_user(raw):
    """One person from Incident IQ's list of people, as a Person plus their
    fees (None when they have no Fee Tracker record) and whether they are active.
    The list's field names are not all confirmed, so each has a fallback."""
    user_id = _text(raw.get("UserId"))
    name = _text(raw.get("FullName")) or " ".join(
        part for part in (_text(raw.get("FirstName")), _text(raw.get("LastName"))) if part
    )
    location = raw.get("Location") if isinstance(raw.get("Location"), dict) else {}
    role = raw.get("Role") if isinstance(raw.get("Role"), dict) else {}
    person = Person(
        user_id="" if user_id == NO_ID else user_id,
        name=name,
        email=_text(raw.get("Email")),
        school_id=_text(raw.get("SchoolIdNumber")),
        role=_text(raw.get("RoleName")) or _text(role.get("Name")),
        grade=_text(raw.get("Grade")),
        homeroom=_text(raw.get("Homeroom")),
        location=_text(raw.get("LocationName")) or _text(location.get("Name")),
        username=_text(raw.get("Username")),
        created=_text(raw.get("CreatedDate")),
    )
    google = _custom_row(raw, "OrgUnitPath") or {}
    person.google_ou = _text(google.get("OrgUnitPath"))
    person.google_last_login = _text(google.get("LastLoginDate"))[:10]
    active = raw.get("IsActive", True) is not False and not raw.get("IsDeleted")
    return person, _fees(raw), active


def _custom_row(raw, marker):
    """The first row of the JSON-list custom field whose text contains `marker`,
    or None. Incident IQ's add-on apps keep their data this way; finding the
    field by its shape means no district-specific field ID is needed."""
    for custom in raw.get("CustomFieldValues") or []:
        value = custom.get("Value") if isinstance(custom, dict) else None
        if not isinstance(value, str) or marker not in value:
            continue
        try:
            rows = json.loads(value)
        except ValueError:
            continue
        row = rows[0] if isinstance(rows, list) and rows else rows
        if isinstance(row, dict):
            return row
    return None


def google_device(raw):
    """The Chromebook record Incident IQ's Google sync keeps on an asset, in the
    shape Google's own API uses, or None. Incident IQ stores it as a custom
    field holding a JSON list; it is found by its shape, so no field ID is needed.
    It has no ChromeOS version, MAC addresses or hardware details."""
    row = _custom_row(raw, "OrgUnitPath")
    if not row:
        return None
    email = _text(row.get("RecentUserEmail"))
    return {
        "serialNumber": _text(raw.get("SerialNumber")),
        "model": _text((raw.get("Model") or {}).get("Name")),
        "status": _text(row.get("ChromebookStatus")),
        "orgUnitPath": _text(row.get("OrgUnitPath")),
        "lastSync": _text(row.get("LastSyncDate")),
        "autoUpdateThrough": _text(row.get("ChromebookAutoUpdateExpiration")),
        "recentUsers": [{"email": email}] if email else [],
    }


def google_device_from_app(raw):
    """The Google Device Data app's answer, in the shape Google's own API uses,
    or None. Its CustomFields are Google's fields with the first letter capitalized."""
    fields = raw.get("CustomFields") if isinstance(raw.get("CustomFields"), dict) else None
    if not fields:
        return None
    book = {key[:1].lower() + key[1:]: value for key, value in fields.items() if value not in (None, "")}
    try:
        users = json.loads(fields.get("RecentUsers") or "[]")
    except (TypeError, ValueError):
        users = []
    book["recentUsers"] = users if isinstance(users, list) else []
    book["autoUpdateThrough"] = _text(fields.get("AutoUpdateExpiration"))[:10]
    book["lastKnownNetwork"] = [{"ipAddress": _text(fields.get("LastKnownIpAddress")),
                                 "wanIpAddress": _text(fields.get("LastKnownWanIpAddress"))}]
    return book
