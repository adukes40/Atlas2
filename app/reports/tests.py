import json
from datetime import datetime, timedelta, timezone as dt_timezone
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from devices.models import LookupLog
from integrations.google import client as google_client
from integrations.models import GoogleConfig, IncidentIQConfig, MerakiConfig, MistConfig
from integrations.testing import (
    BASE,
    GOOGLE_ADMIN,
    GOOGLE_DEVICES_URL,
    GOOGLE_KEY,
    GOOGLE_TOKEN_URL,
    MERAKI_API,
    MERAKI_ORG,
    MIST_API,
    MIST_ORG,
    MIST_SITE,
    TEST_KEY,
    TOKEN,
    WIFI_MAC,
    FakeResponse,
    asset,
    chromebook,
    google_signed_in,
    incident_iq,
)

from . import catalog
from .catalog import BY_SLUG, DEVICE_FIELDS, REPORTS, parse_filter
from .management.commands.run_sync_schedule import due_schedules, queue_due
from .models import DailyCount, Device, NetDevice, Person, Psk, SavedReport, Sighting, SwitchPort, SyncRun, SyncSchedule, Wlan
from . import sync as sync_module
from .sync import _pages, cancel, execute, request_sync, run_pending

User = get_user_model()

FEES = json.dumps([{"Amount": 30, "Balance": 30, "PastDueAmount": 5, "LastActivityDate": "2026-10-01"}])


def page(*rows, total=None):
    return FakeResponse(data={"Items": list(rows), "Paging": {"TotalRows": len(rows) if total is None else total}})


def person(user_id, email, **changes):
    raw = {"UserId": user_id, "FullName": f"Person {user_id}", "Email": email, "RoleName": "Student", "Grade": "7",
           "LocationName": "North Middle School", "IsActive": True}
    raw.update(changes)
    return raw


IIQ_ROUTES = {
    "/assets": page(
        asset(),  # 7XK2RT3, assigned to u-1
        asset(AssetId="a-2", AssetTag="CB-2", SerialNumber="NOGOOGLE1", Owner={"UserId": "u-2", "FullName": "Gone Student"},
              Status={"Name": "Lost"}),
    ),
    "/users?$p=0&$s=500": page(
        person("u-1", "jsample@students.example", CustomFieldValues=[{"Value": FEES}]),
        person("u-2", "gone@students.example", IsActive=False),
        person("u-3", "nobook@students.example"),
    ),
}


def google_page(*books):
    return FakeResponse(data={"chromeosdevices": list(books)})


RECENT = (datetime.now(dt_timezone.utc) - timedelta(hours=3)).isoformat()
OLD = (datetime.now(dt_timezone.utc) - timedelta(days=45)).isoformat()


def others(google=None, mist=None):
    return {
        GOOGLE_TOKEN_URL: google_signed_in,
        GOOGLE_DEVICES_URL: google or google_page(
            # Assigned to jsample, last signed in by someone else, not seen for 45 days, old ChromeOS.
            chromebook(lastSync=OLD, osVersion="138.0.1", recentUsers=[{"email": "friend@students.example"}]),
            chromebook(serialNumber="ONLYGOOGLE", macAddress="0a0b0c0d0e0f", annotatedAssetId="", lastSync=RECENT,
                       osVersion="142.0.1", status="DISABLED"),
        ),
        f"{MIST_API}/orgs/{MIST_ORG}/clients/search": mist or FakeResponse(data={"results": [
            {"mac": WIFI_MAC, "site_id": MIST_SITE, "last_ssid": "CR-Students", "last_psk_name": "Staff PSK",
             "last_vlan": 120, "timestamp": datetime.now().timestamp() - 600},
            {"mac": "ffffffffffff", "site_id": MIST_SITE, "timestamp": datetime.now().timestamp()},  # not a Chromebook
        ]}),
        f"{MIST_API}/sites/{MIST_SITE}": FakeResponse(data={"name": "North Middle School"}),
    }


def connect():
    iiq = IncidentIQConfig.load()
    iiq.base_url = BASE
    iiq.set_token(TOKEN)
    iiq.save()
    google = GoogleConfig.load()
    google.admin_email = GOOGLE_ADMIN
    google.set_token(GOOGLE_KEY)
    google.save()
    mist = MistConfig.load()
    mist.org_id = MIST_ORG
    mist.set_token("mist-token")
    mist.save()


def sync(routes=IIQ_ROUTES, **changes):
    with incident_iq(routes, others=others(**changes)), mock.patch("integrations.http.time.sleep"):
        return execute(SyncRun.objects.create())


