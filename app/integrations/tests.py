from decimal import Decimal

import requests
from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from . import crypto
from .incidentiq.client import (
    AuthFailed,
    BadResponse,
    IncidentIQClient,
    RateLimited,
    Unreachable,
    normalize_url,
)
from .incidentiq.normalize import normalize_asset
from .models import IncidentIQConfig
from .testing import BASE, STANDARD, TEST_KEY, TOKEN, FakeResponse, asset, incident_iq, items

User = get_user_model()


class UrlTests(SimpleTestCase):
    def test_what_people_paste_becomes_a_plain_site_address(self):
        for pasted in [
            "example.incidentiq.com",
            "https://example.incidentiq.com/",
            "  https://example.incidentiq.com/agent/assets  ",
            "https://example.incidentiq.com/api/v1.0",
        ]:
            self.assertEqual(normalize_url(pasted), BASE, pasted)

    def test_refuses_what_cannot_be_a_site(self):
        for pasted in ["", "http://example.incidentiq.com", "localhost", "not a url"]:
            with self.assertRaises(ValueError, msg=pasted):
                normalize_url(pasted)


class ClientTests(SimpleTestCase):
    def client_(self):
        return IncidentIQClient(BASE, TOKEN)

    def test_check_counts_assets_and_sends_the_token_as_a_bearer(self):
        with incident_iq(STANDARD) as fake:
            self.assertEqual(self.client_().check(), 12345)
        method, url, kwargs = fake.calls[0]
        self.assertEqual((method, url), ("POST", f"{BASE}/api/v1.0/assets"))
        self.assertEqual(kwargs["headers"]["Authorization"], f"Bearer {TOKEN}")
        self.assertEqual(kwargs["json"]["Paging"]["PageSize"], 1)
        self.assertTrue(kwargs["timeout"])

    def test_serial_is_tried_first_and_a_hit_stops_there(self):
        with incident_iq(STANDARD) as fake:
            found = self.client_().find_assets("7XK2RT3")
        self.assertEqual(len(found), 1)
        self.assertEqual(fake.paths(), ["/assets/serial/7XK2RT3"])

    def test_asset_tag_is_tried_when_the_serial_misses(self):
        with incident_iq(STANDARD) as fake:
            found = self.client_().find_assets(" 104482 ")
        self.assertEqual(found[0]["AssetTag"], "104482")
        self.assertEqual(fake.paths(), ["/assets/serial/104482", "/assets/assettag/104482"])

    def test_not_found_is_an_empty_list_not_an_error(self):
        with incident_iq(STANDARD):
            self.assertEqual(self.client_().find_assets("nope"), [])
            self.assertEqual(self.client_().find_assets("   "), [])

    def test_odd_characters_cannot_change_the_request_path(self):
        with incident_iq() as fake:
            self.client_().find_assets("../users?x=1 2")
        self.assertEqual(fake.paths()[0], "/assets/serial/..%2Fusers%3Fx%3D1%202")

    def test_problems_become_plain_messages(self):
        cases = [
            ({"/assets": FakeResponse(status=401)}, None, AuthFailed),
            ({"/assets": FakeResponse(status=403)}, None, AuthFailed),
            ({"/assets": FakeResponse(status=429)}, None, RateLimited),
            ({"/assets": FakeResponse(status=404)}, None, BadResponse),
            ({"/assets": FakeResponse(status=500)}, None, BadResponse),
            ({"/assets": FakeResponse(text="<html>Sign in</html>")}, None, BadResponse),
            ({"/assets": FakeResponse(data=["not", "an", "object"])}, None, BadResponse),
            (None, requests.Timeout(), Unreachable),
            (None, requests.ConnectionError(), Unreachable),
        ]
        for routes, fail, expected in cases:
            with incident_iq(routes, fail), self.assertRaises(expected) as caught:
                self.client_().check()
            self.assertNotIn(TOKEN, str(caught.exception))


