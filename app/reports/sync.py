"""The nightly copy that every report reads.

Atlas asks Google for every Chromebook, Incident IQ for every asset and
person, and Mist for every Wi-Fi client seen in the last day, then rebuilds
the Device and Person tables in one step, so a report never sees half a copy.
Meraki's switches, ports and access points and Mist's access points and
wireless networks are copied the same way, each system's rows on their own.

If Google or Incident IQ fails, yesterday's copy stays as it was. If Mist
fails, the copy is rebuilt with the Wi-Fi sightings Atlas already had.
Only reads from the vendors, with the same keys Device 360 uses.
"""

import logging
import time
from datetime import datetime, timezone as dt_timezone

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from core.models import Organization
from integrations.errors import IntegrationError
from integrations.google.normalize import normalize_chromebook
from integrations.incidentiq.normalize import normalize_asset, normalize_user
from integrations.meraki.normalize import normalize_gear, normalize_ports
from integrations.mist.normalize import normalize_ap, normalize_psk, normalize_wlan
from integrations.models import GoogleConfig, IncidentIQConfig, MerakiConfig, MistConfig
from integrations.network import mac_plain

from .models import DailyCount, Device, NetDevice, Person, Psk, Sighting, SwitchPort, SyncRun, Wlan

log = logging.getLogger("atlas.sync")

PAGE = 500  # Incident IQ rows per request
MAX_PAGES = 400  # 200,000 rows: far past any district, and a stop if paging misbehaves
SYNC_TIMEOUT = 60  # seconds per request; pages are large
SYNC_RETRIES = 3
KEEP = object()  # a platform not synced this time: rebuild from what the copy has


PLATFORMS = {  # each platform and the sync steps it runs
    "Google": ("Google",),
    "Incident IQ": ("Incident IQ",),
    "Mist": ("Mist", "Mist access points"),
    "Meraki": ("Meraki",),
}


def request_sync(who, platforms=()):
    """Queue a sync of these platforms (all when none are named). A sync
    already waiting to start takes them on instead. Returns the run."""
    platforms = [p for p in platforms if p in PLATFORMS]
    waiting = SyncRun.objects.filter(state=SyncRun.REQUESTED).first()
    if waiting:
        if waiting.systems and platforms:
            waiting.systems = ",".join(p for p in PLATFORMS if p in waiting.platforms or p in platforms)
        else:
            waiting.systems = ""  # one of them asked for everything
        waiting.save()
        return waiting
    return SyncRun.objects.create(requested_by=who, systems=",".join(platforms))


def as_of():
    """When each platform's data in the reports was last copied: {"Google": datetime}."""
    found = {}
    for run in SyncRun.objects.exclude(finished_at=None).order_by("-finished_at")[:200]:
        for platform, steps in PLATFORMS.items():
            first = run.results.get(steps[0], {})
            if platform in found or not first.get("ok") or first.get("detail", "").startswith("Not connected"):
                continue
            if platform in ("Google", "Incident IQ") and not run.results.get("Atlas", {}).get("ok"):
                continue  # fetched, but the copy was not rebuilt with it
            found[platform] = run.finished_at
        if len(found) == len(PLATFORMS):
            break
    return found


def run_pending():
    """Run the oldest queued sync, if any. Returns it, or None."""
    run = SyncRun.objects.filter(state=SyncRun.REQUESTED).order_by("created_at").first()
    if not run or not SyncRun.objects.filter(pk=run.pk, state=SyncRun.REQUESTED).update(state=SyncRun.RUNNING):
        return None
    execute(run)
    return run


def execute(run):
    run.state, run.started_at = SyncRun.RUNNING, timezone.now()
    run.save()
    try:
        run.results, ok = collect_and_rebuild(run.platforms)
        run.state = SyncRun.DONE if ok else SyncRun.FAILED
    except Exception:
        log.exception("Sync stopped")
        run.results = {**run.results, "Atlas": {"ok": False, "error": "The sync stopped on an unexpected error. The sync log has the details."}}
        run.state = SyncRun.FAILED
    run.finished_at = timezone.now()
    run.save()
    return run


