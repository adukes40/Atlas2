"""Everything Atlas knows about talking to Juniper Mist lives in this file.

Raw Mist JSON goes no further than `normalize.py`. Nothing here logs or
returns the API token.
"""

from urllib.parse import quote, urlsplit

import requests
from django.core.cache import cache

from ..errors import IntegrationError
from ..http import TIMEOUT, get_json

NAME_CACHE = 24 * 3600  # site and access point names rarely change


class MistError(IntegrationError):
    pass


def api_host(raw):
    """Turn the Mist portal or API address into the API host for that cloud,
    for example manage.gc1.mist.com becomes api.gc1.mist.com."""
    raw = (raw or "").strip() or "manage.mist.com"
    host = (urlsplit(raw if "://" in raw else "https://" + raw).hostname or "").lower()
    if not (host == "mist.com" or host.endswith(".mist.com")):
        raise ValueError("Enter your Mist portal address, for example manage.mist.com or manage.gc1.mist.com")
    for prefix in ("manage.", "integration."):
        if host.startswith(prefix):
            return "api." + host[len(prefix) :]
    return host if host.startswith("api.") else "api." + host


class MistClient:
    def __init__(self, host, token, org_id="", session=None):
        self.host = host
        self.org_id = org_id
        self._token = token
        self._session = session or requests.Session()
        self.timeout = TIMEOUT  # the nightly sync allows longer for big pages, and retries
        self.retries = 0

    def _get(self, path, params=None):
        if path.startswith("/api/v1/"):  # a "next" link from a search
            path = path[len("/api/v1") :]
        return get_json(
            self._session,
            f"https://{self.host}/api/v1{path}",
            system="Mist",
            error=MistError,
            rejected="Mist rejected the API token.",
            headers={"Authorization": f"Token {self._token}", "Accept": "application/json"},
            params=params,
            timeout=self.timeout,
            retries=self.retries,
        )

    def check(self):
        """Prove the token works and find the organization. Returns (org_id, org_name)."""
        me = self._get("/self")
        if not isinstance(me, dict):
            raise MistError("Mist did not recognize the request. Check the portal address.")
        orgs = {
            p.get("org_id"): p.get("name") or ""
            for p in me.get("privileges") or []
            if isinstance(p, dict) and p.get("scope") == "org" and p.get("org_id")
        }
        if self.org_id:
            if self.org_id not in orgs:
                raise MistError("This token cannot see that organization. Check the Org ID.")
            return self.org_id, orgs[self.org_id]
        if len(orgs) == 1:
            return next(iter(orgs.items()))
        if not orgs:
            raise MistError("This token has no organization access. Create the token under Organization > Settings.")
        raise MistError("This token can see more than one organization. Enter the Org ID to use.")

    def find_client(self, mac):
        """The most recent wireless client record for this MAC in the last week, or None."""
        data = self._get(
            f"/orgs/{quote(self.org_id, safe='')}/clients/search", {"mac": mac, "duration": "7d", "limit": 5}
        )
        results = [r for r in (data or {}).get("results") or [] if isinstance(r, dict)]
        return max(results, key=lambda r: r.get("timestamp") or 0, default=None)

    def clients_seen(self, duration="1d"):
        """Every wireless client record in the organization for this long, a
        thousand at a time, following Mist's "next" link."""
        path = f"/orgs/{quote(self.org_id, safe='')}/clients/search"
        params = {"duration": duration, "limit": 1000}
        while path:
            data = self._get(path, params) or {}
            yield from (r for r in data.get("results") or [] if isinstance(r, dict))
            path, params = data.get("next") or "", None

    def site_name(self, site_id):
        def fetch():
            return (self._get(f"/sites/{quote(site_id, safe='')}") or {}).get("name") or ""

        return cache.get_or_set(f"mist-site:{site_id}", fetch, NAME_CACHE) if site_id else ""

    def access_point_name(self, mac):
        def fetch():
            data = self._get(f"/orgs/{quote(self.org_id, safe='')}/inventory/search", {"mac": mac, "limit": 1})
            results = (data or {}).get("results") or [{}]
            return (results[0] or {}).get("name") or ""

        return cache.get_or_set(f"mist-ap:{mac}", fetch, NAME_CACHE) if mac else ""

    def org_list(self, what, params=None):
        """Every item of an organization list such as "sites", "wlans" or
        "stats/devices", a thousand per page."""
        path = f"/orgs/{quote(self.org_id, safe='')}/{what}"
        for page in range(1, 101):
            data = self._get(path, {**(params or {}), "limit": 1000, "page": page}) or []
            rows = [r for r in data if isinstance(r, dict)] if isinstance(data, list) else []
            yield from rows
            if len(rows) < 1000:
                return
