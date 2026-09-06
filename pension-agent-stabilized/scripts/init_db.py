from __future__ import annotations

import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from config.settings import get_settings
from database import PostgresStandardStore

settings = get_settings()
if not settings.database_url:
    raise SystemExit("DATABASE_URL을 .env에 설정하세요.")
store = PostgresStandardStore(settings.database_url)
store.ensure_schema()
print("PostgreSQL schema initialized.")
