"""Image intake, validation and quality checks (Pillow for header checks, OpenCV for processing)."""
from __future__ import annotations

import base64
import binascii
import io
import re
from dataclasses import dataclass

import cv2
import numpy as np
from PIL import Image, UnidentifiedImageError

# Guard against decompression bombs even before our own dimension check.
Image.MAX_IMAGE_PIXELS = 1920 * 1920 * 2

_DATA_URL_RE = re.compile(r"^data:image/(jpeg|png);base64,(?P<data>[A-Za-z0-9+/=\s]+)$")
_ALLOWED_FORMATS = {"JPEG", "PNG"}


class ImageRejected(Exception):
    """An image failed validation. ``code`` is machine-readable, ``message`` is user guidance."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class ImageLimits:
    max_bytes: int
    min_dim: int
    max_dim: int
    processing_max_width: int

    @classmethod
    def from_config(cls, cfg) -> "ImageLimits":
        return cls(cfg["MAX_IMAGE_BYTES"], cfg["MIN_IMAGE_DIM"], cfg["MAX_IMAGE_DIM"], cfg["PROCESSING_MAX_WIDTH"])


def decode_data_url(value: object, limits: ImageLimits) -> bytes:
    """Decode a browser canvas data URL (JPEG/PNG only) into raw bytes."""
    if not isinstance(value, str):
        raise ImageRejected("invalid_image", "Image data is missing or malformed.")
    # base64 expands by 4/3; reject oversized payloads before decoding.
    if len(value) > limits.max_bytes * 4 // 3 + 64:
        raise ImageRejected("image_too_large", "Image is too large. Lower the camera resolution.")
    match = _DATA_URL_RE.match(value)
    if not match:
        raise ImageRejected("invalid_image", "Only JPEG or PNG camera frames are accepted.")
    try:
        raw = base64.b64decode(match.group("data"), validate=False)
    except (binascii.Error, ValueError):
        raise ImageRejected("invalid_image", "Image data could not be decoded.") from None
    if len(raw) > limits.max_bytes:
        raise ImageRejected("image_too_large", "Image is too large. Lower the camera resolution.")
    return raw


def load_rgb(raw: bytes, limits: ImageLimits) -> np.ndarray:
    """Validate format/dimensions and return an RGB uint8 array, downscaled for processing."""
    if len(raw) > limits.max_bytes:
        raise ImageRejected("image_too_large", "Image is too large. Lower the camera resolution.")
    if not (raw.startswith(b"\xff\xd8\xff") or raw.startswith(b"\x89PNG\r\n\x1a\n")):
        raise ImageRejected("invalid_image", "Only JPEG or PNG images are accepted.")
    try:
        with Image.open(io.BytesIO(raw)) as probe:
            fmt, (width, height) = probe.format, probe.size
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise ImageRejected("invalid_image", "The image file is corrupt or unsupported.") from None
    if fmt not in _ALLOWED_FORMATS:
        raise ImageRejected("invalid_image", "Only JPEG or PNG images are accepted.")
    if min(width, height) < limits.min_dim:
        raise ImageRejected("image_too_small", f"Image is too small ({width}x{height}). Use at least {limits.min_dim}px.")
    if max(width, height) > limits.max_dim:
        raise ImageRejected("image_too_large", f"Image dimensions {width}x{height} exceed {limits.max_dim}px.")

    bgr = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
    if bgr is None:
        raise ImageRejected("invalid_image", "The image could not be decoded.")
    if bgr.shape[1] > limits.processing_max_width:
        scale = limits.processing_max_width / bgr.shape[1]
        bgr = cv2.resize(bgr, (limits.processing_max_width, int(round(bgr.shape[0] * scale))), interpolation=cv2.INTER_AREA)
    return cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)


def brightness(rgb: np.ndarray) -> float:
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    return float(gray.mean())


def sharpness(rgb: np.ndarray, box: tuple[int, int, int, int] | None = None) -> float:
    """Variance of the Laplacian (higher = sharper), measured on the face crop if given."""
    region = rgb
    if box is not None:
        top, right, bottom, left = box
        h, w = rgb.shape[:2]
        region = rgb[max(top, 0):min(bottom, h), max(left, 0):min(right, w)]
        if region.size == 0:
            region = rgb
    gray = cv2.cvtColor(np.ascontiguousarray(region), cv2.COLOR_RGB2GRAY)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def encode_jpeg(rgb: np.ndarray, quality: int = 90) -> bytes:
    ok, buf = cv2.imencode(".jpg", cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise RuntimeError("JPEG encoding failed")
    return buf.tobytes()
