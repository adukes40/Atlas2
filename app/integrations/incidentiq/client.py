"""Everything Atlas knows about talking to Incident IQ lives in this file.

Raw Incident IQ JSON goes no further than `normalize.py`. Nothing here logs or
returns the API token.
"""

import json
from urllib.parse import quote, urlsplit

import requests

from ..errors import IntegrationError
from ..http import passing, with_retries

API = "/api/v1.0"
TIMEOUT = 10  # seconds


class IncidentIQError(IntegrationError):
    """Base class. The message is written to be shown to a person."""


class AuthFailed(IncidentIQError):
    pass


class Unreachable(IncidentIQError):
    retryable = True


class RateLimited(IncidentIQError):
    retryable = True


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
        self.timeout = TIMEOUT  # the nightly sync allows longer for big pages, and retries
        self.retries = 0

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
        return with_retries(lambda: self._request_once(method, path, **kwargs), self.retries)

    def _request_once(self, method, path, **kwargs):
        host = urlsplit(self.base_url).hostname
        try:
            response = self._session.request(
                method,
                # Incident IQ's add-on apps (Google Device Data, ...) live outside /api/v1.0.
                f"{self.base_url}{'' if path.startswith('/apps/') else API}{path}",
                headers=self._headers(),
                timeout=self.timeout,
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
            raise passing(BadResponse(
                f"Incident IQ had an internal error (HTTP {response.status_code}). "
                "This often means it needs the Site ID and Product ID as well."
            ))
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

    def assets_page(self, index, size):
        """One page of every asset, and the total. Incident IQ pages assets by the request body."""
        data = self._request(
            "POST", "/assets", json={"OnlyShowDeleted": False, "Paging": {"PageSize": size, "PageIndex": index}}
        )
        return _page(data)

    def users_page(self, index, size):
        """One page of every person. Incident IQ ignores paging in a POST body for
        people, so it goes in the address."""
        data = self._request("GET", f"/users?$p={index}&$s={size}")
        return _page(data)

    def location_name(self, location_id):
        data = self._request("GET", f"/locations/{quote(location_id, safe='')}")
        return (data.get("Location") or {}).get("Name") or None

    def user(self, user_id):
        return self._request("GET", f"/users/{quote(user_id, safe='')}")

    def google_device(self, asset_id):
        """Google's record of one Chromebook, as Incident IQ's Google Device Data
        app keeps it (the call its own asset page makes). Not in the public API docs."""
        return self._request(
            "POST", "/apps/googleDeviceData/api/googleDeviceData/data/assets/get-google-device", json={"AssetId": asset_id}
        )

    # One person, for Person 360. These are the calls Incident IQ's own user page makes.

    def assets_for(self, user_id):
        return self._request("GET", f"/assets/for/{quote(user_id, safe='')}").get("Items") or []

    def device_history(self, user_id):
        return self._request("GET", f"/users/{quote(user_id, safe='')}/device-history").get("Items") or []

    def devices_used(self, user_id, days=30):
        """Chromebooks this person signed in to, from Incident IQ's Google Device Data app."""
        data = self._request("GET", f"/apps/googleDeviceData/api/googleDeviceData/data/assets/usage/{quote(user_id, safe='')}/{days}")
        return data.get("Items") or []

    def ticket_stats(self, user_id):
        data = self._request("GET", f"/analytics/requestor/{quote(user_id, safe='')}/summary-stats")
        return data.get("Item") if isinstance(data.get("Item"), dict) else data

    def user_tickets(self, user_id):
        """The person's 50 newest tickets, open or closed."""
        data = self._request("POST", "/tickets?$s=50", json={"Schema": "All", "Filters": [{"Facet": "user", "Id": user_id}]})
        return [item for item in data.get("Items") or [] if isinstance(item, dict)]

    def classes(self, user_id):
        return self._request("GET", f"/sis/classes/for/user/{quote(user_id, safe='')}").get("Items") or []

    def open_tickets(self, asset_id, user_id):
        """Open tickets on one asset (at most 50 of its person's tickets are read).

        Incident IQ files tickets under the person and does not count them on
        the asset, and its asset filter returns nothing, so this asks for the
        assigned person's tickets and keeps the open ones that name the asset.
        """
        data = self._request(
            "POST",
            "/tickets?$s=50",
            json={"Schema": "All", "Filters": [{"Facet": "user", "Id": user_id}]},
        )
        return [
            item
            for item in data.get("Items") or []
            if isinstance(item, dict) and not item.get("IsClosed", True) and _mentions(item, asset_id)
        ]


def _page(data):
    """(items, total rows or None)."""
    paging = data.get("Paging") if isinstance(data.get("Paging"), dict) else {}
    total = paging.get("TotalRows")
    items = [item for item in data.get("Items") or [] if isinstance(item, dict)]
    return items, total if isinstance(total, int) and total >= 0 else None


def _mentions(ticket, asset_id):
    """True if the asset appears anywhere on the ticket (its shape varies)."""
    return bool(asset_id) and asset_id in json.dumps(ticket)
