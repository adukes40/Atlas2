from django.db import models
from django.utils import timezone


class ClassLinkConfig(models.Model):
    """SAML sign-in through ClassLink. Always row 1."""

    enabled = models.BooleanField(default=False)
    # ClassLink's IdP metadata XML. Public information, so stored as is.
    metadata = models.TextField(blank=True)
    # On: anyone new ClassLink signs in gets a turned-off Technician account,
    # waiting for an administrator to approve it on Users.
    # Off: only people already on the Users screen (matched by email) get in.
    create_accounts = models.BooleanField(default=True)

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls):
        config, _ = cls.objects.get_or_create(pk=1)
        return config

    @property
    def ready(self):
        return self.enabled and bool(self.metadata)


class UsedAssertion(models.Model):
    """ClassLink sign-ins already accepted, so a captured one cannot be replayed."""

    assertion_id = models.CharField(max_length=255, unique=True)
    expires = models.DateTimeField()

    @classmethod
    def first_use(cls, assertion_id, expires):
        cls.objects.filter(expires__lt=timezone.now()).delete()
        _, created = cls.objects.get_or_create(assertion_id=assertion_id, defaults={"expires": expires})
        return created
