"""Atlas's nightly copy of Incident IQ, Google and Mist, which every report reads.

Device and Person are rebuilt whole by each sync (see `sync.py`). Sighting
is kept between syncs, so "last on our network" reaches back further than
the one day Mist is asked about each night.
"""

from django.conf import settings
from django.db import models


class SyncRun(models.Model):
    REQUESTED, RUNNING, DONE, FAILED = "requested", "running", "done", "failed"

    state = models.CharField(max_length=10, default=REQUESTED)
    requested_by = models.CharField(max_length=150, blank=True)  # blank: the nightly schedule
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    # {"Google": {"ok": true, "rows": 28114, "seconds": 61}, "Mist": {"ok": false, "error": "..."}}
    results = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at"]

    @classmethod
    def last_good(cls):
        """The latest run that rebuilt the report data."""
        return cls.objects.filter(state=cls.DONE).exclude(finished_at=None).first()

    @property
    def problems(self):
        return {name: r.get("error", "") for name, r in self.results.items() if not r.get("ok")}


class Person(models.Model):
    """Everyone in Incident IQ."""

    user_id = models.CharField(max_length=64, primary_key=True)
    name = models.CharField(max_length=200, blank=True)
    email = models.CharField(max_length=200, blank=True, db_index=True)
    school_id = models.CharField(max_length=64, blank=True)
    role = models.CharField(max_length=100, blank=True)
    grade = models.CharField(max_length=20, blank=True)
    school = models.CharField(max_length=200, blank=True)
    active = models.BooleanField(default=True)
    fee_balance = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    fee_past_due = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    chromebooks = models.IntegerField(default=0)  # assigned devices that Google also has
    devices = models.IntegerField(default=0)  # every assigned asset
    device_tags = models.CharField(max_length=300, blank=True)


class Device(models.Model):
    """One row per serial number, from Incident IQ, Google, or both."""

    serial = models.CharField(max_length=100, unique=True)
    # Incident IQ
    in_iiq = models.BooleanField(default=False)
    iiq_asset_id = models.CharField(max_length=64, blank=True)
    asset_tag = models.CharField(max_length=100, blank=True, db_index=True)
    model = models.CharField(max_length=200, blank=True)
    category = models.CharField(max_length=100, blank=True)
    iiq_status = models.CharField(max_length=100, blank=True)
    iiq_location = models.CharField(max_length=200, blank=True)
    owner = models.ForeignKey(
        Person, null=True, blank=True, on_delete=models.SET_NULL, db_constraint=False, related_name="assets"
    )
    # Google
    in_google = models.BooleanField(default=False)
    google_status = models.CharField(max_length=40, blank=True)
    org_unit = models.CharField(max_length=300, blank=True)
    last_sync = models.DateTimeField(null=True, blank=True)
    recent_user = models.CharField(max_length=200, blank=True)
    google_model = models.CharField(max_length=200, blank=True)
    os_version = models.CharField(max_length=40, blank=True)
    os_behind = models.IntegerField(null=True, blank=True)  # ChromeOS versions behind the newest in the fleet
    updates_until = models.DateField(null=True, blank=True)
    google_asset_id = models.CharField(max_length=100, blank=True)
    macs = models.CharField(max_length=40, blank=True)
    # Mist
    last_on_network = models.DateTimeField(null=True, blank=True)
    network_place = models.CharField(max_length=200, blank=True)
    ssid = models.CharField(max_length=100, blank=True)
    network_policy = models.CharField(max_length=200, blank=True)
    vlan = models.CharField(max_length=20, blank=True)
    # Worked out when the copy is rebuilt
    last_seen_anywhere = models.DateTimeField(null=True, blank=True)
    signed_in_by_other = models.BooleanField(default=False)
    mismatch = models.CharField(max_length=200, blank=True)

    @property
    def key(self):
        return self.asset_tag or self.serial


class Sighting(models.Model):
    """The latest time Mist saw a Chromebook's MAC."""

    mac = models.CharField(max_length=12, primary_key=True)
    last_seen = models.DateTimeField()
    place = models.CharField(max_length=200, blank=True)
    ssid = models.CharField(max_length=100, blank=True)
    policy = models.CharField(max_length=200, blank=True)
    vlan = models.CharField(max_length=20, blank=True)


class DailyCount(models.Model):
    """Each report's row count after each night's sync, for the Overview trend."""

    report = models.CharField(max_length=60)
    day = models.DateField()
    count = models.IntegerField()

    class Meta:
        unique_together = [("report", "day")]
        ordering = ["day"]


