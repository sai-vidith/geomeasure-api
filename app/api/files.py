"""HTTP layer for file uploads. Routes stay thin: validation, persistence and
response shaping only. GIS work lives in app.services.

Routes are plain `def`, not `async def`. GDAL reads and reprojection are blocking,
CPU-bound calls; FastAPI runs sync routes in a thread pool, so one slow upload
does not freeze the event loop for every other request.
"""

import json
from collections import Counter
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, UploadFile, status
from fastapi.responses import JSONResponse
from pyproj import CRS
from shapely.geometry import mapping, shape
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Feature, FileStatus, UploadedFile, new_file_id
from app.schemas import (
    FeatureMeasurement,
    FileInfo,
    LayerSummary,
    MeasurementsResponse,
    MeasurementSummary,
)
from app.services.export import download_name, measurements_csv
from app.services.measure import WGS84, reproject
from app.services.processing import process_file
from app.services.storage import (
    InvalidUpload,
    check_kmz,
    detect_file_type,
    extract_shapefile,
    remove_upload,
    save_upload,
)

router = APIRouter(prefix="/api/files", tags=["files"])

DbSession = Annotated[Session, Depends(get_db)]
LayerFilter = Annotated[str | None, Query(description="Only features from this layer")]
TypeFilter = Annotated[
    str | None, Query(description="Only this geometry type, e.g. Polygon (case-insensitive)")
]


@router.post(
    "/",
    response_model=FileInfo,
    status_code=status.HTTP_201_CREATED,
    responses={
        400: {"description": "File rejected before processing (type, size, archive contents)"},
        422: {"description": "File accepted but could not be read; body is the FAILED record"},
    },
)
def upload_file(file: UploadFile, db: DbSession):
    filename = Path(file.filename or "").name
    file_id = new_file_id()
    try:
        file_type = detect_file_type(filename)
        path = save_upload(file_id, filename, file.file)
        shapefile_layers, archive_warnings = None, []
        if file_type == "shapefile":
            shapefile_layers, archive_warnings = extract_shapefile(path)
        elif file_type == "kmz":
            check_kmz(path)
    except InvalidUpload as exc:
        remove_upload(file_id)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    record = UploadedFile(id=file_id, filename=filename, file_type=file_type)
    db.add(record)
    process_file(record, path, shapefile_layers, archive_warnings)
    db.commit()

    if record.status == FileStatus.FAILED:
        body = FileInfo.model_validate(record).model_dump(mode="json")
        return JSONResponse(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, content=body)
    return record


@router.get("/", response_model=list[FileInfo])
def list_files(db: DbSession, limit: Annotated[int, Query(ge=1, le=200)] = 50):
    query = select(UploadedFile).order_by(UploadedFile.created_at.desc()).limit(limit)
    return db.scalars(query).all()


@router.get("/{file_id}/", response_model=FileInfo)
def get_file(file_id: str, db: DbSession):
    return _get_or_404(db, file_id)


@router.get("/{file_id}/measurements/", response_model=MeasurementsResponse)
def get_measurements(
    file_id: str, db: DbSession, layer: LayerFilter = None, geometry_type: TypeFilter = None
):
    """Measurements for every feature. The summary covers only the features returned,
    so filtering by layer gives that layer's totals."""
    record = _get_ready_or_409(db, file_id)
    features = _select_features(record, layer, geometry_type)
    return MeasurementsResponse(
        file_id=record.id,
        crs=record.crs,
        summary=_summarise(features),
        features=[FeatureMeasurement.model_validate(f) for f in features],
    )


