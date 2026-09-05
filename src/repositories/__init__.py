from config.settings import Settings

from .base import ProductRepository
from .json_cache_repository import JsonCacheRepository
from .postgres_repository import PostgresRepository


def get_repository(settings: Settings) -> ProductRepository:
    backend = (settings.storage_backend or "json").lower()
    if backend == "json":
        return JsonCacheRepository(settings.cache_dir)
    if backend == "postgres":
        return PostgresRepository()
    raise ValueError(f"지원하지 않는 STORAGE_BACKEND: {backend}")
