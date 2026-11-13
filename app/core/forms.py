from django import forms
from django.contrib.auth import get_user_model, password_validation

from .models import Organization


class OrganizationForm(forms.ModelForm):
    class Meta:
        model = Organization
        fields = ["name", "theme"]
        labels = {"name": "Organization name", "theme": "Theme"}

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("label_suffix", "")
        super().__init__(*args, **kwargs)


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
