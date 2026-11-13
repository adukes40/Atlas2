"""Turns one Incident IQ asset into the fields Atlas shows."""

import json
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

NO_ID = "00000000-0000-0000-0000-000000000000"


@dataclass
class Person:
    name: str = ""
    email: str = ""
    school_id: str = ""
    role: str = ""
    grade: str = ""
    homeroom: str = ""
    location_id: str = ""
    location: str = ""  # filled in by the lookup service


@dataclass
class Fees:
    balance: Decimal = Decimal("0")
    past_due: Decimal = Decimal("0")

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


def _fees(raw):
    """Fees are not a normal Incident IQ field. The fee app stores them on the
    asset as a custom field whose value is a JSON list of charges, each with a
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
            fees.balance += balance
            fees.past_due += max(_money(charge.get("PastDueAmount")), Decimal("0"))
        return fees
    return Fees()


def normalize_asset(raw):
    model = raw.get("Model") or {}
    owner = raw.get("Owner") or {}
    location = raw.get("Location") or {}

    person = None
    if _text(owner.get("UserId")) not in ("", NO_ID) or _text(owner.get("FullName")):
        location_id = _text(owner.get("LocationId"))
        person = Person(
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
        fees=_fees(raw),
    )
