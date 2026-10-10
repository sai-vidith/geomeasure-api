"""Measurement correctness.

Reference values come from pyproj.Geod, which computes area and length on the
WGS84 ellipsoid directly. It is independent of the UTM approach used by the app,
so agreement between the two is a meaningful check.
"""

import geopandas as gpd
import pytest
from pyproj import CRS, Geod
from shapely.geometry import LineString, Point, Polygon, box

from app.services.measure import measure, utm_crs_for
from tests.conftest import kml_bytes, shapefile_zip

GEOD = Geod(ellps="WGS84")
TOLERANCE = 0.002  # 0.2 %


def _geodesic_area(geom_lonlat):
    area, _ = GEOD.geometry_area_perimeter(geom_lonlat)
    return abs(area)


def _measurements(client, file_id):
    return client.get(f"/api/files/{file_id}/measurements/").json()


def test_polygon_area_from_geographic_coordinates(client, upload, workdir):
    square = box(77.5, 12.9, 77.51, 12.91)
    frame = gpd.GeoDataFrame({"name": ["plot"]}, geometry=[square], crs=4326)

    file_id = upload("plot.kml", kml_bytes(frame, workdir)).json()["id"]
    feature = _measurements(client, file_id)["features"][0]

    assert feature["measurement_crs"] == "EPSG:32643"
    assert feature["area_m2"] == pytest.approx(_geodesic_area(square), rel=TOLERANCE)


def test_line_length_from_geographic_coordinates(client, upload, workdir):
    line = LineString([(77.5, 12.9), (77.52, 12.93), (77.55, 12.95)])
    frame = gpd.GeoDataFrame({"name": ["road"]}, geometry=[line], crs=4326)

    file_id = upload("road.kml", kml_bytes(frame, workdir)).json()["id"]
    feature = _measurements(client, file_id)["features"][0]

    expected = GEOD.geometry_length(line)
    assert feature["length_m"] == pytest.approx(expected, rel=TOLERANCE)
    assert feature["area_m2"] is None


def test_point_has_no_measurement(client, upload, workdir):
    frame = gpd.GeoDataFrame({"name": ["pin"]}, geometry=[Point(77.5, 12.9)], crs=4326)

    file_id = upload("pin.kml", kml_bytes(frame, workdir)).json()["id"]
    result = _measurements(client, file_id)

    feature = result["features"][0]
    assert feature["area_m2"] is None and feature["length_m"] is None
    assert "Point" in feature["note"]
    assert result["summary"]["unmeasured_features"] == 1


def test_projected_source_is_measured_natively(client, upload, workdir):
    frame = gpd.GeoDataFrame(geometry=[box(700000, 1400000, 701000, 1401000)], crs=32643)

    file_id = upload("utm.zip", shapefile_zip({"plots": frame}, workdir)).json()["id"]
    feature = _measurements(client, file_id)["features"][0]

    assert feature["measurement_crs"] == "EPSG:32643"
    assert feature["area_m2"] == 1_000_000
    assert feature["perimeter_m"] == 4_000


def test_projected_source_in_feet_is_converted_to_metres(client, upload, workdir):
    # EPSG:2263 (NY Long Island) uses US survey feet.
    frame = gpd.GeoDataFrame(geometry=[box(1_000_000, 200_000, 1_001_000, 201_000)], crs=2263)

    file_id = upload("feet.zip", shapefile_zip({"plots": frame}, workdir)).json()["id"]
    feature = _measurements(client, file_id)["features"][0]

    us_foot = 1200 / 3937
    assert feature["area_m2"] == pytest.approx((1000 * us_foot) ** 2, rel=1e-6)


def test_web_mercator_is_reprojected_not_trusted(client, upload, workdir):
    square = box(10.0, 45.0, 10.01, 45.01)
    frame = gpd.GeoDataFrame(geometry=[square], crs=4326).to_crs(3857)
    naive_area = frame.geometry.area.iloc[0]

    file_id = upload("mercator.zip", shapefile_zip({"plots": frame}, workdir)).json()["id"]
    feature = _measurements(client, file_id)["features"][0]

    true_area = _geodesic_area(square)
    assert feature["measurement_crs"] == "EPSG:32632"
    assert feature["area_m2"] == pytest.approx(true_area, rel=TOLERANCE)
    # Measuring in Web Mercator directly would have been about 2x too large at 45 degrees.
    assert naive_area / true_area > 1.9


def test_self_intersecting_polygon_is_repaired(client, upload, workdir):
    bowtie = Polygon([(77.50, 12.90), (77.51, 12.91), (77.51, 12.90), (77.50, 12.91)])
    frame = gpd.GeoDataFrame(geometry=[bowtie], crs=4326)
    assert bowtie.area == 0  # the two lobes cancel out without repair

    file_id = upload("bowtie.zip", shapefile_zip({"plots": frame}, workdir)).json()["id"]
    feature = _measurements(client, file_id)["features"][0]

    half_square = _geodesic_area(box(77.5, 12.9, 77.51, 12.91)) / 2
    assert feature["area_m2"] == pytest.approx(half_square, rel=TOLERANCE)
    assert "repaired" in feature["note"]


def test_features_in_different_utm_zones_use_their_own_zone(client, upload, workdir):
    frame = gpd.GeoDataFrame(
        geometry=[box(72.5, 19.0, 72.51, 19.01), box(88.3, 22.5, 88.31, 22.51)], crs=4326
    )

    file_id = upload("india.kml", kml_bytes(frame, workdir)).json()["id"]
    features = _measurements(client, file_id)["features"]

    assert [f["measurement_crs"] for f in features] == ["EPSG:32643", "EPSG:32645"]


@pytest.mark.parametrize(
    ("lon", "lat", "epsg"),
    [
        (77.59, 12.97, 32643),    # Bengaluru
        (151.21, -33.87, 32756),  # Sydney, southern hemisphere
        (-0.13, 51.51, 32630),    # London, just west of Greenwich
        (180.0, 0.0, 32660),      # antimeridian edge clamps to zone 60
    ],
)
def test_utm_zone_selection(lon, lat, epsg):
    assert utm_crs_for(lon, lat).to_epsg() == epsg


def test_polar_feature_falls_back_to_equal_area():
    square = box(20.0, 85.0, 20.5, 85.1)

    result = measure(square, CRS.from_epsg(4326))

    assert utm_crs_for(20.25, 85.05) is None
    assert result.measurement_crs.startswith("LAEA")
    assert result.area_m2 == pytest.approx(_geodesic_area(square), rel=TOLERANCE)


def test_unsupported_and_empty_geometries_do_not_crash():
    from shapely.geometry import GeometryCollection

    collection = GeometryCollection([Point(0, 0), LineString([(0, 0), (1, 1)])])

    assert "not supported" in measure(collection, CRS.from_epsg(4326)).note
    assert measure(None, CRS.from_epsg(4326)).note == "Empty geometry"
    assert measure(Polygon(), CRS.from_epsg(4326)).note == "Empty geometry"