def collect_and_rebuild(platforms=()):
    """Sync these platforms (all when none are named). Platforms not asked for
    keep what the copy already has. Returns (results, whether it went well)."""
    want = set(platforms or PLATFORMS)
    results = {}
    google = _step(results, "Google", GoogleConfig, _google) if "Google" in want else KEEP
    iiq = _step(results, "Incident IQ", IncidentIQConfig, _incidentiq) if "Incident IQ" in want else KEEP
    if "Mist" in want:
        if isinstance(google, dict):
            macs = {m for raw in google.values() for m in (mac_plain(raw.get(k)) for k in ("macAddress", "ethernetMacAddress")) if m}
        else:
            macs = {m for joined in Device.objects.values_list("macs", flat=True) for m in joined.split(",") if m}
        _step(results, "Mist", MistConfig, lambda client: _mist(client, macs))
        _step(results, "Mist access points", MistConfig, _mist_gear)
    if "Meraki" in want:
        _step(results, "Meraki", MerakiConfig, _meraki_gear)
    if want & {"Mist", "Meraki"}:
        link_gear()
    gear_ok = all(results.get(step, {}).get("ok", True) for step in ("Meraki", "Mist access points"))
    if not want & {"Google", "Incident IQ", "Mist"}:
        record_counts()
        return results, gear_ok
    if google is None or iiq is None:
        return results, False
    started = time.monotonic()
    devices, people = rebuild(google, *(iiq if iiq is not KEEP else (KEEP, KEEP)), google_on=GoogleConfig.load().configured)
    results["Atlas"] = {"ok": True, "detail": f"{devices:,} devices and {people:,} people ready for reports",
                        "seconds": round(time.monotonic() - started)}
    record_counts()
    return results, True


def _step(results, name, config_class, fetch):
    """fetch(client) -> (data, detail). Returns data, {} when the system is not
    set up, or None when it failed (the error is in results)."""
    config = config_class.load()
    if not config.configured:
        results[name] = {"ok": True, "detail": "Not connected, skipped"}
        return {} if name != "Incident IQ" else ([], [])
    started = time.monotonic()
    try:
        client = config.client()
        client.timeout, client.retries = SYNC_TIMEOUT, SYNC_RETRIES
        data, detail = fetch(client)
    except IntegrationError as error:
        results[name] = {"ok": False, "error": str(error), "seconds": round(time.monotonic() - started)}
        return None
    results[name] = {"ok": True, "detail": detail, "seconds": round(time.monotonic() - started)}
    return data


def _google(client):
    found = {}
    for raw in client.all_chromebooks():
        serial = str(raw.get("serialNumber") or "").strip().upper()
        if serial:
            found[serial] = raw
    return found, f"{len(found):,} Chromebooks"


def _pages(fetch, id_key):
    """Every row of a paged Incident IQ list, each once."""
    rows, seen = [], set()
    for index in range(MAX_PAGES):
        items, total = fetch(index, PAGE)
        new = 0
        for item in items:
            if str(item.get(id_key)) not in seen:
                seen.add(str(item.get(id_key)))
                rows.append(item)
                new += 1
        # Stop at the end, or when a page brings nothing new (paging ignored).
        if not new or (total is not None and len(rows) >= total) or (total is None and len(items) < PAGE):
            break
    return rows


def _incidentiq(client):
    assets = _pages(client.assets_page, "AssetId")
    people = _pages(client.users_page, "UserId")
    return (assets, people), f"{len(assets):,} assets, {len(people):,} people"


def _mist(client, macs):
    latest = {}
    for raw in client.clients_seen("1d"):
        mac = mac_plain(raw.get("mac"))
        stamp = raw.get("timestamp")
        if mac in macs and isinstance(stamp, (int, float)) and stamp > latest.get(mac, {}).get("timestamp", 0):
            latest[mac] = raw
    known = Sighting.objects.in_bulk(list(latest))
    changed = []
    for mac, raw in latest.items():
        seen = datetime.fromtimestamp(raw["timestamp"], tz=dt_timezone.utc)
        if mac in known and known[mac].last_seen >= seen:
            continue
        changed.append(Sighting(
            mac=mac,
            last_seen=seen,
            place=client.site_name(str(raw.get("site_id") or "")),
            ssid=_text(raw.get("last_ssid"))[:100],
            policy=_text(raw.get("last_psk_name"))[:200],
            vlan=_text(raw.get("last_vlan"))[:20],
        ))
    Sighting.objects.bulk_create(
        changed, batch_size=1000, update_conflicts=True, unique_fields=["mac"],
        update_fields=["last_seen", "place", "ssid", "policy", "vlan"],
    )
    return None, f"{len(latest):,} Chromebooks seen on Wi-Fi in the last day"


