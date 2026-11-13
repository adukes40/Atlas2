import re

from django import forms

from .incidentiq.client import normalize_url


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
