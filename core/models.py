"""Data models shared across the archive/media/video processing modules."""

from dataclasses import dataclass, asdict


@dataclass
class MediaItem:
    """A single image or video extracted from the uploaded ZIP.

    thumb / src are both base64 data URIs, ready to be dropped straight into
    an <img>/<video> src attribute on the frontend — no further encoding
    needed once this object is built.
    """

    name: str
    type: str  # "image" | "video"
    thumb: str  # small uniform square thumbnail, base64 data URI
    src: str  # full-resolution image or the original video, base64 data URI
    size: str  # human-readable file size, e.g. "3.4 MB"
    date: str  # human-readable modified date, e.g. "2026-07-15 09:53"

    def to_dict(self) -> dict:
        return asdict(self)