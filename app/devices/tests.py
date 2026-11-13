import json

import requests
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from integrations.models import GoogleConfig, IncidentIQConfig, MerakiConfig, MistConfig
from integrations.testing import (
    BASE,
    GOOGLE_ADMIN,
    GOOGLE_DEVICES_URL,
    GOOGLE_KEY,
    MERAKI_API,
    MERAKI_ON_SWITCH,
    MERAKI_SWITCH_ROUTES,
    MERAKI_ORG,
    MIST_API,
    MIST_ORG,
    NO_FEES,
    SCHOOL_ID,
    STANDARD,
    STANDARD_OTHERS,
    TEST_KEY,
    TOKEN,
    FakeResponse,
    asset,
    chromebook,
    incident_iq,
    items,
)

from .models import LookupLog

User = get_user_model()


@override_settings(ATLAS_ENCRYPTION_KEY=TEST_KEY)
class LookupTests(TestCase):
    url = reverse("device360")

    def setUp(self):
        self.tech = User.objects.create_user("tech")
        self.client.force_login(self.tech)
        config = IncidentIQConfig.load()
        config.base_url = BASE
        config.set_token(TOKEN)
        config.save()

    def look_up(self, query, routes=STANDARD, fail=None):
        with incident_iq(routes, fail) as fake:
            page = self.client.get(self.url, {"q": query})
        return page, fake

    def test_a_found_device_fills_the_page(self):
        page, _ = self.look_up("104482")
        for expected in [
            "Dell Chromebook 3110",
            "7XK2RT3",
            "In Service",
            "Jordan Sample",
            "204817",
            "jsample@students.example",
            "North Middle School",
            "B214",
            "$25.00",
            "$10.00 past due",
            "1 open",
            f'href="{BASE}/agent/assets/a-1"',
            f'href="{BASE}/agent/users/u-1"',
            "Cracked screen",
            f'href="{BASE}/agent/tickets/t-5521"',
            "Waiting on parts",
            "2026-10-01",
        ]:
            self.assertContains(page, expected)
        self.assertContains(page, 'class="cell at"', count=2)
        self.assertNotContains(page, "Old ticket")
        self.assertNotContains(page, "Someone else&#x27;s")

    def test_fees_come_from_the_assigned_person_when_they_have_a_record(self):
        routes = dict(STANDARD)
        charges = [{"Amount": 55, "Balance": 55, "PastDueAmount": 0, "LastActivityDate": "2026-11-01", "Description": "Screen"}]
        routes["/users/u-1"] = FakeResponse(
            data={"Item": {"UserId": "u-1", "CustomFieldValues": [{"Value": json.dumps(charges)}]}}
        )
        page, _ = self.look_up("104482", routes)
        self.assertContains(page, "$55.00")
        self.assertContains(page, "Owed by Jordan Sample")
        self.assertContains(page, "Fees owed by Jordan Sample")
        self.assertContains(page, "Screen")
        self.assertNotContains(page, "$25.00")

    def test_fees_on_the_device_itself_are_ignored(self):
        charges = [{"Amount": 99, "Balance": 99, "LastActivityDate": "2026-11-01"}]
        routes = dict(STANDARD)
        routes["/assets/assettag/104482"] = items(asset(CustomFieldValues=[{"Value": json.dumps(charges)}]))
        routes["/users/u-1"] = NO_FEES
        page, _ = self.look_up("104482", routes)
        self.assertContains(page, "Nothing owed")
        self.assertNotContains(page, "$99.00")

    def test_fees_are_not_asked_for_on_an_unassigned_device(self):
        page, fake = self.look_up("104482", {"/assets/assettag/104482": items(asset(Owner=None))})
        self.assertContains(page, "Nothing owed")
        self.assertNotIn("/users/u-1", fake.paths())

    def test_fees_that_do_not_load_show_nothing_owed(self):
        routes = dict(STANDARD)
        routes["/users/u-1"] = FakeResponse(status=500)
        page, _ = self.look_up("104482", routes)
        self.assertContains(page, "Nothing owed")
        self.assertContains(page, "Jordan Sample")

    def test_tickets_that_do_not_load_leave_the_count(self):
        routes = dict(STANDARD)
        routes["/tickets?$s=50"] = FakeResponse(status=500)
        page, _ = self.look_up("104482", routes)
        self.assertContains(page, "Incident IQ counts 1 open, but the list did not load")
        self.assertContains(page, "Jordan Sample")

    def test_tickets_filed_under_the_person_count_even_when_the_asset_says_none(self):
        routes = dict(STANDARD)
        routes["/assets/assettag/104482"] = items(asset(OpenTickets=0))
        page, fake = self.look_up("104482", routes)
        self.assertContains(page, "<b>1 open</b>")
        self.assertContains(page, "Cracked screen")
        body = [kw["json"] for _, url, kw in fake.calls if "/tickets" in url][0]
        self.assertEqual(body["Filters"], [{"Facet": "user", "Id": "u-1"}])

    def test_tickets_are_not_asked_for_on_an_unassigned_device(self):
        _, fake = self.look_up("104482", {"/assets/assettag/104482": items(asset(Owner=None))})
        self.assertNotIn("/tickets?$s=50", fake.paths())

    def test_a_tag_that_is_the_serial_is_flagged(self):
        page, _ = self.look_up("104482", {"/assets/assettag/104482": items(asset(AssetTag="7XK2RT3"))})
        self.assertContains(page, "so a real tag was never entered")

    def test_nothing_owed_and_no_tickets_are_not_flagged(self):
        clean = asset(OpenTickets=0, CustomFieldValues=[])
        page, _ = self.look_up("104482", {"/assets/assettag/104482": items(clean), "/users/u-1": NO_FEES})
        self.assertContains(page, "Nothing owed")
        self.assertContains(page, "None open")
        self.assertNotContains(page, 'class="cell at"')

    def test_a_staff_device_is_not_called_a_student(self):
        staff = asset(Owner={"UserId": "u-2", "FullName": "Casey Teacher", "RoleName": "Faculty"})
        page, _ = self.look_up("104482", {"/assets/assettag/104482": items(staff)})
        self.assertContains(page, '<p class="muted">Faculty</p>')
        self.assertNotContains(page, "Student<")

    def test_an_unassigned_device(self):
        page, _ = self.look_up("104482", {"/assets/assettag/104482": items(asset(Owner=None))})
        self.assertContains(page, "not assigned to anyone")

    def test_not_found(self):
        page, _ = self.look_up("999999")
        self.assertContains(page, "Incident IQ has no device with that asset tag or serial number.")
        entry = LookupLog.objects.get()
        self.assertEqual((entry.query, entry.found, entry.user), ("999999", False, self.tech))
        self.assertNotContains(page, "Open lookups")

    def test_not_connected(self):
        IncidentIQConfig.objects.update(base_url="")
        page, fake = self.look_up("104482")
        self.assertContains(page, "Atlas is not connected to Incident IQ")
        self.assertEqual(fake.calls, [])

    def test_a_rejected_token_is_explained_and_recorded(self):
        page, _ = self.look_up("104482", {"/assets/serial/104482": FakeResponse(status=401)})
        self.assertContains(page, "Incident IQ rejected the API token.")
        self.assertEqual(IncidentIQConfig.load().state, "Error")

    def test_incident_iq_being_down_is_explained(self):
        page, _ = self.look_up("104482", fail=requests.ConnectionError())
        self.assertContains(page, "Could not reach Incident IQ at example.incidentiq.com.")

    def test_a_working_lookup_clears_an_earlier_error(self):
        self.look_up("104482", fail=requests.ConnectionError())
        self.look_up("104482")
        self.assertEqual(IncidentIQConfig.load().state, "Connected")

    def test_more_than_one_match_asks_which(self):
        twins = items(asset(), asset(AssetId="a-2", SerialNumber="OTHER99"))
        page, _ = self.look_up("104482", {"/assets/assettag/104482": twins})
        self.assertContains(page, "2 matches for")
        self.assertContains(page, "?q=OTHER99")

    def test_the_school_name_is_asked_for_once_then_remembered(self):
        _, first = self.look_up("104482")
        _, second = self.look_up("104482")
        self.assertIn(f"/locations/{SCHOOL_ID}", first.paths())
        self.assertNotIn(f"/locations/{SCHOOL_ID}", second.paths())

    def test_a_failed_school_lookup_does_not_sink_the_device(self):
        routes = dict(STANDARD)
        routes[f"/locations/{SCHOOL_ID}"] = FakeResponse(status=500)
        page, _ = self.look_up("104482", routes)
        self.assertContains(page, "Jordan Sample")

    def test_what_incident_iq_sends_is_shown_as_text_never_run(self):
        nasty = asset(Owner={"UserId": "u-3", "FullName": "<script>alert(1)</script>"})
        page, _ = self.look_up("104482", {"/assets/assettag/104482": items(nasty)})
        self.assertNotContains(page, "<script>alert(1)</script>")
        self.assertContains(page, "&lt;script&gt;alert(1)&lt;/script&gt;")

    def test_long_input_is_cut_off(self):
        self.look_up("x" * 500)
        self.assertEqual(len(LookupLog.objects.get().query), 100)


