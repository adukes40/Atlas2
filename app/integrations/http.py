"""The one way Google, Mist and Meraki clients make a request, so a timeout
or a rejected key reads the same everywhere. Nothing here logs the request."""

from urllib.parse import urlsplit

import requests

TIMEOUT = 10  # seconds


def get_json(session, url, *, system, error, rejected, headers=None, params=None, method="GET", data=None):
    """Parsed JSON, or None when the system answers 404 (not found)."""
    host = urlsplit(url).hostname
    try:
        response = session.request(method, url, headers=headers, params=params, data=data, timeout=TIMEOUT)
    except requests.Timeout:
        raise error(f"{system} took too long to answer.") from None
    except requests.RequestException:
        raise error(f"Could not reach {system} at {host}.") from None
    except (UnicodeError, ValueError):
        raise error("The saved key contains characters that cannot be sent. Paste it again.") from None

    status = response.status_code
    if status == 404:
        return None
    if status in (401, 403):
        raise error(rejected)
    if status == 429:
        raise error(f"{system} is limiting requests right now. Try again in a minute.")
    if status >= 500:
        raise error(f"{system} had an internal error (HTTP {status}). Try again in a few minutes.")
    if status != 200:
        raise error(f"{system} answered with an error (HTTP {status}).")
    try:
        return response.json()
    except ValueError:
        raise error(f"{system} sent back something that is not data.") from None