@override_settings(ATLAS_ENCRYPTION_KEY=TEST_KEY)
class SyncTests(TestCase):
    def setUp(self):
        google_client._tokens.clear()
        cache.clear()
        connect()

    def test_a_sync_joins_the_three_systems(self):
        run = sync()
        self.assertEqual(run.state, SyncRun.DONE, run.results)
        self.assertEqual(run.results["Google"]["detail"], "2 Chromebooks")
        self.assertEqual(run.results["Incident IQ"]["detail"], "2 assets, 3 people")

        both = Device.objects.get(serial="7XK2RT3")
        self.assertTrue(both.in_iiq and both.in_google)
        self.assertEqual(both.owner.email, "jsample@students.example")
        self.assertTrue(both.signed_in_by_other)
        self.assertEqual(both.os_behind, 4)
        self.assertEqual(both.network_place, "North Middle School")
        self.assertEqual(both.network_policy, "Staff PSK")
        self.assertEqual(both.last_seen_anywhere, both.last_on_network)
        self.assertEqual(both.mismatch, "")

        self.assertEqual(Device.objects.get(serial="ONLYGOOGLE").mismatch, "Not in Incident IQ")
        self.assertEqual(Device.objects.get(serial="NOGOOGLE1").mismatch, "Not in Google")
        self.assertEqual(Sighting.objects.count(), 1)  # only Chromebook MACs are kept

        owner = Person.objects.get(user_id="u-1")
        self.assertEqual((owner.fee_balance, owner.fee_past_due), (Decimal("30"), Decimal("5")))
        self.assertEqual((owner.chromebooks, owner.devices, owner.device_tags), (1, 1, "104482"))
        self.assertFalse(Person.objects.get(user_id="u-2").active)
        self.assertEqual(DailyCount.objects.get(report="signed-in-by-other").count, 1)

    def test_every_report_answers_from_the_copy(self):
        sync()
        expected = {
            "not-seen-30": 1, "lost-but-online": 0, "disabled-but-assigned": 0, "signed-in-by-other": 1,
            "more-than-one": 0, "no-chromebook": 1, "departed-holding": 1, "fee-balances": 1,
            "old-chromeos": 1, "wrong-network": 1, "on-wifi-by-school": 1,
        }
        for slug, count in expected.items():
            self.assertEqual(catalog.count(BY_SLUG[slug]), count, slug)

    def test_without_google_chromebooks_are_counted_from_incident_iq(self):
        GoogleConfig.objects.all().delete()
        MistConfig.objects.all().delete()
        routes = {**IIQ_ROUTES, "/assets": page(asset(), asset(
            AssetId="a-2", AssetTag="L-2", SerialNumber="LAPTOP1", Owner={"UserId": "u-3"},
            Model={"Name": "Laptop 14", "Category": {"Name": "Laptop"}}))}
        self.assertEqual(sync(routes).state, SyncRun.DONE)
        self.assertEqual(Person.objects.get(user_id="u-1").chromebooks, 1)
        self.assertEqual(Person.objects.get(user_id="u-3").chromebooks, 0)
        self.assertEqual(catalog.count(BY_SLUG["no-chromebook"]), 1)  # u-3, who has only a laptop

    def test_a_sync_keeps_earlier_sign_ins(self):
        from .models import SignIn

        SignIn.objects.create(email="earlier@students.example", serial="7XK2RT3", last_seen=timezone.now() - timedelta(days=90))
        sync()
        self.assertTrue(SignIn.objects.filter(email="earlier@students.example").exists())
        self.assertTrue(SignIn.objects.filter(email="friend@students.example").exists())
        sync()  # a second night updates the same row
        self.assertEqual(SignIn.objects.filter(email="friend@students.example").count(), 1)

    def test_without_google_incident_iqs_google_sync_fills_the_google_fields(self):
        GoogleConfig.objects.all().delete()
        synced = json.dumps([{"LastSyncDate": "2026-01-02", "OrgUnitPath": "/Students/North", "ChromebookStatus": "DISABLED",
                              "RecentUserEmail": "friend@students.example", "ChromebookAutoUpdateExpiration": "2031-06-01"}])
        routes = {**IIQ_ROUTES, "/assets": page(asset(CustomFieldValues=[{"Value": synced}]))}
        self.assertEqual(sync(routes).state, SyncRun.DONE)
        device = Device.objects.get(serial="7XK2RT3")
        self.assertEqual((device.in_google, device.org_unit, device.google_status), (True, "/Students/North", "Disabled"))
        self.assertTrue(device.signed_in_by_other)
        self.assertEqual(catalog.count(BY_SLUG["disabled-but-assigned"]), 1)

    def test_people_get_their_google_sign_in_homeroom_and_start_date(self):
        old_login = (datetime.now(dt_timezone.utc) - timedelta(days=40)).date().isoformat()
        google = json.dumps([{"LastLoginDate": old_login, "OrgUnitPath": "/Students/North", "OrgUnitPathNormalized": "/Students/North"}])
        new = (datetime.now(dt_timezone.utc) - timedelta(days=3)).isoformat()
        routes = {**IIQ_ROUTES, "/users?$p=0&$s=500": page(
            person("u-1", "jsample@students.example", Homeroom="B214", Username="jsample",
                   CustomFieldValues=[{"Value": FEES}, {"Value": google}]),
            person("u-3", "nobook@students.example", CreatedDate=new),
        )}
        self.assertEqual(sync(routes).state, SyncRun.DONE)
        jordan = Person.objects.get(user_id="u-1")
        self.assertEqual((jordan.homeroom, jordan.username, jordan.google_ou), ("B214", "jsample", "/Students/North"))
        self.assertEqual(jordan.google_last_login.date().isoformat(), old_login)
        self.assertEqual(jordan.fee_balance, Decimal("30"))  # the Google field is not mistaken for fees
        self.assertEqual(catalog.count(BY_SLUG["not-signing-in"]), 1)
        self.assertEqual(catalog.count(BY_SLUG["new-no-device"]), 1)

    def test_a_google_failure_keeps_yesterdays_copy(self):
        sync()
        run = sync(google=FakeResponse(status=403))
        self.assertEqual(run.state, SyncRun.FAILED)
        self.assertIn("Google refused", run.problems["Google"])
        self.assertEqual(Device.objects.count(), 3)

    def test_a_mist_failure_still_rebuilds_with_the_sightings_it_had(self):
        sync()
        run = sync(mist=FakeResponse(status=401))
        self.assertEqual(run.state, SyncRun.DONE)
        self.assertEqual(run.problems, {"Mist": "Mist rejected the API token."})
        self.assertEqual(Device.objects.get(serial="7XK2RT3").network_place, "North Middle School")

    def test_one_platform_syncs_on_its_own_and_the_rest_comes_from_the_copy(self):
        sync()
        before = Device.objects.get(serial="7XK2RT3")
        # Only Google: Incident IQ is not asked, and its assignments stay.
        with incident_iq(IIQ_ROUTES, others=others(google=google_page(chromebook(osVersion="142.0.1")))) as fake:
            run = execute(SyncRun.objects.create(systems="Google"))
        self.assertEqual(run.state, SyncRun.DONE)
        self.assertEqual(set(run.results), {"Google", "Atlas"})
        self.assertFalse(fake.paths())  # no Incident IQ call
        after = Device.objects.get(serial="7XK2RT3")
        self.assertEqual((after.owner_id, after.asset_tag, after.os_version), (before.owner_id, before.asset_tag, "142.0.1"))
        self.assertEqual(Person.objects.get(user_id="u-1").chromebooks, 1)
        self.assertTrue(Device.objects.filter(serial="NOGOOGLE1", in_iiq=True).exists())
        self.assertEqual(after.network_place, "North Middle School")  # Wi-Fi sightings kept
        # Only Incident IQ: Google's fields stay.
        with incident_iq(IIQ_ROUTES, others=others()) as fake:
            run = execute(SyncRun.objects.create(systems="Incident IQ"))
        self.assertEqual(set(run.results), {"Incident IQ", "Atlas"})
        self.assertFalse(any("googleapis" in u for u in fake.urls()))
        self.assertEqual(Device.objects.get(serial="7XK2RT3").os_version, "142.0.1")
        # Only Mist: neither Google nor Incident IQ is asked, and the copy is rebuilt from itself.
        with incident_iq(IIQ_ROUTES, others=others()) as fake:
            run = execute(SyncRun.objects.create(systems="Mist"))
        self.assertEqual(set(run.results), {"Mist", "Mist access points", "Atlas"})
        self.assertFalse(fake.paths())
        self.assertEqual(Device.objects.count(), 2)  # the Google-only run's one Chromebook plus NOGOOGLE1
        from .sync import as_of
        self.assertEqual(set(as_of()), {"Google", "Incident IQ", "Mist"})  # Meraki was never connected

    def test_off_network_comes_from_the_wan_ip_setting(self):
        from core.models import Organization
        Organization.objects.update_or_create(pk=1, defaults={"wan_ips": "198.51.100"})
        sync()
        device = Device.objects.get(serial="7XK2RT3")
        self.assertEqual((device.wan_ip, device.off_network), ("203.0.113.9", True))
        self.assertIsNone(Device.objects.get(serial="NOGOOGLE1").off_network)

    def test_rate_limits_are_waited_out(self):
        answers = iter([FakeResponse(status=429), google_page(chromebook())])
        with incident_iq(IIQ_ROUTES, others={**others(), GOOGLE_DEVICES_URL: lambda kwargs: next(answers)}), \
                mock.patch("integrations.http.time.sleep") as wait:
            run = execute(SyncRun.objects.create())
        self.assertEqual(run.results["Google"]["detail"], "1 Chromebooks")
        wait.assert_called_once()

    def test_paging_stops_when_a_page_brings_nothing_new(self):
        calls = []

        def fetch(index, size):
            calls.append(index)
            return [{"AssetId": "same"}] * size, None

        self.assertEqual(len(_pages(fetch, "AssetId")), 1)
        self.assertEqual(calls, [0, 1])

    def test_sync_now_queues_one_run_and_the_schedule_runs_it(self):
        first, second = request_sync("tech"), request_sync("tech")
        self.assertEqual(first, second)
        with incident_iq(IIQ_ROUTES, others=others()):
            self.assertEqual(run_pending().state, SyncRun.DONE)
        self.assertIsNone(run_pending())

    @override_settings(ATLAS_TIME_ZONE="America/New_York")
    def test_a_schedule_is_due_once_after_its_time_on_its_days(self):
        SyncSchedule.objects.all().delete()
        schedule = SyncSchedule.objects.create(at="02:00", days="01234", systems="Meraki")  # weekdays
        before = datetime(2026, 11, 13, 6, 30, tzinfo=dt_timezone.utc)  # Friday 1:30 AM Eastern
        after = datetime(2026, 11, 13, 7, 30, tzinfo=dt_timezone.utc)  # Friday 2:30 AM Eastern
        self.assertEqual(due_schedules(before), [])
        self.assertEqual(due_schedules(after), [schedule])
        queue_due(after)
        self.assertEqual(SyncRun.objects.get(state=SyncRun.REQUESTED).platforms, ["Meraki"])
        self.assertEqual(due_schedules(after + timedelta(hours=3)), [])
        self.assertEqual(due_schedules(after + timedelta(days=1)), [])  # Saturday is not one of its days
        self.assertEqual(due_schedules(after + timedelta(days=3)), [schedule])  # Monday
        SyncSchedule.objects.update(enabled=False)
        self.assertEqual(due_schedules(after + timedelta(days=3)), [])

    def test_the_old_nightly_hour_becomes_the_first_schedule(self):
        schedule = SyncSchedule.objects.get()
        self.assertEqual((schedule.at.hour, schedule.days, schedule.platforms, schedule.enabled), (2, "0123456", [], True))

    def test_cancel_stops_a_waiting_sync_at_once(self):
        run = request_sync("tech")
        cancel(run.pk, "tech")
        run.refresh_from_db()
        self.assertEqual((run.state, run.cancelled_by), (SyncRun.CANCELLED, "tech"))
        self.assertIsNone(run_pending())

    def test_cancel_stops_a_running_sync_and_keeps_the_reports(self):
        Device.objects.create(serial="KEEP1")
        real = sync_module._google

        def google_then_cancel(client):
            found = real(client)
            cancel(SyncRun.objects.get(state=SyncRun.RUNNING).pk, "tech")
            return found

        with mock.patch.object(sync_module, "_google", google_then_cancel):
            run = sync()
        self.assertEqual((run.state, run.cancelled_by), (SyncRun.CANCELLED, "tech"))
        self.assertEqual(list(run.results), ["Google"])  # Incident IQ never started
        self.assertEqual(list(Device.objects.values_list("serial", flat=True)), ["KEEP1"])
        self.assertEqual(run.progress, {})

    def test_progress_shows_the_running_step(self):
        run = SyncRun.objects.create(state=SyncRun.RUNNING, results={"Google": {"ok": True, "detail": "2 Chromebooks"}},
                                     progress={"steps": ["Google", "Incident IQ", "Atlas"], "at": 1,
                                               "done": 500, "total": 2000, "what": "assets"})
        self.client.force_login(User.objects.create_user("viewer"))
        page = self.client.get(reverse("sync-progress") + "?full=1")
        self.assertContains(page, "Syncing Incident IQ</b> · step 2 of 3 · 500 of 2,000 assets")
        self.assertContains(page, "width:25%")
        self.assertContains(page, "Building reports")
        self.assertContains(self.client.get(reverse("reports")), "Cancel sync")
        self.client.post(reverse("sync-cancel", args=[run.pk]), HTTP_REFERER="http://testserver/system/")
        self.assertContains(self.client.get(reverse("sync-progress")), "Cancelling")
        SyncRun.objects.filter(pk=run.pk).update(state=SyncRun.CANCELLED)
        self.assertContains(self.client.get(reverse("sync-progress")), "data-sync-done")


