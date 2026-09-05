from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

from schemas.versions import (
    CANONICAL_SCHEMA_VERSION,
    STANDARD_SCHEMA_VERSION,
    CanonicalSchemaVersion,
    StandardSchemaVersion,
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    clova_studio_api_key: str = ""
    clova_base_url: str = "https://clovastudio.stream.ntruss.com"
    clova_model: str = "HCX-005"
    clova_extraction_model: str = "HCX-005"

    llm_provider: str = "hyperclova"
    llm_temperature: float = 0.1
    llm_max_tokens: int = 4096
    llm_timeout: int = 300
    llm_max_retries: int = 2
    semantic_review_enabled: bool = True
    llm_fail_fast: bool = False

    storage_backend: str = "json"
    cache_dir: Path = Path("data/cache")
    schema_version: CanonicalSchemaVersion = CANONICAL_SCHEMA_VERSION

    # Standard schema JSON -> PostgreSQL automatic persistence
    standard_schema_version: StandardSchemaVersion = STANDARD_SCHEMA_VERSION
    standard_json_dir: Path = Path("data/standard_json")
    database_url: str = ""
    db_auto_save: bool = True

    log_level: str = "INFO"

    @property
    def pdf_dir(self) -> Path:
        return self.cache_dir / "pdf"

    @property
    def parsed_dir(self) -> Path:
        return self.cache_dir / "parsed"

    @property
    def extracted_dir(self) -> Path:
        return self.cache_dir / "extracted"

    @property
    def index_path(self) -> Path:
        return self.cache_dir / "index.json"

    @property
    def checkpoint_dir(self) -> Path:
        return self.cache_dir / "node_checkpoints"


def get_settings() -> Settings:
    return Settings()
