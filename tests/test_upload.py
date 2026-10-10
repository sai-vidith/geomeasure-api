"""Upload validation, lifecycle endpoints and failure handling."""

import io
import zipfile

import geopandas as gpd
from shapely.geometry import Point, box

from tests.conftest import kml_bytes, shapefile_zip


def _square_frame():
    return gpd.GeoDataFrame({"name": ["plot"]}, geometry=[box(77.5, 12.9, 77.51, 12.91)], crs=4326)


def test_kml_upload_returns_file_info(upload, workdir):
    response = upload("survey.kml", kml_bytes(_square_frame(), workdir))

    assert response.status_code == 201
    body = response.json()
    assert body["filename"] == "survey.kml"
    assert body["file_type"] == "kml"
    assert body["status"] == "COMPLETED"
    assert body["crs"] == "EPSG:4326"
    assert body["feature_count"] == 1


def test_get_file_info(client, upload, workdir):
    file_id = upload("survey.kml", kml_bytes(_square_frame(), workdir)).json()["id"]

    response = client.get(f"/api/files/{file_id}/")

    assert response.status_code == 200
    assert response.json()["id"] == file_id


def test_unknown_file_returns_404(client):
    assert client.get("/api/files/doesnotexist/").status_code == 404
    assert client.get("/api/files/doesnotexist/measurements/").status_code == 404


def test_rejects_unsupported_extension(upload):
    response = upload("notes.txt", b"hello")

    assert response.status_code == 400
    assert "Unsupported file extension" in response.json()["detail"]


def test_rejects_empty_file(upload):
    response = upload("empty.kml", b"")

    assert response.status_code == 400
    assert "empty" in response.json()["detail"]


def test_rejects_zip_that_is_not_an_archive(upload):
    response = upload("fake.zip", b"definitely not a zip")

    assert response.status_code == 400
    assert "not a valid zip" in response.json()["detail"]


def test_rejects_zip_without_shapefile(upload):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("readme.txt", "no shapes here")

    response = upload("nothing.zip", buffer.getvalue())

    assert response.status_code == 400
    assert "No .shp file" in response.json()["detail"]


def test_rejects_zip_slip_paths(upload):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("../../escape.shp", b"x")

    response = upload("evil.zip", buffer.getvalue())

    assert response.status_code == 400
    assert "unsafe path" in response.json()["detail"]


def test_corrupt_kml_is_stored_as_failed(client, upload):
    response = upload("broken.kml", b"<kml><Document><Placemark>")

    assert response.status_code == 422
    body = response.json()
    assert body["status"] == "FAILED"
    assert body["error"]

    # The record still exists, and measurement requests explain why they cannot succeed.
    assert client.get(f"/api/files/{body['id']}/").json()["status"] == "FAILED"
    assert client.get(f"/api/files/{body['id']}/measurements/").status_code == 409


def test_list_and_delete(client, upload, workdir):
    file_id = upload("survey.kml", kml_bytes(_square_frame(), workdir)).json()["id"]

    listed = [f["id"] for f in client.get("/api/files/").json()]
    assert file_id in listed

    assert client.delete(f"/api/files/{file_id}/").status_code == 204
    assert client.get(f"/api/files/{file_id}/").status_code == 404


def test_features_endpoint_returns_geojson_in_wgs84(client, upload, workdir):
    frame = gpd.GeoDataFrame(
        {"name": ["plot"]}, geometry=[box(700000, 1400000, 701000, 1401000)], crs=32643
    )
    file_id = upload("plots.zip", shapefile_zip({"plots": frame}, workdir)).json()["id"]

    collection = client.get(f"/api/files/{file_id}/features/").json()
    feature = collection["features"][0]
    lon, lat = feature["geometry"]["coordinates"][0][0]

    assert collection["type"] == "FeatureCollection"
    assert feature["properties"]["name"] == "plot"
    # UTM 43N easting 700000 / northing 1400000 is near Bengaluru.
    assert 76 < lon < 79 and 12 < lat < 13

    source = client.get(f"/api/files/{file_id}/features/?crs=source").json()
    assert source["features"][0]["geometry"]["coordinates"][0][0][0] > 1000


def test_kml_display_fields_are_not_reported_as_properties(client, upload, workdir):
    file_id = upload("survey.kml", kml_bytes(_square_frame(), workdir)).json()["id"]

    props = client.get(f"/api/files/{file_id}/features/").json()["features"][0]["properties"]

    assert props["Name"] == "plot"
    assert not {"tessellate", "extrude", "visibility"} & props.keys()


def test_created_at_keeps_utc_offset(client, upload, workdir):
    file_id = upload("survey.kml", kml_bytes(_square_frame(), workdir)).json()["id"]

    created_at = client.get(f"/api/files/{file_id}/").json()["created_at"]

    assert created_at.endswith("Z") or created_at.endswith("+00:00")


def test_properties_are_cleaned(client, upload, workdir):
    frame = gpd.GeoDataFrame(
        {"name": ["marker"], "owner": [None]}, geometry=[Point(77.5, 12.9)], crs=4326
    )
    file_id = upload("points.zip", shapefile_zip({"points": frame}, workdir)).json()["id"]

    props = client.get(f"/api/files/{file_id}/features/").json()["features"][0]["properties"]

    assert props["name"] == "marker"
    assert "owner" not in props
