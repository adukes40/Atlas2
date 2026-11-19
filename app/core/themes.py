"""Themes Atlas can wear. Each one is a CSS file that sets the variables
atlas.css reads, for light and dark mode.

A district's own theme goes in core/static/core/themes/local/ on its server.
That folder is kept out of git, so a district's colors never reach the public
repo. The label comes from the file's opening comment, "/* Label: ... */",
or else the file name. Local themes follow the person's device for Light/Dark.

`mode` is what a person sees before they pick Light, Dark or Auto themselves:
"dark", "light", or "auto" to follow their device."""

import re
from pathlib import Path

DEFAULT_THEME = "graphite"
LOCAL_DIR = Path(__file__).resolve().parent / "static" / "core" / "themes" / "local"

THEMES = {
    "graphite": {"label": "Graphite", "css": "core/themes/graphite.css", "mode": "dark"},
    "slate": {"label": "Slate", "css": "core/themes/slate.css", "mode": "auto"},
}


def local_themes(folder=LOCAL_DIR):
    found = {}
    for path in sorted(folder.glob("*.css")):
        head = path.read_text(encoding="utf-8", errors="replace")[:400]
        label = re.match(r"\s*/\*\s*([^:*\n]{1,60}):", head)
        key = "local-" + path.stem[:30]
        found[key] = {
            "label": label.group(1).strip() if label else path.stem.replace("-", " ").title(),
            "css": f"core/themes/local/{path.name}",
            "mode": "auto",
        }
    return found


THEMES.update(local_themes())


def theme_choices():
    return [(key, theme["label"]) for key, theme in THEMES.items()]


def get_theme(key):
    return THEMES.get(key, THEMES[DEFAULT_THEME])