@router.get(
    "/{file_id}/export/",
    responses={200: {"content": {"text/csv": {}, "application/geo+json": {}}}},
)
def export_file(
    file_id: str,
    db: DbSession,
    format: Annotated[Literal["csv", "geojson"], Query()] = "csv",
    layer: LayerFilter = None,
    geometry_type: TypeFilter = None,
):
    """Download the results as a file: a CSV for spreadsheets, or GeoJSON for GIS tools."""
    record = _get_ready_or_409(db, file_id)
    features = _select_features(record, layer, geometry_type)
    if format == "csv":
        content = measurements_csv(features)
        media_type = "text/csv; charset=utf-8"
        name = download_name(record.filename, "_measurements.csv")
    else:
        content = json.dumps(_feature_collection(record, features, "wgs84"))
        media_type = "application/geo+json"
        name = download_name(record.filename, ".geojson")
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@router.get("/{file_id}/features/")
def get_features(
    file_id: str,
    db: DbSession,
    crs: Annotated[
        Literal["wgs84", "source"],
        Query(description="wgs84 (GeoJSON standard, default) or the file's original CRS"),
    ] = "wgs84",
    layer: LayerFilter = None,
    geometry_type: TypeFilter = None,
) -> dict:
    """Every feature as a GeoJSON FeatureCollection, with attributes and measurements."""
    record = _get_ready_or_409(db, file_id)
    return _feature_collection(record, _select_features(record, layer, geometry_type), crs)


def _feature_collection(record: UploadedFile, features: list[Feature], crs: str) -> dict:
    layer_crs = {
        layer["name"]: CRS.from_wkt(layer["crs_wkt"]) if layer.get("crs_wkt") else None
        for layer in record.layers
    }

    collection = []
    for f in features:
        geometry = f.geometry
        source_crs = layer_crs.get(f.layer)
        if crs == "wgs84" and geometry is not None:
            # Projected coordinates without a known CRS cannot be placed on a map.
            if source_crs is None:
                geometry = None
            elif source_crs != WGS84:
                geometry = mapping(reproject(shape(geometry), source_crs, WGS84))
        collection.append({
            "type": "Feature",
            "id": f.index,
            "geometry": geometry,
            "properties": {
                **f.properties,
                "_layer": f.layer,
                "_geometry_type": f.geometry_type,
                "_crs": f.crs,
                "_area_m2": f.area_m2,
                "_perimeter_m": f.perimeter_m,
                "_length_m": f.length_m,
                "_note": f.note,
            },
        })
    return {"type": "FeatureCollection", "features": collection}


@router.delete("/{file_id}/", status_code=status.HTTP_204_NO_CONTENT)
def delete_file(file_id: str, db: DbSession):
    record = _get_or_404(db, file_id)
    db.delete(record)
    db.commit()
    remove_upload(file_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _select_features(
    record: UploadedFile, layer: str | None, geometry_type: str | None
) -> list[Feature]:
    features = list(record.features)
    if layer is not None:
        known = [info["name"] for info in record.layers]
        if layer not in known:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                f"Layer '{layer}' not found. Layers in this file: {', '.join(known) or 'none'}",
            )
        features = [f for f in features if f.layer == layer]
    if geometry_type is not None:
        wanted = geometry_type.lower()
        features = [f for f in features if (f.geometry_type or "").lower() == wanted]
    return features


def _summarise(features: list[Feature]) -> MeasurementSummary:
    measured = [f for f in features if f.area_m2 is not None or f.length_m is not None]
    by_layer: dict[str, list[Feature]] = {}
    for f in features:
        by_layer.setdefault(f.layer, []).append(f)
    return MeasurementSummary(
        total_area_m2=round(sum(f.area_m2 or 0 for f in features), 3),
        total_length_m=round(sum(f.length_m or 0 for f in features), 3),
        measured_features=len(measured),
        unmeasured_features=len(features) - len(measured),
        geometry_types=dict(Counter(f.geometry_type or "None" for f in features)),
        layers=[
            LayerSummary(
                name=name,
                feature_count=len(group),
                area_m2=round(sum(f.area_m2 or 0 for f in group), 3),
                length_m=round(sum(f.length_m or 0 for f in group), 3),
            )
            for name, group in by_layer.items()
        ],
    )


def _get_or_404(db: Session, file_id: str) -> UploadedFile:
    record = db.get(UploadedFile, file_id)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"File '{file_id}' not found")
    return record


def _get_ready_or_409(db: Session, file_id: str) -> UploadedFile:
    record = _get_or_404(db, file_id)
    if record.status == FileStatus.FAILED:
        raise HTTPException(status.HTTP_409_CONFLICT, f"File processing failed: {record.error}")
    if record.status == FileStatus.PROCESSING:
        raise HTTPException(status.HTTP_409_CONFLICT, "File is still being processed")
    return record
