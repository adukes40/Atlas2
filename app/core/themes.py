"""Themes Atlas can wear. Each one is a CSS file that sets the variables
atlas.css reads, for light and dark mode."""

DEFAULT_THEME = "default"

THEMES = {
    "default": {"label": "Atlas", "css": "core/themes/default.css"},
    "caesar-rodney": {
        "label": "Caesar Rodney School District",
        "css": "core/themes/caesar-rodney.css",
    },
}


def theme_choices():
    return [(key, theme["label"]) for key, theme in THEMES.items()]


def theme_css(key):
    return THEMES.get(key, THEMES[DEFAULT_THEME])["css"]
