"""Turns a saved upload into Feature rows with measurements."""

import logging
from collections.abc import Iterable
from pathlib import Path

from shapely.geometry import mapping

from app.models import Feature, FileStatus, UploadedFile
from app.services.measure import crs_label, measure
from app.services.parser import ParsedLayer, clean_properties, read_kml, read_shapefiles
from app.services.storage import ShapefileLayer

logger = logging.getLogger(__name__)

MAX_ERROR_LENGTH = 500


def process_file(
    record: UploadedFile,
    path: Path,
    shapefile_layers: list[ShapefileLayer] | None = None,
    archive_warnings: Iterable[str] = (),
) -> None:
    """Populate `record` in place. Any failure marks the record FAILED instead of
    raising, so the client still gets an id it can look up later."""
    try:
        if record.file_type == "kml":
            layers = read_kml(path)
        else:
            layers = read_shapefiles(shapefile_layers or [])
        _store_layers(record, layers, list(archive_warnings))
        record.status = FileStatus.COMPLETED
    except Exception as exc:  # GDAL raises several error types for corrupt input
        logger.exception("Processing failed for file %s", record.id)
        record.features.clear()
        record.feature_count = 0
        record.status = FileStatus.FAILED
        record.error = f"Could not read geospatial data: {exc}"[:MAX_ERROR_LENGTH]


def _store_layers(record: UploadedFile, layers: list[ParsedLayer], warnings: list[str]) -> None:
    index = 0
    layer_info = []
    for layer in layers:
        warnings.extend(layer.warnings)
        label = crs_label(layer.crs)
        frame = layer.frame
        # to_dict("records") returns [] for a frame with rows but no attribute columns
        # (e.g. a shapefile without its .dbf); "index" keeps one dict per row.
        attributes = frame.drop(columns=frame.geometry.name).to_dict("index").values()

        for geom, props in zip(frame.geometry, attributes, strict=True):
            result = measure(geom, layer.crs)
            record.features.append(
                Feature(
                    index=index,
                    layer=layer.name,
                    geometry_type=geom.geom_type if geom is not None else None,
                    geometry=mapping(geom) if geom is not None else None,
                    crs=label,
                    properties=clean_properties(props),
                    area_m2=result.area_m2,
                    perimeter_m=result.perimeter_m,
                    length_m=result.length_m,
                    measurement_crs=result.measurement_crs,
                    note=result.note,
                )
            )
            index += 1

        layer_info.append({
            "name": layer.name,
            "crs": label,
            "crs_wkt": layer.crs.to_wkt() if layer.crs else None,
            "feature_count": len(frame),
        })

    labels = {info["crs"] for info in layer_info if info["feature_count"]}
    if len(labels) == 1:
        record.crs = labels.pop()
    else:
        record.crs = "mixed" if labels else None
    record.feature_count = index
    record.layers = layer_info
    record.warnings = warnings