def _meraki_gear(client):
    if not client.org_id:
        client.org_id = client.check()[0]
    networks = {str(n.get("id")): str(n.get("name") or "") for n in client.org_list("networks", {"perPage": 100000})}
    kinds = {"productTypes[]": ["switch", "wireless"]}
    status = {d.get("serial"): d.get("status") for d in client.org_list("devices/availabilities", {**kinds, "perPage": 1000})}
    gear = {}
    for raw in client.org_list("devices", {**kinds, "perPage": 1000}):
        row = normalize_gear(raw, status.get(raw.get("serial"), ""), networks)
        if row["mac"]:
            gear[row["mac"]] = NetDevice(**_fit(NetDevice, row))
    statuses = {s.get("serial"): s for s in client.org_list("switch/ports/statuses/bySwitch", {"perPage": 20})}
    neighbors = {s.get("serial"): s for s in client.org_list("switch/ports/topology/discovery/byDevice", {"perPage": 20})}
    ports = []
    for config in client.org_list("switch/ports/bySwitch", {"perPage": 50}):
        serial = config.get("serial")
        ports += [SwitchPort(**_fit(SwitchPort, row)) for row in normalize_ports(config, statuses.get(serial), neighbors.get(serial))]
    by_switch = {}
    for port in ports:
        by_switch.setdefault(port.switch_serial, []).append(port)
    for device in gear.values():
        mine = by_switch.get(device.serial)
        if mine:
            device.ports_total, device.ports_up = len(mine), sum(p.link == "Connected" for p in mine)
    with transaction.atomic():
        NetDevice.objects.filter(vendor="Meraki").delete()
        SwitchPort.objects.all().delete()
        NetDevice.objects.bulk_create(_new(gear), batch_size=1000)
        SwitchPort.objects.bulk_create(ports, batch_size=1000)
    switches = sum(d.kind == "Switch" for d in gear.values())
    return None, f"{switches:,} switches, {len(gear) - switches:,} access points, {len(ports):,} ports"


def _mist_gear(client):
    sites = {str(s.get("id")): str(s.get("name") or "") for s in client.org_list("sites")}
    gear = {}
    for raw in client.org_list("stats/devices", {"type": "ap"}):
        row = normalize_ap(raw, sites)
        if row["mac"]:
            gear[row["mac"]] = NetDevice(**_fit(NetDevice, row))
    psks = [Psk(**_fit(Psk, normalize_psk(raw))) for raw in client.org_list("psks")]
    keys_per_ssid = {}
    for psk in psks:
        keys_per_ssid[psk.ssid] = keys_per_ssid.get(psk.ssid, 0) + 1
    wlans = [Wlan(**_fit(Wlan, normalize_wlan(raw, sites))) for raw in client.org_list("wlans")]
    for wlan in wlans:
        wlan.psks = keys_per_ssid.get(wlan.ssid, 0)
    with transaction.atomic():
        NetDevice.objects.filter(vendor="Mist").delete()
        Wlan.objects.all().delete()
        Psk.objects.all().delete()
        NetDevice.objects.bulk_create(_new(gear), batch_size=1000)
        Wlan.objects.bulk_create(wlans)
        Psk.objects.bulk_create(psks, batch_size=1000)
    return None, f"{len(gear):,} access points, {len(wlans):,} wireless networks, {len(psks):,} personal keys"


def _fit(model, row):
    """The row with text cut to fit each column."""
    for name, value in row.items():
        size = getattr(model._meta.get_field(name), "max_length", None)
        if size and isinstance(value, str):
            row[name] = value[:size]
    return row


def _new(gear):
    """Gear whose MAC the other system has not already claimed."""
    taken = set(NetDevice.objects.filter(mac__in=list(gear)).values_list("mac", flat=True))
    return [d for mac, d in gear.items() if mac not in taken]


