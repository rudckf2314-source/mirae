from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import pgserver  # noqa: E402
from config.settings import Settings, get_settings  # noqa: E402
from database import PostgresStandardStore  # noqa: E402
from exceptions import ConfigurationError  # noqa: E402
from schemas.product_schema import ProductExtraction  # noqa: E402
from services.extraction_service import ExtractionService  # noqa: E402
from standardization import StandardJsonStore  # noqa: E402

PG_DATA_DIR = ROOT / "data" / "postgres"
DATABASE_URL_FILE = ROOT / "data" / "postgres" / "database_url.txt"
DEFAULT_TIMEOUT_SEC = 900  # 15 min per PDF hard ceiling


def resolve_input_dir(raw: str) -> Path:
    path = Path(raw)
    return path if path.is_absolute() else (ROOT / path).resolve()


def start_database(use_embedded: bool, database_url: str | None) -> str:
    if use_embedded:
        PG_DATA_DIR.mkdir(parents=True, exist_ok=True)
        logging.info("Embedded PostgreSQL 시작: %s", PG_DATA_DIR)
        server = pgserver.get_server(PG_DATA_DIR)
        url = server.get_uri()
        DATABASE_URL_FILE.write_text(url, encoding="utf-8")
        logging.info("database_url=%s", url)
        return url
    if not database_url:
        raise ConfigurationError("DATABASE_URL이 비어 있습니다.")
    return database_url


def verify_database(database_url: str, expected: int) -> dict[str, int]:
    import psycopg

    with psycopg.connect(database_url, connect_timeout=10) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM source_documents")
            docs = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM products")
            products = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM product_classes")
            classes = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM fees")
            fees = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM performance")
            performance = cur.fetchone()[0]
    logging.info(
        "DB 검증 source_documents=%s products=%s classes=%s fees=%s performance=%s (expected>=%s)",
        docs,
        products,
        classes,
        fees,
        performance,
        expected,
    )
    return {
        "source_documents": docs,
        "products": products,
        "product_classes": classes,
        "fees": fees,
        "performance": performance,
    }


def backfill_standard_json(database_url: str, standard_dir: Path) -> int:
    store = PostgresStandardStore(database_url)
    saved = 0
    for path in sorted(standard_dir.glob("*.schema_v0.1.json")):
        document_id = path.name.replace(".schema_v0.1.json", "")
        try:
            product = ProductExtraction.model_validate_json(path.read_text(encoding="utf-8"))
            store.save(document_id, product)
            saved += 1
            logging.info("backfill DB: %s", document_id)
        except Exception:
            logging.exception("backfill 실패: %s", document_id)
    return saved


def process_one_inline(
    pdf_path: Path,
    *,
    force: bool,
    database_url: str,
) -> dict:
    base = get_settings()
    settings = Settings(
        **{
            **base.model_dump(),
            "database_url": database_url,
            "db_auto_save": True,
            "llm_fail_fast": False,
        }
    )
    service = ExtractionService(settings=settings)

    def callback(event):
        logging.info("[%s] %s", event.step, event.message)

    result = service.process_pdf(
        pdf_path,
        file_name=pdf_path.name,
        progress_callback=callback,
        force=force,
    )
    if result.error or result.product is None:
        return {
            "ok": False,
            "cached": False,
            "db_saved": False,
            "error": result.error or "missing product",
            "document_id": None,
        }
    return {
        "ok": True,
        "cached": bool(result.cached or result.duplicate),
        "db_saved": bool(result.db_saved),
        "error": result.db_error,
        "document_id": result.product.document.document_id,
        "status": result.product.extraction.status,
        "verify": result.product.extraction.verification.status,
        "schema": result.standard_json_path,
    }


def process_one_subprocess(
    pdf_path: Path,
    *,
    force: bool,
    timeout_sec: int,
) -> dict:
    """Isolate each PDF so a hang/crash cannot stop the batch."""
    cmd = [
        sys.executable,
        str(ROOT / "scripts" / "full_pipeline_batch.py"),
        "--one",
        str(pdf_path),
        "--embedded-pg",
    ]
    if force:
        cmd.append("--force")
    try:
        completed = subprocess.run(
            cmd,
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_sec,
            env={**os.environ, "PYTHONPATH": "src"},
        )
        if completed.stdout:
            for line in completed.stdout.splitlines()[-20:]:
                logging.info("[child] %s", line)
        if completed.stderr:
            for line in completed.stderr.splitlines()[-20:]:
                logging.warning("[child-err] %s", line)
        if completed.returncode == 0:
            return {"ok": True, "cached": False, "db_saved": True, "error": None, "document_id": pdf_path.stem}
        return {
            "ok": False,
            "cached": False,
            "db_saved": False,
            "error": f"exit={completed.returncode}",
            "document_id": pdf_path.stem,
        }
    except subprocess.TimeoutExpired:
        logging.error("TIMEOUT %ss: %s — skip and continue", timeout_sec, pdf_path.name)
        return {
            "ok": False,
            "cached": False,
            "db_saved": False,
            "error": f"timeout after {timeout_sec}s",
            "document_id": pdf_path.stem,
        }
    except Exception as exc:
        logging.exception("subprocess 실패: %s", pdf_path.name)
        return {
            "ok": False,
            "cached": False,
            "db_saved": False,
            "error": str(exc),
            "document_id": pdf_path.stem,
        }


