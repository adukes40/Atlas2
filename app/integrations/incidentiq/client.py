"""Everything Atlas knows about talking to Incident IQ lives in this file.

Raw Incident IQ JSON goes no further than `normalize.py`. Nothing here logs or
returns the API token.
"""

from urllib.parse import quote, urlsplit

import requests

API = "/api/v1.0"
TIMEOUT = 10  # seconds


class IncidentIQError(Exception):
    """Base class. The message is written to be shown to a person."""


class AuthFailed(IncidentIQError):
    pass


class Unreachable(IncidentIQError):
    pass


class RateLimited(IncidentIQError):
    pass


class BadResponse(IncidentIQError):
    pass


def normalize_url(raw):
    """Turn whatever was pasted into https://host. Raises ValueError if it
    cannot be a site address."""
    raw = (raw or "").strip()
    if not raw:
        raise ValueError("Enter your Incident IQ address, for example https://yourdistrict.incidentiq.com")
    if "://" not in raw:
        raw = "https://" + raw
    parts = urlsplit(raw)
    if parts.scheme != "https":
        raise ValueError("The address must start with https://")
    if not parts.hostname or "." not in parts.hostname:
        raise ValueError("That does not look like a site address.")
    return f"https://{parts.netloc}"


class IncidentIQClient:
    def __init__(self, base_url, token, session=None):
        self.base_url = base_url.rstrip("/")
        self._token = token
        self._session = session or requests.Session()

    def _request(self, method, path, **kwargs):
        host = urlsplit(self.base_url).hostname
        try:
            response = self._session.request(
                method,
                f"{self.base_url}{API}{path}",
                headers={"Authorization": f"Bearer {self._token}", "Accept": "application/json"},
                timeout=TIMEOUT,
                **kwargs,
            )
        except requests.Timeout:
            raise Unreachable(f"Incident IQ at {host} took too long to answer.") from None
        except requests.RequestException:
            raise Unreachable(f"Could not reach Incident IQ at {host}.") from None

        if response.status_code in (401, 403):
            raise AuthFailed("Incident IQ rejected the API token.")
        if response.status_code == 429:
            raise RateLimited("Incident IQ is limiting requests right now. Try again in a minute.")
        if response.status_code == 404:
            raise BadResponse("Incident IQ did not recognize the request (HTTP 404). Check the site address.")
        if response.status_code != 200:
            raise BadResponse(f"Incident IQ answered with an error (HTTP {response.status_code}).")
        try:
            data = response.json()
        except ValueError:
            raise BadResponse("Incident IQ sent back something that is not data. Check the site address.") from None
        if not isinstance(data, dict):
            raise BadResponse("Incident IQ sent back data in a shape Atlas does not understand.")
        return data

    def check(self):
        """Prove the address and token work. Returns the number of assets."""
        data = self._request(
            "POST", "/assets", json={"OnlyShowDeleted": False, "Paging": {"PageSize": 1, "PageIndex": 0}}
        )
        return (data.get("Paging") or {}).get("TotalRows", 0)

    def find_assets(self, query):
        """Assets whose serial number or asset tag is exactly `query`.

        Serial first, then asset tag. "Not found" is an ordinary answer from
        Incident IQ (HTTP 200 with no items), so it comes back as an empty list.
        """
        value = quote(query.strip(), safe="")
        if not value:
            return []
        for kind in ("serial", "assettag"):
            items = self._request("GET", f"/assets/{kind}/{value}").get("Items") or []
            if items:
                return [item for item in items if isinstance(item, dict)]
        return []

    def location_name(self, location_id):
        data = self._request("GET", f"/locations/{quote(location_id, safe='')}")
        return (data.get("Location") or {}).get("Name") or None
