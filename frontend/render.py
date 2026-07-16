"""Builds the final gallery HTML by combining the static HTML/CSS/JS files
in this directory with the JSON-serialized MediaItem data for one upload.
"""

import json
from pathlib import Path

from core.models import MediaItem

_FRONTEND_DIR = Path(__file__).parent


def _read(filename: str) -> str:
    return (_FRONTEND_DIR / filename).read_text(encoding="utf-8")


def build_gallery_html(items: list[MediaItem]) -> str:
    html = _read("gallery.html")
    css = _read("gallery.css")
    js = _read("gallery.js")

    payload = json.dumps([item.to_dict() for item in items])

    html = html.replace("__GALLERY_CSS__", css)
    html = html.replace("__GALLERY_JS__", js)
    html = html.replace("__ITEMS_JSON__", payload)
    html = html.replace("__COUNT__", str(len(items)))
    return html