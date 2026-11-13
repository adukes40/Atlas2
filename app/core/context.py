from atlas import __version__
from devices.models import recent_lookups
from integrations.status import systems

from .models import Organization
from .themes import get_theme

MODE_COOKIE = "atlas_mode"
MODES = ("light", "dark", "auto")


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
        # The one appearance button switches to the other mode (on Auto, atlas.js picks by the device).
        "mode_next": "light" if choice == "dark" else "dark",
        "systems": systems(),
        "recent_lookups": recent_lookups(request.user) if request.user.is_authenticated else [],
        "atlas_version": __version__,
    }