class FilterTests(TestCase):
    def test_only_known_fields_operations_and_numbers_are_accepted(self):
        for token in ("nope.is.x", "serial.older.3", "last_sync.older.soon", "last_sync.older.", "owner__fee_balance.gt.lots"):
            self.assertIsNone(parse_filter(DEVICE_FIELDS, token), token)
        self.assertEqual(parse_filter(DEVICE_FIELDS, "os_version.is.142.0").value, "142.0")
        self.assertEqual(parse_filter(DEVICE_FIELDS, "owner__fee_balance.gt.$1,000").wording, "is more than $1,000")

    def test_not_in_the_last_days_includes_never(self):
        Device.objects.create(serial="NEVER")
        Device.objects.create(serial="OLD", last_on_network=timezone.now() - timedelta(days=40))
        Device.objects.create(serial="NEW", last_on_network=timezone.now())
        found = Device.objects.filter(parse_filter(DEVICE_FIELDS, "last_on_network.notwithin.30").q())
        self.assertEqual(sorted(d.serial for d in found), ["NEVER", "OLD"])

    def test_every_default_filter_is_valid(self):
        for report in REPORTS:
            self.assertTrue(all(report.default_filters()), report.slug)
            self.assertTrue(set(report.columns) <= set(report.fields), report.slug)


