from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration. Every field can be overridden with a GEOMEASURE_* env var."""

    model_config = SettingsConfigDict(env_prefix="GEOMEASURE_", env_file=".env")

    data_dir: Path = Path("data")
    database_url: str = "sqlite:///data/geomeasure.db"
    max_upload_mb: int = 50

    @property
    def upload_dir(self) -> Path:
        return self.data_dir / "uploads"


settings = Settings()
