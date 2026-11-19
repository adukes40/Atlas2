"""Themes Atlas can wear. Each one is a CSS file that sets the variables
atlas.css reads, for light and dark mode.

`mode` is what a person sees before they pick Light, Dark or Auto themselves:
"dark", "light", or "auto" to follow their device."""

DEFAULT_THEME = "graphite"
UPLOADED = "uploaded"

THEMES = {
    "graphite": {"label": "Graphite", "css": "core/themes/graphite.css", "mode": "dark"},
    "slate": {"label": "Slate", "css": "core/themes/slate.css", "mode": "auto"},
    # The CSS file an administrator uploaded in Settings, kept in the database.
    UPLOADED: {"label": "Uploaded theme file", "css": None, "mode": "auto"},
}


def theme_choices():
    return [(key, theme["label"]) for key, theme in THEMES.items()]


def get_theme(key):
    return THEMES.get(key, THEMES[DEFAULT_THEME])
