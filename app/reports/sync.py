"""The nightly copy that every report reads.

Atlas asks Google for every Chromebook, Incident IQ for every asset and
person, and Mist for every Wi-Fi client seen in the last day, then rebuilds
the Device and Person tables in one step, so a report never sees half a copy.

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

from integrations.errors import IntegrationError
from integrations.google.normalize import normalize_chromebook
from integrations.incidentiq.normalize import normalize_asset, normalize_user
from integrations.models import GoogleConfig, IncidentIQConfig, MistConfig
from integrations.network import mac_plain

from .models import DailyCount, Device, Person, Sighting, SyncRun

log = logging.getLogger("atlas.sync")

PAGE = 500  # Incident IQ rows per request
MAX_PAGES = 400  # 200,000 rows: far past any district, and a stop if paging misbehaves
SYNC_TIMEOUT = 60  # seconds per request; pages are large
SYNC_RETRIES = 3


def request_sync(who):
    """Queue a sync unless one is already queued or running. Returns the run."""
    waiting = SyncRun.objects.filter(state__in=[SyncRun.REQUESTED, SyncRun.RUNNING]).first()
    return waiting or SyncRun.objects.create(requested_by=who)


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
        run.results, rebuilt = collect_and_rebuild()
        run.state = SyncRun.DONE if rebuilt else SyncRun.FAILED
    except Exception:
        log.exception("Sync stopped")
        run.results = {**run.results, "Atlas": {"ok": False, "error": "The sync stopped on an unexpected error. The sync log has the details."}}
        run.state = SyncRun.FAILED
    run.finished_at = timezone.now()
    run.save()
    return run


def collect_and_rebuild():
    results = {}
    google = _step(results, "Google", GoogleConfig, _google)
    iiq = _step(results, "Incident IQ", IncidentIQConfig, _incidentiq)
    macs = set()
    for raw in (google or {}).values():
        macs.update(m for m in (mac_plain(raw.get(k)) for k in ("macAddress", "ethernetMacAddress")) if m)
    _step(results, "Mist", MistConfig, lambda client: _mist(client, macs))
    if google is None or iiq is None:
        return results, False
    started = time.monotonic()
    devices, people = rebuild(google, *iiq, google_on=GoogleConfig.load().configured)
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


def _text(value):
    return str(value).strip() if value not in (None, "") else ""


def _cut(value, field):
    return (value or "")[: Device._meta.get_field(field).max_length]


def rebuild(google, assets, people, google_on=True):
    """Replace the report copy. Returns (devices, people) counts."""
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
