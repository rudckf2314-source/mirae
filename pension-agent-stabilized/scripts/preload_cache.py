from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from config.settings import get_settings  # noqa: E402
from services.extraction_service import ExtractionService  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Preload investment prospectus PDFs into persistent cache.")
    parser.add_argument("--input", default="data/preload", help="Directory of source PDFs")
    parser.add_argument("--limit", type=int, default=0, help="Process at most N PDFs (0 = all)")
    parser.add_argument("--force", action="store_true", help="Re-run LLM even if hash already exists")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    input_dir = (ROOT / args.input).resolve() if not Path(args.input).is_absolute() else Path(args.input)
    pdfs = sorted(input_dir.glob("*.pdf")) + sorted(input_dir.glob("*.PDF"))
    if args.limit:
        pdfs = pdfs[: args.limit]
    if not pdfs:
        logging.error("PDF가 없습니다: %s", input_dir)
        return 1

    settings = get_settings()
    service = ExtractionService(settings=settings)
    success = 0
    skipped = 0
    failed = 0

    def callback(event):
        logging.info("[%s] %s", event.step, event.message)

    for pdf_path in pdfs:
        logging.info("=== %s ===", pdf_path.name)
        try:
            result = service.process_pdf(pdf_path, progress_callback=callback, force=args.force)
            if result.duplicate or result.cached:
                skipped += 1
                logging.info("cached/skip: %s", result.product.document.document_id)
            else:
                success += 1
                logging.info("saved: %s", result.product.document.document_id)
        except Exception:
            failed += 1
            logging.exception("failed: %s", pdf_path.name)

    logging.info("done success=%s skipped=%s failed=%s", success, skipped, failed)
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
