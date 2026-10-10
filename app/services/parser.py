"""Reading geospatial files into GeoDataFrames, one per layer."""

from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import pyogrio
from pyproj import CRS

from app.services.measure import WGS84
from app.services.storage import ShapefileLayer

# Lets GDAL rebuild a missing .shx index from the .shp instead of refusing to open it.
pyogrio.set_gdal_config_options({"SHAPE_RESTORE_SHX": True})

# Rendering hints the LIBKML driver exposes as columns on every feature (with -1
# meaning "not set"). They describe how Google Earth draws a shape, not the data.
KML_DISPLAY_FIELDS = ("tessellate", "extrude", "visibility", "drawOrder", "icon", "altitudeMode")


@dataclass
class ParsedLayer:
    name: str
    frame: gpd.GeoDataFrame
    crs: CRS | None
    warnings: list[str] = field(default_factory=list)


def read_kml(path: Path) -> list[ParsedLayer]:
    # A KML Folder is exposed by GDAL as a separate layer. gpd.read_file() without a
    # layer argument returns only the first one, silently dropping everything else.
    layer_names = [str(row[0]) for row in pyogrio.list_layers(path)]
    layers = []
    for name in layer_names:
        frame = gpd.read_file(path, layer=name)
        frame = frame.drop(columns=[c for c in KML_DISPLAY_FIELDS if c in frame.columns])
        # The KML spec mandates WGS84, so a missing CRS here is not ambiguous.
        layers.append(ParsedLayer(name=name, frame=frame, crs=frame.crs or WGS84))
    return layers


def read_shapefiles(shapefile_layers: list[ShapefileLayer]) -> list[ParsedLayer]:
    layers = []
    for layer in shapefile_layers:
        frame = gpd.read_file(layer.shp_path)
        warnings = list(layer.warnings)
        crs = frame.crs
        if crs is None:
            crs, note = infer_crs(frame)
            warnings.append(note)
        layers.append(ParsedLayer(name=layer.name, frame=frame, crs=crs, warnings=warnings))
    return layers


def infer_crs(frame: gpd.GeoDataFrame) -> tuple[CRS | None, str]:
    """Best guess for a layer without a CRS definition.

    Coordinates that all fall inside longitude/latitude ranges are almost certainly
    WGS84. Anything else is some projected system we cannot identify, and guessing
    would produce confidently wrong measurements, so we leave it unknown.
    """
    if frame.empty or frame.geometry.isna().all():
        return None, "CRS unknown and layer has no geometry to infer it from"
    minx, miny, maxx, maxy = frame.total_bounds
    if -180 <= minx <= maxx <= 180 and -90 <= miny <= maxy <= 90:
        return WGS84, "CRS missing; coordinates look like longitude/latitude, assumed EPSG:4326"
    return None, "CRS missing and coordinates are not longitude/latitude; measurements skipped"


def clean_properties(record: dict) -> dict:
    """Turn a row of attributes into plain JSON values, dropping empty ones.

    The LIBKML driver adds columns like begin/end/altitudeMode to every feature
    even when the file never sets them; dropping nulls keeps responses readable.
    """
    cleaned = {}
    for key, value in record.items():
        # pd.isna covers None, NaN and NaT. It returns numpy.bool_ for scalars and an
        # array for list-like values, so check the type before trusting the result.
        missing = pd.isna(value)
        if isinstance(missing, (bool, np.bool_)) and missing:
            continue
        if isinstance(value, np.generic):
            value = value.item()
        if isinstance(value, (datetime, date)):
            value = value.isoformat()
        elif isinstance(value, bytes):
            value = value.decode("utf-8", errors="replace")
        elif not isinstance(value, (str, int, float, bool)):
            value = str(value)
        if value == "":
            continue
        cleaned[key] = value
    return cleaned