@override_settings(ATLAS_ENCRYPTION_KEY=TEST_KEY)
class TabsAndRecentsTests(TestCase):
    url = reverse("device360")

    def setUp(self):
        self.tech = User.objects.create_user("tech")
        self.client.force_login(self.tech)
        config = IncidentIQConfig.load()
        config.base_url = BASE
        config.set_token(TOKEN)
        config.save()
        other = asset(AssetId="a-9", AssetTag="104519", SerialNumber="9ZZ9ZZ9")
        other["Owner"] = dict(other["Owner"], FullName="Riley Demo")
        self.routes = dict(STANDARD)
        self.routes["/assets/assettag/104519"] = items(other)

    def look_up(self, query):
        with incident_iq(self.routes):
            return self.client.get(self.url, {"q": query})

    def test_each_found_device_opens_a_tab_once(self):
        self.look_up("104482")
        self.look_up("7XK2RT3")  # same device, by serial
        page = self.look_up("104519")
        self.assertEqual([tab["key"] for tab in self.client.session["open_lookups"]], ["104482", "104519"])
        self.assertContains(page, 'aria-current="true"', count=1)

    def test_closing_the_tab_being_viewed_moves_to_the_next(self):
        self.look_up("104482")
        self.look_up("104519")
        response = self.client.post(reverse("close-lookup"), {"key": "104519", "current": "104519"})
        self.assertRedirects(response, f"{self.url}?q=104482", fetch_redirect_response=False)
        self.assertEqual([tab["key"] for tab in self.client.session["open_lookups"]], ["104482"])

    def test_closing_another_tab_stays_put(self):
        self.look_up("104482")
        self.look_up("104519")
        response = self.client.post(reverse("close-lookup"), {"key": "104482", "current": "104519"})
        self.assertRedirects(response, f"{self.url}?q=104519", fetch_redirect_response=False)

    def test_closing_the_last_tab_goes_back_to_the_start(self):
        self.look_up("104482")
        response = self.client.post(reverse("close-lookup"), {"key": "104482", "current": "104482"})
        self.assertRedirects(response, self.url)

    def test_closing_needs_a_post_and_a_sign_in(self):
        self.assertEqual(self.client.get(reverse("close-lookup")).status_code, 405)
        self.client.logout()
        self.assertEqual(self.client.post(reverse("close-lookup"), {"key": "x"}).status_code, 302)

    def test_recent_lookups_are_your_own_newest_first_each_once(self):
        self.look_up("104482")
        self.look_up("104519")
        self.look_up("104482")
        self.look_up("999999")  # not found: not listed
        page = self.client.get(reverse("reports"))
        html = page.content.decode()
        self.assertContains(page, "Recent lookups")
        self.assertEqual(html.count("?q=104482"), 1)
        self.assertLess(html.index("?q=104482"), html.index("?q=104519"))
        self.assertContains(page, "Riley Demo")

        self.client.force_login(User.objects.create_user("someone-else"))
        self.assertNotContains(self.client.get(reverse("reports")), "Recent lookups")