class SavedReport(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="saved_reports")
    name = models.CharField(max_length=100)
    report = models.CharField(max_length=60)
    query = models.CharField(max_length=2000)  # the report page's query string
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]


class NetDevice(models.Model):
    """A switch or access point, from Meraki or Mist. Each system's rows are
    replaced whole by each sync; a failed system keeps yesterday's rows."""

    mac = models.CharField(max_length=12, unique=True)
    vendor = models.CharField(max_length=10)  # Meraki or Mist
    kind = models.CharField(max_length=20)  # Switch or Access point
    name = models.CharField(max_length=200, blank=True)
    model = models.CharField(max_length=40, blank=True)
    serial = models.CharField(max_length=40, blank=True)
    site = models.CharField(max_length=200, blank=True)
    ip = models.CharField(max_length=45, blank=True)
    status = models.CharField(max_length=20, blank=True)
    firmware = models.CharField(max_length=60, blank=True)
    clients = models.IntegerField(null=True, blank=True)
    last_seen = models.DateTimeField(null=True, blank=True)
    url = models.CharField(max_length=300, blank=True)  # the device's page in the vendor's dashboard
    ports_up = models.IntegerField(null=True, blank=True)
    ports_total = models.IntegerField(null=True, blank=True)
    poe_draw = models.FloatField(null=True, blank=True)  # watts, as Mist reports it
    # The switch port it plugs into: from Meraki's neighbor list, else from what the AP reports.
    uplink_switch = models.CharField(max_length=200, blank=True)
    uplink_serial = models.CharField(max_length=40, blank=True)
    uplink_port = models.CharField(max_length=20, blank=True)
    port_mode = models.CharField(max_length=20, blank=True)
    port_vlan = models.CharField(max_length=20, blank=True)
    reported_switch = models.CharField(max_length=200, blank=True)  # what the AP itself says (LLDP)
    reported_port = models.CharField(max_length=40, blank=True)


class SwitchPort(models.Model):
    """One Meraki switch port: its settings, its link, and what is plugged in."""

    switch_serial = models.CharField(max_length=40, db_index=True)
    switch_name = models.CharField(max_length=200, blank=True)
    site = models.CharField(max_length=200, blank=True)
    port_id = models.CharField(max_length=20)
    number = models.IntegerField(default=0)  # for sorting 2 before 10
    name = models.CharField(max_length=200, blank=True)
    enabled = models.BooleanField(default=True)
    mode = models.CharField(max_length=20, blank=True)  # Access, Trunk, Stack
    vlan = models.CharField(max_length=20, blank=True)
    voice_vlan = models.CharField(max_length=20, blank=True)
    allowed_vlans = models.CharField(max_length=300, blank=True)
    poe = models.BooleanField(default=False)
    stp_guard = models.CharField(max_length=40, blank=True)
    access_policy = models.CharField(max_length=60, blank=True)
    link = models.CharField(max_length=20, blank=True)  # Connected, Disconnected, Disabled
    speed = models.CharField(max_length=20, blank=True)
    uplink = models.BooleanField(default=False)
    poe_in_use = models.BooleanField(default=False)
    problems = models.CharField(max_length=300, blank=True)
    neighbor = models.CharField(max_length=200, blank=True)
    neighbor_kind = models.CharField(max_length=20, blank=True)  # Access point, Camera, Switch, Phone, Other
    neighbor_model = models.CharField(max_length=200, blank=True)
    neighbor_mac = models.CharField(max_length=12, blank=True, db_index=True)
    neighbor_ip = models.CharField(max_length=45, blank=True)


class Wlan(models.Model):
    """A Mist wireless network (SSID) configuration."""

    ssid = models.CharField(max_length=100)
    enabled = models.BooleanField(default=True)
    security = models.CharField(max_length=40, blank=True)
    vlan = models.CharField(max_length=40, blank=True)
    bands = models.CharField(max_length=40, blank=True)
    scope = models.CharField(max_length=200, blank=True)
    psks = models.IntegerField(default=0)


class Psk(models.Model):
    """A Mist personal pre-shared key, without the key itself."""

    name = models.CharField(max_length=200, blank=True)
    ssid = models.CharField(max_length=100, blank=True)
    role = models.CharField(max_length=100, blank=True)
    vlan = models.CharField(max_length=20, blank=True)
    expires = models.DateTimeField(null=True, blank=True)
