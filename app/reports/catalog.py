"""Every report, every field a report can filter on, and how a filter becomes a query.

A report is a starting set of filters on one table (devices, people,
lookups, network gear, switch ports, wireless networks, personal keys). Anyone can add, change or remove a filter on any field,
so "filter on anything" and the fixed reports are the same thing.

Filters travel in the page address as `f=<field>.<op>.<value>`.
"""

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.db.models import Count, F, Q
from django.utils import timezone

from devices.models import LookupLog

from .models import Device, NetDevice, Person, Psk, SwitchPort, Wlan


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    kind: str  # text, bool, number, money, when (date and time), day (date only)
    system: str = ""

    @property
    def path(self):
        return self.key


def _fields(*rows):
    return {key: Field(key, label, kind, system) for key, label, kind, system in rows}


IIQ, GOOGLE, MIST, MERAKI, ATLAS = "Incident IQ", "Google", "Mist", "Meraki", "Atlas"
BOTH = "Meraki and Mist"

DEVICE_FIELDS = _fields(
    ("asset_tag", "Asset tag", "text", IIQ),
    ("serial", "Serial", "text", ""),
    ("model", "Model", "text", IIQ),
    ("category", "Category", "text", IIQ),
    ("iiq_status", "Incident IQ status", "text", IIQ),
    ("iiq_location", "Device location", "text", IIQ),
    ("owner__name", "Assigned to", "text", IIQ),
    ("owner__email", "Assigned email", "text", IIQ),
    ("owner__role", "Role", "text", IIQ),
    ("owner__grade", "Grade", "text", IIQ),
    ("owner__school", "School", "text", IIQ),
    ("owner__active", "Person is active", "bool", IIQ),
    ("owner__fee_balance", "Fees owed", "money", IIQ),
    ("in_iiq", "In Incident IQ", "bool", IIQ),
    ("in_google", "In Google", "bool", GOOGLE),
    ("google_status", "Google status", "text", GOOGLE),
    ("org_unit", "Google OU", "text", GOOGLE),
    ("last_sync", "Google last sync", "when", GOOGLE),
    ("recent_user", "Last Google sign-in", "text", GOOGLE),
    ("google_model", "Google model", "text", GOOGLE),
    ("os_version", "ChromeOS", "text", GOOGLE),
    ("os_behind", "ChromeOS versions behind", "number", GOOGLE),
    ("updates_until", "Updates until", "day", GOOGLE),
    ("google_asset_id", "Google asset ID", "text", GOOGLE),
    ("last_on_network", "Last on our network", "when", MIST),
    ("network_place", "Network location", "text", MIST),
    ("ssid", "SSID", "text", MIST),
    ("network_policy", "Network policy", "text", MIST),
    ("vlan", "VLAN", "text", MIST),
    ("last_seen_anywhere", "Last seen anywhere", "when", ATLAS),
    ("signed_in_by_other", "Signed in by someone else", "bool", ATLAS),
    ("mismatch", "Mismatch", "text", ATLAS),
)

PEOPLE_FIELDS = _fields(
    ("name", "Name", "text", IIQ),
    ("email", "Email", "text", IIQ),
    ("school_id", "School ID number", "text", IIQ),
    ("role", "Role", "text", IIQ),
    ("grade", "Grade", "text", IIQ),
    ("school", "School", "text", IIQ),
    ("active", "Active", "bool", IIQ),
    ("fee_balance", "Fees owed", "money", IIQ),
    ("fee_past_due", "Past due", "money", IIQ),
    ("chromebooks", "Chromebooks assigned", "number", ATLAS),
    ("devices", "Assets assigned", "number", IIQ),
    ("device_tags", "Asset tags", "text", IIQ),
)

LOOKUP_FIELDS = _fields(
    ("created_at", "When", "when", ATLAS),
    ("user__username", "Who", "text", ATLAS),
    ("query", "Searched for", "text", ATLAS),
    ("found", "Found", "bool", ATLAS),
    ("label", "Turned out to be", "text", ATLAS),
)

