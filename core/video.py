"""Video handling: first-frame extraction for thumbnails, MIME type mapping.

OpenCV's VideoCapture needs a real file path to read frames, so video bytes
are briefly written to a temp file just long enough to grab one frame, then
the temp file is deleted immediately — nothing persists on disk.
"""

import os
import tempfile

from PIL import Image

try:
    import cv2
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False

VIDEO_EXTS = ('.mp4', '.mov', '.avi', '.mkv', '.webm', '.m4v')

VIDEO_MIME = {
    '.mp4': 'video/mp4',
    '.m4v': 'video/mp4',
    '.mov': 'video/quicktime',
    '.webm': 'video/webm',
    '.mkv': 'video/x-matroska',
    '.avi': 'video/x-msvideo',
}


def video_mime_for(ext: str) -> str:
    return VIDEO_MIME.get(ext, 'video/mp4')


def video_first_frame(video_bytes: bytes):
    """Return the first frame of the video as a PIL Image, or None if
    extraction isn't possible (missing OpenCV, unreadable codec, etc.)."""
    if not HAS_CV2:
        return None
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
        tmp.write(video_bytes)
        tmp_path = tmp.name
    try:
        cap = cv2.VideoCapture(tmp_path)
        success, frame = cap.read()
        cap.release()
        if not success:
            return None
        return Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
    finally:
        os.remove(tmp_path)