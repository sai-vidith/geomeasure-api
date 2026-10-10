from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import settings
from app.database import Base, engine


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


@app.get("/health", tags=["meta"])
def health() -> dict:
    return {"status": "ok"}