@override_settings(ATLAS_ENCRYPTION_KEY=TEST_KEY)
class PageTests(TestCase):
    def setUp(self):
        google_client._tokens.clear()
        cache.clear()
        connect()
        sync()
        self.tech = User.objects.create_user("tech")
        self.client.force_login(self.tech)
        LookupLog.objects.create(user=self.tech, query="104482", found=True, key="104482", label="Jordan Sample")

    def test_signed_out_people_are_sent_to_sign_in(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse("reports")).status_code, 302)
        self.assertEqual(self.client.get(reverse("report", args=["fleet"])).status_code, 302)

    def test_the_overview_shows_every_report_the_connected_systems_can_fill(self):
        page = self.client.get(reverse("reports"))
        for report in REPORTS:
            if "Meraki" in report.needs:
                self.assertNotContains(page, report.title)
            else:
                self.assertContains(page, report.title)
        self.assertContains(page, "Data as of")

    def test_with_only_incident_iq_the_google_and_network_reports_are_hidden(self):
        GoogleConfig.objects.all().delete()
        MistConfig.objects.all().delete()
        Device.objects.update(in_google=False)  # as if Incident IQ had no Google sync either
        page = self.client.get(reverse("reports"))
        self.assertContains(page, "Fee balances")
        self.assertNotContains(page, "Fleet overview")
        self.assertNotContains(page, "Access points")
        self.assertNotContains(page, 'value="Google"')  # nor offered in Sync now

    def test_every_report_page_opens(self):
        for report in REPORTS:
            self.assertEqual(self.client.get(reverse("report", args=[report.slug])).status_code, 200, report.slug)

    def test_a_report_lists_its_rows_with_links_to_device_360(self):
        page = self.client.get(reverse("report", args=["signed-in-by-other"]))
        self.assertContains(page, "friend@students.example")
        self.assertContains(page, f'href="{reverse("device360")}?q=104482"')
        self.assertContains(page, "1 device<")

    def test_filters_can_be_added_and_removed(self):
        url = reverse("report", args=["signed-in-by-other"])  # any device report: edited filters replace its own
        added = self.client.get(url, {"add_field": "mismatch", "add_op": "has", "add_value": "Google"})
        self.assertEqual(added.status_code, 302)
        page = self.client.get(added["Location"])
        self.assertContains(page, "contains Google")
        self.assertContains(page, "is yes")  # the report's own filter stays
        self.assertContains(page, "0 devices")
        none = self.client.get(url, {"edited": "1", "f": ["mismatch.is.Nothing like this"]})
        self.assertContains(none, "Nothing matches these filters.")
        everything = self.client.get(url, {"edited": "1"})
        self.assertContains(everything, "3 devices")
        bad = self.client.get(url, {"add_field": "last_sync", "add_op": "older", "add_value": "soon"}, follow=True)
        self.assertContains(bad, "Days and amounts are numbers")

    def test_a_filter_chip_can_be_changed_in_place(self):
        url = reverse("report", args=["not-seen-30"])
        page = self.client.get(url)
        self.assertContains(page, 'name="replace" value="last_sync.older.30"')
        changed = self.client.get(url, {"add_field": "last_sync", "add_op": "older", "add_value": "60",
                                        "replace": "last_sync.older.30", "edited": "1",
                                        "f": ["last_sync.older.30", "owner__name.set."]})
        self.assertIn("f=last_sync.older.60&f=owner__name.set.", changed["Location"])
        self.assertNotIn("replace", changed["Location"])
        self.assertContains(self.client.get(changed["Location"]), "more than 60 days ago")

    def test_grouping_counts_rows(self):
        page = self.client.get(reverse("report", args=["fleet"]))
        self.assertContains(page, "rows in 1 group<")
        self.assertContains(page, "Show every row instead")

    def test_csv_export_has_every_row(self):
        response = self.client.get(reverse("report", args=["signed-in-by-other"]), {"format": "csv"})
        lines = b"".join(response.streaming_content).decode().splitlines()
        self.assertEqual(lines[0], "Asset tag,Serial,Assigned to,School,Model,Assigned email,Last Google sign-in,Google last sync")
        self.assertEqual(len(lines), 2)
        self.assertIn("attachment", response["Content-Disposition"])

    def test_a_report_can_be_saved_and_removed(self):
        self.client.post(reverse("save-report", args=["fleet"]), {"name": "Mine", "query": "g=org_unit"})
        saved = SavedReport.objects.get(user=self.tech)
        self.assertContains(self.client.get(reverse("reports")), "Mine")
        other = User.objects.create_user("other")
        self.client.force_login(other)
        self.assertEqual(self.client.post(reverse("delete-saved", args=[saved.pk])).status_code, 404)
        self.client.force_login(self.tech)
        self.client.post(reverse("delete-saved", args=[saved.pk]))
        self.assertFalse(SavedReport.objects.exists())

    def test_sync_now_queues_a_run(self):
        page = self.client.get(reverse("reports"))
        self.assertContains(page, "Mist <small>last ")
        self.assertNotContains(page, 'name="platform" value="Meraki"')  # not set up, so not offered
        self.client.post(reverse("sync-now"), {"next": reverse("reports")}, follow=True)
        self.assertFalse(SyncRun.objects.filter(state=SyncRun.REQUESTED).exists())  # nothing ticked
        self.client.post(reverse("sync-now"), {"next": reverse("reports"), "platform": ["Meraki", "Nope"]})
        run = SyncRun.objects.get(state=SyncRun.REQUESTED, requested_by="tech")
        self.assertEqual(run.platforms, ["Meraki"])
        # A second ask joins the waiting run; "everything" widens it to all.
        self.client.post(reverse("sync-now"), {"platform": "Google"})
        run.refresh_from_db()
        self.assertEqual(run.platforms, ["Google", "Meraki"])
        self.client.post(reverse("sync-now"), {"everything": "1"})
        run.refresh_from_db()
        self.assertEqual((run.platforms, SyncRun.objects.filter(state=SyncRun.REQUESTED).count()), ([], 1))
        self.assertContains(self.client.get(reverse("reports")), "Sync waiting to start")