GEAR_FIELDS = _fields(
    ("name", "Name", "text", BOTH),
    ("kind", "Type", "text", BOTH),
    ("vendor", "Vendor", "text", BOTH),
    ("model", "Model", "text", BOTH),
    ("serial", "Serial", "text", BOTH),
    ("mac", "MAC", "text", BOTH),
    ("site", "Site", "text", BOTH),
    ("ip", "IP address", "text", BOTH),
    ("status", "Status", "text", BOTH),
    ("firmware", "Firmware", "text", BOTH),
    ("clients", "Clients", "number", MIST),
    ("last_seen", "Last seen", "when", MIST),
    ("poe_draw", "PoE draw (W)", "number", MIST),
    ("ports_up", "Ports up", "number", MERAKI),
    ("ports_total", "Ports", "number", MERAKI),
    ("uplink_switch", "Switch", "text", BOTH),
    ("uplink_port", "Switch port", "text", BOTH),
    ("port_mode", "Port mode", "text", MERAKI),
    ("port_vlan", "Port VLAN", "text", MERAKI),
)

PORT_FIELDS = _fields(
    ("switch_name", "Switch", "text", MERAKI),
    ("site", "Site", "text", MERAKI),
    ("port_id", "Port", "text", MERAKI),
    ("name", "Port name", "text", MERAKI),
    ("enabled", "Enabled", "bool", MERAKI),
    ("link", "Link", "text", MERAKI),
    ("speed", "Speed", "text", MERAKI),
    ("uplink", "Uplink", "bool", MERAKI),
    ("mode", "Mode", "text", MERAKI),
    ("vlan", "VLAN", "text", MERAKI),
    ("voice_vlan", "Voice VLAN", "text", MERAKI),
    ("allowed_vlans", "Allowed VLANs", "text", MERAKI),
    ("poe", "PoE on", "bool", MERAKI),
    ("poe_in_use", "Drawing PoE", "bool", MERAKI),
    ("stp_guard", "STP guard", "text", MERAKI),
    ("access_policy", "Access policy", "text", MERAKI),
    ("problems", "Errors and warnings", "text", MERAKI),
    ("neighbor", "Plugged in", "text", MERAKI),
    ("neighbor_kind", "Plugged-in type", "text", MERAKI),
    ("neighbor_model", "Plugged-in model", "text", MERAKI),
    ("neighbor_ip", "Plugged-in IP", "text", MERAKI),
)

WLAN_FIELDS = _fields(
    ("ssid", "SSID", "text", MIST),
    ("enabled", "Enabled", "bool", MIST),
    ("security", "Security", "text", MIST),
    ("vlan", "VLAN", "text", MIST),
    ("bands", "Bands", "text", MIST),
    ("scope", "Where", "text", MIST),
    ("psks", "Personal keys", "number", MIST),
)

PSK_FIELDS = _fields(
    ("name", "Name", "text", MIST),
    ("ssid", "SSID", "text", MIST),
    ("role", "Role", "text", MIST),
    ("vlan", "VLAN", "text", MIST),
    ("expires", "Expires", "when", MIST),
)

BASES = {
    "devices": (DEVICE_FIELDS, lambda: Device.objects.select_related("owner"), ("device", "devices")),
    "people": (PEOPLE_FIELDS, lambda: Person.objects.all(), ("person", "people")),
    "lookups": (LOOKUP_FIELDS, lambda: LookupLog.objects.select_related("user"), ("lookup", "lookups")),
    "gear": (GEAR_FIELDS, lambda: NetDevice.objects.all(), ("device", "devices")),
    "ports": (PORT_FIELDS, lambda: SwitchPort.objects.all(), ("port", "ports")),
    "wlans": (WLAN_FIELDS, lambda: Wlan.objects.all(), ("network", "networks")),
    "psks": (PSK_FIELDS, lambda: Psk.objects.all(), ("key", "keys")),
}

# What each kind of field can be filtered by. The label reads after the field
# name; {v} is the value typed.
OPS = {
    "text": {"is": "is {v}", "not": "is not {v}", "has": "contains {v}", "set": "is not empty", "empty": "is empty"},
    "bool": {"yes": "is yes", "no": "is no"},
    "number": {"gt": "is more than {v}", "lt": "is less than {v}", "eq": "is {v}", "set": "is not empty", "empty": "is empty"},
    "when": {
        "older": "more than {v} days ago",
        "within": "in the last {v} days",
        "notwithin": "not in the last {v} days",
        "set": "is not empty",
        "empty": "is empty",
    },
    "day": {"before": "before {v} days from now", "after": "after {v} days from now", "set": "is not empty", "empty": "is empty"},
}
OPS["money"] = OPS["number"]
NEEDS_VALUE = {"is", "not", "has", "gt", "lt", "eq", "older", "within", "notwithin", "before", "after"}


