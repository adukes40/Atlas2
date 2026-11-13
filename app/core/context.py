from atlas import __version__

from .models import Organization
from .themes import get_theme

MODE_COOKIE = "atlas_mode"
MODES = ("light", "dark", "auto")

# Filled in for real as each integration is built.
SYSTEMS = ["Incident IQ", "Google Workspace", "Mist", "Meraki"]


def atlas(request):
    org = Organization.load()
    theme = get_theme(org.theme)
    # The person's own choice wins; otherwise the theme decides.
    choice = request.COOKIES.get(MODE_COOKIE)
    if choice not in MODES:
        choice = theme["mode"]
    return {
        "org": org,
        "theme_css": theme["css"],
        "mode_choice": choice,
        # Written to <html data-mode>. Empty for "auto", so the device decides.
        "mode": choice if choice != "auto" else None,
        "systems": SYSTEMS,
        "atlas_version": __version__,
    }
