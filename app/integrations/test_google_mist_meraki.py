import base64
import json

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from .google import client as google_client
from .google.client import GoogleClient, GoogleError, read_key
from .google.normalize import normalize_chromebook
from .meraki.client import MerakiClient, MerakiError
from .meraki.normalize import normalize_client as meraki_sighting
from .mist.client import MistClient, MistError, api_host
from .models import GoogleConfig, MerakiConfig, MistConfig
from .network import mac_plain
from .testing import (
    GOOGLE_ADMIN,
    GOOGLE_DEVICES_URL,
    GOOGLE_KEY,
    GOOGLE_PRIVATE_KEY,
    GOOGLE_TOKEN_URL,
    MERAKI_API,
    MERAKI_ORG,
    MIST_API,
    MIST_ORG,
    STANDARD_OTHERS,
    TEST_KEY,
    FakeResponse,
    chromebook,
    incident_iq,
)

User = get_user_model()


def with_(**changes):
    return {**STANDARD_OTHERS, **changes}


def unpad(part):
    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))


class Fresh:
    def setUp(self):
        google_client._tokens.clear()
        cache.clear()


class GoogleTests(Fresh, SimpleTestCase):
    def client_(self):
        return GoogleClient(GOOGLE_KEY, GOOGLE_ADMIN)

    def test_a_key_that_is_not_a_service_account_key_is_refused(self):
        bad_private = json.dumps({"type": "service_account", "client_email": "x@y", "private_key": "nope"})
        for text in ("", "{", "[]", '{"type": "authorized_user"}', '{"type": "service_account"}', bad_private):
            with self.assertRaises(ValueError, msg=text):
                read_key(text)

    def test_signs_in_as_the_admin_with_only_the_chrome_devices_scope(self):
        with incident_iq(others=STANDARD_OTHERS) as fake:
            self.client_().check()
        method, url, kwargs = fake.calls[0]
        self.assertEqual((method, url), ("POST", GOOGLE_TOKEN_URL))
        header, claims, signature = kwargs["data"]["assertion"].split(".")
        claims = json.loads(unpad(claims))
        self.assertEqual(claims["sub"], GOOGLE_ADMIN)
        self.assertEqual(claims["scope"], "https://www.googleapis.com/auth/admin.directory.device.chromeos.readonly")
        GOOGLE_PRIVATE_KEY.public_key().verify(
            unpad(signature), f"{header}.{kwargs['data']['assertion'].split('.')[1]}".encode(),
            padding.PKCS1v15(), hashes.SHA256(),
        )
        self.assertEqual(fake.calls[1][2]["headers"]["Authorization"], "Bearer google-access-not-real")

    def test_signs_in_once_then_reuses_the_access_token(self):
        with incident_iq(others=STANDARD_OTHERS) as fake:
            self.client_().check()
            self.client_().find_chromebook("7XK2RT3")
        self.assertEqual(fake.urls().count(GOOGLE_TOKEN_URL), 1)

    def test_sign_in_problems_say_what_to_fix(self):
        cases = {
            "unauthorized_client": "domain-wide delegation",
            "invalid_grant": GOOGLE_ADMIN,
            "invalid_client": "Download a new key",
        }
        for code, expected in cases.items():
            google_client._tokens.clear()
            answer = FakeResponse(status=401 if code == "invalid_client" else 400, data={"error": code})
            with incident_iq(others=with_(**{GOOGLE_TOKEN_URL: answer})), self.assertRaises(GoogleError) as caught:
                self.client_().check()
            self.assertIn(expected, str(caught.exception))

    def test_an_admin_without_rights_is_explained(self):
        with incident_iq(others=with_(**{GOOGLE_DEVICES_URL: FakeResponse(status=403)})), self.assertRaises(
            GoogleError
        ) as caught:
            self.client_().check()
        self.assertIn("can view Chrome devices", str(caught.exception))

    def test_only_the_exact_serial_counts(self):
        answer = FakeResponse(data={"chromeosdevices": [chromebook(serialNumber="7XK2RT3X"), chromebook()]})
        with incident_iq(others=with_(**{GOOGLE_DEVICES_URL: answer})) as fake:
            found = self.client_().find_chromebook(" 7xk2rt3 ")
        self.assertEqual(found["serialNumber"], "7XK2RT3")
        self.assertEqual(fake.calls[-1][2]["params"]["query"], "id:7xk2rt3")
        with incident_iq(others=with_(**{GOOGLE_DEVICES_URL: FakeResponse(data={})})):
            self.assertIsNone(self.client_().find_chromebook("7XK2RT3"))
            self.assertIsNone(self.client_().find_chromebook('7XK" OR x'))

    def test_normalize(self):
        book = normalize_chromebook(chromebook(ethernetMacAddress="AA:BB:CC:DD:EE:FF"))
        self.assertEqual((book.status, book.active, book.org_unit), ("Active", True, "/Students/North Middle"))
        self.assertEqual(book.macs, ["a1b2c3d4e5f6", "aabbccddeeff"])
        self.assertEqual(len(book.recent_users), 5)
        self.assertEqual((book.last_sync.year, book.updates_until.year, book.last_ip), (2026, 2031, "10.20.30.40"))
        empty = normalize_chromebook({"status": "DISABLED", "lastKnownNetwork": [], "lastSync": "soon"})
        self.assertEqual((empty.status, empty.active, empty.macs, empty.last_sync), ("Disabled", False, [], None))


