from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app import models  # noqa: F401  (registers tables on Base.metadata)
from app.api.files import router as files_router
from app.config import settings
from app.database import Base, engine

STATIC_DIR = Path(__file__).parent / "static"
SAMPLES_DIR = Path(__file__).parent.parent / "samples"
SAMPLE_EXTENSIONS = {".kml", ".kmz", ".zip"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(engine)
    yield


app = FastAPI(
    title="geomeasure-api",
    description="Upload Shapefile or KML data and get area/length measurements in metres.",
    version="0.1.0",
    lifespan=lifespan,
)
app.include_router(files_router)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
if SAMPLES_DIR.is_dir():
    app.mount("/samples", StaticFiles(directory=SAMPLES_DIR), name="samples")


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/samples/", tags=["meta"])
def list_samples() -> list[dict]:
    """Example files bundled with the repo, so the UI can offer them in one click."""
    if not SAMPLES_DIR.is_dir():
        return []
    return [
        {"name": path.name, "url": f"/samples/{path.name}"}
        for path in sorted(SAMPLES_DIR.iterdir())
        if path.suffix.lower() in SAMPLE_EXTENSIONS
    ]


@app.get("/health", tags=["meta"])
def health() -> dict:
    return {"status": "ok"}
