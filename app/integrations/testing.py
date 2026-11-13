"""A stand-in for Incident IQ, used by the tests. No test talks to the real one.

All of the data here is made up.
"""

import json
from contextlib import contextmanager
from unittest import mock

import requests
from cryptography.fernet import Fernet

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

    def __init__(self, routes=None, fail=None):
        self.routes = routes or {}
        self.fail = fail
        self.calls = []

    def __call__(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if self.fail:
            raise self.fail
        path = url.split("/api/v1.0", 1)[1]
        answer = self.routes.get(path)
        if answer is None:
            return items()
        return answer

    def paths(self):
        return [url.split("/api/v1.0", 1)[1] for _, url, _ in self.calls]


@contextmanager
def incident_iq(routes=None, fail=None):
    fake = FakeIncidentIQ(routes, fail)
    with mock.patch.object(requests.Session, "request", side_effect=fake):
        yield fake


STANDARD = {
    "/assets": FakeResponse(data={"Paging": {"TotalRows": 12345}, "Items": []}),
    "/assets/assettag/104482": None,  # replaced below
    f"/locations/{SCHOOL_ID}": FakeResponse(data={"Location": {"Name": "North Middle School"}}),
}
STANDARD["/assets/assettag/104482"] = items(asset())
STANDARD["/assets/serial/7XK2RT3"] = items(asset())