@dataclass(frozen=True)
class Filter:
    field: Field
    op: str
    value: str = ""

    @property
    def token(self):
        return f"{self.field.key}.{self.op}.{self.value}"

    @property
    def wording(self):
        return OPS[self.field.kind][self.op].format(v=self.value)

    def q(self):
        path, op, v = self.field.path, self.op, self.value
        kind = self.field.kind
        if op == "empty":
            return Q(**{f"{path}__isnull": True}) | Q(**{path: ""}) if kind == "text" else Q(**{f"{path}__isnull": True})
        if op == "set":
            return ~self._empty()
        if op in ("yes", "no"):
            return Q(**{path: op == "yes"})
        if kind == "text":
            words = [w.strip() for w in v.split(",") if w.strip()]
            lookup = "icontains" if op == "has" else "iexact"
            match = Q()
            for word in words:
                match |= Q(**{f"{path}__{lookup}": word})
            return ~match if op == "not" else match
        if kind in ("number", "money"):
            return Q(**{f"{path}__{ {'gt': 'gt', 'lt': 'lt', 'eq': 'exact'}[op] }": _number(v)})
        days = timedelta(days=int(v))
        now = timezone.now()
        if kind == "day":
            cutoff = (now + days).date()
            return Q(**{f"{path}__lt" if op == "before" else f"{path}__gte": cutoff})
        if op == "older":
            return Q(**{f"{path}__lt": now - days})
        if op == "within":
            return Q(**{f"{path}__gte": now - days})
        return Q(**{f"{path}__isnull": True}) | Q(**{f"{path}__lt": now - days})  # notwithin

    def _empty(self):
        return Filter(self.field, "empty").q()


def _number(value):
    try:
        return Decimal(value.strip().lstrip("$").replace(",", ""))
    except (InvalidOperation, AttributeError):
        raise ValueError from None


def parse_filter(fields, token):
    """A Filter from `field.op.value`, or None if any part is not allowed."""
    key, _, rest = str(token).partition(".")
    op, _, value = rest.partition(".")
    found = fields.get(key)
    if not found or op not in OPS[found.kind]:
        return None
    value = value.strip()[:200]
    if op in NEEDS_VALUE and not value:
        return None
    try:
        if found.kind in ("when", "day") and op in NEEDS_VALUE:
            if not 0 <= int(value) <= 36500:
                return None
        elif found.kind in ("number", "money") and op in NEEDS_VALUE:
            _number(value)
    except ValueError:
        return None
    return Filter(found, op, value)


@dataclass(frozen=True)
class Report:
    slug: str
    title: str
    group: str
    question: str
    base: str
    filters: tuple = ()
    columns: tuple = ()
    sort: str = ""
    group_by: str = ""
    note: str = ""  # shown under the question: what to know about the data

    @property
    def fields(self):
        return BASES[self.base][0]

    @property
    def noun(self):
        """What one row is called, and many."""
        return BASES[self.base][2]

    def default_filters(self):
        return [parse_filter(self.fields, token) for token in self.filters]


def query(report, filters, sort=""):
    fields, rows, _ = BASES[report.base]
    qs = rows()
    for item in filters:
        qs = qs.filter(item.q())
    key = sort.lstrip("-")
    if key in fields:
        column = F(fields[key].path)
        qs = qs.order_by(column.desc(nulls_last=True) if sort.startswith("-") else column.asc(nulls_last=True), "pk")
    else:
        qs = qs.order_by("pk")
    return qs


def grouped(report, filters, key):
    """Counts by one field, largest first."""
    path = report.fields[key].path
    return (
        query(report, filters).order_by().values(path).annotate(n=Count("pk")).order_by("-n", path)
    ), path


DEVICE_COLUMNS = ("asset_tag", "serial", "owner__name", "owner__school", "model")
G = {
    "where": "Where devices are",
    "who": "Who has what",
    "money": "Money",
    "life": "Lifecycle",
    "network": "Network",
    "gear": "Network gear",
    "atlas": "Atlas",
}

