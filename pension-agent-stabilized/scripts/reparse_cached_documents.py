from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from parsers.pdf_parser import PdfParser  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Rebuild parsed JSON using current deterministic PDF parsers.")
    parser.add_argument("document_ids", nargs="+")
    parser.add_argument("--pdf-dir", type=Path, default=ROOT / "cache")
    parser.add_argument("--parsed-dir", type=Path, default=ROOT / "data/cache/parsed")
    args = parser.parse_args()

    args.parsed_dir.mkdir(parents=True, exist_ok=True)
    pdf_parser = PdfParser()
    for document_id in args.document_ids:
        pdf_path = args.pdf_dir / f"{document_id}.pdf"
        if not pdf_path.exists():
            raise FileNotFoundError(pdf_path)
        parsed = pdf_parser.parse(
            pdf_path,
            file_name=pdf_path.name,
            document_id=document_id,
        )
        output = args.parsed_dir / f"{document_id}.json"
        output.write_text(parsed.model_dump_json(indent=2), encoding="utf-8")
        print(f"{document_id}: pages={parsed.page_count} tables={len(parsed.tables)} -> {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
