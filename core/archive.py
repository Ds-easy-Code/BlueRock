"""Media ingestion: reads a ZIP archive's entries, or one or more standalone
image/video files, entirely in memory and routes each one to the image or
video pipeline based on its extension.
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


def _build_image_item(name: str, data: bytes, size_bytes: int, date: str) -> MediaItem:
    img = Image.open(io.BytesIO(data))
    img.load()
    return MediaItem(
        name=name,
        type="image",
        thumb=img_to_b64(make_uniform_thumbnail(img)),
        src=full_image_b64(img),
        size=human_size(size_bytes),
        date=date,
    )


def _build_video_item(name: str, data: bytes, size_bytes: int, date: str) -> MediaItem:
    ext = os.path.splitext(name.lower())[1]
    frame = video_first_frame(data)
    thumb_img = make_uniform_thumbnail(frame) if frame is not None else Image.new("RGB", THUMB_SIZE, (35, 38, 42))
    mime = video_mime_for(ext)
    return MediaItem(
        name=name,
        type="video",
        thumb=img_to_b64(thumb_img),
        src=f"data:{mime};base64,{base64.b64encode(data).decode()}",
        size=human_size(size_bytes),
        date=date,
    )


def _entry_date(info: zipfile.ZipInfo) -> str:
    try:
        return datetime(*info.date_time).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return "-"


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
                    with archive.open(info.filename) as f:
                        data = f.read()
                    items.append(_build_image_item(info.filename, data, info.file_size, _entry_date(info)))
                elif lower.endswith(VIDEO_EXTS):
                    with archive.open(info.filename) as f:
                        data = f.read()
                    items.append(_build_video_item(info.filename, data, info.file_size, _entry_date(info)))
            except Exception as e:
                errors.append(f"Failed to process {info.filename} safely: {e}")

    return items, errors


def process_direct_file(name: str, data: bytes) -> tuple[list[MediaItem], list[str]]:
    """Process one standalone image or video file (not inside a ZIP).
    Returns (items, errors) in the same shape as process_zip for uniform
    handling upstream, even though this only ever produces 0 or 1 item.
    """
    lower = name.lower()
    date = datetime.now().strftime("%Y-%m-%d %H:%M")
    try:
        if lower.endswith(IMAGE_EXTS):
            return [_build_image_item(name, data, len(data), date)], []
        elif lower.endswith(VIDEO_EXTS):
            return [_build_video_item(name, data, len(data), date)], []
        else:
            return [], [f"Unsupported file type: {name}"]
    except Exception as e:
        return [], [f"Failed to process {name} safely: {e}"]


def process_uploads(files: list[tuple[str, bytes]]) -> tuple[list[MediaItem], list[str]]:
    """Process a batch of uploads, each a (filename, raw_bytes) tuple.
    ZIP files are expanded into their contained media; anything else is
    treated as a standalone image or video. Returns the combined
    (items, errors) across every file in the batch.
    """
    all_items: list[MediaItem] = []
    all_errors: list[str] = []

    for name, data in files:
        if name.lower().endswith('.zip'):
            items, errors = process_zip(data)
        else:
            items, errors = process_direct_file(name, data)
        all_items.extend(items)
        all_errors.extend(errors)

    return all_items, all_errors