# Network gear. Made-up names, serials and addresses.
ORG = f"{MERAKI_API}/organizations/{MERAKI_ORG}"
SWITCH = {"name": "North-MDF", "serial": "Q2GW-0000-0001", "mac": "ac:17:c8:00:00:01",
          "network": {"id": "N_1", "name": "North-switch"}, "model": "MS225-48FP"}
MIST_AP_MAC = "7cb68d000001"
LLDP_AP = [{"name": "System name", "value": "7cb68d000001"},
           {"name": "System description", "value": "Mist Systems 802.11ax Access Point."},
           {"name": "Chassis ID", "value": "7c:b6:8d:00:00:01"}, {"name": "Management address", "value": "10.1.8.3"}]
LLDP_CAMERA = [{"name": "System name", "value": "axis-accc8e000001"},
               {"name": "System description", "value": "AXIS M3047-P Network Camera 8.40"},
               {"name": "Chassis ID", "value": "ac:cc:8e:00:00:01"}]


def gear_routes(meraki=None):
    port = {"enabled": True, "poeEnabled": True, "type": "access", "vlan": 196, "voiceVlan": None, "allowedVlans": "all",
            "stpGuard": "disabled", "accessPolicyType": "Open"}
    return {
        f"{ORG}/networks": FakeResponse(data=[{"id": "N_1", "name": "North-switch"}]),
        f"{ORG}/devices/availabilities": FakeResponse(data=[{"serial": SWITCH["serial"], "status": "online"},
                                                           {"serial": "Q2EK-0000-0002", "status": "offline"}]),
        # Two pages, joined by Meraki's next-page link.
        f"{ORG}/devices": meraki or FakeResponse(
            data=[{**SWITCH, "productType": "switch", "networkId": "N_1", "lanIp": "10.1.252.2",
                   "firmware": "switch-17-2-1", "url": "https://n1.meraki.com/North-switch/n/x/manage/nodes/new_list/1"}],
            next_url=f"{ORG}/devices?startingAfter=1"),
        f"{ORG}/devices?startingAfter=1": FakeResponse(data=[
            {"name": "North-MR-1", "serial": "Q2EK-0000-0002", "mac": "e0:cb:bc:00:00:02", "productType": "wireless",
             "networkId": "N_1", "model": "MR42"}]),
        f"{ORG}/switch/ports/bySwitch": FakeResponse(data=[{**SWITCH, "ports": [
            {**port, "portId": "1"},
            {**port, "portId": "2"},
            {**port, "portId": "20", "type": "trunk", "vlan": 1, "allowedVlans": "1,255,120"},
            {**port, "portId": "21", "enabled": False},
        ]}]),
        f"{ORG}/switch/ports/statuses/bySwitch": FakeResponse(data={"items": [{**SWITCH, "ports": [
            {"portId": "1", "status": "Connected", "speed": "100 Mbps", "errors": [], "warnings": [], "poe": {"isAllocated": True}},
            {"portId": "2", "status": "Disconnected", "errors": ["Port disconnected"], "warnings": []},
            {"portId": "20", "status": "Connected", "speed": "1 Gbps", "errors": ["CRC errors"], "warnings": [],
             "poe": {"isAllocated": True}},
        ]}]}),
        f"{ORG}/switch/ports/topology/discovery/byDevice": FakeResponse(data={"items": [{**SWITCH, "ports": [
            {"portId": "1", "lldp": LLDP_CAMERA, "cdp": []},
            {"portId": "20", "lldp": LLDP_AP, "cdp": []},
        ]}]}),
        f"{MIST_API}/orgs/{MIST_ORG}/sites": FakeResponse(data=[{"id": MIST_SITE, "name": "North Middle School"}]),
        f"{MIST_API}/orgs/{MIST_ORG}/stats/devices": FakeResponse(data=[
            {"name": "NMS-AP-3", "mac": MIST_AP_MAC, "model": "AP45", "status": "connected", "site_id": MIST_SITE,
             "num_clients": 2, "version": "0.12.27458",
             "lldp_stat": {"system_name": "North-MDF", "port_id": "Port 20", "power_draw": 23.14}},
            # Plugged into a switch Meraki doesn't manage: only what the AP reports.
            {"name": "NMS-AP-9", "mac": "7cb68d000009", "model": "AP45", "status": "disconnected", "site_id": MIST_SITE,
             "lldp_stat": {"system_name": "core-3850", "port_id": "Gi1/0/4"}},
        ]),
        f"{MIST_API}/orgs/{MIST_ORG}/wlans": FakeResponse(data=[
            {"ssid": "Staff-Keys", "enabled": True, "auth": {"type": "psk", "multi_psk_only": True}, "vlan_id": 120,
             "bands": ["24", "5"], "site_id": "00000000-0000-0000-0000-000000000000"},
            {"ssid": "CR-Old", "enabled": False, "auth": {"type": "eap"}, "bands": ["5"], "site_id": MIST_SITE},
        ]),
        f"{MIST_API}/orgs/{MIST_ORG}/psks": FakeResponse(data=[
            {"name": "teacher@example.org", "ssid": "Staff-Keys", "role": "Staff", "vlan_id": 120,
             "passphrase": "do-not-copy-this", "expire_time": 0},
        ]),
    }


