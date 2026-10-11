"""HTTP layer for file uploads. Routes stay thin: validation, persistence and
response shaping only. GIS work lives in app.services.

Routes are plain `def`, not `async def`. GDAL reads and reprojection are blocking,
CPU-bound calls; FastAPI runs sync routes in a thread pool, so one slow upload
does not freeze the event loop for every other request.
"""

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
from app.models import FileStatus, UploadedFile, new_file_id
from app.schemas import FeatureMeasurement, FileInfo, MeasurementsResponse, MeasurementSummary
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
def get_measurements(file_id: str, db: DbSession):
    record = _get_ready_or_409(db, file_id)
    features = record.features
    measured = [f for f in features if f.area_m2 is not None or f.length_m is not None]
    summary = MeasurementSummary(
        total_area_m2=round(sum(f.area_m2 or 0 for f in features), 3),
        total_length_m=round(sum(f.length_m or 0 for f in features), 3),
        measured_features=len(measured),
        unmeasured_features=len(features) - len(measured),
        geometry_types=dict(Counter(f.geometry_type or "None" for f in features)),
    )
    return MeasurementsResponse(
        file_id=record.id,
        crs=record.crs,
        summary=summary,
        features=[FeatureMeasurement.model_validate(f) for f in features],
    )


@router.get("/{file_id}/features/")
def get_features(
    file_id: str,
    db: DbSession,
    crs: Annotated[
        Literal["wgs84", "source"],
        Query(description="wgs84 (GeoJSON standard, default) or the file's original CRS"),
    ] = "wgs84",
) -> dict:
    """Every feature as a GeoJSON FeatureCollection, with attributes and measurements."""
    record = _get_ready_or_409(db, file_id)
    layer_crs = {
        layer["name"]: CRS.from_wkt(layer["crs_wkt"]) if layer.get("crs_wkt") else None
        for layer in record.layers
    }

    collection = []
    for f in record.features:
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
