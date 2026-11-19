import requests
from django import forms

from . import saml


class ClassLinkForm(forms.Form):
    metadata = forms.CharField(
        label="ClassLink metadata",
        required=False,
        strip=True,
        widget=forms.Textarea(attrs={"rows": 6, "spellcheck": "false"}),
        help_text="Paste the IdP metadata XML from the ClassLink app, or its metadata URL and Atlas will fetch it once.",
    )
    enabled = forms.BooleanField(label="Turn on ClassLink sign-in", required=False)
    create_accounts = forms.BooleanField(
        label="Request accounts for new people",
        required=False,
        help_text="On: someone new who signs in with ClassLink gets an account that is waiting for approval. "
                  "Turn on \u201cCan sign in\u201d on Users to let them in. "
                  "Off: only people already on Users (matched by email) can sign in.",
    )

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("label_suffix", "")
        super().__init__(*args, **kwargs)

    def clean_metadata(self):
        value = self.cleaned_data["metadata"]
        if value.startswith(("http://", "https://")):
            try:
                response = requests.get(value, timeout=10)
                response.raise_for_status()
            except requests.RequestException:
                raise forms.ValidationError("Atlas could not download that metadata URL. Paste the XML instead.") from None
            value = response.text.strip()
        if value:
            try:
                saml.parse_metadata(value)
            except ValueError as error:
                raise forms.ValidationError(str(error)) from None
        return value

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("enabled") and not cleaned.get("metadata") and "metadata" not in self.errors:
            self.add_error("metadata", "Add ClassLink's metadata before turning sign-in on.")
        return cleaned