def link_gear():
    """Put each access point on its switch port: Meraki's neighbor list first,
    else the switch and port the access point reports itself."""
    ports = list(SwitchPort.objects.all())
    by_mac = {p.neighbor_mac: p for p in ports if p.neighbor_mac}
    by_name = {(p.switch_name.lower(), p.port_id): p for p in ports}
    aps = list(NetDevice.objects.filter(kind="Access point"))
    named = []
    for ap in aps:
        port = by_mac.get(ap.mac) or by_name.get((ap.reported_switch.lower(), ap.reported_port))
        if port:
            ap.uplink_switch, ap.uplink_serial, ap.uplink_port = port.switch_name, port.switch_serial, port.port_id
            ap.port_mode, ap.port_vlan = port.mode, port.vlan
            if port.neighbor != ap.name:
                port.neighbor, port.neighbor_kind, port.neighbor_mac = ap.name, "Access point", ap.mac
                named.append(port)
        else:
            ap.uplink_switch, ap.uplink_port = ap.reported_switch, ap.reported_port
            ap.uplink_serial = ap.port_mode = ap.port_vlan = ""
    fields = ["uplink_switch", "uplink_serial", "uplink_port", "port_mode", "port_vlan"]
    NetDevice.objects.bulk_update(aps, fields, batch_size=1000)
    SwitchPort.objects.bulk_update(named, ["neighbor", "neighbor_kind", "neighbor_mac"], batch_size=1000)


def _text(value):
    return str(value).strip() if value not in (None, "") else ""


def _cut(value, field):
    return (value or "")[: Device._meta.get_field(field).max_length]


IIQ_FIELDS = ["serial", "in_iiq", "iiq_asset_id", "asset_tag", "model", "category", "iiq_status", "iiq_location", "owner_id"]
GOOGLE_FIELDS = ["in_google", "google_status", "org_unit", "last_sync", "recent_user", "google_model", "os_version",
                 "updates_until", "google_asset_id", "macs", "wan_ip"]
PERSON_FIELDS = ["user_id", "name", "email", "school_id", "role", "grade", "school", "active", "fee_balance", "fee_past_due"]


def rebuild(google, assets, people, google_on=True):
    """Replace the report copy. KEEP for google, or for assets and people,
    takes that platform's part from the copy as it is. Returns (devices, people) counts."""
    if assets is KEEP:
        persons, devices = _iiq_from_copy()
    else:
        persons, devices = _iiq(assets, people)
    if google is KEEP:
        _google_from_copy(devices)
    else:
        _google_into(devices, google)
    return _finish(devices, persons, google_on)


def _iiq_from_copy():
    persons = {p.user_id: Person(**{f: getattr(p, f) for f in PERSON_FIELDS}) for p in Person.objects.all()}
    devices = {d.serial.upper(): Device(**{f: getattr(d, f) for f in IIQ_FIELDS})
               for d in Device.objects.filter(in_iiq=True).only(*IIQ_FIELDS)}
    return persons, devices


def _google_from_copy(devices):
    for d in Device.objects.filter(in_google=True).only("serial", *GOOGLE_FIELDS):
        device = devices.setdefault(d.serial.upper(), Device(serial=d.serial))
        for f in GOOGLE_FIELDS:
            setattr(device, f, getattr(d, f))


def _iiq(assets, people):
    persons = {}
    for raw in people:
        person, fees, active = normalize_user(raw)
        if person.user_id:
            persons[person.user_id] = Person(
                user_id=person.user_id, name=person.name[:200], email=person.email[:200],
                school_id=person.school_id[:64], role=person.role[:100], grade=person.grade[:20],
                school=person.location[:200], active=active,
                fee_balance=fees.balance if fees else None, fee_past_due=fees.past_due if fees else None,
            )

    devices = {}
    for raw in assets:
        asset = normalize_asset(raw)
        serial = asset.serial.upper()
        if not serial or serial in devices:
            continue  # shortcut: an asset with no serial cannot be matched to Google; left out of reports
        owner = asset.owner
        if owner and owner.user_id and owner.user_id not in persons:
            # Someone the people list did not include: use what the asset says about them.
            persons[owner.user_id] = Person(user_id=owner.user_id, name=owner.name[:200], email=owner.email[:200],
                                            school_id=owner.school_id[:64], role=owner.role[:100], grade=owner.grade[:20])
        devices[serial] = Device(
            serial=_cut(asset.serial, "serial"), in_iiq=True, iiq_asset_id=asset.asset_id[:64],
            asset_tag=_cut(asset.asset_tag, "asset_tag"), model=_cut(asset.model, "model"),
            category=_cut(asset.category, "category"), iiq_status=_cut(asset.status, "iiq_status"),
            iiq_location=_cut(asset.location, "iiq_location"),
            owner_id=owner.user_id if owner and owner.user_id else None,
        )
    return persons, devices


