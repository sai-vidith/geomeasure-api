"""Flat exports of a processed file, for people who want the numbers in a spreadsheet."""

import csv
import io
import re
from collections.abc import Sequence

from app.models import Feature

MEASUREMENT_COLUMNS = [
    "index",
    "layer",
    "geometry_type",
    "area_m2",
    "area_ha",
    "perimeter_m",
    "length_m",
    "measurement_crs",
    "note",
]

# Excel and Google Sheets evaluate a cell that starts with one of these as a formula.
# Attribute text comes from uploaded files, so it must not be able to run anything.
FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def measurements_csv(features: Sequence[Feature]) -> bytes:
    """One row per feature: measurements first, then the file's own attributes."""
    # dict.fromkeys keeps first-seen order, so columns follow the source file.
    attribute_keys = list(dict.fromkeys(key for f in features for key in f.properties))
    headers = {key: _attribute_header(key) for key in attribute_keys}

    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow(MEASUREMENT_COLUMNS + list(headers.values()))
    for f in features:
        area_ha = round(f.area_m2 / 10_000, 6) if f.area_m2 is not None else None
        row = [
            f.index, f.layer, f.geometry_type, f.area_m2, area_ha,
            f.perimeter_m, f.length_m, f.measurement_crs, f.note,
        ]
        row += [f.properties.get(key) for key in attribute_keys]
        writer.writerow(_safe_cell(value) for value in row)

    # The BOM makes Excel open the file as UTF-8 instead of the system code page.
    return buffer.getvalue().encode("utf-8-sig")


def download_name(filename: str, suffix: str) -> str:
    """ASCII-only name for the Content-Disposition header."""
    stem = filename.rsplit(".", 1)[0]
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("_") or "export"
    return f"{stem}{suffix}"


def _attribute_header(key: str) -> str:
    return f"attr_{key}" if key in MEASUREMENT_COLUMNS else key


def _safe_cell(value):
    if value is None:
        return ""
    if isinstance(value, str) and value.startswith(FORMULA_PREFIXES):
        return "'" + value
    return value
