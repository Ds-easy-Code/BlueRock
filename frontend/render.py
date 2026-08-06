"""Builds the final gallery HTML by combining the static HTML/CSS/JS files
in this directory with the JSON-serialized MediaItem data for one upload.
"""

import json
import re
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

    replacements = {
        "__GALLERY_CSS__": css,
        "__GALLERY_JS__": js,
        "__ITEMS_JSON__": payload,
        "__COUNT__": str(len(items)),
    }

    # Four sequential .replace() calls would let a value inserted by an
    # earlier call get re-scanned (and corrupted) by a later one -- e.g. if
    # an archive contains a file literally named "__COUNT__.jpg", that
    # substring ends up inside `payload`, and a later `.replace("__COUNT__",
    # ...)` would rewrite it there too, breaking the embedded JSON. Matching
    # every placeholder in a single pass over the *original* template means
    # replacement values are never rescanned for further placeholder text.
    pattern = re.compile("|".join(re.escape(key) for key in replacements))
    return pattern.sub(lambda m: replacements[m.group(0)], html)