from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import Organization

User = get_user_model()
PAGES = ["device360", "reports", "settings", "system"]


class SignInTests(TestCase):
    def test_pages_require_sign_in(self):
        for name in PAGES:
            response = self.client.get(reverse(name))
            self.assertRedirects(response, f"{reverse('login')}?next={reverse(name)}")

    def test_health_check_is_public(self):
        response = self.client.get(reverse("healthz"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True})

    def test_sign_in_lands_on_device_360(self):
        User.objects.create_user("tech", password="a-long-test-password")
        response = self.client.post(
            reverse("login"), {"username": "tech", "password": "a-long-test-password"}
        )
        self.assertRedirects(response, reverse("device360"))

    def test_wrong_password_shows_a_plain_message(self):
        User.objects.create_user("tech", password="a-long-test-password")
        response = self.client.post(reverse("login"), {"username": "tech", "password": "nope"})
        self.assertContains(response, "did not match")

    def test_sign_out_needs_a_post(self):
        self.client.force_login(User.objects.create_user("tech"))
        self.assertEqual(self.client.get(reverse("logout")).status_code, 405)
        self.assertRedirects(self.client.post(reverse("logout")), reverse("login"))


class ShellTests(TestCase):
    def setUp(self):
        self.tech = User.objects.create_user("tech")
        self.admin = User.objects.create_superuser("admin")

    def test_every_page_renders_for_an_administrator(self):
        self.client.force_login(self.admin)
        for name in PAGES:
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)

    def test_settings_is_administrators_only(self):
        self.client.force_login(self.tech)
        self.assertEqual(self.client.get(reverse("settings")).status_code, 403)
        self.assertNotContains(self.client.get(reverse("device360")), reverse("settings"))

    def test_lookup_says_nothing_is_connected_and_escapes_input(self):
        self.client.force_login(self.tech)
        response = self.client.get(reverse("device360"), {"q": "<b>104482</b>"})
        self.assertContains(response, "not connected to Incident IQ")
        self.assertContains(response, "&lt;b&gt;104482&lt;/b&gt;")
        self.assertNotContains(response, "<b>104482</b>")


class ThemeTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser("admin")
        self.client.force_login(self.admin)

    def test_default_theme_is_graphite_and_dark(self):
        response = self.client.get(reverse("device360"))
        self.assertContains(response, "core/themes/graphite.css")
        self.assertContains(response, 'data-mode="dark"')
        self.assertContains(response, "Your Organization")

    def test_other_themes_follow_the_device_until_told_otherwise(self):
        Organization.objects.update_or_create(pk=1, defaults={"theme": "slate"})
        response = self.client.get(reverse("device360"))
        self.assertContains(response, "core/themes/slate.css")
        self.assertNotContains(response, "data-mode=")

    def test_administrator_can_rename_and_retheme(self):
        response = self.client.post(
            reverse("settings"), {"name": "Example District", "theme": "slate"}
        )
        self.assertRedirects(response, reverse("settings"))
        page = self.client.get(reverse("device360"))
        self.assertContains(page, "core/themes/slate.css")
        self.assertNotContains(page, "core/themes/graphite.css")
        self.assertContains(page, "Example District")
        self.assertEqual(Organization.objects.count(), 1)

    def test_unknown_theme_is_rejected(self):
        response = self.client.post(reverse("settings"), {"name": "X", "theme": "nope"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Organization.load().theme, "graphite")

    def test_a_stored_theme_that_no_longer_exists_falls_back(self):
        Organization.objects.update_or_create(pk=1, defaults={"theme": "removed"})
        self.assertContains(self.client.get(reverse("device360")), "core/themes/graphite.css")


class AppearanceTests(TestCase):
    def setUp(self):
        self.client.force_login(User.objects.create_user("tech"))

    def test_light_mode_is_remembered(self):
        response = self.client.post(reverse("appearance"), {"mode": "light", "next": "/reports/"})
        self.assertRedirects(response, "/reports/")
        page = self.client.get(reverse("reports"))
        self.assertContains(page, 'data-mode="light"')
        self.assertContains(page, 'name="mode" value="dark"')  # the one button now offers dark

    def test_auto_overrides_the_theme_default_and_follows_the_device(self):
        self.client.post(reverse("appearance"), {"mode": "auto"})
        page = self.client.get(reverse("reports"))
        self.assertNotContains(page, "data-mode=")
        self.assertContains(page, 'value="dark" data-auto="1"')  # atlas.js offers light on a dark device

    def test_unknown_mode_is_ignored(self):
        self.client.post(reverse("appearance"), {"mode": "<script>"})
        self.assertNotIn("atlas_mode", self.client.cookies)
        self.assertContains(self.client.get(reverse("reports")), 'data-mode="dark"')

    def test_the_button_offers_the_other_mode(self):
        page = self.client.get(reverse("reports"))
        self.assertContains(page, 'data-mode="dark"')
        self.assertContains(page, 'name="mode" value="light" data-auto=""')  # one button: switch to light

    def test_cannot_redirect_off_site(self):
        response = self.client.post(
            reverse("appearance"), {"mode": "dark", "next": "https://evil.example/"}
        )
        self.assertRedirects(response, "/", fetch_redirect_response=False)

    def test_get_is_not_allowed(self):
        self.assertEqual(self.client.get(reverse("appearance")).status_code, 405)


class UsersTests(TestCase):
    PASSWORD = "lantern-orchard-91"

    def setUp(self):
        self.admin = User.objects.create_superuser("admin", password=self.PASSWORD)
        self.tech = User.objects.create_user("tech", password=self.PASSWORD)
        self.client.force_login(self.admin)

    def add(self, **extra):
        data = {"username": "newtech", "first_name": "Pat", "password1": self.PASSWORD,
                "password2": self.PASSWORD, "is_active": "on", **extra}
        return self.client.post(reverse("user-add"), data)

    def test_users_is_administrators_only(self):
        self.client.force_login(self.tech)
        for url in (reverse("users"), reverse("user-add"), reverse("user-edit", args=[self.admin.pk])):
            self.assertEqual(self.client.get(url).status_code, 403, url)

    def test_list_shows_everyone_with_their_role(self):
        response = self.client.get(reverse("users"))
        self.assertContains(response, "Administrator")
        self.assertContains(response, "Technician")
        self.assertContains(self.client.get(reverse("settings")), "2 people can sign in")

    def test_add_a_technician_who_can_then_sign_in(self):
        self.assertRedirects(self.add(), reverse("users"))
        person = User.objects.get(username="newtech")
        self.assertFalse(person.is_superuser or person.is_staff)
        self.client.logout()
        self.assertTrue(self.client.login(username="newtech", password=self.PASSWORD))

    def test_new_user_needs_a_matching_strong_password(self):
        self.assertContains(self.add(password1="", password2=""), "Set a password")
        self.assertContains(self.add(password2="something-else-77"), "do not match")
        self.assertContains(self.add(password1="12345678", password2="12345678"), "too common")
        self.assertFalse(User.objects.filter(username="newtech").exists())

    def test_edit_keeps_password_when_left_blank_and_can_turn_off(self):
        url = reverse("user-edit", args=[self.tech.pk])
        self.assertRedirects(self.client.post(url, {"username": "tech"}), reverse("users"))
        self.tech.refresh_from_db()
        self.assertFalse(self.tech.is_active)
        self.assertTrue(self.tech.check_password(self.PASSWORD))

    def test_reset_someone_elses_password(self):
        url = reverse("user-edit", args=[self.tech.pk])
        self.client.post(url, {"username": "tech", "is_active": "on", "password1": "harbor-violet-28", "password2": "harbor-violet-28"})
        self.tech.refresh_from_db()
        self.assertTrue(self.tech.check_password("harbor-violet-28"))

    def test_cannot_lock_yourself_out(self):
        url = reverse("user-edit", args=[self.admin.pk])
        response = self.client.post(url, {"username": "admin", "is_active": "on"})
        self.assertContains(response, "your own administrator access")
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_superuser)

    def test_changing_your_own_password_keeps_you_signed_in(self):
        url = reverse("user-edit", args=[self.admin.pk])
        self.client.post(url, {"username": "admin", "is_active": "on", "is_superuser": "on",
                               "password1": "harbor-violet-28", "password2": "harbor-violet-28"})
        self.assertEqual(self.client.get(reverse("users")).status_code, 200)


class WanIpTests(TestCase):
    def test_an_address_is_ours_when_it_equals_starts_with_or_falls_in_an_entry(self):
        organization = Organization(wan_ips="203.0.113.9\n198.51.100\n10.1.\n192.0.2.0/24")
        for ip, ours in [("203.0.113.9", True), ("203.0.113.99", False), ("198.51.100.7", True), ("198.51.1000.1", False),
                         ("10.1.4.4", True), ("10.10.4.4", False), ("192.0.2.200", True), ("192.0.3.1", False)]:
            self.assertEqual(organization.on_network(ip), ours, ip)
        self.assertIsNone(organization.on_network(""))
        self.assertIsNone(Organization(wan_ips="").on_network("203.0.113.9"))

    def test_the_setting_is_saved_one_per_line_and_checked(self):
        admin = User.objects.create_superuser("boss", password="x")
        self.client.force_login(admin)
        self.client.post(reverse("settings"), {"name": "Example Schools", "theme": "graphite",
                                               "wan_ips": " 203.0.113.9 \n\n192.0.2.0/24"})
        self.assertEqual(Organization.load().wan_ips, "203.0.113.9\n192.0.2.0/24")
        bad = self.client.post(reverse("settings"), {"name": "Example Schools", "theme": "graphite", "wan_ips": "school wifi"})
        self.assertContains(bad, "is not an address or the start of one")
        self.assertEqual(Organization.load().wan_ips, "203.0.113.9\n192.0.2.0/24")
