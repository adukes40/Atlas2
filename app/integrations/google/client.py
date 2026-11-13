"""Everything Atlas knows about talking to Google Workspace lives in this file.

Atlas signs in as a service account with domain-wide delegation, acting as
one admin, with read-only access to Chrome devices. Raw Google JSON goes no
further than `normalize.py`. Nothing here logs or returns the key.
"""

import base64
import json
import time

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from ..errors import IntegrationError
from ..http import TIMEOUT, get_json

SCOPE = "https://www.googleapis.com/auth/admin.directory.device.chromeos.readonly"
TOKEN_URL = "https://oauth2.googleapis.com/token"
DEVICES_URL = "https://admin.googleapis.com/admin/directory/v1/customer/my_customer/devices/chromeos"
# Only what Atlas shows. FULL projection is needed for recent users, but its
# CPU and memory reports are large, so ask for these fields only.
FIELDS = (
    "chromeosdevices(serialNumber,status,orgUnitPath,lastSync,recentUsers,macAddress,ethernetMacAddress,"
    "model,osVersion,annotatedAssetId,annotatedUser,annotatedLocation,lastKnownNetwork,autoUpdateThrough)"
)

# Access tokens last an hour. Keep one per process so a lookup does not sign in every time.
_tokens = {}


class GoogleError(IntegrationError):
    pass


def read_key(text):
    """The parts of a service account JSON key Atlas needs. Raises ValueError
    with a message for a person if it is not one."""
    try:
        key = json.loads(text)
    except ValueError:
        raise ValueError("That is not a JSON key file. Paste the whole contents of the .json file.") from None
    if not isinstance(key, dict) or key.get("type") != "service_account":
        raise ValueError("That JSON is not a service account key. Download a JSON key from the service account.")
    if not key.get("client_email") or not key.get("private_key"):
        raise ValueError("The key file is missing its client_email or private_key. Download a new key.")
    try:
        serialization.load_pem_private_key(key["private_key"].encode(), password=None)
    except (ValueError, TypeError):
        raise ValueError("The private key in that file cannot be read. Download a new key.") from None
    return key


def _b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=")


class GoogleClient:
    def __init__(self, key_json, admin_email, session=None):
        self._key = read_key(key_json)
        self.admin_email = admin_email
        self._session = session or requests.Session()

    def _assertion(self):
        now = int(time.time())
        header = {"alg": "RS256", "typ": "JWT"}
        claims = {
            "iss": self._key["client_email"],
            "sub": self.admin_email,
            "scope": SCOPE,
            "aud": TOKEN_URL,
            "iat": now,
            "exp": now + 3600,
        }
        signing_input = _b64(json.dumps(header).encode()) + b"." + _b64(json.dumps(claims).encode())
        private_key = serialization.load_pem_private_key(self._key["private_key"].encode(), password=None)
        signature = private_key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
        return (signing_input + b"." + _b64(signature)).decode()

    def _access_token(self):
        cache_key = (self._key["client_email"], self.admin_email)
        token, expires = _tokens.get(cache_key, (None, 0))
        if token and expires > time.time() + 60:
            return token
        try:
            response = self._session.request(
                "POST",
                TOKEN_URL,
                data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": self._assertion()},
                timeout=TIMEOUT,
            )
        except requests.Timeout:
            raise GoogleError("Google took too long to answer.") from None
        except requests.RequestException:
            raise GoogleError("Could not reach Google at oauth2.googleapis.com.") from None
        try:
            data = response.json()
        except ValueError:
            data = {}
        if not isinstance(data, dict):
            data = {}
        if response.status_code != 200 or not data.get("access_token"):
            raise GoogleError(self._sign_in_problem(data.get("error"), response.status_code))
        _tokens[cache_key] = (data["access_token"], time.time() + int(data.get("expires_in") or 3600))
        return data["access_token"]

    def _sign_in_problem(self, code, status):
        if code == "unauthorized_client":
            return (
                "Google has not given this service account domain-wide delegation for the Chrome devices scope. "
                f"Add its Client ID with {SCOPE} in the Admin console."
            )
        if code == "invalid_grant":
            return f"Google would not let the service account act as {self.admin_email}. Check that it is an admin account in your domain."
        if code == "invalid_client":
            return "Google does not recognize this service account key. It may have been deleted. Download a new key."
        return f"Google refused to sign in the service account (HTTP {status})."

    def _get(self, params):
        data = get_json(
            self._session,
            DEVICES_URL,
            system="Google",
            error=GoogleError,
            rejected=f"Google refused. Check that {self.admin_email} can view Chrome devices in the Admin console.",
            headers={"Authorization": f"Bearer {self._access_token()}", "Accept": "application/json"},
            params=params,
        )
        return data if isinstance(data, dict) else {}

    def check(self):
        """Prove the key, delegation and admin work."""
        self._get({"maxResults": 1, "projection": "BASIC", "fields": "chromeosdevices(deviceId)"})

    def find_chromebook(self, serial):
        """The Chromebook with exactly this serial number, or None."""
        serial = serial.strip()
        if not serial or '"' in serial:
            return None
        found = self._get({"query": f"id:{serial}", "projection": "FULL", "maxResults": 10, "fields": FIELDS})
        for device in found.get("chromeosdevices") or []:
            if isinstance(device, dict) and str(device.get("serialNumber", "")).upper() == serial.upper():
                return device
        return None
