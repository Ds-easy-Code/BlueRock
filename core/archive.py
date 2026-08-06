"""Media ingestion: reads a ZIP archive's entries, or one or more standalone
image/video files, entirely in memory and routes each one to the image or
video pipeline based on its extension.

Decoding (image thumbnailing, video first-frame extraction) is CPU-bound
and independent per-entry, so it's parallelized across a small thread pool
-- Pillow/OpenCV both release the GIL for the bulk of their C-level work,
so this meaningfully cuts wall-clock time for archives with many files.
Reading bytes out of the shared ZipFile handle is still serialized behind
a lock, since concurrent `ZipFile.open()` calls on the same handle aren't
guaranteed safe across Python versions.
"""

import base64
import io
import os
import threading
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from PIL import Image

from core.media import full_image_b64, human_size, img_to_b64, make_uniform_thumbnail, THUMB_SIZE
from core.models import MediaItem
from core.video import VIDEO_EXTS, VIDEO_INLINE_MAX_BYTES, ensure_browser_playable, video_first_frame

IMAGE_EXTS = ('.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp', '.tiff')

# Number of worker threads used to decode images/videos in parallel. Kept
# modest and overridable via env var -- this is bounded by CPU cores doing
# useful work, not I/O wait, so going much higher than the core count
# doesn't help and just adds contention.
DECODE_WORKERS = max(1, int(os.environ.get("BLUEROCK_DECODE_WORKERS", str(min(8, (os.cpu_count() or 4))))))


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
    frame = video_first_frame(data, ext)
    thumb_img = make_uniform_thumbnail(frame) if frame is not None else Image.new("RGB", THUMB_SIZE, (35, 38, 42))

    # `too_large` is checked against the ORIGINAL size, before any
    # transcoding -- it's gating whether we embed this video at all (see
    # VIDEO_INLINE_MAX_BYTES), and transcoding a video we're not even
    # going to embed would be wasted work.
    too_large = size_bytes > VIDEO_INLINE_MAX_BYTES
    if too_large:
        src = ""
    else:
        # Re-encodes to H.264 only if the source isn't already in a
        # browser-safe codec (most commonly: HEVC/H.265 from phone
        # recordings, which decodes fine for the thumbnail above but
        # can't actually play in most browsers' <video> element). See
        # core/video.py:ensure_browser_playable for the fallback behavior
        # if ffmpeg isn't available or the transcode itself fails.
        playable_bytes, mime = ensure_browser_playable(data, ext)
        src = f"data:{mime};base64,{base64.b64encode(playable_bytes).decode()}"

    return MediaItem(
        name=name,
        type="video",
        thumb=img_to_b64(thumb_img),
        src=src,
        size=human_size(size_bytes),
        date=date,
        too_large=too_large,
    )


def _entry_date(info: zipfile.ZipInfo) -> str:
    try:
        return datetime(*info.date_time).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return "-"


