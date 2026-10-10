import io
import os
import tempfile
import zipfile
from pathlib import Path

# Point the app at a throwaway data directory before anything imports app.config.
_TMP = Path(tempfile.mkdtemp(prefix="geomeasure-tests-"))
os.environ["GEOMEASURE_DATA_DIR"] = str(_TMP)
os.environ["GEOMEASURE_DATABASE_URL"] = f"sqlite:///{(_TMP / 'test.db').as_posix()}"

import geopandas as gpd  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

SHAPEFILE_PARTS = (".shp", ".shx", ".dbf", ".prj", ".cpg")


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def upload(client):
    def _upload(filename: str, content: bytes):
        return client.post("/api/files/", files={"file": (filename, content)})

    return _upload


@pytest.fixture
def workdir(tmp_path):
    return tmp_path


def shapefile_zip(
    frames: dict[str, gpd.GeoDataFrame],
    workdir: Path,
    drop: tuple[str, ...] = (),
    prefix: str = "",
    upper: bool = False,
) -> bytes:
    """Write each frame as <name>.shp and zip the parts.

    drop:   extensions to leave out of the archive, e.g. (".prj",)
    prefix: folder inside the archive
    upper:  uppercase file names, as some older GIS exports produce
    """
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, frame in frames.items():
            folder = workdir / name
            folder.mkdir(parents=True, exist_ok=True)
            frame.to_file(folder / f"{name}.shp")
            for part in folder.iterdir():
                if part.suffix.lower() in drop or part.suffix.lower() not in SHAPEFILE_PARTS:
                    continue
                arcname = f"{prefix}{part.name.upper() if upper else part.name}"
                archive.write(part, arcname)
    return buffer.getvalue()


def kml_bytes(frame: gpd.GeoDataFrame, workdir: Path, name: str = "data") -> bytes:
    path = workdir / f"{name}.kml"
    frame.to_crs(4326).to_file(path, driver="KML")
    return path.read_bytes()
