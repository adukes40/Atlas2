from atlas import __version__

from .models import Organization
from .themes import theme_css

MODE_COOKIE = "atlas_mode"
MODES = ("light", "dark")

# Filled in for real as each integration is built.
SYSTEMS = ["Incident IQ", "Google Workspace", "Mist", "Meraki"]


def atlas(request):
    org = Organization.load()
    mode = request.COOKIES.get(MODE_COOKIE)
    return {
        "org": org,
        "theme_css": theme_css(org.theme),
        "mode": mode if mode in MODES else None,
        "systems": SYSTEMS,
        "atlas_version": __version__,
    }
