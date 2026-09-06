from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Any, Iterable

from .paths import REPO_ROOT


DEFAULT_DB_PATH = REPO_ROOT / "data" / "legal" / "pension_legal.db"
DEFAULT_GUARDRAIL_PATH = REPO_ROOT / "config" / "pension_legal_guardrail_v0.1.json"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def content_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def serving_date(value: str | None = None) -> str:
    raw = value or datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y%m%d")
    compact = raw.replace("-", "").replace("/", "")
    datetime.strptime(compact, "%Y%m%d")
    return compact


@dataclass(frozen=True)
class LegalArticle:
    source_key: str
    law_name: str
    law_type: str
    law_id: str | None
    law_serial: str | None
    promulgation_date: str | None
    effective_date: str | None
    article_no: str
    article_title: str | None
    article_text: str
    source_channel: str
    source_url: str | None
    fetched_at: str


class LegalStore:
    """Portable, deterministic legal serving store.

    The contest runtime can use this SQLite snapshot directly.  Periodic sync jobs
    refresh the same schema from the National Law Information Center API.  Keeping
    the serving store local means chat requests do not depend on a live external API.
    """

    def __init__(self, db_path: str | Path | None = None) -> None:
        configured = os.getenv("LEGAL_DB_PATH")
        self.db_path = Path(db_path or configured or DEFAULT_DB_PATH)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.init_schema()

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA foreign_keys=ON")
            yield conn
            conn.commit()
        finally:
            conn.close()

    def init_schema(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS legal_sources (
                    source_key TEXT PRIMARY KEY,
                    law_name TEXT NOT NULL,
                    law_type TEXT NOT NULL,
                    default_scope TEXT NOT NULL,
                    allowed_articles_json TEXT NOT NULL DEFAULT '[]',
                    source_priority TEXT NOT NULL DEFAULT 'OFFICIAL_LEGAL',
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS legal_articles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_key TEXT NOT NULL,
                    law_name TEXT NOT NULL,
                    law_type TEXT NOT NULL,
                    law_id TEXT,
                    law_serial TEXT,
                    promulgation_date TEXT,
                    effective_date TEXT,
                    article_no TEXT NOT NULL,
                    article_title TEXT,
                    article_text TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    source_channel TEXT NOT NULL,
                    source_url TEXT,
                    fetched_at TEXT NOT NULL,
                    UNIQUE(source_key, effective_date, article_no, content_hash),
                    FOREIGN KEY(source_key) REFERENCES legal_sources(source_key)
                );

                CREATE INDEX IF NOT EXISTS idx_legal_article_lookup
                    ON legal_articles(source_key, article_no, effective_date);
                CREATE INDEX IF NOT EXISTS idx_legal_law_name
                    ON legal_articles(law_name, article_no);

                CREATE TABLE IF NOT EXISTS legal_sync_runs (
                    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    status TEXT NOT NULL,
                    source_count INTEGER NOT NULL DEFAULT 0,
                    article_count INTEGER NOT NULL DEFAULT 0,
                    error_count INTEGER NOT NULL DEFAULT 0,
                    details_json TEXT NOT NULL DEFAULT '{}'
                );

                CREATE TABLE IF NOT EXISTS tax_policy_rules (
                    policy_key TEXT NOT NULL,
                    policy_year INTEGER NOT NULL,
                    effective_from TEXT NOT NULL,
                    effective_to TEXT,
                    formula_id TEXT NOT NULL,
                    version TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    evidence_source_key TEXT NOT NULL,
                    evidence_article_no TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    source_priority TEXT NOT NULL,
                    verified INTEGER NOT NULL DEFAULT 1,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(policy_key, policy_year, version)
                );
                """
            )

    def load_guardrail_registry(self, guardrail_path: str | Path | None = None) -> dict[str, Any]:
        path = Path(guardrail_path or DEFAULT_GUARDRAIL_PATH)
        data = json.loads(path.read_text(encoding="utf-8"))
        now = utc_now()
        with self.connect() as conn:
            for source_key, spec in data.get("source_registry", {}).items():
                conn.execute(
                    """
                    INSERT INTO legal_sources(
                        source_key, law_name, law_type, default_scope,
                        allowed_articles_json, source_priority, updated_at
                    ) VALUES (?, ?, ?, ?, ?, 'OFFICIAL_LEGAL', ?)
                    ON CONFLICT(source_key) DO UPDATE SET
                        law_name=excluded.law_name,
                        law_type=excluded.law_type,
                        default_scope=excluded.default_scope,
                        allowed_articles_json=excluded.allowed_articles_json,
                        updated_at=excluded.updated_at
                    """,
                    (
                        source_key,
                        spec["law_name"],
                        spec["law_type"],
                        spec["default_scope"],
                        json.dumps(spec.get("allowed_articles", []), ensure_ascii=False),
                        now,
                    ),
                )
        return data

    def upsert_articles(self, articles: Iterable[LegalArticle]) -> int:
        count = 0
        with self.connect() as conn:
            for item in articles:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO legal_articles(
                        source_key, law_name, law_type, law_id, law_serial,
                        promulgation_date, effective_date, article_no, article_title,
                        article_text, content_hash, source_channel, source_url, fetched_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item.source_key,
                        item.law_name,
                        item.law_type,
                        item.law_id,
                        item.law_serial,
                        item.promulgation_date,
                        item.effective_date,
                        item.article_no,
                        item.article_title,
                        item.article_text,
                        content_hash(item.article_text),
                        item.source_channel,
                        item.source_url,
                        item.fetched_at,
                    ),
                )
                count += int(conn.execute("SELECT changes()").fetchone()[0])
        return count

    def get_article(self, source_key: str, article_no: str, as_of: str | None = None) -> dict[str, Any] | None:
        params: list[Any] = [source_key, article_no, serving_date(as_of)]
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM legal_articles
                 WHERE source_key=? AND article_no=?
                   AND effective_date IS NOT NULL
                   AND REPLACE(effective_date, '-', '') <= ?
                 ORDER BY REPLACE(effective_date, '-', '') DESC, id DESC
                """,
                params,
            ).fetchall()
        if not rows:
            return None
        current = [r for r in rows if r["effective_date"].replace('-', '') == rows[0]["effective_date"].replace('-', '')]
        if len({r["content_hash"] for r in current}) > 1:
            return None  # Same-effective-date conflict requires review.
        return dict(current[0])

    def get_articles_for_sources(
        self,
        source_keys: Iterable[str],
        allowed_articles: dict[str, set[str]] | None = None,
        query: str | None = None,
        limit: int = 24,
        as_of: str | None = None,
    ) -> list[dict[str, Any]]:
        keys = [key for key in source_keys if key]
        if not keys:
            return []
        placeholders = ",".join("?" for _ in keys)
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM legal_articles
                 WHERE source_key IN ({placeholders})
                 ORDER BY COALESCE(effective_date, '') DESC, source_key, article_no
                """,
                keys,
            ).fetchall()
        output = []
        cutoff = serving_date(as_of)
        latest: dict[tuple[str, str], dict] = {}
        conflicts: set[tuple[str, str]] = set()
        for raw in rows:
            item = dict(raw)
            effective = str(item.get("effective_date") or "").replace('-', '')
            if not effective or effective > cutoff:
                continue
            key = (item["source_key"], item["article_no"])
            previous = latest.get(key)
            if previous is None or effective > str(previous["effective_date"]).replace('-', ''):
                latest[key] = item
                conflicts.discard(key)
            elif effective == str(previous["effective_date"]).replace('-', '') and item["content_hash"] != previous["content_hash"]:
                conflicts.add(key)
        q_tokens = [token for token in (query or "").replace("?", " ").split() if len(token) >= 2]
        for key, item in latest.items():
            if key in conflicts:
                continue
            allowed = (allowed_articles or {}).get(item["source_key"])
            if allowed is not None and item["article_no"] not in allowed:
                continue
            if q_tokens:
                hay = f"{item.get('law_name','')} {item.get('article_title','')} {item.get('article_text','')}"
                score = sum(1 for token in q_tokens if token in hay)
            else:
                score = 0
            item["_score"] = score
            output.append(item)
        output.sort(key=lambda x: (x.get("_score", 0), x.get("effective_date") or ""), reverse=True)
        for item in output:
            item.pop("_score", None)
        return output[:limit]

    def upsert_policy_rule(
        self,
        *,
        policy_key: str,
        policy_year: int,
        effective_from: str,
        formula_id: str,
        version: str,
        payload: dict[str, Any],
        evidence_source_key: str,
        evidence_article_no: str,
        source_type: str,
        source_priority: str,
        verified: bool = True,
        effective_to: str | None = None,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO tax_policy_rules(
                    policy_key, policy_year, effective_from, effective_to,
                    formula_id, version, payload_json, evidence_source_key,
                    evidence_article_no, source_type, source_priority, verified, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(policy_key, policy_year, version) DO UPDATE SET
                    effective_from=excluded.effective_from,
                    effective_to=excluded.effective_to,
                    payload_json=excluded.payload_json,
                    evidence_source_key=excluded.evidence_source_key,
                    evidence_article_no=excluded.evidence_article_no,
                    source_type=excluded.source_type,
                    source_priority=excluded.source_priority,
                    verified=excluded.verified,
                    updated_at=excluded.updated_at
                """,
                (
                    policy_key,
                    policy_year,
                    effective_from,
                    effective_to,
                    formula_id,
                    version,
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                    evidence_source_key,
                    evidence_article_no,
                    source_type,
                    source_priority,
                    1 if verified else 0,
                    utc_now(),
                ),
            )

    def get_policy_rule(self, policy_key: str, policy_year: int, as_of: str | None = None) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM tax_policy_rules
                 WHERE policy_key=? AND policy_year=? AND verified=1
                   AND REPLACE(effective_from, '-', '') <= ?
                   AND (effective_to IS NULL OR REPLACE(effective_to, '-', '') >= ?)
                 ORDER BY REPLACE(effective_from, '-', '') DESC
                 LIMIT 1
                """,
                (policy_key, policy_year, serving_date(as_of), serving_date(as_of)),
            ).fetchone()
        if not row:
            return None
        item = dict(row)
        item["payload"] = json.loads(item.pop("payload_json"))
        return item

    def stats(self) -> dict[str, int]:
        with self.connect() as conn:
            return {
                "sources": int(conn.execute("SELECT COUNT(*) FROM legal_sources").fetchone()[0]),
                "articles": int(conn.execute("SELECT COUNT(*) FROM legal_articles").fetchone()[0]),
                "policies": int(conn.execute("SELECT COUNT(*) FROM tax_policy_rules WHERE verified=1").fetchone()[0]),
            }
