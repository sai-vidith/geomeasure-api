from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models import FileStatus


class LayerInfo(BaseModel):
    name: str
    crs: str | None
    feature_count: int


class FileInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    filename: str
    file_type: str
    status: FileStatus
    crs: str | None
    feature_count: int
    layers: list[LayerInfo]
    warnings: list[str]
    error: str | None
    created_at: datetime


class FeatureMeasurement(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    index: int
    layer: str
    geometry_type: str | None
    area_m2: float | None
    perimeter_m: float | None
    length_m: float | None
    measurement_crs: str | None
    note: str | None


class MeasurementSummary(BaseModel):
    total_area_m2: float
    total_length_m: float
    measured_features: int
    unmeasured_features: int
    geometry_types: dict[str, int]


class MeasurementsResponse(BaseModel):
    file_id: str
    crs: str | None
    summary: MeasurementSummary
    features: list[FeatureMeasurement]
