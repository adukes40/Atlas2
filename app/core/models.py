from django.db import models

from .themes import DEFAULT_THEME, theme_choices


class Organization(models.Model):
    """The one organization this Atlas belongs to. Always row 1."""

    name = models.CharField(max_length=120, default="Your Organization")
    theme = models.CharField(max_length=40, choices=theme_choices, default=DEFAULT_THEME)

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls):
        org, _ = cls.objects.get_or_create(pk=1)
        return org

    def __str__(self):
        return self.name
