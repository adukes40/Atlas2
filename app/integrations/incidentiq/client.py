"""Everything Atlas knows about talking to Incident IQ lives in this file.

Raw Incident IQ JSON goes no further than `normalize.py`. Nothing here logs or
returns the API token.
"""

import json
from urllib.parse import quote, urlsplit

import requests

from ..errors import IntegrationError

API = "/api/v1.0"
TIMEOUT = 10  # seconds


class IncidentIQError(IntegrationError):
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
    def __init__(self, base_url, token, site_id="", product_id="", session=None):
        self.base_url = base_url.rstrip("/")
        self._token = token
        self._site_id = site_id
        self._product_id = product_id
        self._session = session or requests.Session()

    def _headers(self):
        headers = {"Authorization": f"Bearer {self._token}", "Accept": "application/json"}
        # Some Incident IQ sites answer with the token alone. Others also want
        # to be told which site and product the call is for.
        if self._site_id or self._product_id:
            headers["Client"] = "ApiClient"
            if self._site_id:
                headers["SiteId"] = self._site_id
            if self._product_id:
                headers["ProductId"] = self._product_id
        return headers

    def _request(self, method, path, **kwargs):
        host = urlsplit(self.base_url).hostname
        try:
            response = self._session.request(
                method,
                f"{self.base_url}{API}{path}",
                headers=self._headers(),
                timeout=TIMEOUT,
                **kwargs,
            )
        except requests.Timeout:
            raise Unreachable(f"Incident IQ at {host} took too long to answer.") from None
        except requests.RequestException:
            raise Unreachable(f"Could not reach Incident IQ at {host}.") from None
        except (UnicodeError, ValueError):
            raise BadResponse(
                "The token or IDs contain characters that cannot be sent. Paste them again."
            ) from None

        if response.status_code in (401, 403):
            raise AuthFailed("Incident IQ rejected the API token.")
        if response.status_code == 429:
            raise RateLimited("Incident IQ is limiting requests right now. Try again in a minute.")
        if response.status_code == 404:
            raise BadResponse("Incident IQ did not recognize the request (HTTP 404). Check the site address.")
        if response.status_code >= 500:
            raise BadResponse(
                f"Incident IQ had an internal error (HTTP {response.status_code}). "
                "This often means it needs the Site ID and Product ID as well."
            )
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
        paging = data.get("Paging")
        total = paging.get("TotalRows") if isinstance(paging, dict) else None
        return total if isinstance(total, int) and total >= 0 else 0

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

    def open_tickets(self, asset_id):
        """Open tickets on one asset (at most 25).

        Incident IQ has been seen to ignore filters on this call, so every
        ticket that comes back is checked for the asset before it is kept.
        """
        data = self._request(
            "POST",
            "/tickets?$s=25",
            json={"Filters": [{"Facet": "asset", "Id": asset_id}]},
        )
        return [
            item
            for item in data.get("Items") or []
            if isinstance(item, dict) and not item.get("IsClosed", True) and _mentions(item, asset_id)
        ]


def _mentions(ticket, asset_id):
    """True if the asset appears anywhere on the ticket (its shape varies)."""
    return bool(asset_id) and asset_id in json.dumps(ticket)