class MistTests(Fresh, SimpleTestCase):
    def test_portal_address_becomes_the_api_host(self):
        for pasted, host in [
            ("", "api.mist.com"),
            ("manage.mist.com", "api.mist.com"),
            ("https://manage.gc1.mist.com/admin/?org_id=x", "api.gc1.mist.com"),
            ("api.eu.mist.com", "api.eu.mist.com"),
        ]:
            self.assertEqual(api_host(pasted), host, pasted)
        for pasted in ("evil.example.com", "mist.com.evil.example"):
            with self.assertRaises(ValueError):
                api_host(pasted)

    def test_check_finds_the_one_organization(self):
        with incident_iq(others=STANDARD_OTHERS) as fake:
            self.assertEqual(MistClient("api.mist.com", "mist-token").check(), (MIST_ORG, "Example Schools"))
        self.assertEqual(fake.calls[0][2]["headers"]["Authorization"], "Token mist-token")

    def test_check_with_the_wrong_or_several_organizations(self):
        with incident_iq(others=STANDARD_OTHERS), self.assertRaises(MistError):
            MistClient("api.mist.com", "t", org_id="ffffffff-0000-0000-0000-000000000000").check()
        several = FakeResponse(data={"privileges": [{"scope": "org", "org_id": "a"}, {"scope": "org", "org_id": "b"}]})
        with incident_iq(others=with_(**{f"{MIST_API}/self": several})), self.assertRaises(MistError) as caught:
            MistClient("api.mist.com", "t").check()
        self.assertIn("Enter the Org ID", str(caught.exception))
        with incident_iq(others=with_(**{f"{MIST_API}/self": FakeResponse(status=401)})), self.assertRaises(MistError):
            MistClient("api.mist.com", "t").check()

    def test_the_latest_record_wins_and_names_are_remembered(self):
        older = {"site_id": "old", "timestamp": 100}
        newer = {"site_id": "new", "timestamp": 200}
        answer = FakeResponse(data={"results": [older, newer]})
        client = MistClient("api.mist.com", "t", org_id=MIST_ORG)
        with incident_iq(others=with_(**{f"{MIST_API}/orgs/{MIST_ORG}/clients/search": answer})) as fake:
            self.assertEqual(client.find_client("a1b2c3d4e5f6")["site_id"], "new")
            client.site_name("5f4e3d2c-1b0a-4987-8654-3210fedcba98")
            client.site_name("5f4e3d2c-1b0a-4987-8654-3210fedcba98")
        self.assertEqual(fake.calls[0][2]["params"]["duration"], "7d")
        self.assertEqual(len(fake.calls), 2)


