"""Themes Atlas can wear. Each one is a CSS file that sets the variables
atlas.css reads, for light and dark mode.

`mode` is what a person sees before they pick Light, Dark or Auto themselves:
"dark", "light", or "auto" to follow their device."""

DEFAULT_THEME = "graphite"

THEMES = {
    "graphite": {"label": "Graphite", "css": "core/themes/graphite.css", "mode": "dark"},
    "slate": {"label": "Slate", "css": "core/themes/slate.css", "mode": "auto"},
    "caesar-rodney": {
        "label": "Caesar Rodney School District",
        "css": "core/themes/caesar-rodney.css",
        "mode": "auto",
    },
}


def theme_choices():
    return [(key, theme["label"]) for key, theme in THEMES.items()]


def get_theme(key):
    return THEMES.get(key, THEMES[DEFAULT_THEME])
