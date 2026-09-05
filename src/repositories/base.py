from abc import ABC, abstractmethod

from schemas.document import ParsedDocument
from schemas.product import CanonicalProduct


class ProductRepository(ABC):
    @abstractmethod
    def save_product(
        self,
        product: CanonicalProduct,
        pdf_bytes: bytes | None = None,
        parsed: ParsedDocument | None = None,
    ) -> CanonicalProduct:
        raise NotImplementedError

    @abstractmethod
    def get_by_document_id(self, document_id: str) -> CanonicalProduct | None:
        raise NotImplementedError

    @abstractmethod
    def get_by_hash(self, document_hash: str) -> CanonicalProduct | None:
        raise NotImplementedError

    @abstractmethod
    def list_products(self) -> list[dict]:
        raise NotImplementedError

    @abstractmethod
    def get_parsed(self, document_id: str) -> ParsedDocument | None:
        raise NotImplementedError

    @abstractmethod
    def get_pdf_bytes(self, document_id: str) -> bytes | None:
        raise NotImplementedError
