"""Stand-ins for Incident IQ, Google, Mist and Meraki, used by the tests. No
test talks to a real one.

All of the data here is made up.
"""

import json
from contextlib import contextmanager
from unittest import mock

import requests
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

TEST_KEY = Fernet.generate_key().decode()
BASE = "https://example.incidentiq.com"
TOKEN = "test-token-not-real"
SCHOOL_ID = "11111111-2222-3333-4444-555555555555"


def asset(**changes):
    raw = {
        "AssetId": "a-1",
        "AssetTag": "104482",
        "SerialNumber": "7XK2RT3",
        "Model": {"Name": "Dell Chromebook 3110", "Category": {"Name": "Chromebook"}},
        "Status": {"Name": "In Service"},
        "Location": {"LocationId": SCHOOL_ID, "Name": "North Middle School"},
        "OpenTickets": 1,
        "Owner": {
            "UserId": "u-1",
            "FullName": "Jordan Sample",
            "Email": "jsample@students.example",
            "SchoolIdNumber": "204817",
            "RoleName": "Student",
            "Grade": "7",
            "Homeroom": "B214",
            "LocationId": SCHOOL_ID,
        },
        "CustomFieldValues": [
            {"CustomFieldTypeId": "other", "Value": "Blue case"},
            {
                "CustomFieldTypeId": "fees",
                "Value": json.dumps(
                    [
                        {"Amount": 25, "Balance": 25, "PastDueAmount": 10, "LastActivityDate": "2026-10-01"},
                        # The fee app lists the same charge twice; it counts once.
                        {"Amount": 25, "Balance": 25, "PastDueAmount": 10, "LastActivityDate": "2026-10-01"},
                        {"Amount": 40, "Balance": 0, "PastDueAmount": 0, "LastActivityDate": "2026-09-01"},
                    ]
                ),
            },
        ],
    }
    raw.update(changes)
    return raw


class FakeResponse:
    def __init__(self, status=200, data=None, text=None):
        self.status_code = status
        self._data = data
        self._text = text

    def json(self):
        if self._text is not None:
            raise ValueError("not JSON")
        return self._data


def items(*assets):
    return FakeResponse(data={"ItemCount": len(assets), "Items": list(assets)})


class FakeIncidentIQ:
    """Answers by the end of the URL path. Anything not listed is "not found",
    which Incident IQ reports as HTTP 200 with no items."""

    def __init__(self, routes=None, fail=None, others=None):
        self.routes = routes or {}
        self.fail = fail
        self.others = others or {}
        self.calls = []

    def __call__(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if self.fail:
            raise self.fail
        if "/api/v1.0" not in url:
            # Google, Mist and Meraki answer by full URL; anything else is "not found".
            answer = self.others.get(url, FakeResponse(status=404))
            return answer(kwargs) if callable(answer) else answer
        path = url.split("/api/v1.0", 1)[1]
        answer = self.routes.get(path)
        if answer is None:
            return items()
        return answer

    def paths(self):
        return [url.split("/api/v1.0", 1)[1] for _, url, _ in self.calls if "/api/v1.0" in url]

    def urls(self):
        return [url for _, url, _ in self.calls]


@contextmanager
def incident_iq(routes=None, fail=None, others=None):
    fake = FakeIncidentIQ(routes, fail, others)
    with mock.patch.object(requests.Session, "request", side_effect=fake):
        yield fake


STANDARD = {
    "/assets": FakeResponse(data={"Paging": {"TotalRows": 12345}, "Items": []}),
    "/assets/assettag/104482": None,  # replaced below
    f"/locations/{SCHOOL_ID}": FakeResponse(data={"Location": {"Name": "North Middle School"}}),
}
STANDARD["/assets/assettag/104482"] = items(asset())
STANDARD["/assets/serial/7XK2RT3"] = items(asset())


# Google, Mist and Meraki. Made-up keys, IDs and addresses.

GOOGLE_PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
GOOGLE_KEY = json.dumps(
    {
        "type": "service_account",
        "client_email": "atlas@example-project.iam.gserviceaccount.com",
        "private_key": GOOGLE_PRIVATE_KEY.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        ).decode(),
    }
)
GOOGLE_ADMIN = "atlas-admin@example.org"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_DEVICES_URL = "https://admin.googleapis.com/admin/directory/v1/customer/my_customer/devices/chromeos"
WIFI_MAC = "a1b2c3d4e5f6"


def chromebook(**changes):
    raw = {
        "serialNumber": "7XK2RT3",
        "status": "ACTIVE",
        "orgUnitPath": "/Students/North Middle",
        "lastSync": "2026-11-12T14:05:00.000Z",
        "osVersion": "142.0.7444.100",
        "autoUpdateThrough": "2031-06-01",
        "macAddress": WIFI_MAC,
        "recentUsers": [{"type": "USER_TYPE_MANAGED", "email": f"student{n}@students.example"} for n in range(1, 8)],
        "lastKnownNetwork": [{"ipAddress": "10.20.30.40", "wanIpAddress": "203.0.113.9"}],
    }
    raw.update(changes)
    return raw


def google_signed_in(kwargs=None):
    return FakeResponse(data={"access_token": "google-access-not-real", "expires_in": 3600})


MIST_ORG = "0d1e2f30-4a5b-4c6d-8e9f-a0b1c2d3e4f5"
MIST_SITE = "5f4e3d2c-1b0a-4987-8654-3210fedcba98"
MIST_AP = "0011223344aa"
MIST_API = "https://api.mist.com/api/v1"
MERAKI_ORG = "123456"
MERAKI_API = "https://api.meraki.com/api/v1"

STANDARD_OTHERS = {
    GOOGLE_TOKEN_URL: google_signed_in,
    GOOGLE_DEVICES_URL: FakeResponse(data={"chromeosdevices": [chromebook()]}),
    f"{MIST_API}/self": FakeResponse(
        data={"name": "Atlas", "privileges": [{"scope": "org", "org_id": MIST_ORG, "name": "Example Schools"}]}
    ),
    f"{MIST_API}/orgs/{MIST_ORG}/clients/search": FakeResponse(
        data={
            "results": [
                {"mac": WIFI_MAC, "site_id": MIST_SITE, "last_ap": MIST_AP, "last_ssid": "Students",
                 "last_ip": "10.20.30.41", "timestamp": 1794580000.5},
            ]
        }
    ),
    f"{MIST_API}/sites/{MIST_SITE}": FakeResponse(data={"name": "North Middle School"}),
    f"{MIST_API}/orgs/{MIST_ORG}/inventory/search": FakeResponse(data={"results": [{"name": "NMS-AP-214"}]}),
    f"{MERAKI_API}/organizations": FakeResponse(data=[{"id": MERAKI_ORG, "name": "Example Schools"}]),
    f"{MERAKI_API}/organizations/{MERAKI_ORG}/clients/search": FakeResponse(
        data={
            "mac": "a1:b2:c3:d4:e5:f6",
            "records": [
                {"network": {"name": "District Office"}, "recentDeviceName": "DO-AP-1", "ssid": "Staff",
                 "ip": "10.9.9.9", "lastSeen": "2026-11-01T09:00:00Z"},
            ],
        }
    ),
}
