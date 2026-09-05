from pathlib import Path
import re

import pymupdf

from exceptions import EmptyPdfError, PdfParseError
from parsers.table_fallback import recover_page_tables
from parsers.table_parser import fallback_tables_from_text, normalize_page_tables
from parsers.table_quality import page_needs_fallback
from schemas.chunk import SectionType
from schemas.document import LayoutBlock, PageText, ParsedDocument
from utils.hashing import sanitize_document_id, sha256_bytes
from utils.text import normalize_pdf_text


class PdfParser:
    def parse(
        self,
        pdf: str | Path | bytes,
        file_name: str | None = None,
        document_hash: str | None = None,
        document_id: str | None = None,
    ) -> ParsedDocument:
        pdf_bytes, resolved_name = self._load_bytes(pdf, file_name)
        document_hash = document_hash or sha256_bytes(pdf_bytes)
        document_id = document_id or sanitize_document_id(resolved_name)

        try:
            doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
        except Exception as exc:
            raise PdfParseError(f"PDF 파싱 실패: {resolved_name}") from exc

        try:
            pages: list[PageText] = []
            tables = []
            for index in range(doc.page_count):
                page = doc[index]
                raw = page.get_text() or ""
                pages.append(
                    PageText(
                        page_number=index + 1,
                        text=normalize_pdf_text(raw),
                        blocks=self._extract_layout_blocks(page, index + 1),
                    )
                )
                raw_tables = self._extract_raw_tables(page)
                page_tables = normalize_page_tables(raw_tables, document_id, index + 1)
                if page_needs_fallback(pages[-1].text, page_tables):
                    page_tables = recover_page_tables(
                        pdf_bytes,
                        index + 1,
                        document_id,
                        page_tables,
                        pages[-1].text,
                    )
                if self._needs_text_fallback(pages[-1].text, page_tables):
                    page_tables.extend(
                        fallback_tables_from_text(
                            pages[-1].text,
                            document_id,
                            index + 1,
                            seq_start=len(page_tables) + 80,
                        )
                    )
                tables.extend(page_tables)
        finally:
            doc.close()

        if not pages:
            raise EmptyPdfError(f"페이지가 없는 PDF입니다: {resolved_name}")

        if all(not page.text.strip() for page in pages):
            raise EmptyPdfError(f"추출 가능한 텍스트가 없는 PDF입니다: {resolved_name}")

        return ParsedDocument(
            document_id=document_id,
            document_hash=document_hash,
            file_name=resolved_name,
            page_count=len(pages),
            pages=pages,
            tables=self._dedupe_tables(tables),
        )

    def _extract_raw_tables(self, page) -> list[list[list[object]]]:
        if not hasattr(page, "find_tables"):
            return []
        try:
            found = page.find_tables()
        except Exception:
            return []
        tables = getattr(found, "tables", found) or []
        extracted = []
        for table in tables:
            try:
                extracted.append(table.extract())
            except Exception:
                continue
        return extracted

    @staticmethod
    def _extract_layout_blocks(page, page_number: int) -> list[LayoutBlock]:
        try:
            raw_blocks = page.get_text("blocks") or []
        except Exception:
            return []
        blocks: list[LayoutBlock] = []
        for index, block in enumerate(raw_blocks):
            if len(block) < 5:
                continue
            text = normalize_pdf_text(str(block[4] or ""))
            if not text.strip():
                continue
            blocks.append(
                LayoutBlock(
                    block_id=f"p{page_number:03d}_b{index:04d}",
                    text=text,
                    bbox=(
                        float(block[0]),
                        float(block[1]),
                        float(block[2]),
                        float(block[3]),
                    ),
                )
            )
        return blocks

    def _needs_text_fallback(self, text: str, tables: list) -> bool:
        compact = re.sub(r"\s+", "", text)
        has_perf_heading = "투자실적" in compact or "연평균수익률" in compact
        has_fee_heading = "투자비용" in compact or ("판매수수료" in compact and "총보수" in compact)
        has_perf = any(
            table.section_type == SectionType.PERFORMANCE
            and table.rows
            and any(
                "비교지수" in " ".join(row) or "변동성" in " ".join(row) or any("-" in cell and cell[:4].isdigit() for cell in row)
                for row in table.rows
            )
            for table in tables
        )
        has_fee = any(
            table.section_type == SectionType.FEES
            and table.rows
            and any("총보수" in h or "판매" in h for h in table.headers)
            for table in tables
        )
        return (has_perf_heading and not has_perf) or (has_fee_heading and not has_fee)

    def _dedupe_tables(self, tables: list) -> list:
        seen: set[tuple] = set()
        unique = []
        for table in tables:
            identity = (
                table.section_type,
                table.page_number,
                tuple(tuple(row) for row in table.rows[:4]),
            )
            if identity in seen:
                continue
            seen.add(identity)
            unique.append(table)
        return unique

    def _load_bytes(
        self,
        pdf: str | Path | bytes,
        file_name: str | None,
    ) -> tuple[bytes, str]:
        if isinstance(pdf, bytes):
            if not pdf:
                raise PdfParseError("빈 PDF 바이트입니다.")
            return pdf, file_name or "uploaded.pdf"

        path = Path(pdf)
        if not path.exists():
            raise PdfParseError(f"PDF 파일을 찾을 수 없습니다: {path}")
        return path.read_bytes(), file_name or path.name
