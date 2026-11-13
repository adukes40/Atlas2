from django.db import models
from django.utils import timezone

from . import crypto
from .google.client import GoogleClient, GoogleError
from .incidentiq.client import IncidentIQClient, IncidentIQError
from .meraki.client import MerakiClient, MerakiError
from .mist.client import MistClient, MistError


class Connection(models.Model):
    """One connection to an outside system. Always row 1. The token or key is
    stored encrypted and is never sent back to a browser."""

    token_encrypted = models.TextField(blank=True)
    last_checked_at = models.DateTimeField(null=True, blank=True)
    last_ok_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=300, blank=True)

    error = None  # the system's error class, for a key that cannot be read

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls):
        config, _ = cls.objects.get_or_create(pk=1)
        return config

    @property
    def has_token(self):
        return bool(self.token_encrypted)

    @property
    def configured(self):
        return self.has_token

    @property
    def state(self):
        if not self.configured:
            return "Not connected"
        if self.last_error:
            return "Error"
        return "Connected" if self.last_ok_at else "Not tested"

    @property
    def ok(self):
        return self.state == "Connected"

    def set_token(self, token):
        self.token_encrypted = crypto.encrypt(token)

    def token(self):
        token = crypto.decrypt(self.token_encrypted)
        if not token:
            raise self.error(f"The saved {self.name} key cannot be read. Enter it again in Settings.")
        return token

    def record_ok(self):
        now = timezone.now()
        type(self).objects.filter(pk=1).update(last_checked_at=now, last_ok_at=now, last_error="")
        self.last_checked_at = self.last_ok_at = now
        self.last_error = ""

    def record_error(self, message):
        now = timezone.now()
        type(self).objects.filter(pk=1).update(last_checked_at=now, last_error=message[:300])
        self.last_checked_at = now
        self.last_error = message[:300]


class IncidentIQConfig(Connection):
    name = "Incident IQ"
    error = IncidentIQError

    base_url = models.CharField(max_length=200, blank=True)
    site_id = models.CharField(max_length=64, blank=True)
    product_id = models.CharField(max_length=64, blank=True)

    @property
    def configured(self):
        return bool(self.base_url and self.token_encrypted)

    def client(self):
        token = crypto.decrypt(self.token_encrypted)
        if not token:
            raise IncidentIQError("The saved Incident IQ token cannot be read. Enter it again in Settings.")
        return IncidentIQClient(self.base_url, token, site_id=self.site_id, product_id=self.product_id)


class GoogleConfig(Connection):
    """The service account JSON key is the encrypted token."""

    name = "Google Workspace"
    error = GoogleError

    admin_email = models.EmailField(blank=True)
    client_email = models.CharField(max_length=200, blank=True)  # the service account, shown in Settings

    @property
    def configured(self):
        return bool(self.admin_email and self.token_encrypted)

    def client(self):
        try:
            return GoogleClient(self.token(), self.admin_email)
        except ValueError as error:
            raise GoogleError(str(error)) from None


class MistConfig(Connection):
    name = "Mist"
    error = MistError

    api_host = models.CharField(max_length=100, default="api.mist.com")
    org_id = models.CharField(max_length=64, blank=True)
    org_name = models.CharField(max_length=200, blank=True)

    def client(self):
        return MistClient(self.api_host, self.token(), org_id=self.org_id)


class MerakiConfig(Connection):
    name = "Meraki"
    error = MerakiError

    org_id = models.CharField(max_length=64, blank=True)
    org_name = models.CharField(max_length=200, blank=True)

    def client(self):
        return MerakiClient(self.token(), org_id=self.org_id)


class IncidentIQLocation(models.Model):
    """Names of Incident IQ locations Atlas has already asked about, so a
    lookup does not ask again every time."""

    location_id = models.CharField(max_length=64, primary_key=True)
    name = models.CharField(max_length=200)

    def __str__(self):
        return self.name
