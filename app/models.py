import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Enum, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class FileStatus(str, enum.Enum):
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


def new_file_id() -> str:
    return uuid.uuid4().hex[:12]


class UploadedFile(Base):
    __tablename__ = "uploaded_files"

    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=new_file_id)
    filename: Mapped[str] = mapped_column(String(255))
    file_type: Mapped[str] = mapped_column(String(16))  # "shapefile" | "kml" | "kmz"
    status: Mapped[FileStatus] = mapped_column(Enum(FileStatus), default=FileStatus.PROCESSING)
    # Single CRS label when every layer agrees, "mixed" when they differ.
    crs: Mapped[str | None] = mapped_column(String(64))
    feature_count: Mapped[int] = mapped_column(Integer, default=0)
    # [{name, crs, crs_wkt, feature_count}]. The WKT is kept so geometries can be
    # reprojected later even when the CRS has no EPSG code.
    layers: Mapped[list] = mapped_column(JSON, default=list)
    warnings: Mapped[list] = mapped_column(JSON, default=list)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    features: Mapped[list["Feature"]] = relationship(
        back_populates="file", cascade="all, delete-orphan", order_by="Feature.index"
    )


class Feature(Base):
    __tablename__ = "features"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    file_id: Mapped[str] = mapped_column(ForeignKey("uploaded_files.id"), index=True)
    index: Mapped[int] = mapped_column(Integer)
    layer: Mapped[str] = mapped_column(String(255))
    geometry_type: Mapped[str | None] = mapped_column(String(32))
    geometry: Mapped[dict | None] = mapped_column(JSON)  # GeoJSON in the source CRS
    crs: Mapped[str | None] = mapped_column(String(64))
    properties: Mapped[dict] = mapped_column(JSON, default=dict)

    # Measurement results. Null when the geometry type has no measurement.
    area_m2: Mapped[float | None] = mapped_column(Float)
    perimeter_m: Mapped[float | None] = mapped_column(Float)
    length_m: Mapped[float | None] = mapped_column(Float)
    measurement_crs: Mapped[str | None] = mapped_column(String(64))
    note: Mapped[str | None] = mapped_column(String(255))

    file: Mapped[UploadedFile] = relationship(back_populates="features")
