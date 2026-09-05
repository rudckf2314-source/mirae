from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from config.settings import Settings, get_settings  # noqa: E402
from exceptions import ConfigurationError, LlmError, ProductIngestionError  # noqa: E402
from services.extraction_service import ExtractionService  # noqa: E402


def resolve_input_dir(raw: str) -> Path:
    path = Path(raw)
    if path.is_absolute():
        return path
    return (ROOT / path).resolve()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Batch PDF extraction. Stops immediately on HyperCLOVA X LLM failure when --stop-on-llm-error is set.",
    )
    parser.add_argument("--input", default="cache", help="Directory containing PDF files")
    parser.add_argument("--limit", type=int, default=0, help="Process at most N PDFs (0 = all)")
    parser.add_argument("--force", action="store_true", help="Re-run LLM even if hash already exists")
    parser.add_argument(
        "--review",
        action="store_true",
        help="Review mode: continue on errors, ignore verification status (implies --no-stop-on-llm-error)",
    )
    parser.add_argument(
        "--stop-on-llm-error",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Stop the batch when an HyperCLOVA X LLM call fails (default: true, false with --review)",
    )
    args = parser.parse_args()
    if args.review:
        args.stop_on_llm_error = False
    elif args.stop_on_llm_error is None:
        args.stop_on_llm_error = True

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    input_dir = resolve_input_dir(args.input)
    pdfs = sorted(set(input_dir.glob("*.pdf")) | set(input_dir.glob("*.PDF")), key=lambda p: p.name.lower())
    if args.limit:
        pdfs = pdfs[: args.limit]
    if not pdfs:
        logging.error("PDF가 없습니다: %s", input_dir)
        return 1

    settings = get_settings()
    if args.stop_on_llm_error:
        settings = Settings(**{**settings.model_dump(), "llm_fail_fast": True})

    try:
        service = ExtractionService(settings=settings)
    except ConfigurationError as exc:
        logging.error("설정 오류: %s", exc)
        return 2

    success = 0
    skipped = 0
    failed = 0
    index = 0

    def callback(event):
        logging.info("[%s] %s", event.step, event.message)

    for index, pdf_path in enumerate(pdfs, start=1):
        logging.info("=== [%s/%s] %s ===", index, len(pdfs), pdf_path.name)
        try:
            result = service.process_pdf(
                pdf_path,
                file_name=pdf_path.name,
                progress_callback=callback,
                force=args.force,
            )
        except (LlmError, ConfigurationError) as exc:
            failed += 1
            logging.error("HyperCLOVA X LLM 실패 [%s/%s] %s: %s", index, len(pdfs), pdf_path.name, exc)
            if args.stop_on_llm_error:
                return 3
            continue
        except ProductIngestionError as exc:
            failed += 1
            logging.error("처리 실패 [%s/%s] %s: %s", index, len(pdfs), pdf_path.name, exc)
            if not args.review:
                return 4
            continue
        except Exception:
            failed += 1
            logging.exception("예기치 않은 오류 [%s/%s] %s", index, len(pdfs), pdf_path.name)
            if not args.review:
                return 5
            continue

        if result.error or result.product is None:
            failed += 1
            logging.error("결과 없음 [%s/%s] %s: %s", index, len(pdfs), pdf_path.name, result.error)
            if not args.review:
                return 6
            continue

        if result.cached or result.duplicate:
            skipped += 1
            logging.info("cached/skip: %s", result.product.document.document_id)
        else:
            success += 1
            logging.info(
                "saved: %s status=%s verify=%s",
                result.product.document.document_id,
                result.product.extraction.status,
                result.product.extraction.verification.status,
            )

    logging.info(
        "배치 완료 total=%s success=%s skipped=%s failed=%s",
        len(pdfs),
        success,
        skipped,
        failed,
    )
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
