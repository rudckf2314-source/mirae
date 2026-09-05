"""PostgreSQL repository placeholder.

TODO: Implement SQL persistence after the logical schema is finalized.

Future mapping (not implemented in this PoC):
- products
- product_classes
- fees
- performance
- aum_history
- evidence
- document registry (replaces data/cache/index.json)

ExtractionService / Streamlit / LangChain must keep depending on ProductRepository only.
"""

from schemas.document import ParsedDocument
from schemas.product import CanonicalProduct

from .base import ProductRepository


class PostgresRepository(ProductRepository):
    def __init__(self, dsn: str | None = None):
        self.dsn = dsn

    def save_product(
        self,
        product: CanonicalProduct,
        pdf_bytes: bytes | None = None,
        parsed: ParsedDocument | None = None,
    ) -> CanonicalProduct:
        raise NotImplementedError("PostgresRepository는 이번 PoC에서 구현하지 않습니다.")

    def get_by_document_id(self, document_id: str) -> CanonicalProduct | None:
        raise NotImplementedError("PostgresRepository는 이번 PoC에서 구현하지 않습니다.")

    def get_by_hash(self, document_hash: str) -> CanonicalProduct | None:
        raise NotImplementedError("PostgresRepository는 이번 PoC에서 구현하지 않습니다.")

    def list_products(self) -> list[dict]:
        raise NotImplementedError("PostgresRepository는 이번 PoC에서 구현하지 않습니다.")

    def get_parsed(self, document_id: str) -> ParsedDocument | None:
        raise NotImplementedError("PostgresRepository는 이번 PoC에서 구현하지 않습니다.")

    def get_pdf_bytes(self, document_id: str) -> bytes | None:
        raise NotImplementedError("PostgresRepository는 이번 PoC에서 구현하지 않습니다.")
