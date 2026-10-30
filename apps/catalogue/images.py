"""Reference-image validation and processing.

Accepts PNG/JPEG by content (magic bytes), enforces size and dimension limits before decoding
pixels, applies the EXIF orientation, then re-encodes. Re-encoding drops all metadata (EXIF, GPS,
camera data), which the upload UI promises. Output is a display image (longest side capped) and a
thumbnail. Images with transparency stay PNG; everything else becomes an optimised JPEG.
"""

from dataclasses import dataclass
from io import BytesIO

from PIL import Image, ImageOps

MAX_BYTES = 5 * 1024 * 1024
MIN_SIDE = 400
MAX_SIDE = 4000
DISPLAY_MAX = 1600
THUMB_MAX = 320
JPEG_QUALITY = 85

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_JPEG_MAGIC = b"\xff\xd8\xff"

# Hard ceiling on decoded pixels; the dimension limits above already imply it (4000 x 4000).
Image.MAX_IMAGE_PIXELS = MAX_SIDE * MAX_SIDE


LIMITS = {
    "max_bytes": MAX_BYTES,
    "min_side_px": MIN_SIDE,
    "max_side_px": MAX_SIDE,
    "accepted_types": ["image/png", "image/jpeg"],
}


def _mb(n: int) -> str:
    return f"{n / (1024 * 1024):.1f} MB"


class ImageRejected(Exception):
    """A user-facing rejection. ``message`` says what is wrong and what to do about it;
    ``details`` carries the limits and the received values so a client can build its own hint."""

    def __init__(self, status: int, code: str, message: str, details: dict | None = None):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message
        self.details = {"limits": LIMITS, **(details or {})}


@dataclass
class ProcessedImage:
    main: bytes
    thumbnail: bytes
    ext: str
    content_type: str
    width: int
    height: int


def _sniff(header: bytes) -> str:
    if header.startswith(_PNG_MAGIC):
        return "PNG"
    if header.startswith(_JPEG_MAGIC):
        return "JPEG"
    raise ImageRejected(
        415, "unsupported_media_type",
        "This file type isn't supported. Please upload a PNG or JPEG image "
        "(other formats such as GIF, WebP or HEIC need to be converted first).",
    )


def _has_alpha(img: Image.Image) -> bool:
    return img.mode in ("RGBA", "LA", "PA") or "transparency" in img.info


def _encode(img: Image.Image, keep_alpha: bool, max_side: int) -> tuple[bytes, int, int]:
    out = img.copy()
    out.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)  # never upscales
    buf = BytesIO()
    if keep_alpha:
        out.convert("RGBA").save(buf, "PNG", optimize=True)
    else:
        out.convert("RGB").save(buf, "JPEG", quality=JPEG_QUALITY, optimize=True, progressive=True)
    return buf.getvalue(), *out.size


def process_upload(uploaded) -> ProcessedImage:
    if uploaded.size > MAX_BYTES:
        raise ImageRejected(
            413, "file_too_large",
            f"This image is {_mb(uploaded.size)}, which is over the {_mb(MAX_BYTES)} limit. "
            "Please compress it or save a smaller version, then upload it again.",
            {"received_bytes": uploaded.size},
        )

    header = uploaded.read(16)
    uploaded.seek(0)
    _sniff(header)

    try:
        probe = Image.open(uploaded)
        probe.verify()  # structural check; verify() invalidates the object
        uploaded.seek(0)
        img = Image.open(uploaded)
        width, height = img.size  # read from the header, before pixels are decoded
        received = {"received_width_px": width, "received_height_px": height}
        if min(width, height) < MIN_SIDE:
            raise ImageRejected(
                422, "image_invalid",
                f"This image is {width} x {height} px, which is too small. Please upload one that "
                f"is at least {MIN_SIDE} px on its shortest side.",
                {**received, "reason": "too_small"},
            )
        if max(width, height) > MAX_SIDE:
            raise ImageRejected(
                422, "image_invalid",
                f"This image is {width} x {height} px, which is too large. Please resize it so "
                f"its longest side is {MAX_SIDE} px or less, then upload it again.",
                {**received, "reason": "too_large"},
            )
        img = ImageOps.exif_transpose(img)  # bake in orientation before metadata is dropped
        keep_alpha = _has_alpha(img)
        main, w, h = _encode(img, keep_alpha, DISPLAY_MAX)
        thumb, _, _ = _encode(img, keep_alpha, THUMB_MAX)
    except ImageRejected:
        raise
    except Exception as exc:  # truncated, corrupt, decompression bomb, ...
        raise ImageRejected(
            422, "image_invalid",
            "We couldn't read this image. The file may be damaged. "
            "Try opening it and saving or exporting it again, then upload it once more.",
            {"reason": "unreadable"},
        ) from exc

    ext = "png" if keep_alpha else "jpg"
    return ProcessedImage(
        main=main, thumbnail=thumb, ext=ext,
        content_type="image/png" if keep_alpha else "image/jpeg", width=w, height=h,
    )
