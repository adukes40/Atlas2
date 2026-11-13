"""What the sidebar and Settings show about each outside system."""

from .models import IncidentIQConfig


def systems():
    iiq = IncidentIQConfig.load()
    return [
        {"name": "Incident IQ", "state": iiq.state, "ok": iiq.ok, "url_name": "settings-incidentiq"},
        {"name": "Google Workspace", "state": "Not connected", "ok": False, "url_name": None},
        {"name": "Mist", "state": "Not connected", "ok": False, "url_name": None},
        {"name": "Meraki", "state": "Not connected", "ok": False, "url_name": None},
    ]
