"""Image handling: uniform thumbnails, full-resolution encoding, base64 conversion.

Kept framework-agnostic (no Streamlit imports) so it can be unit-tested and
reused independent of the app entrypoint.
"""

import base64
import io

from PIL import Image, ImageOps

THUMB_SIZE = (300, 300)
FULL_MAX_DIM = 1920


def make_uniform_thumbnail(img: Image.Image, size=THUMB_SIZE) -> Image.Image:
    """Crop-to-fill so every thumbnail is exactly `size`, regardless of the
    source image's aspect ratio — keeps the gallery grid visually uniform."""
    return ImageOps.fit(img.convert("RGB"), size, Image.LANCZOS)


def img_to_b64(img: Image.Image, fmt: str = "JPEG", quality: int = 88) -> str:
    """Encode a PIL image as a base64 data URI."""
    buf = io.BytesIO()
    if fmt == "JPEG":
        img = img.convert("RGB")
        img.save(buf, format="JPEG", quality=quality)
        mime = "image/jpeg"
    else:
        img.save(buf, format="PNG")
        mime = "image/png"
    return f"data:{mime};base64,{base64.b64encode(buf.getvalue()).decode()}"


def full_image_b64(img: Image.Image) -> str:
    """Encode a full-resolution (but dimension-capped) version of the image.

    Downscales anything larger than FULL_MAX_DIM on its longest side to keep
    the payload reasonable, and preserves transparency (PNG) only when the
    source actually has an alpha channel — otherwise uses JPEG for a smaller
    payload.
    """
    w, h = img.size
    scale = min(1.0, FULL_MAX_DIM / max(w, h))
    if scale < 1.0:
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    has_alpha = img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info)
    return img_to_b64(img, fmt="PNG" if has_alpha else "JPEG")


def human_size(n: int) -> str:
    """Format a byte count as a human-readable string, e.g. 3512 -> '3.4 KB'."""
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"