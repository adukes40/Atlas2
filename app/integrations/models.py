from django.db import models
from django.utils import timezone

from . import crypto
from .incidentiq.client import IncidentIQClient, IncidentIQError


class IncidentIQConfig(models.Model):
    """The one Incident IQ connection. Always row 1. The token is stored
    encrypted and is never sent back to a browser."""

    base_url = models.CharField(max_length=200, blank=True)
    token_encrypted = models.TextField(blank=True)
    last_checked_at = models.DateTimeField(null=True, blank=True)
    last_ok_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=300, blank=True)

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
        return bool(self.base_url and self.token_encrypted)

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

    def client(self):
        token = crypto.decrypt(self.token_encrypted)
        if not token:
            raise IncidentIQError("The saved Incident IQ token cannot be read. Enter it again in Settings.")
        return IncidentIQClient(self.base_url, token)

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


class IncidentIQLocation(models.Model):
    """Names of Incident IQ locations Atlas has already asked about, so a
    lookup does not ask again every time."""

    location_id = models.CharField(max_length=64, primary_key=True)
    name = models.CharField(max_length=200)

    def __str__(self):
        return self.name
