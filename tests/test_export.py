"""Layer filters, per-layer subtotals and file downloads."""

import csv
import io

import geopandas as gpd
import pytest
from shapely.geometry import LineString, box

from tests.conftest import shapefile_zip


@pytest.fixture
def site(upload, workdir):
    """Two layers in one archive: two 1 km squares and one 2 km line."""
    plots = gpd.GeoDataFrame(
        {"name": ["north", "=HYPERLINK(\"http://example.com\")"], "owner": ["A", "B"]},
        geometry=[box(700000, 1400000, 701000, 1401000), box(702000, 1400000, 703000, 1401000)],
        crs=32643,
    )
    roads = gpd.GeoDataFrame(
        {"name": ["track"], "surface": ["gravel"]},
        geometry=[LineString([(700000, 1400000), (700000, 1402000)])],
        crs=32643,
    )
    return upload("site.zip", shapefile_zip({"plots": plots, "roads": roads}, workdir)).json()["id"]


def _csv_rows(response):
    text = response.content.decode("utf-8-sig")
    return list(csv.DictReader(io.StringIO(text)))


def test_summary_has_subtotals_per_layer(client, site):
    summary = client.get(f"/api/files/{site}/measurements/").json()["summary"]

    by_name = {layer["name"]: layer for layer in summary["layers"]}
    assert by_name["plots"] == {
        "name": "plots", "feature_count": 2, "area_m2": 2_000_000, "length_m": 0,
    }
    assert by_name["roads"]["length_m"] == 2_000


def test_filter_by_layer_narrows_features_and_summary(client, site):
    result = client.get(f"/api/files/{site}/measurements/", params={"layer": "roads"}).json()

    assert [f["layer"] for f in result["features"]] == ["roads"]
    assert result["summary"]["total_area_m2"] == 0
    assert result["summary"]["total_length_m"] == 2_000


def test_filter_by_geometry_type_ignores_case(client, site):
    result = client.get(f"/api/files/{site}/measurements/", params={"geometry_type": "polygon"}).json()

    assert len(result["features"]) == 2
    assert result["summary"]["geometry_types"] == {"Polygon": 2}


def test_unknown_layer_lists_the_valid_ones(client, site):
    response = client.get(f"/api/files/{site}/measurements/", params={"layer": "rivers"})

    assert response.status_code == 404
    assert "plots, roads" in response.json()["detail"]


def test_csv_export_has_measurements_and_attributes(client, site):
    response = client.get(f"/api/files/{site}/export/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert 'filename="site_measurements.csv"' in response.headers["content-disposition"]
    # Excel only reads UTF-8 correctly when the file starts with a byte-order mark.
    assert response.content.startswith(b"\xef\xbb\xbf")

    rows = _csv_rows(response)
    assert len(rows) == 3
    assert rows[0]["area_m2"] == "1000000.0"
    assert rows[0]["area_ha"] == "100.0"
    assert rows[0]["owner"] == "A"
    # Columns from both layers are present; a layer without that attribute leaves it blank.
    assert rows[2]["surface"] == "gravel"
    assert rows[2]["owner"] == ""
    assert rows[2]["area_m2"] == ""


def test_csv_export_neutralises_spreadsheet_formulas(client, site):
    rows = _csv_rows(client.get(f"/api/files/{site}/export/"))

    assert rows[1]["name"].startswith("'=HYPERLINK")


def test_csv_export_respects_layer_filter(client, site):
    rows = _csv_rows(client.get(f"/api/files/{site}/export/", params={"layer": "roads"}))

    assert [row["layer"] for row in rows] == ["roads"]


def test_geojson_export_is_a_wgs84_download(client, site):
    response = client.get(f"/api/files/{site}/export/", params={"format": "geojson"})

    assert response.headers["content-type"] == "application/geo+json"
    assert 'filename="site.geojson"' in response.headers["content-disposition"]
    collection = response.json()
    lon, lat = collection["features"][0]["geometry"]["coordinates"][0][0]
    assert len(collection["features"]) == 3
    assert 76 < lon < 79 and 12 < lat < 13
