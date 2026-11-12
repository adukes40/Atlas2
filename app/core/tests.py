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

    def test_default_theme_is_neutral(self):
        response = self.client.get(reverse("device360"))
        self.assertContains(response, "core/themes/default.css")
        self.assertContains(response, "Your Organization")

    def test_administrator_can_rename_and_retheme(self):
        response = self.client.post(
            reverse("settings"), {"name": "Example District", "theme": "caesar-rodney"}
        )
        self.assertRedirects(response, reverse("settings"))
        page = self.client.get(reverse("device360"))
        self.assertContains(page, "core/themes/caesar-rodney.css")
        self.assertNotContains(page, "core/themes/default.css")
        self.assertContains(page, "Example District")
        self.assertEqual(Organization.objects.count(), 1)

    def test_unknown_theme_is_rejected(self):
        response = self.client.post(reverse("settings"), {"name": "X", "theme": "nope"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Organization.load().theme, "default")

    def test_a_stored_theme_that_no_longer_exists_falls_back(self):
        Organization.objects.update_or_create(pk=1, defaults={"theme": "removed"})
        self.assertContains(self.client.get(reverse("device360")), "core/themes/default.css")


class AppearanceTests(TestCase):
    def setUp(self):
        self.client.force_login(User.objects.create_user("tech"))

    def test_dark_mode_is_remembered(self):
        response = self.client.post(reverse("appearance"), {"mode": "dark", "next": "/reports/"})
        self.assertRedirects(response, "/reports/")
        self.assertContains(self.client.get(reverse("reports")), 'data-mode="dark"')

    def test_auto_forgets_the_choice(self):
        self.client.post(reverse("appearance"), {"mode": "dark"})
        self.client.post(reverse("appearance"), {"mode": "system"})
        self.assertNotContains(self.client.get(reverse("reports")), "data-mode=")

    def test_unknown_mode_is_ignored(self):
        self.client.post(reverse("appearance"), {"mode": "<script>"})
        self.assertNotContains(self.client.get(reverse("reports")), "data-mode=")

    def test_cannot_redirect_off_site(self):
        response = self.client.post(
            reverse("appearance"), {"mode": "dark", "next": "https://evil.example/"}
        )
        self.assertRedirects(response, "/", fetch_redirect_response=False)

    def test_get_is_not_allowed(self):
        self.assertEqual(self.client.get(reverse("appearance")).status_code, 405)