class NormalizeTests(SimpleTestCase):
    def test_a_full_asset(self):
        device = normalize_asset(asset())
        self.assertEqual((device.asset_tag, device.serial), ("104482", "7XK2RT3"))
        self.assertEqual((device.model, device.category), ("Dell Chromebook 3110", "Chromebook"))
        self.assertEqual((device.status, device.location), ("In Service", "North Middle School"))
        self.assertEqual(device.open_tickets, 1)
        self.assertEqual(device.owner.name, "Jordan Sample")
        self.assertEqual((device.owner.grade, device.owner.homeroom), ("7", "B214"))
        self.assertEqual(device.key, "104482")
        self.assertEqual(device.label, "Jordan Sample")

    def test_the_same_charge_listed_twice_counts_once(self):
        fees = normalize_asset(asset()).fees
        self.assertEqual(fees.balance, Decimal("25"))
        self.assertEqual(fees.past_due, Decimal("10"))
        self.assertEqual(fees.balance_display, "$25.00")
        self.assertTrue(fees.owed)

    def test_no_fee_field_means_nothing_owed(self):
        for values in (None, [], [{"Value": "Balance due soon"}], [{"Value": '{"Balance": 5}'}], [{"Value": 7}]):
            fees = normalize_asset(asset(CustomFieldValues=values)).fees
            self.assertFalse(fees.owed, values)

    def test_an_unassigned_device(self):
        for owner in (None, {}, {"UserId": "00000000-0000-0000-0000-000000000000"}):
            device = normalize_asset(asset(Owner=owner))
            self.assertIsNone(device.owner)
            self.assertEqual(device.label, "Dell Chromebook 3110")

    def test_missing_pieces_do_not_break_it(self):
        device = normalize_asset({"SerialNumber": "ONLY-SERIAL", "OpenTickets": "many", "Model": None})
        self.assertEqual((device.key, device.model, device.open_tickets), ("ONLY-SERIAL", "", 0))


class CryptoTests(SimpleTestCase):
    @override_settings(ATLAS_ENCRYPTION_KEY=TEST_KEY)
    def test_round_trip_and_not_readable_as_stored(self):
        stored = crypto.encrypt("secret-value")
        self.assertNotIn("secret-value", stored)
        self.assertEqual(crypto.decrypt(stored), "secret-value")

    def test_a_different_key_cannot_read_it(self):
        with override_settings(ATLAS_ENCRYPTION_KEY=TEST_KEY):
            stored = crypto.encrypt("secret-value")
        with override_settings(ATLAS_ENCRYPTION_KEY=Fernet.generate_key().decode()):
            self.assertIsNone(crypto.decrypt(stored))

    def test_no_key_or_a_bad_key_refuses_to_store(self):
        for key in ("", "not-a-key"):
            with override_settings(ATLAS_ENCRYPTION_KEY=key), self.assertRaises(crypto.EncryptionNotConfigured):
                crypto.encrypt("secret-value")


@override_settings(ATLAS_ENCRYPTION_KEY=TEST_KEY)
class SettingsPageTests(TestCase):
    url = reverse("settings-incidentiq")

    def setUp(self):
        self.admin = User.objects.create_superuser("admin")
        self.client.force_login(self.admin)

    def save(self, **data):
        data.setdefault("base_url", "example.incidentiq.com")
        return self.client.post(self.url, data, follow=True)

    def test_administrators_only(self):
        self.client.force_login(User.objects.create_user("tech"))
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_save_and_test_connects(self):
        with incident_iq(STANDARD):
            page = self.save(token=TOKEN)
        self.assertContains(page, "Connected. Incident IQ reports 12,345 assets.")
        config = IncidentIQConfig.load()
        self.assertEqual((config.base_url, config.state), (BASE, "Connected"))
        self.assertContains(self.client.get(reverse("device360")), '<span class="on">Connected</span>')

    def test_the_token_is_encrypted_at_rest_and_never_sent_back(self):
        with incident_iq(STANDARD):
            page = self.save(token=TOKEN)
        self.assertNotIn(TOKEN, IncidentIQConfig.load().token_encrypted)
        self.assertNotContains(page, TOKEN)
        self.assertNotContains(self.client.get(self.url), TOKEN)
        self.assertContains(page, "A token is saved")

    def test_a_failed_test_is_saved_and_explained(self):
        with incident_iq({"/assets": FakeResponse(status=401)}):
            page = self.save(token="wrong")
        self.assertContains(page, "Saved, but the connection test failed. Incident IQ rejected the API token.")
        self.assertEqual(IncidentIQConfig.load().state, "Error")

    def test_leaving_the_token_empty_keeps_the_saved_one(self):
        with incident_iq(STANDARD) as fake:
            self.save(token=TOKEN)
            self.save(base_url="https://example.incidentiq.com/")
        self.assertEqual(fake.calls[-1][2]["headers"]["Authorization"], f"Bearer {TOKEN}")

    def test_a_token_is_required_the_first_time(self):
        page = self.save()
        self.assertContains(page, "Paste the API token from Incident IQ.")
        self.assertFalse(IncidentIQConfig.load().configured)

    def test_a_bad_address_is_refused(self):
        page = self.save(base_url="http://example.incidentiq.com", token=TOKEN)
        self.assertContains(page, "The address must start with https://")

    @override_settings(ATLAS_ENCRYPTION_KEY="")
    def test_without_an_encryption_key_nothing_is_stored(self):
        page = self.save(token=TOKEN)
        self.assertContains(page, "Run sudo ./install.sh on the server again")
        self.assertFalse(IncidentIQConfig.load().has_token)
