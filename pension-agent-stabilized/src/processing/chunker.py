from parsers.table_parser import tables_to_markdown
from schemas.chunk import SECTION_SLUG, Chunk, SectionSpan, SectionType
from schemas.document import DetectedTable, ParsedDocument

DEFAULT_MAX_CHARS = 3500


class Chunker:
    def __init__(self, max_chars: int = DEFAULT_MAX_CHARS):
        self.max_chars = max_chars

    def chunk(
        self,
        parsed: ParsedDocument,
        sections: list[SectionSpan] | None = None,
        tables: list[DetectedTable] | None = None,
    ) -> list[Chunk]:
        chunks: list[Chunk] = []
        counters: dict[tuple[int, str], int] = {}
        used_pages: set[int] = set()
        page_texts = {page.page_number: page.text for page in parsed.pages}

        for span in sections or []:
            text = (span.text or "").strip()
            if not text:
                continue
            used_pages.add(span.page_start)
            for piece in self._split_text(text):
                chunks.append(
                    self._make_chunk(
                        parsed.document_id,
                        span.page_start,
                        span.page_end,
                        span.section_type,
                        piece,
                        counters,
                    )
                )

        for page in parsed.pages:
            if page.page_number in used_pages:
                continue
            mapping = self._page_section_map(parsed, sections)
            section_type = mapping.get(page.page_number, SectionType.OTHER)
            for piece in self._split_text(page.text):
                chunks.append(
                    self._make_chunk(
                        parsed.document_id,
                        page.page_number,
                        page.page_number,
                        section_type,
                        piece,
                        counters,
                    )
                )

        for table in tables or parsed.tables:
            markdown = tables_to_markdown(table)
            chunks.append(
                self._make_chunk(
                    parsed.document_id,
                    table.page_number,
                    table.page_number,
                    table.section_type,
                    markdown,
                    counters,
                    table=table,
                    page_source_text=page_texts.get(table.page_number),
                )
            )
        return chunks

    def _make_chunk(
        self,
        document_id: str,
        page_start: int,
        page_end: int,
        section_type: SectionType,
        text: str,
        counters: dict[tuple[int, str], int],
        table: DetectedTable | None = None,
        page_source_text: str | None = None,
    ) -> Chunk:
        slug = SECTION_SLUG.get(section_type, "other")
        key = (page_start, slug if not table else f"{slug}_t")
        counters[key] = counters.get(key, 0) + 1
        seq = counters[key]
        suffix = f"{slug}_t{seq:03d}" if table else f"{slug}_c{seq:03d}"
        return Chunk(
            chunk_id=f"{document_id}_p{page_start:03d}_{suffix}",
            document_id=document_id,
            page_start=page_start,
            page_end=page_end,
            section_type=section_type,
            text=text,
            page_source_text=page_source_text,
            table_id=table.table_id if table else None,
            headers=list(table.headers) if table else [],
            rows=[list(row) for row in table.rows] if table else [],
        )

    def _page_section_map(
        self,
        parsed: ParsedDocument,
        sections: list[SectionSpan] | None,
    ) -> dict[int, SectionType]:
        mapping: dict[int, SectionType] = {
            page.page_number: SectionType.OTHER for page in parsed.pages
        }
        if not sections:
            return mapping
        for span in sections:
            for page_number in range(span.page_start, span.page_end + 1):
                mapping[page_number] = span.section_type
        return mapping

    def _split_text(self, text: str) -> list[str]:
        cleaned = (text or "").strip()
        if not cleaned:
            return []
        if len(cleaned) <= self.max_chars:
            return [cleaned]
        paragraphs = [p.strip() for p in cleaned.split("\n") if p.strip()]
        pieces: list[str] = []
        current = ""
        for para in paragraphs:
            candidate = f"{current}\n{para}".strip() if current else para
            if len(candidate) <= self.max_chars:
                current = candidate
                continue
            if current:
                pieces.append(current)
            if len(para) <= self.max_chars:
                current = para
            else:
                pieces.extend([para[i : i + self.max_chars] for i in range(0, len(para), self.max_chars)])
                current = ""
        if current:
            pieces.append(current)
        return pieces
