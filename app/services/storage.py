"""Saving uploads to disk and unpacking shapefile archives.

Each upload gets its own directory under settings.upload_dir/<file_id>/ so files
from different requests can never collide.
"""

import hashlib
import shutil
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO

from app.config import settings

ALLOWED_EXTENSIONS = {".zip": "shapefile", ".kml": "kml"}
CHUNK_SIZE = 1024 * 1024
# Guard against zip bombs: refuse archives that expand far beyond the upload limit.
MAX_EXPANSION_RATIO = 10


class InvalidUpload(ValueError):
    """Raised when an uploaded file cannot be accepted. The message is shown to the client."""


@dataclass
class ShapefileLayer:
    name: str
    shp_path: Path
    warnings: list[str] = field(default_factory=list)


def detect_file_type(filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        allowed = ", ".join(sorted(ALLOWED_EXTENSIONS))
        raise InvalidUpload(f"Unsupported file extension '{suffix or '(none)'}'. Allowed: {allowed}")
    return ALLOWED_EXTENSIONS[suffix]


def upload_dir_for(file_id: str) -> Path:
    return settings.upload_dir / file_id


def save_upload(file_id: str, filename: str, source: BinaryIO) -> Path:
    """Stream the upload to disk in chunks so a large file is never fully held in memory."""
    max_bytes = settings.max_upload_mb * 1024 * 1024
    target_dir = upload_dir_for(file_id)
    target_dir.mkdir(parents=True, exist_ok=True)
    # Never trust the client's path; keep only the base name.
    target = target_dir / Path(filename).name

    written = 0
    with target.open("wb") as out:
        while chunk := source.read(CHUNK_SIZE):
            written += len(chunk)
            if written > max_bytes:
                raise InvalidUpload(f"File exceeds the {settings.max_upload_mb} MB limit")
            out.write(chunk)

    if written == 0:
        raise InvalidUpload("Uploaded file is empty")
    return target


def remove_upload(file_id: str) -> None:
    shutil.rmtree(upload_dir_for(file_id), ignore_errors=True)


def extract_shapefile(zip_path: Path) -> tuple[list[ShapefileLayer], list[str]]:
    """Unzip a shapefile archive and return every distinct layer inside it.

    Returns (layers, archive_warnings). Archive-level warnings cover things like
    duplicate copies of the same shapefile that were skipped.
    """
    if not zipfile.is_zipfile(zip_path):
        raise InvalidUpload("File is not a valid zip archive")

    extract_dir = zip_path.parent / "extracted"
    extract_dir.mkdir(exist_ok=True)

    with zipfile.ZipFile(zip_path) as archive:
        members = archive.infolist()
        total_size = sum(m.file_size for m in members)
        if total_size > settings.max_upload_mb * 1024 * 1024 * MAX_EXPANSION_RATIO:
            raise InvalidUpload("Archive expands to an unreasonably large size")
        for member in members:
            # Zip-slip guard: a member like "../../app/main.py" must not escape extract_dir.
            destination = (extract_dir / member.filename).resolve()
            if not destination.is_relative_to(extract_dir.resolve()):
                raise InvalidUpload(f"Archive contains an unsafe path: {member.filename}")
        archive.extractall(extract_dir)

    shp_files = sorted(
        p for p in extract_dir.rglob("*")
        if p.is_file()
        and p.suffix.lower() == ".shp"
        # macOS zips include __MACOSX/._name.shp resource-fork files; ignore them.
        and "__MACOSX" not in p.parts
        and not p.name.startswith("._")
    )
    if not shp_files:
        raise InvalidUpload(
            "No .shp file found in the archive. Expected at least a .shp with its .shx and .dbf "
            "(and ideally .prj) sharing the same base name."
        )

    unique, archive_warnings = _drop_duplicates(shp_files, extract_dir)
    layers = [
        ShapefileLayer(
            name=shp.relative_to(extract_dir).with_suffix("").as_posix(),
            shp_path=shp,
            warnings=_check_companions(shp),
        )
        for shp in unique
    ]
    return layers, archive_warnings


def _companion(shp: Path, ext: str) -> Path | None:
    """Find a sidecar file next to the .shp, matching the extension case-insensitively."""
    for candidate in shp.parent.iterdir():
        if candidate.stem == shp.stem and candidate.suffix.lower() == ext:
            return candidate
    return None


def _check_companions(shp: Path) -> list[str]:
    """Report missing sidecar files. Only the .shp itself is mandatory; GDAL can work
    around the rest, so each gap becomes a warning describing what was done about it."""
    missing = [ext for ext in (".shx", ".dbf", ".prj") if _companion(shp, ext) is None]
    if not missing:
        return []

    consequences = {
        ".shx": "the spatial index was rebuilt from the .shp",
        ".dbf": "features have geometry but no attributes",
        ".prj": "CRS is unknown and will be inferred from the coordinates if possible",
    }
    details = "; ".join(f"{ext}: {consequences[ext]}" for ext in missing)
    return [f"Missing {', '.join(missing)} for '{shp.name}' ({details})"]


def _fingerprint(shp: Path) -> str:
    """Content hash over the files that define a layer: geometry, attributes and CRS.
    The .shx is derived from the .shp, so it is left out."""
    digest = hashlib.sha256()
    for ext in (".shp", ".dbf", ".prj"):
        part = _companion(shp, ext)
        digest.update(ext.encode())
        if part is not None:
            digest.update(part.read_bytes())
    return digest.hexdigest()


def _drop_duplicates(shp_files: list[Path], base: Path) -> tuple[list[Path], list[str]]:
    """Keep one copy of byte-identical shapefiles; every distinct one is processed."""
    seen: dict[str, Path] = {}
    warnings: list[str] = []
    for shp in shp_files:
        key = _fingerprint(shp)
        if key in seen:
            dup, kept = shp.relative_to(base).as_posix(), seen[key].relative_to(base).as_posix()
            warnings.append(f"Skipped '{dup}': identical to '{kept}'")
        else:
            seen[key] = shp
    return list(seen.values()), warnings