def process_zip(zip_bytes: bytes, progress_cb=None, done_so_far: int = 0, total: int | None = None) -> tuple[list[MediaItem], list[str]]:
    """Parse a ZIP file's bytes entirely in memory and return
    (items, errors). Each item is a MediaItem for one supported image or
    video found in the archive; unsupported files are silently skipped.

    progress_cb(done, total), if given, is called after each entry finishes
    decoding -- `done_so_far`/`total` let a caller processing several files
    in one batch (see process_uploads) report cumulative progress rather
    than restarting the count at 0 for every file.
    """
    zip_data = io.BytesIO(zip_bytes)

    try:
        archive = zipfile.ZipFile(zip_data, 'r')
    except zipfile.BadZipFile as e:
        return [], [f"Not a valid ZIP file: {e}"]

    with archive:
        # Encrypted or otherwise unreadable archives fail the same way on
        # every single entry -- check once up front instead of letting the
        # per-entry loop below produce one near-identical error per file.
        try:
            bad_entry = archive.testzip()
        except (RuntimeError, NotImplementedError) as e:
            return [], [
                "Couldn't read this archive -- it may be password-protected "
                f"or use an unsupported compression method ({type(e).__name__}: {e})."
            ]
        if bad_entry is not None:
            return [], [f"Archive failed its integrity check at: {bad_entry}"]

        infos = [
            info for info in archive.infolist()
            if info.filename.lower().endswith(IMAGE_EXTS) or info.filename.lower().endswith(VIDEO_EXTS)
        ]

        read_lock = threading.Lock()

        def _load(info: zipfile.ZipInfo):
            lower = info.filename.lower()
            try:
                with read_lock:
                    with archive.open(info.filename) as f:
                        data = f.read()
                # Decoding (the slow part) happens outside the lock, so
                # multiple entries' images/frames can decode in parallel
                # even though the raw reads themselves are serialized.
                if lower.endswith(IMAGE_EXTS):
                    return _build_image_item(info.filename, data, info.file_size, _entry_date(info)), None
                return _build_video_item(info.filename, data, info.file_size, _entry_date(info)), None
            except Exception as e:
                return None, f"Failed to process {info.filename} safely: {type(e).__name__}: {e}"

        items: list[MediaItem] = []
        errors: list[str] = []
        done = done_so_far
        if infos:
            with ThreadPoolExecutor(max_workers=min(DECODE_WORKERS, len(infos))) as pool:
                # pool.map yields results in submission order as each becomes
                # ready -- decoding still happens concurrently across worker
                # threads, but this consuming loop (and therefore each
                # progress_cb call) stays single-threaded, so no locking is
                # needed around the counter itself.
                for item, err in pool.map(_load, infos):
                    if item is not None:
                        items.append(item)
                    if err is not None:
                        errors.append(err)
                    done += 1
                    if progress_cb:
                        progress_cb(done, total if total is not None else len(infos))

    return items, errors


def _count_supported(name: str, data: bytes) -> int:
    """Cheaply count how many supported entries a file will contribute,
    without decoding anything -- used to size the progress bar in
    process_uploads before the real (slow) work starts. For a ZIP this
    only reads the central directory (fast, no decompression); for a
    direct file it's always 1 regardless of whether it's supported, so a
    genuinely unsupported file still advances the bar instead of silently
    making the total look one short.
    """
    if name.lower().endswith('.zip'):
        try:
            with zipfile.ZipFile(io.BytesIO(data), 'r') as archive:
                return sum(
                    1 for info in archive.infolist()
                    if info.filename.lower().endswith(IMAGE_EXTS) or info.filename.lower().endswith(VIDEO_EXTS)
                )
        except zipfile.BadZipFile:
            return 0
    return 1


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
        return [], [f"Failed to process {name} safely: {type(e).__name__}: {e}"]


def process_uploads(files: list[tuple[str, bytes]], progress_cb=None) -> tuple[list[MediaItem], list[str]]:
    """Process a batch of uploads, each a (filename, raw_bytes) tuple.
    ZIP files are expanded into their contained media; anything else is
    treated as a standalone image or video. Returns the combined
    (items, errors) across every file in the batch.

    progress_cb(done, total), if given, is called as entries finish across
    the *whole* batch (all zips + direct files combined), so a caller can
    drive a single progress bar instead of one per input file. `total` is
    computed with a fast pre-scan (_count_supported) before any real
    decoding starts.
    """
    all_items: list[MediaItem] = []
    all_errors: list[str] = []

    if progress_cb:
        total = sum(_count_supported(name, data) for name, data in files) or 1
        progress_cb(0, total)
    else:
        total = None

    done = 0
    for name, data in files:
        if name.lower().endswith('.zip'):
            items, errors = process_zip(data, progress_cb=progress_cb, done_so_far=done, total=total)
            done += len(items) + len([e for e in errors if e.startswith("Failed to process")])
        else:
            items, errors = process_direct_file(name, data)
            done += 1
            if progress_cb:
                progress_cb(done, total)
        all_items.extend(items)
        all_errors.extend(errors)

    return all_items, all_errors