class MerakiTests(Fresh, SimpleTestCase):
    def test_check_finds_the_one_organization(self):
        with incident_iq(others=STANDARD_OTHERS) as fake:
            self.assertEqual(MerakiClient("meraki-key").check(), (MERAKI_ORG, "Example Schools"))
        self.assertEqual(fake.calls[0][2]["headers"]["Authorization"], "Bearer meraki-key")
        with incident_iq(others=STANDARD_OTHERS), self.assertRaises(MerakiError):
            MerakiClient("k", org_id="999").check()

    def test_a_mac_meraki_never_saw_is_not_an_error(self):
        with incident_iq(others={}) as fake:
            self.assertIsNone(MerakiClient("k", org_id=MERAKI_ORG).find_client("a1b2c3d4e5f6"))
        self.assertEqual(fake.calls[0][2]["params"]["mac"], "a1:b2:c3:d4:e5:f6")

    def test_the_most_recent_network_wins(self):
        seen = meraki_sighting(
            {
                "records": [
                    {"network": {"name": "Old"}, "lastSeen": 1700000000},
                    {"network": {"name": "New"}, "lastSeen": "2026-11-01T09:00:00Z", "recentDeviceSerial": "Q2XX"},
                    {"network": {"name": "Never"}, "lastSeen": None},
                ]
            }
        )
        self.assertEqual((seen.place, seen.access_point, seen.source), ("New", "Q2XX", "Meraki"))
        self.assertIsNone(meraki_sighting({"records": []}))

    def test_mac_plain(self):
        self.assertEqual(mac_plain("A1:B2:C3:D4:E5:F6"), "a1b2c3d4e5f6")
        for junk in (None, "", "a1b2", "zz:zz:zz:zz:zz:zz"):
            self.assertEqual(mac_plain(junk), "")


@override_settings(ATLAS_ENCRYPTION_KEY=TEST_KEY)
class SettingsPagesTests(Fresh, TestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(User.objects.create_superuser("admin"))

    def post(self, name, others=STANDARD_OTHERS, **data):
        with incident_iq(others=others):
            return self.client.post(reverse(name), data, follow=True)

    def test_administrators_only(self):
        self.client.force_login(User.objects.create_user("tech"))
        for name in ("settings-google", "settings-mist", "settings-meraki"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 403)

    def test_google_save_and_test(self):
        page = self.post("settings-google", admin_email=GOOGLE_ADMIN, token=GOOGLE_KEY)
        self.assertContains(page, f"Connected. Google lets Atlas read Chrome devices as {GOOGLE_ADMIN}.")
        self.assertContains(page, "atlas@example-project.iam.gserviceaccount.com")
        self.assertNotContains(page, "PRIVATE KEY")
        config = GoogleConfig.load()
        self.assertEqual(config.state, "Connected")
        self.assertNotIn("PRIVATE KEY", config.token_encrypted)
        # Leaving the key empty keeps it.
        page = self.post("settings-google", admin_email=GOOGLE_ADMIN)
        self.assertContains(page, "Connected.")

    def test_google_refuses_a_key_that_is_not_one(self):
        page = self.post("settings-google", admin_email=GOOGLE_ADMIN, token="hello")
        self.assertContains(page, "That is not a JSON key file.")
        self.assertFalse(GoogleConfig.load().configured)

    def test_google_failure_is_saved_and_explained(self):
        refused = with_(**{GOOGLE_TOKEN_URL: FakeResponse(status=401, data={"error": "unauthorized_client"})})
        page = self.post("settings-google", others=refused, admin_email=GOOGLE_ADMIN, token=GOOGLE_KEY)
        self.assertContains(page, "Saved, but the connection test failed. Google has not given")
        self.assertEqual(GoogleConfig.load().state, "Error")

    def test_mist_saves_the_organization_it_found(self):
        page = self.post("settings-mist", api_host="manage.mist.com", token="mist-token")
        self.assertContains(page, "Connected to Example Schools.")
        config = MistConfig.load()
        self.assertEqual((config.api_host, config.org_id, config.state), ("api.mist.com", MIST_ORG, "Connected"))
        self.assertNotContains(page, "mist-token")

    def test_meraki_saves_the_organization_it_found(self):
        page = self.post("settings-meraki", token="meraki-key")
        self.assertContains(page, "Connected to Example Schools.")
        self.assertEqual(MerakiConfig.load().org_id, MERAKI_ORG)
        page = self.post("settings-meraki", token="meraki-key", org_id="not a number")
        self.assertContains(page, "A Meraki Org ID is a number.")

    def test_the_sidebar_lists_every_system(self):
        self.post("settings-meraki", token="meraki-key")
        page = self.client.get(reverse("settings"))
        for name in ("settings-incidentiq", "settings-google", "settings-mist", "settings-meraki"):
            self.assertContains(page, f'href="{reverse(name)}"')
