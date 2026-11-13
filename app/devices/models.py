from django.conf import settings
from django.db import models


class LookupLog(models.Model):
    """Who looked up what, and when. Also where "Recent lookups" comes from."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="lookups")
    query = models.CharField(max_length=100)
    found = models.BooleanField(default=False)
    key = models.CharField(max_length=100, blank=True)  # asset tag or serial to look up again
    label = models.CharField(max_length=200, blank=True)  # who or what it turned out to be
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["user", "-created_at"])]

    def __str__(self):
        return f"{self.query} by {self.user}"


def recent_lookups(user, limit=5):
    """The devices this person most recently found, newest first, each once."""
    seen, recent = set(), []
    for entry in LookupLog.objects.filter(user=user, found=True).exclude(key="")[:40]:
        if entry.key not in seen:
            seen.add(entry.key)
            recent.append(entry)
            if len(recent) == limit:
                break
    return recent
