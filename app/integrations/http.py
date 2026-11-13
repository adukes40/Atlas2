"""The one way Google, Mist and Meraki clients make a request, so a timeout
or a rejected key reads the same everywhere. Nothing here logs the request."""

import time
from urllib.parse import urlsplit

import requests

TIMEOUT = 10  # seconds
RETRY_WAIT = 30  # seconds, doubled each time; only the nightly sync retries


def with_retries(call, retries):
    """call(), tried again up to `retries` times when the error says it may pass
    (rate limits, timeouts, the system's own errors). Rejected keys fail at once."""
    for attempt in range(retries + 1):
        try:
            return call()
        except Exception as error:
            if attempt == retries or not getattr(error, "retryable", False):
                raise
            time.sleep(RETRY_WAIT * 2**attempt)


def passing(error):
    error.retryable = True
    return error


def get_json(session, url, *, system, error, rejected, headers=None, params=None, method="GET", data=None,
             timeout=TIMEOUT, retries=0, link=False):
    """Parsed JSON, or None when the system answers 404 (not found). With
    link=True, (JSON, the address of the next page or "") for lists paged by
    a Link header."""
    return with_retries(
        lambda: _get_json(session, url, system, error, rejected, headers, params, method, data, timeout, link), retries
    )


def _get_json(session, url, system, error, rejected, headers, params, method, data, timeout, link=False):
    host = urlsplit(url).hostname
    try:
        response = session.request(method, url, headers=headers, params=params, data=data, timeout=timeout)
    except requests.Timeout:
        raise passing(error(f"{system} took too long to answer.")) from None
    except requests.RequestException:
        raise passing(error(f"Could not reach {system} at {host}.")) from None
    except (UnicodeError, ValueError):
        raise error("The saved key contains characters that cannot be sent. Paste it again.") from None

    status = response.status_code
    if status == 404:
        return (None, "") if link else None
    if status in (401, 403):
        raise error(rejected)
    if status == 429:
        raise passing(error(f"{system} is limiting requests right now. Try again in a minute."))
    if status >= 500:
        raise passing(error(f"{system} had an internal error (HTTP {status}). Try again in a few minutes."))
    if status != 200:
        raise error(f"{system} answered with an error (HTTP {status}).")
    try:
        parsed = response.json()
    except ValueError:
        raise error(f"{system} sent back something that is not data.") from None
    return (parsed, response.links.get("next", {}).get("url", "")) if link else parsed
