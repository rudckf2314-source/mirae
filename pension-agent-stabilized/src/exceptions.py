class ProductIngestionError(Exception):
    """Base error for the product ingestion module."""


class ConfigurationError(ProductIngestionError):
    pass


class PdfParseError(ProductIngestionError):
    pass


class EmptyPdfError(PdfParseError):
    pass


class LlmError(ProductIngestionError):
    pass


class LlmTimeoutError(LlmError):
    pass


class LlmRateLimitError(LlmError):
    pass


class MalformedLlmResponseError(LlmError):
    pass


class ValidationFailedError(ProductIngestionError):
    pass


class CacheWriteError(ProductIngestionError):
    pass


class DuplicateDocumentError(ProductIngestionError):
    def __init__(self, document_hash: str, existing_id: str):
        self.document_hash = document_hash
        self.existing_id = existing_id
        super().__init__(f"이미 처리된 문서입니다: {existing_id}")


class DeterminismConflictError(ProductIngestionError):
    def __init__(self, document_hash: str, previous: str, current: str):
        self.document_hash = document_hash
        self.previous = previous
        self.current = current
        super().__init__(
            "동일 document_hash의 canonical fact fingerprint가 변경되어 저장을 차단했습니다: "
            f"hash={document_hash[:12]} previous={previous[:12]} current={current[:12]}"
        )


class QualityGateError(ProductIngestionError):
    def __init__(self, blockers: list[str]):
        self.blockers = list(blockers)
        super().__init__("저장 전 품질 게이트 실패: " + "; ".join(self.blockers))


class ProspectusRejectedError(ProductIngestionError):
    """PDF is not an investment prospectus and must not be persisted."""

    def __init__(self, file_name: str, missing: list[str]):
        self.file_name = file_name
        self.missing = list(missing)
        detail = ", ".join(missing) if missing else "투자설명서 구조 신호 부족"
        super().__init__(
            f"가드레일: '{file_name}'은 투자설명서 스키마 적재 대상이 아닙니다. ({detail})"
        )