REPORTS = [
    Report(
        "not-seen-30", "Chromebooks not seen in 30+ days", G["where"],
        "Assigned Chromebooks Google hasn't heard from in 30 days or more, with who Incident IQ says has them.",
        "devices", ("last_sync.older.30", "owner__name.set."),
        DEVICE_COLUMNS + ("owner__grade", "last_sync", "last_on_network", "owner__fee_balance"), "last_sync",
    ),
    Report(
        "lost-but-online", "Lost but still online", G["where"],
        "Devices Incident IQ marks lost, stolen, missing or retired that Google or our Wi-Fi saw in the last 7 days.",
        "devices", ("iiq_status.has.lost, stolen, missing, retired, dispos", "last_seen_anywhere.within.7"),
        DEVICE_COLUMNS + ("iiq_status", "last_sync", "last_on_network", "network_place", "recent_user"), "-last_seen_anywhere",
        note="Status names are matched by the words in the first filter. Change them if yours differ.",
    ),
    Report(
        "disabled-but-assigned", "Disabled but still assigned", G["where"],
        "Chromebooks disabled or deprovisioned in Google that someone still has in Incident IQ.",
        "devices", ("google_status.is.Disabled, Deprovisioned", "owner__name.set."),
        DEVICE_COLUMNS + ("google_status", "org_unit", "last_sync"), "owner__school",
    ),
    Report(
        "signed-in-by-other", "Signed in by someone else", G["who"],
        "Chromebooks whose last Google sign-in is not the person Incident IQ has them assigned to.",
        "devices", ("signed_in_by_other.yes.",),
        DEVICE_COLUMNS + ("owner__email", "recent_user", "last_sync"), "-last_sync",
    ),
    Report(
        "more-than-one", "People with more than one Chromebook", G["who"],
        "Who has two or more Chromebooks assigned in Incident IQ.",
        "people", ("chromebooks.gt.1",),
        ("name", "email", "role", "grade", "school", "chromebooks", "device_tags"), "-chromebooks",
    ),
    Report(
        "no-chromebook", "Students with no Chromebook", G["who"],
        "Active students with no Chromebook assigned in Incident IQ.",
        "people", ("role.is.Student", "active.yes.", "chromebooks.eq.0"),
        ("name", "email", "school_id", "grade", "school", "devices"), "school",
    ),
    Report(
        "departed-holding", "Departed, still holding devices", G["who"],
        "People Incident IQ marks inactive who still have an asset assigned.",
        "people", ("active.no.", "devices.gt.0"),
        ("name", "email", "role", "grade", "school", "devices", "device_tags", "fee_balance"), "school",
    ),
    Report(
        "fee-balances", "Fee balances", G["money"],
        "Who owes, how much, and which devices they have now.",
        "people", ("fee_balance.gt.0",),
        ("name", "email", "school_id", "role", "grade", "school", "fee_balance", "fee_past_due", "device_tags"), "-fee_balance",
    ),
    Report(
        "update-end-dates", "Update end dates", G["life"],
        "Chromebooks past their automatic update date, or reaching it within 12 months.",
        "devices", ("updates_until.before.365",),
        ("asset_tag", "serial", "google_model", "updates_until", "owner__school", "org_unit", "google_status"), "updates_until",
        group_by="google_model",
    ),
    Report(
        "fleet", "Fleet overview", G["life"],
        "How many Chromebooks Google has, by model. Group by any other field to slice it differently.",
        "devices", ("in_google.yes.",),
        ("asset_tag", "serial", "google_model", "google_status", "org_unit", "owner__school"), "google_model",
        group_by="google_model",
    ),
    Report(
        "old-chromeos", "Out-of-date ChromeOS", G["life"],
        "Chromebooks three or more versions behind the newest ChromeOS in the fleet.",
        "devices", ("os_behind.gt.2", "google_status.is.Active"),
        DEVICE_COLUMNS + ("os_version", "os_behind", "updates_until", "last_sync"), "-os_behind",
    ),
    Report(
        "wrong-network", "Wrong network or policy", G["network"],
        "Student Chromebooks that joined our Wi-Fi with a staff network or policy in the last 7 days.",
        "devices", ("org_unit.has.student", "network_policy.has.staff", "last_on_network.within.7"),
        DEVICE_COLUMNS + ("org_unit", "ssid", "network_policy", "vlan", "network_place", "last_on_network"), "network_place",
        note="Matched by the words \"student\" in the Google OU and \"staff\" in the Mist policy. Change them to fit your names.",
    ),
    Report(
        "on-wifi-by-school", "On Wi-Fi at each school", G["network"],
        "How many Chromebooks were on our Wi-Fi at each site in the last day of the copy.",
        "devices", ("last_on_network.within.1",),
        DEVICE_COLUMNS + ("network_place", "ssid", "last_on_network"), "network_place",
        group_by="network_place",
    ),
    Report(
        "access-points", "Access points", G["gear"],
        "Every Mist and Meraki access point, with the switch port it is plugged into and that port's settings.",
        "gear", ("kind.is.Access point",),
        ("name", "vendor", "model", "status", "clients", "site", "uplink_switch", "uplink_port", "port_mode",
         "port_vlan", "poe_draw", "firmware"), "name",
        note="The switch port comes from Meraki's neighbor list, or from what a Mist access point reports when the switch isn't a Meraki one.",
    ),
    Report(
        "aps-offline", "Access points offline", G["gear"],
        "Access points Mist or Meraki doesn't have online, and where they plug in.",
        "gear", ("kind.is.Access point", "status.not.Online"),
        ("name", "vendor", "model", "status", "site", "last_seen", "uplink_switch", "uplink_port"), "site",
    ),
    Report(
        "switches", "Switches", G["gear"],
        "Every Meraki switch, how many of its ports are up, and its firmware. Click a switch to see its ports.",
        "gear", ("kind.is.Switch",),
        ("name", "model", "site", "status", "ip", "ports_up", "ports_total", "firmware"), "name",
    ),
    Report(
        "switch-ports", "Switch ports", G["gear"],
        "Every Meraki switch port: its settings, its link, and what is plugged in.",
        "ports", (),
        ("switch_name", "port_id", "name", "link", "speed", "mode", "vlan", "allowed_vlans", "poe", "neighbor",
         "neighbor_kind"), "switch_name",
    ),
    Report(
        "plugged-in", "Devices on switch ports", G["gear"],
        "Access points, cameras, phones and switches each switch port sees, by type.",
        "ports", ("neighbor.set.",),
        ("switch_name", "port_id", "neighbor", "neighbor_kind", "neighbor_model", "neighbor_ip", "mode", "vlan",
         "poe_in_use"), "switch_name",
        group_by="neighbor_kind",
        note="Read from what each device announces on the port (LLDP or CDP). A device that announces nothing isn't listed.",
    ),
    Report(
        "port-errors", "Ports with errors", G["gear"],
        "Switch ports Meraki reports errors or warnings on, such as CRC errors or PoE over budget.",
        "ports", ("problems.set.",),
        ("switch_name", "port_id", "problems", "link", "speed", "neighbor", "neighbor_kind"), "switch_name",
    ),
    Report(
        "firmware", "Firmware versions", G["gear"],
        "Which firmware every switch and access point runs. Group by model or site to compare.",
        "gear", (),
        ("name", "kind", "vendor", "model", "site", "firmware", "status"), "firmware",
        group_by="firmware",
    ),
    Report(
        "wireless-networks", "Wireless networks", G["gear"],
        "Every Mist wireless network: on or off, how it signs people in, its VLAN and bands.",
        "wlans", (),
        ("ssid", "enabled", "security", "vlan", "bands", "scope", "psks"), "ssid",
    ),
    Report(
        "personal-keys", "Personal Wi-Fi keys", G["gear"],
        "Who has a Mist personal key, for which network, with which role. The keys themselves are not copied.",
        "psks", (),
        ("name", "ssid", "role", "vlan", "expires"), "ssid",
    ),
    Report(
        "lookups", "Lookup activity", G["atlas"],
        "Who looked up which devices in Atlas in the last 30 days. This one is live.",
        "lookups", ("created_at.within.30",),
        ("created_at", "user__username", "query", "found", "label"), "-created_at",
    ),
]
BY_SLUG = {r.slug: r for r in REPORTS}


def library():
    """Reports in their groups, in order."""
    groups = {}
    for report in REPORTS:
        groups.setdefault(report.group, []).append(report)
    return list(groups.items())


def count(report):
    filters = report.default_filters()
    return query(report, filters).count()
