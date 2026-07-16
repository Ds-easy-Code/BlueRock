"""ZIP archive parsing: reads entries entirely in memory and routes each
one to the image or video pipeline based on its extension.
"""

import base64
import io
import os
import zipfile
from datetime import datetime

from PIL import Image

from core.media import full_image_b64, human_size, img_to_b64, make_uniform_thumbnail, THUMB_SIZE
from core.models import MediaItem
from core.video import VIDEO_EXTS, video_first_frame, video_mime_for

IMAGE_EXTS = ('.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp', '.tiff')


def _entry_date(info: zipfile.ZipInfo) -> str:
    try:
        return datetime(*info.date_time).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return "-"


def _process_image_entry(archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> MediaItem:
    with archive.open(info.filename) as f:
        img = Image.open(f)
        img.load()
        img_copy = img.copy()
    return MediaItem(
        name=info.filename,
        type="image",
        thumb=img_to_b64(make_uniform_thumbnail(img_copy)),
        src=full_image_b64(img_copy),
        size=human_size(info.file_size),
        date=_entry_date(info),
    )


def _process_video_entry(archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> MediaItem:
    ext = os.path.splitext(info.filename.lower())[1]
    with archive.open(info.filename) as f:
        video_bytes = f.read()

    frame = video_first_frame(video_bytes)
    thumb_img = make_uniform_thumbnail(frame) if frame is not None else Image.new("RGB", THUMB_SIZE, (35, 38, 42))
    mime = video_mime_for(ext)

    return MediaItem(
        name=info.filename,
        type="video",
        thumb=img_to_b64(thumb_img),
        src=f"data:{mime};base64,{base64.b64encode(video_bytes).decode()}",
        size=human_size(info.file_size),
        date=_entry_date(info),
    )


def process_zip(zip_bytes: bytes) -> tuple[list[MediaItem], list[str]]:
    """Parse a ZIP file's bytes entirely in memory and return
    (items, errors). Each item is a MediaItem for one supported image or
    video found in the archive; unsupported files are silently skipped.
    """
    items: list[MediaItem] = []
    errors: list[str] = []
    zip_data = io.BytesIO(zip_bytes)

    with zipfile.ZipFile(zip_data, 'r') as archive:
        for info in archive.infolist():
            lower = info.filename.lower()
            try:
                if lower.endswith(IMAGE_EXTS):
                    items.append(_process_image_entry(archive, info))
                elif lower.endswith(VIDEO_EXTS):
                    items.append(_process_video_entry(archive, info))
            except Exception as e:
                errors.append(f"Failed to process {info.filename} safely: {e}")

    return items, errors