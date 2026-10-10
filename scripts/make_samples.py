"""Generate the sample files in samples/.

    python scripts/make_samples.py

site_survey.kml      three KML folders (plots, access tracks, control points) in WGS84
parcels_utm.zip      the same plots as a shapefile in UTM zone 43N (EPSG:32643)
parcels_no_prj.zip   the plots in WGS84 with the .prj removed, to show CRS inference
"""

import shutil
import tempfile
import zipfile
from pathlib import Path

import geopandas as gpd
from shapely.geometry import Polygon

OUT = Path(__file__).resolve().parent.parent / "samples"

# A small fictional survey site north of Bengaluru.
PLOTS = {
    "Plot A": [(77.5962, 13.1987), (77.5991, 13.1989), (77.5993, 13.1968), (77.5964, 13.1965)],
    "Plot B": [(77.5995, 13.1989), (77.6031, 13.1992), (77.6029, 13.1969), (77.5997, 13.1968)],
    "Plot C": [(77.5966, 13.1962), (77.6012, 13.1964), (77.6008, 13.1941), (77.5975, 13.1938)],
}
TRACKS = {
    "North access": [(77.5948, 13.1995), (77.5978, 13.1993), (77.6012, 13.1995), (77.6040, 13.1998)],
    "Service lane": [(77.5994, 13.2001), (77.5994, 13.1966), (77.6010, 13.1950)],
}
CONTROL_POINTS = {
    "GCP-01": (77.5960, 13.1990),
    "GCP-02": (77.6034, 13.1994),
    "GCP-03": (77.6009, 13.1937),
}


def coords(points):
    return " ".join(f"{lon},{lat},0" for lon, lat in points)


def placemark(name, geometry_xml):
    return f"      <Placemark><name>{name}</name>{geometry_xml}</Placemark>"


def build_kml() -> str:
    plots = [
        placemark(name, "<Polygon><outerBoundaryIs><LinearRing><coordinates>"
                  f"{coords(ring + ring[:1])}</coordinates></LinearRing></outerBoundaryIs></Polygon>")
        for name, ring in PLOTS.items()
    ]
    tracks = [
        placemark(name, f"<LineString><coordinates>{coords(line)}</coordinates></LineString>")
        for name, line in TRACKS.items()
    ]
    points = [
        placemark(name, f"<Point><coordinates>{lon},{lat},0</coordinates></Point>")
        for name, (lon, lat) in CONTROL_POINTS.items()
    ]
    folders = [("Plots", plots), ("Access tracks", tracks), ("Ground control points", points)]
    body = "\n".join(
        f"    <Folder><name>{title}</name>\n" + "\n".join(items) + "\n    </Folder>"
        for title, items in folders
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<kml xmlns="http://www.opengis.net/kml/2.2">\n'
        "  <Document>\n    <name>Site survey</name>\n"
        f"{body}\n  </Document>\n</kml>\n"
    )


def write_zip(frame: gpd.GeoDataFrame, name: str, drop: tuple[str, ...] = ()) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        shp = Path(tmp) / f"{name}.shp"
        frame.to_file(shp)
        with zipfile.ZipFile(OUT / f"{name}.zip", "w", zipfile.ZIP_DEFLATED) as archive:
            for part in sorted(Path(tmp).iterdir()):
                if part.suffix.lower() not in drop:
                    archive.write(part, part.name)


def main() -> None:
    shutil.rmtree(OUT, ignore_errors=True)
    OUT.mkdir()
    (OUT / "site_survey.kml").write_text(build_kml(), encoding="utf-8")

    plots = gpd.GeoDataFrame(
        {"name": list(PLOTS), "owner": ["Survey No. 41", "Survey No. 42", "Survey No. 47"]},
        geometry=[Polygon(ring) for ring in PLOTS.values()],
        crs=4326,
    )
    write_zip(plots.to_crs(32643), "parcels_utm")
    write_zip(plots, "parcels_no_prj", drop=(".prj",))
    print(f"Wrote samples to {OUT}")


if __name__ == "__main__":
    main()
