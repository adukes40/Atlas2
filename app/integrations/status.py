"""What the sidebar and Settings show about each outside system."""

from .models import GoogleConfig, IncidentIQConfig, MerakiConfig, MistConfig

URL_NAMES = {
    IncidentIQConfig: "settings-incidentiq",
    GoogleConfig: "settings-google",
    MistConfig: "settings-mist",
    MerakiConfig: "settings-meraki",
}


def systems():
    configs = (model.load() for model in URL_NAMES)
    return [
        {"name": c.name, "state": c.state, "ok": c.ok, "url_name": URL_NAMES[type(c)]} for c in configs
    ]
