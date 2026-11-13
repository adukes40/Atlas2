import re

from django import forms

from .google.client import read_key
from .incidentiq.client import normalize_url
from .mist.client import api_host


class IncidentIQForm(forms.Form):
    base_url = forms.CharField(label="Site address", max_length=200)
    # Never pre-filled: a saved token is not sent back to the browser.
    token = forms.CharField(
        label="API token",
        required=False,
        strip=True,
        widget=forms.PasswordInput(render_value=False, attrs={"autocomplete": "off"}),
    )

    site_id = forms.CharField(label="Site ID", max_length=64, required=False)
    product_id = forms.CharField(label="Product ID", max_length=64, required=False)

    def __init__(self, *args, has_token=False, **kwargs):
        kwargs.setdefault("label_suffix", "")
        super().__init__(*args, **kwargs)
        self.has_token = has_token

    def clean_base_url(self):
        try:
            return normalize_url(self.cleaned_data["base_url"])
        except ValueError as error:
            raise forms.ValidationError(str(error)) from None

    def _clean_id(self, name):
        value = self.cleaned_data[name]
        if value and not re.fullmatch(r"[0-9A-Fa-f-]{8,64}", value):
            raise forms.ValidationError("That does not look like an Incident IQ ID. It is letters a-f, digits and dashes.")
        return value.lower()

    def clean_site_id(self):
        return self._clean_id("site_id")

    def clean_product_id(self):
        return self._clean_id("product_id")

    def clean_token(self):
        token = self.cleaned_data["token"]
        if not token and not self.has_token:
            raise forms.ValidationError("Paste the API token from Incident IQ.")
        return token

    def apply(self, config):
        config.base_url = self.cleaned_data["base_url"]
        config.site_id = self.cleaned_data["site_id"]
        config.product_id = self.cleaned_data["product_id"]
        if self.cleaned_data["token"]:
            config.set_token(self.cleaned_data["token"])


class ConnectionForm(forms.Form):
    """A connection whose `token` field holds a secret. The secret is never
    pre-filled; leaving it empty keeps the saved one."""

    token_missing = "Paste the key."

    def __init__(self, *args, has_token=False, **kwargs):
        kwargs.setdefault("label_suffix", "")
        super().__init__(*args, **kwargs)
        self.has_token = has_token
        if has_token:
            self.fields["token"].help_text = "One is saved. Leave this empty to keep it, or paste a new one to replace it."

    def clean_token(self):
        token = self.cleaned_data["token"]
        if not token and not self.has_token:
            raise forms.ValidationError(self.token_missing)
        return token

    def apply(self, config):
        for name, value in self.cleaned_data.items():
            if name != "token":
                setattr(config, name, value)
        if self.cleaned_data["token"]:
            config.set_token(self.cleaned_data["token"])


def _secret(label, help_text, textarea=False):
    widget = (
        forms.Textarea(attrs={"rows": 6, "autocomplete": "off", "spellcheck": "false"})
        if textarea
        else forms.PasswordInput(render_value=False, attrs={"autocomplete": "off"})
    )
    return forms.CharField(label=label, required=False, strip=True, widget=widget, help_text=help_text)


def _org_id(help_text):
    return forms.CharField(label="Org ID", max_length=64, required=False, help_text=help_text)


class GoogleForm(ConnectionForm):
    admin_email = forms.EmailField(
        label="Admin to act as", help_text="A Google admin who can view Chrome devices. Atlas only reads."
    )
    token = _secret(
        "Service account key",
        "Paste the whole contents of the service account's JSON key file.",
        textarea=True,
    )
    token_missing = "Paste the service account's JSON key."

    def clean_token(self):
        token = super().clean_token()
        if token:
            try:
                self.client_email = read_key(token)["client_email"]
            except ValueError as error:
                raise forms.ValidationError(str(error)) from None
        return token

    def apply(self, config):
        super().apply(config)
        if self.cleaned_data["token"]:
            config.client_email = self.client_email


class MistForm(ConnectionForm):
    api_host = forms.CharField(
        label="Portal address",
        max_length=200,
        help_text="The address you sign in to Mist at, for example manage.mist.com or manage.gc1.mist.com.",
    )
    token = _secret("API token", "An organization API token with Observer (read-only) access.")
    org_id = _org_id("Leave empty if the token can see only one organization.")
    token_missing = "Paste the Mist API token."

    def clean_api_host(self):
        try:
            return api_host(self.cleaned_data["api_host"])
        except ValueError as error:
            raise forms.ValidationError(str(error)) from None

    def clean_org_id(self):
        value = self.cleaned_data["org_id"].strip().lower()
        if value and not re.fullmatch(r"[0-9a-f-]{36}", value):
            raise forms.ValidationError("A Mist Org ID looks like 01234567-89ab-cdef-0123-456789abcdef.")
        return value


class MerakiForm(ConnectionForm):
    token = _secret("API key", "A key from a dashboard admin with read-only organization access.")
    org_id = _org_id("Leave empty if the key can see only one organization.")
    token_missing = "Paste the Meraki API key."

    def clean_org_id(self):
        value = self.cleaned_data["org_id"].strip()
        if value and not re.fullmatch(r"[0-9]{1,30}", value):
            raise forms.ValidationError("A Meraki Org ID is a number.")
        return value
