import ipaddress

from django.db import models

from .themes import DEFAULT_THEME, theme_choices


class Organization(models.Model):
    """The one organization this Atlas belongs to. Always row 1."""

    name = models.CharField(max_length=120, default="Your Organization")
    theme = models.CharField(max_length=40, choices=theme_choices, default=DEFAULT_THEME)
    # One per line: a full address, the start of one (10.20.) or a range (203.0.113.0/24).
    wan_ips = models.TextField(blank=True)

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    def on_network(self, ip):
        """True when this WAN IP is one of ours, False when not, None when
        there is no IP or no WAN IP is set up."""
        entries = [line.strip() for line in self.wan_ips.splitlines() if line.strip()]
        ip = (ip or "").strip()
        if not entries or not ip:
            return None
        return any(wan_ip_matches(ip, entry) for entry in entries)

    @classmethod
    def load(cls):
        org, _ = cls.objects.get_or_create(pk=1)
        return org

    def __str__(self):
        return self.name


def wan_ip_matches(ip, entry):
    """The address is the entry, starts with it, or falls in its range."""
    if "/" in entry:
        try:
            return ipaddress.ip_address(ip) in ipaddress.ip_network(entry, strict=False)
        except ValueError:
            return False
    return ip == entry or (entry.endswith((".", ":")) and ip.startswith(entry)) or ip.startswith(entry + ".")
