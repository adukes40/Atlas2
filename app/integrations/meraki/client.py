"""Everything Atlas knows about talking to Cisco Meraki lives in this file.

Raw Meraki JSON goes no further than `normalize.py`. Nothing here logs or
returns the API key.
"""

from urllib.parse import quote

import requests

from ..errors import IntegrationError
from ..http import get_json

API = "https://api.meraki.com/api/v1"


class MerakiError(IntegrationError):
    pass


class MerakiClient:
    def __init__(self, key, org_id="", session=None):
        self.org_id = org_id
        self._key = key
        self._session = session or requests.Session()

    def _get(self, path, params=None):
        return get_json(
            self._session,
            f"{API}{path}",
            system="Meraki",
            error=MerakiError,
            rejected="Meraki rejected the API key, or API access is off for the organization.",
            headers={"Authorization": f"Bearer {self._key}", "Accept": "application/json"},
            params=params,
        )

    def check(self):
        """Prove the key works and find the organization. Returns (org_id, org_name)."""
        orgs = {
            str(o.get("id")): o.get("name") or ""
            for o in self._get("/organizations") or []
            if isinstance(o, dict) and o.get("id")
        }
        if self.org_id:
            if self.org_id not in orgs:
                raise MerakiError("This key cannot see that organization. Check the Org ID.")
            return self.org_id, orgs[self.org_id]
        if len(orgs) == 1:
            return next(iter(orgs.items()))
        if not orgs:
            raise MerakiError("This key cannot see any organization.")
        raise MerakiError("This key can see more than one organization. Enter the Org ID to use.")

    def find_client(self, mac):
        """Meraki's answer for this MAC, or None. Meraki answers 404 when it has never seen it."""
        pretty = ":".join(mac[i : i + 2] for i in range(0, 12, 2))
        data = self._get(f"/organizations/{quote(self.org_id, safe='')}/clients/search", {"mac": pretty})
        return data if isinstance(data, dict) else None
