from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from processing.chunker import Chunker  # noqa: E402
from processing.final_reconciler import FinalReconciler  # noqa: E402
from processing.post_processor import PostProcessor  # noqa: E402
from processing.section_detector import SectionDetector  # noqa: E402
from schemas.document import ParsedDocument  # noqa: E402
from schemas.product import CanonicalProduct  # noqa: E402
from standardization import SchemaMapper, StandardJsonStore  # noqa: E402
from validators.pipeline import ValidationPipeline  # noqa: E402
from verification.pipeline import VerificationPipeline  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--extracted-dir", type=Path, default=ROOT / "data/cache/extracted")
    parser.add_argument("--parsed-dir", type=Path, default=ROOT / "data/cache/parsed")
    parser.add_argument("--schema-dir", type=Path, default=ROOT / "data/standard_json")
    args = parser.parse_args()

    detector = SectionDetector()
    chunker = Chunker()
    post_processor = PostProcessor()
    validator = ValidationPipeline()
    verifier = VerificationPipeline()
    reconciler = FinalReconciler()
    mapper = SchemaMapper()
    schema_store = StandardJsonStore(args.schema_dir)
    results = []

    for path in sorted(args.extracted_dir.glob("*.json")):
        parsed_path = args.parsed_dir / path.name
        row = {"document_id": path.stem, "canonical": False, "schema": False, "error": None}
        try:
            product = CanonicalProduct.model_validate_json(path.read_text(encoding="utf-8"))
            parsed = ParsedDocument.model_validate_json(parsed_path.read_text(encoding="utf-8"))
            sections = detector.detect(parsed)
            chunks = chunker.chunk(parsed, sections, tables=parsed.tables)
            product = post_processor.process(product, chunks, tables=parsed.tables)
            product = validator.validate(product, chunks, tables=parsed.tables)
            product = verifier.verify(product, chunks, tables=parsed.tables, llm=None)
            product = reconciler.reconcile(product, chunks, tables=parsed.tables)

            # Validation guarantees every surviving ref has a materialized
            # EvidenceItem. Refuse to persist if that invariant regresses.
            evidence_ids = {item.chunk_id for item in product.evidence}
            missing = sorted(set(product.all_evidence_refs()) - evidence_ids)
            if missing:
                raise ValueError(f"canonical evidence invariant failed: {missing[:5]}")

            path.write_text(
                json.dumps(product.model_dump(mode="json"), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            row["canonical"] = True
            standardized = mapper.map(product)
            schema_store.save(product.document.document_id, standardized)
            row["schema"] = True
            row["evidence"] = len(product.evidence)
            row["refs"] = len(set(product.all_evidence_refs()))
        except Exception as exc:
            row["error"] = str(exc)
        results.append(row)

    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0 if all(row["canonical"] and row["schema"] for row in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