def connect_meraki():
    meraki = MerakiConfig.load()
    meraki.org_id = MERAKI_ORG
    meraki.set_token("meraki-key")
    meraki.save()


def gear_sync(**changes):
    with incident_iq(IIQ_ROUTES, others={**others(), **gear_routes(**changes)}), mock.patch("integrations.http.time.sleep"):
        return execute(SyncRun.objects.create())


@override_settings(ATLAS_ENCRYPTION_KEY=TEST_KEY)
class NetworkGearTests(TestCase):
    def setUp(self):
        google_client._tokens.clear()
        cache.clear()
        connect()
        connect_meraki()

    def test_switches_ports_and_access_points_are_copied_and_joined(self):
        run = gear_sync()
        self.assertTrue(run.results["Meraki"]["ok"], run.results)
        self.assertEqual(run.results["Meraki"]["detail"], "1 switches, 1 access points, 4 ports")
        self.assertEqual(run.results["Mist access points"]["detail"], "2 access points, 2 wireless networks, 1 personal keys")

        switch = NetDevice.objects.get(kind="Switch")
        self.assertEqual((switch.status, switch.site, switch.ports_up, switch.ports_total), ("Online", "North-switch", 2, 4))
        self.assertEqual(NetDevice.objects.get(serial="Q2EK-0000-0002").status, "Offline")

        # Meraki's neighbor list puts the Mist AP on port 20, with that port's settings.
        ap = NetDevice.objects.get(mac=MIST_AP_MAC)
        self.assertEqual((ap.uplink_switch, ap.uplink_port, ap.port_mode, ap.port_vlan), ("North-MDF", "20", "Trunk", "1"))
        self.assertEqual((ap.status, ap.clients, ap.poe_draw, ap.site), ("Online", 2, 23.1, "North Middle School"))
        port = SwitchPort.objects.get(port_id="20")
        self.assertEqual((port.neighbor, port.neighbor_kind, port.problems), ("NMS-AP-3", "Access point", "CRC errors"))
        self.assertEqual(SwitchPort.objects.get(port_id="1").neighbor_kind, "Camera")
        self.assertEqual(SwitchPort.objects.get(port_id="21").link, "Disabled")
        # Not a Meraki switch: what the AP reports is all there is.
        other = NetDevice.objects.get(mac="7cb68d000009")
        self.assertEqual((other.uplink_switch, other.uplink_port, other.port_mode, other.status), ("core-3850", "Gi1/0/4", "", "Offline"))

        psk = Wlan.objects.get(ssid="Staff-Keys")
        self.assertEqual((psk.security, psk.vlan, psk.bands, psk.scope, psk.psks), ("Personal keys", "120", "2.4, 5 GHz", "Whole district", 1))
        self.assertEqual(Wlan.objects.get(ssid="CR-Old").scope, "North Middle School")
        key = Psk.objects.get()
        self.assertEqual((key.name, key.role, key.expires), ("teacher@example.org", "Staff", None))
        self.assertNotIn("do-not-copy-this", json.dumps(list(Psk.objects.values()), default=str))

    def test_meraki_alone_asks_nothing_else(self):
        gear_sync()
        with incident_iq(IIQ_ROUTES, others={**others(), **gear_routes()}) as fake:
            run = execute(SyncRun.objects.create(systems="Meraki"))
        self.assertEqual((run.state, list(run.results)), (SyncRun.DONE, ["Meraki"]))
        self.assertTrue(all("meraki.com" in u for u in fake.urls()))
        self.assertEqual(NetDevice.objects.get(mac=MIST_AP_MAC).uplink_port, "20")

    def test_a_meraki_failure_keeps_yesterdays_meraki_gear(self):
        gear_sync()
        run = gear_sync(meraki=FakeResponse(status=500))
        self.assertFalse(run.results["Meraki"]["ok"])
        self.assertEqual(run.state, SyncRun.DONE)  # Chromebook reports still rebuilt
        self.assertEqual(NetDevice.objects.filter(vendor="Meraki").count(), 2)
        self.assertEqual(SwitchPort.objects.count(), 4)
        self.assertEqual(NetDevice.objects.get(mac=MIST_AP_MAC).uplink_port, "20")

    def test_the_gear_reports_and_switch_page_open(self):
        gear_sync()
        tech = User.objects.create_user("tech", password="x")
        self.client.force_login(tech)
        switch_url = reverse("switch", args=[SWITCH["serial"]])
        aps = self.client.get(reverse("report", args=["access-points"]))
        self.assertContains(aps, "NMS-AP-3")
        self.assertContains(aps, f'href="{switch_url}"')
        self.assertContains(aps, "3 devices")
        self.assertContains(self.client.get(reverse("report", args=["plugged-in"])), "Camera")
        self.assertContains(self.client.get(reverse("report", args=["switch-ports"])), "4 ports")
        page = self.client.get(switch_url)
        self.assertContains(page, "Port 20")  # the first AP's port is shown first
        self.assertContains(page, "Trunk, native VLAN 1")
        self.assertContains(page, "drawing 23.1 W")
        self.assertContains(page, "Open in Meraki")
        self.assertContains(self.client.get(switch_url, {"port": "1"}), "AXIS M3047-P")
        self.assertEqual(self.client.get(reverse("switch", args=["NOPE"])).status_code, 404)
        self.assertEqual(catalog.count(BY_SLUG["aps-offline"]), 2)


class ScheduleSettingsTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser("boss", password="x")
        self.client.force_login(self.admin)

    def test_administrators_add_change_switch_off_and_delete_schedules(self):
        self.assertContains(self.client.get(reverse("settings")), "2:00 AM")
        self.client.post(reverse("schedule-add"), {"enabled": "on", "at": "03:30", "day_list": ["0"], "system_list": ["Meraki"]})
        schedule = SyncSchedule.objects.get(at="03:30")
        self.assertEqual((schedule.days, schedule.platforms, schedule.enabled), ("0", ["Meraki"], True))
        self.assertIsNotNone(schedule.last_queued)  # waits for its next time, not right now
        self.client.post(reverse("schedule-edit", args=[schedule.pk]),
                         {"enabled": "on", "at": "04:00", "day_list": ["0", "6"], "system_list": list(sync_module.PLATFORMS)})
        schedule.refresh_from_db()
        self.assertEqual((str(schedule.at), schedule.days, schedule.systems), ("04:00:00", "06", ""))
        self.client.post(reverse("schedule-toggle", args=[schedule.pk]))
        schedule.refresh_from_db()
        self.assertFalse(schedule.enabled)
        self.client.post(reverse("schedule-edit", args=[schedule.pk]), {"delete": "1"})
        self.assertFalse(SyncSchedule.objects.filter(pk=schedule.pk).exists())

    def test_a_schedule_needs_a_day_and_a_system(self):
        page = self.client.post(reverse("schedule-add"), {"enabled": "on", "at": "03:30"})
        self.assertEqual(page.status_code, 200)
        self.assertEqual(SyncSchedule.objects.count(), 1)

    def test_technicians_cannot_change_schedules(self):
        self.client.force_login(User.objects.create_user("tech"))
        self.assertEqual(self.client.get(reverse("schedule-add")).status_code, 403)
        self.assertEqual(self.client.post(reverse("schedule-toggle", args=[SyncSchedule.objects.get().pk])).status_code, 403)
