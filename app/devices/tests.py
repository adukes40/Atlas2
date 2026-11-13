import requests
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from integrations.models import IncidentIQConfig
from integrations.testing import BASE, SCHOOL_ID, STANDARD, TEST_KEY, TOKEN, FakeResponse, asset, incident_iq, items

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
        ]:
            self.assertContains(page, expected)
        self.assertContains(page, 'class="cell at"', count=2)

    def test_nothing_owed_and_no_tickets_are_not_flagged(self):
        clean = asset(OpenTickets=0, CustomFieldValues=[])
        page, _ = self.look_up("104482", {"/assets/assettag/104482": items(clean)})
        self.assertContains(page, "Nothing owed")
        self.assertContains(page, "None open")
        self.assertNotContains(page, 'class="cell at"')

    def test_a_staff_device_is_not_called_a_student(self):
        staff = asset(Owner={"UserId": "u-2", "FullName": "Casey Teacher", "RoleName": "Faculty"})
        page, _ = self.look_up("104482", {"/assets/assettag/104482": items(staff)})
        self.assertContains(page, "<h2>Assigned to</h2>")

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
