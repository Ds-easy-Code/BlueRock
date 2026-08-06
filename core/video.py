"""Video handling: first-frame extraction for thumbnails, MIME type mapping,
and transcoding to a browser-safe codec.

OpenCV's VideoCapture needs a real file path to read frames, so video bytes
are briefly written to a temp file just long enough to grab one frame, then
the temp file is deleted immediately -- nothing persists on disk.

Some videos (very commonly HEVC/H.265, the default recording codec on most
modern phones) decode fine here -- OpenCV's bundled backend supports a much
wider codec set than browsers do -- but then fail to play at all in the
actual <video> element, because Chrome/Firefox generally have no HEVC
decoder. To embed those reliably, non-H.264 videos under the inline size
cap get transcoded to H.264/AAC with ffmpeg (a real binary, installed via
apt in the Dockerfile -- OpenCV's own bundled ffmpeg libs can decode HEVC
but the pip wheel excludes the H.264 encoder for licensing reasons, so it
can't do this transcode itself). If ffmpeg isn't available (e.g. running
this module outside the container), transcoding is silently skipped and
the original bytes are used as before -- the frontend's codec-error
message (frontend/gallery.js) is the fallback for whatever's left.
"""

import os
import shutil
import subprocess
import tempfile

from PIL import Image

try:
    import cv2
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False

HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None

VIDEO_EXTS = ('.mp4', '.mov', '.avi', '.mkv', '.webm', '.m4v')

VIDEO_MIME = {
    '.mp4': 'video/mp4',
    '.m4v': 'video/mp4',
    '.mov': 'video/quicktime',
    '.webm': 'video/webm',
    '.mkv': 'video/x-matroska',
    '.avi': 'video/x-msvideo',
}

# Above this size, the video's full bytes are NOT base64-inlined into the
# gallery document. A single self-contained HTML document has no way to
# stream or byte-range a `data:` URI, so a large video would otherwise be
# read into memory in full, inflated ~33% by base64, and dumped into the
# page as one enormous string the browser must parse before anything
# renders. The thumbnail is still generated and shown; the item is just
# flagged `too_large` so the frontend can say so instead of silently
# embedding hundreds of MB.
VIDEO_INLINE_MAX_BYTES = 120 * 1024 * 1024  # 120 MB

# How much of the file to write to a temp file when hunting for the first
# frame. Container/metadata headers plus a few frames of most common
# formats live well within this, so this avoids writing multi-GB files to
# disk just to decode one frame. If decoding fails on the partial file we
# fall back to writing the whole thing.
_PARTIAL_PROBE_BYTES = 8 * 1024 * 1024  # 8 MB


def video_mime_for(ext: str) -> str:
    return VIDEO_MIME.get(ext, 'video/mp4')


# Codecs that play natively in essentially every modern browser without
# needing a transcode. h264 covers the vast majority of "normal" videos;
# vp8/vp9/av1 cover webm. Anything else (hevc/h265 most commonly, but also
# things like mpeg4/wmv variants some old camcorders and NVRs produce)
# gets transcoded below.
_BROWSER_SAFE_CODECS = {"h264", "vp8", "vp9", "av1"}

# Bounded so one huge/slow video can't stall the whole batch indefinitely
# -- if it can't finish in time, fall back to the original bytes and let
# the frontend's codec-error message (frontend/gallery.js) handle it.
TRANSCODE_TIMEOUT_SECONDS = int(os.environ.get("BLUEROCK_TRANSCODE_TIMEOUT_SECONDS", "120"))


def _probe_codec(path: str) -> str | None:
    """Return the video stream's codec name (e.g. "h264", "hevc"), or None
    if ffprobe isn't available or the file can't be probed."""
    if not HAS_FFMPEG:
        return None
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=codec_name", "-of", "csv=p=0", path],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=15, text=True,
        )
        codec = result.stdout.strip().lower()
        return codec or None
    except (subprocess.TimeoutExpired, OSError):
        return None


def ensure_browser_playable(video_bytes: bytes, ext: str) -> tuple[bytes, str]:
    """Return (bytes, mime) ready to embed as a browser <video> src.

    If the source is already in a browser-safe codec (or ffmpeg isn't
    available, or probing/transcoding fails for any reason), the original
    bytes and their normal MIME type are returned unchanged -- this never
    raises, it just falls back to "do nothing" on any problem, since a
    playable-but-unconverted video plus the frontend's own error message
    is strictly better than losing the video item entirely.
    """
    original_mime = video_mime_for(ext)
    if not HAS_FFMPEG:
        return video_bytes, original_mime

    with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
        tmp.write(video_bytes)
        src_path = tmp.name

    try:
        codec = _probe_codec(src_path)
        if codec is None or codec in _BROWSER_SAFE_CODECS:
            return video_bytes, original_mime

        out_path = src_path + "_h264.mp4"
        try:
            subprocess.run(
                ["ffmpeg", "-y", "-loglevel", "error", "-i", src_path,
                 "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                 "-pix_fmt", "yuv420p",  # broadest player compatibility
                 "-c:a", "aac", "-b:a", "128k",
                 "-movflags", "+faststart",  # so it can start playing before fully downloaded
                 out_path],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=TRANSCODE_TIMEOUT_SECONDS, check=True,
            )
            with open(out_path, "rb") as f:
                return f.read(), "video/mp4"
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError, OSError):
            return video_bytes, original_mime
        finally:
            if os.path.exists(out_path):
                os.remove(out_path)
    finally:
        os.remove(src_path)


def _decode_first_frame(path: str):
    cap = cv2.VideoCapture(path)
    try:
        success, frame = cap.read()
    finally:
        cap.release()
    if not success:
        return None
    return Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))


def video_first_frame(video_bytes: bytes, ext: str = ".mp4"):
    """Return the first frame of the video as a PIL Image, or None if
    extraction isn't possible (missing OpenCV, unreadable codec, etc.).

    `ext` should be the file's real extension (e.g. ".webm") -- some
    OpenCV backends pick a demuxer based on the file extension hint, so
    hardcoding ".mp4" for every format could make frame extraction less
    reliable for non-mp4 containers.
    """
    if not HAS_CV2:
        return None
    suffix = ext if ext in VIDEO_MIME else ".mp4"

    # First try decoding from just the first few MB -- enough for headers
    # and an early frame in the vast majority of files, and much cheaper
    # than writing the entire video to disk.
    probe = video_bytes[:_PARTIAL_PROBE_BYTES]
    if len(probe) < len(video_bytes):
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(probe)
            tmp_path = tmp.name
        try:
            frame = _decode_first_frame(tmp_path)
            if frame is not None:
                return frame
        finally:
            os.remove(tmp_path)

    # Fall back to the full file if the partial read didn't decode
    # (small files, or a container whose header lives further in).
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(video_bytes)
        tmp_path = tmp.name
    try:
        return _decode_first_frame(tmp_path)
    finally:
        os.remove(tmp_path)