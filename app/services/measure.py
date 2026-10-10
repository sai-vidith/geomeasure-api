"""Area and length calculation in metres.

Measuring directly on longitude/latitude gives "square degrees", which are not a
real unit: a degree of longitude is ~111 km at the equator and 0 km at the poles.
So every geometry is measured in a projected CRS whose units are metres and whose
distortion is small around that geometry.

Strategy:
  * Source CRS is projected (e.g. a UTM zone or a national grid) and not Mercator:
    measure in it directly, converting its linear unit (feet, etc.) to metres.
  * Source CRS is geographic, or is a Mercator variant (Web Mercator inflates area
    by 1/cos^2(latitude), e.g. ~2x at 45 degrees): reproject to the UTM zone that
    contains the feature's centroid. Each feature gets its own zone, so a file that
    spans several zones is still measured accurately.
  * Near the poles UTM is undefined, so a Lambert azimuthal equal-area projection
    centred on the feature is used instead.
"""

from dataclasses import dataclass
from functools import lru_cache

import numpy as np
import shapely
from pyproj import CRS, Transformer
from shapely.geometry.base import BaseGeometry

WGS84 = CRS.from_epsg(4326)

POLYGONAL = {"Polygon", "MultiPolygon"}
LINEAR = {"LineString", "MultiLineString", "LinearRing"}
POINTS = {"Point", "MultiPoint"}

# Transverse Mercator (UTM) is fine for measuring; these cylindrical variants are not.
MERCATOR_METHODS = {
    "Popular Visualisation Pseudo Mercator",
    "Mercator (variant A)",
    "Mercator (variant B)",
    "Mercator (1SP)",
    "Mercator (2SP)",
}

# A UTM zone is 6 degrees wide; scale error grows quickly beyond it.
UTM_ZONE_WIDTH_DEG = 6.0


@dataclass
class Measurement:
    area_m2: float | None = None
    perimeter_m: float | None = None
    length_m: float | None = None
    measurement_crs: str | None = None
    note: str | None = None


def measure(geom: BaseGeometry | None, crs: CRS | None) -> Measurement:
    if geom is None or geom.is_empty:
        return Measurement(note="Empty geometry")

    geom_type = geom.geom_type
    if geom_type in POINTS:
        return Measurement(note="Point geometry: no measurement required")
    if geom_type not in POLYGONAL | LINEAR:
        return Measurement(note=f"{geom_type} is not supported for measurement")
    if crs is None:
        return Measurement(note="CRS unknown: cannot measure reliably")

    notes = []
    if geom_type in POLYGONAL and not geom.is_valid:
        # A self-intersecting "bow-tie" polygon reports a wrong (often near-zero) area.
        # "structure" rebuilds the rings and keeps only polygonal output.
        geom = shapely.make_valid(geom, method="structure", keep_collapsed=False)
        notes.append("invalid polygon repaired before measuring")

    target, target_label, unit_to_m, crs_notes = choose_measurement_crs(geom, crs)
    notes.extend(crs_notes)
    projected = geom if target == crs else reproject(geom, crs, target)

    result = Measurement(measurement_crs=target_label)
    if geom_type in POLYGONAL:
        result.area_m2 = round(projected.area * unit_to_m**2, 3)
        result.perimeter_m = round(projected.length * unit_to_m, 3)
    else:
        result.length_m = round(projected.length * unit_to_m, 3)
    if notes:
        result.note = "; ".join(notes).capitalize()
    return result


def choose_measurement_crs(geom: BaseGeometry, crs: CRS) -> tuple[CRS, str, float, list[str]]:
    """Return (target CRS, label, factor from target units to metres, notes)."""
    if crs.is_projected and not is_mercator(crs):
        unit_to_m = crs.axis_info[0].unit_conversion_factor
        return crs, crs_label(crs), unit_to_m, []

    lonlat = geom if crs == WGS84 else reproject(geom, crs, WGS84)
    lon, lat = lonlat.centroid.x, lonlat.centroid.y
    notes = []
    minx, _, maxx, _ = lonlat.bounds
    if maxx - minx > UTM_ZONE_WIDTH_DEG:
        notes.append("feature is wider than one UTM zone, expect reduced accuracy")

    target = utm_crs_for(lon, lat)
    if target is None:
        target = polar_equal_area(round(lon, 4), round(lat, 4))
        return target, f"LAEA({lat:.4f}, {lon:.4f})", 1.0, notes
    return target, crs_label(target), 1.0, notes


def is_mercator(crs: CRS) -> bool:
    operation = crs.coordinate_operation
    return operation is not None and operation.method_name in MERCATOR_METHODS


def utm_crs_for(lon: float, lat: float) -> CRS | None:
    """WGS84 / UTM zone for a point, or None outside UTM's latitude range (80S-84N)."""
    if not -80.0 <= lat <= 84.0:
        return None
    zone = min(int((lon + 180.0) // 6) + 1, 60)
    epsg = (32600 if lat >= 0 else 32700) + zone
    return _crs_from_epsg(epsg)


@lru_cache(maxsize=128)
def _crs_from_epsg(epsg: int) -> CRS:
    return CRS.from_epsg(epsg)


@lru_cache(maxsize=32)
def polar_equal_area(lon: float, lat: float) -> CRS:
    return CRS.from_proj4(f"+proj=laea +lat_0={lat} +lon_0={lon} +datum=WGS84 +units=m +no_defs")


def reproject(geom: BaseGeometry, source: CRS, target: CRS) -> BaseGeometry:
    transformer = _transformer(source.to_wkt(), target.to_wkt())

    def _apply(coords: np.ndarray) -> np.ndarray:
        x, y = transformer.transform(coords[:, 0], coords[:, 1])
        return np.column_stack([x, y])

    return shapely.transform(geom, _apply)


@lru_cache(maxsize=256)
def _transformer(source_wkt: str, target_wkt: str) -> Transformer:
    # always_xy: EPSG:4326 officially orders axes lat, lon, but shapely/GeoJSON
    # coordinates are x=lon, y=lat. Without this flag the axes get swapped.
    return Transformer.from_crs(CRS(source_wkt), CRS(target_wkt), always_xy=True)


def crs_label(crs: CRS | None) -> str | None:
    if crs is None:
        return None
    epsg = crs.to_epsg()
    return f"EPSG:{epsg}" if epsg else crs.name
