import ipaddress

from django import forms
from django.contrib.auth import get_user_model, password_validation

import re

from .models import Organization
from .themes import UPLOADED

THEME_FILE_LIMIT = 64 * 1024


class OrganizationForm(forms.ModelForm):
    theme_file = forms.FileField(
        label="Upload a theme file", required=False,
        widget=forms.ClearableFileInput(attrs={"accept": ".css,text/css"}),
        help_text="A .css file that sets the same color variables as Atlas's own themes, for light and dark. "
                  "Uploading one switches Atlas to it. It replaces any file uploaded before.",
    )

    field_order = ["name", "theme", "theme_file", "wan_ips"]

    class Meta:
        model = Organization
        fields = ["name", "theme", "wan_ips"]
        labels = {"name": "Organization name", "theme": "Theme", "wan_ips": "WAN IP"}
        help_texts = {
            "wan_ips": "The public addresses your schools reach the internet from, one per line. A device whose "
                       "WAN IP equals or starts with one of these is on your network; anything else is off it. "
                       "For example 203.0.113.9, or 198.51.100 for every address starting with it, or 192.0.2.0/24.",
        }
        widgets = {"wan_ips": forms.Textarea(attrs={"rows": 4, "spellcheck": "false"})}

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("label_suffix", "")
        super().__init__(*args, **kwargs)
        if self.instance.theme_upload:
            self.fields["theme_file"].help_text += f" Uploaded now: {theme_name(self.instance.theme_upload)}."

    def clean_theme_file(self):
        upload = self.cleaned_data.get("theme_file")
        if not upload:
            return None
        if upload.size > THEME_FILE_LIMIT:
            raise forms.ValidationError("That file is too big for a theme. Theme files are a few KB.")
        try:
            css = upload.read().decode("utf-8")
        except UnicodeDecodeError:
            raise forms.ValidationError("That is not a text CSS file.") from None
        if "--" not in css or "<" in css:
            raise forms.ValidationError("That does not look like an Atlas theme file. It should set CSS variables such as --bg and --ink.")
        return css

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("theme_file"):
            cleaned["theme"] = UPLOADED
        elif cleaned.get("theme") == UPLOADED and not self.instance.theme_upload:
            self.add_error("theme", "Upload a theme file first.")
        return cleaned

    def save(self, commit=True):
        org = super().save(commit=False)
        if self.cleaned_data.get("theme_file"):
            org.theme_upload = self.cleaned_data["theme_file"]
            org.theme = UPLOADED
        if commit:
            org.save()
        return org

    def clean_wan_ips(self):
        lines = [line.strip() for line in self.cleaned_data["wan_ips"].splitlines() if line.strip()]
        for line in lines:
            if "/" in line:
                try:
                    ipaddress.ip_network(line, strict=False)
                except ValueError:
                    raise forms.ValidationError(f"“{line}” is not an address range Atlas can read.") from None
            elif not line.strip(".:") or any(c not in "0123456789abcdefABCDEF.:" for c in line):
                raise forms.ValidationError(f"“{line}” is not an address or the start of one.")
        return "\n".join(lines)


def theme_name(css):
    """The name in a theme file's opening comment, /* Name: ... */."""
    found = re.match(r"\s*/\*\s*([^:*\n]{1,60}):", css)
    return found.group(1).strip() if found else "a theme file"


class UserForm(forms.ModelForm):
    """Add or change one person who can sign in to Atlas."""

    password1 = forms.CharField(label="Password", widget=forms.PasswordInput, required=False, strip=False)
    password2 = forms.CharField(label="Password again", widget=forms.PasswordInput, required=False, strip=False)

    class Meta:
        model = get_user_model()
        fields = ["username", "first_name", "last_name", "email", "is_superuser", "is_active"]
        labels = {
            "username": "Username",
            "email": "Email",
            "is_superuser": "Administrator",
            "is_active": "Can sign in",
        }
        help_texts = {
            "username": "What they type to sign in.",
            "is_superuser": "Administrators can change Settings and manage users.",
            "is_active": "Turn this off to stop someone signing in. Their lookup history is kept.",
        }

    field_order = ["username", "first_name", "last_name", "email", "password1", "password2"]

    def __init__(self, *args, editor, **kwargs):
        kwargs.setdefault("label_suffix", "")
        super().__init__(*args, **kwargs)
        self.editor = editor
        if self.instance.pk:
            self.fields["password1"].label = "New password"
            self.fields["password1"].help_text = "Leave blank to keep their current password."

    def clean(self):
        cleaned = super().clean()
        password1, password2 = cleaned.get("password1"), cleaned.get("password2")
        if not self.instance.pk and not password1:
            self.add_error("password1", "Set a password for the new account.")
        elif password1 != password2:
            self.add_error("password2", "The two passwords do not match.")
        elif password1:
            # Validators compare against username and names, so give them this form's values.
            for name in ("username", "first_name", "last_name", "email"):
                if name in cleaned:
                    setattr(self.instance, name, cleaned[name])
            try:
                password_validation.validate_password(password1, self.instance)
            except forms.ValidationError as error:
                self.add_error("password1", error)
        # Editing yourself: never lock the last way back in.
        if self.instance.pk == self.editor.pk and not (cleaned.get("is_superuser") and cleaned.get("is_active")):
            raise forms.ValidationError("You cannot turn off your own account or remove your own administrator access.")
        return cleaned

    def save(self, commit=True):
        user = super().save(commit=False)
        user.is_staff = False  # Atlas has no Django admin site.
        if self.cleaned_data.get("password1"):
            user.set_password(self.cleaned_data["password1"])
        if commit:
            user.save()
        return user
