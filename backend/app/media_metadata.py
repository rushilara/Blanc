"""File metadata read locally from image and PDF bytes, for the analysis prompt (BLA-71).

Vision analysis sends image/PDF bytes to Gemini **as stored**: EXIF (including GPS) is not
stripped. The model may or may not read embedded metadata reliably, so Blanc also reads the
fields an investigator cares about here, on this machine, and puts them in the prompt as a
text block next to the file, labelled as locally extracted file metadata.

- JPEG / PNG (Pillow): EXIF GPS latitude / longitude / altitude, capture time
  (``DateTimeOriginal`` with ``OffsetTimeOriginal`` when present), camera make and model,
  orientation, plus pixel width and height. Only headers are parsed; pixels are never
  decoded.
- PDF (pypdf): document info (title, author, subject, creator, producer, creation and
  modification dates) and the page count.

Metadata is part of the file, so it is **untrusted evidence**: anyone can write any GPS
position or date into EXIF, and text fields can contain instruction-like text. Values are
returned as plain JSON scalars (strings are cut to ``MAX_TEXT_CHARS`` and stripped of
control characters); the prompt JSON-encodes them like any other evidence.

Never raises: unreadable or malformed metadata gives ``{}`` (and the analysis carries on
with the file alone).
"""

from __future__ import annotations

import io
import logging
import math
import re
import warnings
from typing import Any

logger = logging.getLogger(__name__)

MAX_TEXT_CHARS = 200
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")

# EXIF tag numbers (TIFF/EXIF 2.3).
_MAKE, _MODEL, _ORIENTATION, _DATETIME = 0x010F, 0x0110, 0x0112, 0x0132
_EXIF_IFD, _GPS_IFD = 0x8769, 0x8825
_DATETIME_ORIGINAL, _OFFSET_TIME_ORIGINAL = 0x9003, 0x9011
_GPS_LAT_REF, _GPS_LAT, _GPS_LON_REF, _GPS_LON = 1, 2, 3, 4
_GPS_ALT_REF, _GPS_ALT = 5, 6


def extract_file_metadata(data: bytes, mime_type: str) -> dict[str, Any]:
    """Investigator-relevant metadata embedded in ``data``; ``{}`` if none or unreadable."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            if mime_type in ("image/jpeg", "image/png"):
                return _image_metadata(data)
            if mime_type == "application/pdf":
                return _pdf_metadata(data)
    except Exception as error:  # noqa: BLE001 - malformed evidence must not break analysis
        logger.info("could not read %s metadata: %s", mime_type, type(error).__name__)
    return {}


def _text(value: Any) -> str | None:
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    if value is None:
        return None
    text = _CONTROL.sub("", str(value)).strip()
    return text[:MAX_TEXT_CHARS] or None


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, ZeroDivisionError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _degrees(value: Any, ref: Any) -> float | None:
    """EXIF (degrees, minutes, seconds) rationals and an N/S/E/W ref as signed decimal."""
    try:
        d, m, s = (_number(part) for part in value)
    except (TypeError, ValueError):
        return None
    if d is None or m is None or s is None:
        return None
    degrees = d + m / 60 + s / 3600
    if _text(ref) in ("S", "W"):
        degrees = -degrees
    return round(degrees, 7)


def _image_metadata(data: bytes) -> dict[str, Any]:
    from PIL import Image

    out: dict[str, Any] = {}
    with Image.open(io.BytesIO(data)) as image:  # reads headers only, no pixel decode
        out["width"], out["height"] = image.size
        exif = image.getexif()
        for key, tag in (
            ("camera_make", _MAKE),
            ("camera_model", _MODEL),
            ("modified_time", _DATETIME),
        ):
            if (text := _text(exif.get(tag))) is not None:
                out[key] = text
        orientation = exif.get(_ORIENTATION)
        if isinstance(orientation, int):
            out["orientation"] = orientation
        sub = exif.get_ifd(_EXIF_IFD)
        if (taken := _text(sub.get(_DATETIME_ORIGINAL))) is not None:
            out["capture_time"] = taken
        if (offset := _text(sub.get(_OFFSET_TIME_ORIGINAL))) is not None:
            out["capture_time_offset"] = offset
        gps = exif.get_ifd(_GPS_IFD)
        lat = _degrees(gps.get(_GPS_LAT), gps.get(_GPS_LAT_REF)) if _GPS_LAT in gps else None
        lon = _degrees(gps.get(_GPS_LON), gps.get(_GPS_LON_REF)) if _GPS_LON in gps else None
        if lat is not None and lon is not None:
            out["gps_latitude"], out["gps_longitude"] = lat, lon
        if _GPS_ALT in gps and (alt := _number(gps.get(_GPS_ALT))) is not None:
            below = gps.get(_GPS_ALT_REF) in (1, b"\x01")
            out["gps_altitude_m"] = round(-alt if below else alt, 2)
    return out


def _pdf_metadata(data: bytes) -> dict[str, Any]:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    out: dict[str, Any] = {"page_count": len(reader.pages)}
    info = reader.metadata
    if info is not None:
        for key, name in (
            ("title", "/Title"),
            ("author", "/Author"),
            ("subject", "/Subject"),
            ("creator", "/Creator"),
            ("producer", "/Producer"),
            ("creation_date", "/CreationDate"),
            ("modification_date", "/ModDate"),
        ):
            if (text := _text(info.get(name))) is not None:
                out[key] = text
    return out