def _google_into(devices, google):
    for serial, raw in google.items():
        book = normalize_chromebook(raw)
        device = devices.setdefault(serial, Device(serial=_cut(_text(raw.get("serialNumber")), "serial")))
        device.in_google = True
        device.google_status = _cut(book.status, "google_status")
        device.org_unit = _cut(book.org_unit, "org_unit")
        device.last_sync = book.last_sync
        device.recent_user = _cut(book.recent_users[0] if book.recent_users else "", "recent_user")
        device.google_model = _cut(_text(raw.get("model")), "google_model")
        device.os_version = _cut(book.os_version, "os_version")
        device.updates_until = book.updates_until
        device.google_asset_id = _cut(book.asset_id, "google_asset_id")
        device.macs = _cut(",".join(book.macs), "macs")
        device.wan_ip = _cut(book.wan_ip, "wan_ip")


def _finish(devices, persons, google_on):
    organization = Organization.load()
    newest = max((_major(d.os_version) or 0 for d in devices.values()), default=0)
    sightings = Sighting.objects.in_bulk(field_name="mac")
    for device in devices.values():
        major = _major(device.os_version)
        device.os_behind = newest - major if major else None
        seen = [sightings[m] for m in device.macs.split(",") if m in sightings]
        if seen:
            last = max(seen, key=lambda s: s.last_seen)
            device.last_on_network, device.network_place = last.last_seen, last.place
            device.ssid, device.network_policy, device.vlan = last.ssid, last.policy, last.vlan
        stamps = [t for t in (device.last_sync, device.last_on_network) if t]
        device.last_seen_anywhere = max(stamps) if stamps else None
        owner = persons.get(device.owner_id)
        device.signed_in_by_other = bool(
            device.recent_user and owner and owner.email and device.recent_user.lower() != owner.email.lower()
        )
        device.mismatch = _cut("; ".join(_mismatches(device, google_on)), "mismatch")
        on = organization.on_network(device.wan_ip)
        device.off_network = None if on is None else not on
        if owner:
            owner.devices += 1
            owner.chromebooks += device.in_google
            owner.device_tags = _cut_tags(owner.device_tags, device.key)

    with transaction.atomic():
        Device.objects.all().delete()
        Person.objects.all().delete()
        Person.objects.bulk_create(persons.values(), batch_size=1000)
        Device.objects.bulk_create(devices.values(), batch_size=1000)
    return len(devices), len(persons)


def _major(version):
    head = (version or "").split(".")[0]
    return int(head) if head.isdigit() else None


def _cut_tags(tags, tag):
    joined = f"{tags}, {tag}" if tags else tag
    return joined if len(joined) <= 300 else tags


def _mismatches(device, google_on):
    found = []
    if device.in_google and not device.in_iiq:
        found.append("Not in Incident IQ")
    looks_chromebook = "chromebook" in f"{device.model} {device.category}".lower()
    if google_on and device.in_iiq and not device.in_google and looks_chromebook:
        found.append("Not in Google")
    if device.in_iiq and device.in_google and device.google_asset_id and device.asset_tag \
            and device.google_asset_id.lower() != device.asset_tag.lower():
        found.append("Google asset ID differs")
    if device.asset_tag and device.asset_tag.upper() == device.serial.upper():
        found.append("Tag is the serial")
    return found


def record_counts():
    """Each report's count today, for the Overview trend."""
    from .catalog import REPORTS, count

    day = timezone.localdate(timezone=sync_zone())
    for report in REPORTS:
        if report.base != "lookups":
            DailyCount.objects.update_or_create(report=report.slug, day=day, defaults={"count": count(report)})


def sync_zone():
    from zoneinfo import ZoneInfo

    return ZoneInfo(settings.ATLAS_TIME_ZONE)
