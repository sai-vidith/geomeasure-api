"""Shapefile archives that are incomplete, duplicated or contain several layers."""

import io
import zipfile

import geopandas as gpd
from shapely.geometry import LineString, box

from tests.conftest import shapefile_zip


def _plots():
    return gpd.GeoDataFrame(
        {"name": ["plot"]}, geometry=[box(700000, 1400000, 701000, 1401000)], crs=32643
    )


def _roads():
    return gpd.GeoDataFrame(
        {"name": ["road"]}, geometry=[LineString([(700000, 1400000), (700000, 1402000)])], crs=32643
    )


def _measurements(client, file_id):
    return client.get(f"/api/files/{file_id}/measurements/").json()


def test_distinct_shapefiles_are_all_processed(client, upload, workdir):
    response = upload("site.zip", shapefile_zip({"plots": _plots(), "roads": _roads()}, workdir))

    body = response.json()
    assert response.status_code == 201
    assert {layer["name"] for layer in body["layers"]} == {"plots", "roads"}
    assert body["feature_count"] == 2

    summary = _measurements(client, body["id"])["summary"]
    assert summary["total_area_m2"] == 1_000_000
    assert summary["total_length_m"] == 2_000


def test_identical_shapefiles_are_processed_once(upload, workdir):
    single = shapefile_zip({"plots": _plots()}, workdir)
    buffer = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(single)) as source, zipfile.ZipFile(buffer, "w") as target:
        for item in source.infolist():
            data = source.read(item)
            target.writestr(f"copy1/{item.filename}", data)
            target.writestr(f"copy2/{item.filename}", data)

    body = upload("dupes.zip", buffer.getvalue()).json()

    assert body["feature_count"] == 1
    assert len(body["layers"]) == 1
    assert any("identical" in w for w in body["warnings"])


def test_missing_prj_with_lonlat_coordinates_assumes_wgs84(client, upload, workdir):
    frame = gpd.GeoDataFrame({"name": ["plot"]}, geometry=[box(77.5, 12.9, 77.51, 12.91)], crs=4326)

    body = upload("noprj.zip", shapefile_zip({"plots": frame}, workdir, drop=(".prj",))).json()

    assert body["status"] == "COMPLETED"
    assert body["crs"] == "EPSG:4326"
    assert any("assumed EPSG:4326" in w for w in body["warnings"])
    assert _measurements(client, body["id"])["features"][0]["area_m2"] > 0


def test_missing_prj_with_projected_coordinates_skips_measurement(client, upload, workdir):
    body = upload("noprj.zip", shapefile_zip({"plots": _plots()}, workdir, drop=(".prj",))).json()

    assert body["status"] == "COMPLETED"
    assert body["crs"] is None
    feature = _measurements(client, body["id"])["features"][0]
    assert feature["area_m2"] is None
    assert "CRS unknown" in feature["note"]


def test_missing_dbf_keeps_geometry_and_measurements(client, upload, workdir):
    body = upload("nodbf.zip", shapefile_zip({"plots": _plots()}, workdir, drop=(".dbf",))).json()

    assert body["status"] == "COMPLETED"
    assert any(".dbf" in w for w in body["warnings"])
    assert _measurements(client, body["id"])["features"][0]["area_m2"] == 1_000_000


def test_missing_shx_is_rebuilt(client, upload, workdir):
    body = upload("noshx.zip", shapefile_zip({"plots": _plots()}, workdir, drop=(".shx",))).json()

    assert body["status"] == "COMPLETED"
    assert any(".shx" in w for w in body["warnings"])
    assert _measurements(client, body["id"])["features"][0]["area_m2"] == 1_000_000


def test_all_missing_parts_reported_together(upload, workdir):
    body = upload(
        "bare.zip", shapefile_zip({"plots": _plots()}, workdir, drop=(".shx", ".dbf", ".prj"))
    ).json()

    message = next(w for w in body["warnings"] if w.startswith("Missing"))
    assert ".shx" in message and ".dbf" in message and ".prj" in message


def test_uppercase_extensions_and_nested_folder(upload, workdir):
    body = upload(
        "legacy.zip", shapefile_zip({"plots": _plots()}, workdir, prefix="export/", upper=True)
    ).json()

    assert body["status"] == "COMPLETED"
    assert body["layers"][0]["name"] == "export/PLOTS"
    assert body["warnings"] == []