@override_settings(ATLAS_ENCRYPTION_KEY=TEST_KEY)
class GoogleAndNetworkTests(TestCase):
    url = reverse("device360")

    def setUp(self):
        from django.core.cache import cache

        from integrations.google import client as google_client

        google_client._tokens.clear()
        cache.clear()
        self.client.force_login(User.objects.create_user("tech"))
        iiq = IncidentIQConfig.load()
        iiq.base_url = BASE
        iiq.set_token(TOKEN)
        iiq.save()

    def connect(self, google=True, mist=True, meraki=True):
        if google:
            config = GoogleConfig.load()
            config.admin_email = GOOGLE_ADMIN
            config.set_token(GOOGLE_KEY)
            config.save()
        if mist:
            config = MistConfig.load()
            config.org_id = MIST_ORG
            config.set_token("mist-token")
            config.save()
        if meraki:
            config = MerakiConfig.load()
            config.org_id = MERAKI_ORG
            config.set_token("meraki-key")
            config.save()

    def look_up(self, others=STANDARD_OTHERS):
        with incident_iq(STANDARD, others=others):
            return self.client.get(self.url, {"q": "104482"})

    def test_nothing_else_connected_reads_as_before(self):
        page = self.look_up()
        self.assertContains(page, "Needs Mist or Meraki", count=1)
        self.assertContains(page, "Not connected yet")
        self.assertContains(page, "once Google Workspace is connected")

    def test_everything_connected_fills_every_card(self):
        self.connect()
        page = self.look_up()
        for expected in [
            "/Students/North Middle",
            "student1@students.example",
            "Jun 2031",
            "North Middle School",
            "NMS-AP-214",
            "Students",
            "10.20.30.41",
            "District Office",
            "DO-AP-1",
            "Synced",
            "203.0.113.9",
            "Verified",
            "38% of 4.0 GB",
            "52% of 32.0 GB",
            "https://admin.google.com/ac/chrome/devices/g-device-1",
            "Incident IQ and Google agree",
        ]:
            self.assertContains(page, expected)
        self.assertNotContains(page, "student6@students.example")
        # Mist saw it more recently than Meraki, so Mist leads the network card.
        self.assertContains(page, "<b>North Middle School</b>")
        self.assertEqual(GoogleConfig.load().state, "Connected")

    def test_disagreements_with_google_are_listed(self):
        self.connect(mist=False, meraki=False)
        other = chromebook(annotatedAssetId="104428", recentUsers=[{"email": "someone@students.example"}])
        page = self.look_up(dict(STANDARD_OTHERS, **{GOOGLE_DEVICES_URL: FakeResponse(data={"chromeosdevices": [other]})}))
        self.assertContains(page, "Incident IQ and Google disagree")
        self.assertContains(page, "Google has asset ID 104428, but Incident IQ has asset tag 104482.")
        self.assertContains(page, "the last sign-in Google saw was someone@students.example")

    def test_the_connection_card_follows_the_access_point_to_its_switch_port(self):
        self.connect()
        search = f"{MERAKI_API}/organizations/{MERAKI_ORG}/clients/search"
        page = self.look_up(dict(STANDARD_OTHERS, **MERAKI_SWITCH_ROUTES, **{search: MERAKI_ON_SWITCH}))
        for expected in [
            "Mist policy: Chromebooks - Student",
            "Meraki policy: Students",
            "North-IDF-5, port 38",
            "The access point's uplink",
            "Trunk, native 71",
            "Allows all",
        ]:
            self.assertContains(page, expected)
        # The switch sighting does not take over "Where it is" from Mist.
        self.assertContains(page, "<b>North Middle School</b>")
        self.assertNotContains(page, "North-switch")

    def test_a_wired_device_shows_its_own_port(self):
        self.connect(mist=False)
        search = f"{MERAKI_API}/organizations/{MERAKI_ORG}/clients/search"
        page = self.look_up(dict(STANDARD_OTHERS, **MERAKI_SWITCH_ROUTES, **{search: MERAKI_ON_SWITCH}))
        self.assertContains(page, "by Meraki, wired")
        self.assertContains(page, "The device's own port")
        self.assertContains(page, "VLAN 355")

    def test_a_switch_lookup_problem_is_shown_on_the_connection_card(self):
        self.connect()
        search = f"{MERAKI_API}/organizations/{MERAKI_ORG}/clients/search"
        routes = dict(STANDARD_OTHERS, **MERAKI_SWITCH_ROUTES, **{search: MERAKI_ON_SWITCH})
        routes[f"{MERAKI_API}/devices/Q2KW-0000-0001/switch/ports/38"] = FakeResponse(status=500)
        page = self.look_up(routes)
        self.assertContains(page, "Meraki: Meraki had an internal error")
        self.assertContains(page, "<b>North Middle School</b>")

    def test_a_google_problem_does_not_sink_the_lookup(self):
        self.connect()
        page = self.look_up(dict(STANDARD_OTHERS, **{GOOGLE_DEVICES_URL: FakeResponse(status=500)}))
        self.assertContains(page, "Jordan Sample")
        self.assertContains(page, "Google had an internal error (HTTP 500).")
        self.assertContains(page, "Atlas finds devices on the network by the MAC address", count=2)
        self.assertEqual(GoogleConfig.load().state, "Error")

    def test_a_chromebook_google_does_not_have(self):
        self.connect(mist=False, meraki=False)
        page = self.look_up(dict(STANDARD_OTHERS, **{GOOGLE_DEVICES_URL: FakeResponse(data={})}))
        self.assertContains(page, "Google Workspace has no Chromebook with serial")

    def test_not_seen_on_the_network(self):
        self.connect(meraki=False)
        page = self.look_up(
            dict(STANDARD_OTHERS, **{f"{MIST_API}/orgs/{MIST_ORG}/clients/search": FakeResponse(data={"results": []})})
        )
        self.assertContains(page, "Mist has not seen this device in the last week.")
        self.assertContains(page, "Not seen on the network")

    def test_a_network_problem_is_shown_on_the_card(self):
        self.connect(mist=False)
        page = self.look_up(dict(STANDARD_OTHERS, **{f"{MERAKI_API}/organizations/{MERAKI_ORG}/clients/search": FakeResponse(status=401)}))
        self.assertContains(page, "Meraki: Meraki rejected the API key")
        self.assertEqual(MerakiConfig.load().state, "Error")