def is_complete(pdf_stem: str, standard_dir: Path, extracted_dir: Path) -> bool:
    return (
        (extracted_dir / f"{pdf_stem}.json").exists()
        and (standard_dir / f"{pdf_stem}.schema_v0.1.json").exists()
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Never-stop PDF→JSON→SQL batch")
    parser.add_argument("--input", default="cache")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--embedded-pg", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--backfill-only", action="store_true")
    parser.add_argument("--timeout-sec", type=int, default=DEFAULT_TIMEOUT_SEC)
    parser.add_argument("--one", type=Path, default=None, help=argparse.SUPPRESS)
    parser.add_argument(
        "--inline",
        action="store_true",
        help="Process in-process (faster). Default uses per-PDF subprocess isolation.",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    base = get_settings()
    try:
        database_url = start_database(args.embedded_pg, base.database_url or None)
        PostgresStandardStore(database_url).ensure_schema()
    except Exception:
        logging.exception("DB 시작 실패 — 재시도 없이 종료할 수 없음, 계속 시도")
        for attempt in range(1, 6):
            try:
                time.sleep(2 * attempt)
                database_url = start_database(args.embedded_pg, base.database_url or None)
                PostgresStandardStore(database_url).ensure_schema()
                break
            except Exception:
                logging.exception("DB 재시도 %s/5", attempt)
        else:
            return 2

    standard_dir = base.standard_json_dir
    standard_dir.mkdir(parents=True, exist_ok=True)
    extracted_dir = ROOT / "data" / "cache" / "extracted"
    extracted_dir.mkdir(parents=True, exist_ok=True)

    if args.one:
        try:
            outcome = process_one_inline(args.one, force=args.force, database_url=database_url)
            logging.info("ONE RESULT %s", json.dumps(outcome, ensure_ascii=False))
            return 0 if outcome.get("ok") else 1
        except Exception:
            logging.exception("ONE 실패 %s", args.one)
            return 1

    if args.backfill_only:
        count = backfill_standard_json(database_url, standard_dir)
        verify_database(database_url, count)
        return 0

    input_dir = resolve_input_dir(args.input)
    pdfs = sorted(set(input_dir.glob("*.pdf")) | set(input_dir.glob("*.PDF")), key=lambda p: p.name.lower())
    if args.limit:
        pdfs = pdfs[: args.limit]
    if not pdfs:
        logging.error("PDF가 없습니다: %s", input_dir)
        return 1

    success = skipped = failed = db_ok = db_fail = 0

    for index, pdf_path in enumerate(pdfs, start=1):
        logging.info("=== [%s/%s] %s ===", index, len(pdfs), pdf_path.name)
        try:
            if not args.force and is_complete(pdf_path.stem, standard_dir, extracted_dir):
                # Ensure DB has it even if earlier run stopped before backfill.
                try:
                    path = standard_dir / f"{pdf_path.stem}.schema_v0.1.json"
                    product = ProductExtraction.model_validate_json(path.read_text(encoding="utf-8"))
                    PostgresStandardStore(database_url).save(pdf_path.stem, product)
                    skipped += 1
                    db_ok += 1
                    logging.info("already-complete+db: %s", pdf_path.stem)
                    continue
                except Exception:
                    logging.exception("complete 파일 DB 재적재 실패 — 재처리: %s", pdf_path.name)

            if args.inline:
                outcome = process_one_inline(pdf_path, force=args.force, database_url=database_url)
            else:
                # Prefer inline for speed; fall back to isolation only when requested.
                # Isolation is default for hang safety.
                outcome = process_one_subprocess(
                    pdf_path, force=args.force, timeout_sec=args.timeout_sec
                )
        except Exception:
            failed += 1
            logging.exception("배치 루프 예외 — 다음 PDF로 계속 [%s/%s] %s", index, len(pdfs), pdf_path.name)
            continue

        if not outcome.get("ok"):
            failed += 1
            logging.error(
                "실패(계속) [%s/%s] %s: %s",
                index,
                len(pdfs),
                pdf_path.name,
                outcome.get("error"),
            )
            continue

        if outcome.get("db_saved"):
            db_ok += 1
        else:
            db_fail += 1
            logging.warning("DB 미저장(계속) %s: %s", outcome.get("document_id"), outcome.get("error"))

        if outcome.get("cached"):
            skipped += 1
            logging.info("cached: %s", outcome.get("document_id"))
        else:
            success += 1
            logging.info(
                "saved: %s status=%s verify=%s db_saved=%s",
                outcome.get("document_id"),
                outcome.get("status"),
                outcome.get("verify"),
                outcome.get("db_saved"),
            )

    # Always backfill whatever Standard JSON exists.
    try:
        backfill_standard_json(database_url, standard_dir)
    except Exception:
        logging.exception("최종 backfill 실패 — 카운트만 보고")

    try:
        counts = verify_database(database_url, len(pdfs))
    except Exception:
        logging.exception("DB 검증 실패")
        counts = {}

    summary = {
        "total_pdfs": len(pdfs),
        "new_extractions": success,
        "cached": skipped,
        "failed": failed,
        "db_saved_ok": db_ok,
        "db_saved_fail": db_fail,
        "database_url_file": str(DATABASE_URL_FILE),
        "counts": counts,
    }
    summary_path = ROOT / "data" / "cache" / "full_pipeline_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    logging.info("배치 완료 %s", json.dumps(summary, ensure_ascii=False))

    canonical_count = len(list(extracted_dir.glob("*.json")))
    standard_count = len(list(standard_dir.glob("*.schema_v0.1.json")))
    db_count = counts.get("source_documents", 0)
    logging.info(
        "FINAL canonical=%s standard=%s db=%s expected=%s",
        canonical_count,
        standard_count,
        db_count,
        len(pdfs),
    )
    # Never abort mid-batch; exit code only reflects completeness.
    return 0 if canonical_count >= len(pdfs) and standard_count >= len(pdfs) and db_count >= len(pdfs) else 1


if __name__ == "__main__":
    raise SystemExit(main())
