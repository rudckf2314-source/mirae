from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import time
from typing import Any, Callable, Protocol


CACHE_SCHEMA_VERSION = "pension-cache-v1"
ROUTER_POLICY_VERSION = "query-router-v3-conversation"
FAQ_POLICY_VERSION = "faq-evidence-v1"


@dataclass(frozen=True)
class CacheEntry:
    namespace: str
    key_hash: str
    value: Any
    schema_version: str
    policy_version: str
    source_version: str
    created_at: float
    expires_at: float
    evidence_status: tuple[str, ...]
    eligible: bool
    hit_count: int = 0


class CacheBackend(Protocol):
    def get(self, namespace: str, key_hash: str) -> CacheEntry | None: ...

    def set(self, entry: CacheEntry) -> None: ...

    def delete(self, namespace: str, key_hash: str) -> None: ...

    def invalidate_namespace(self, namespace: str) -> int: ...

    def cleanup_expired(self) -> int: ...


class InMemoryCache:
    """테스트 가능하고 교체 가능한 bounded TTL cache입니다."""

    def __init__(
        self,
        max_entries: int = 256,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.max_entries = max(1, max_entries)
        self._clock = clock
        self._entries: OrderedDict[tuple[str, str], CacheEntry] = OrderedDict()

    def get(self, namespace: str, key_hash: str) -> CacheEntry | None:
        self.cleanup_expired()
        key = (namespace, key_hash)
        entry = self._entries.get(key)
        if entry is None:
            return None

        self._entries.move_to_end(key)
        refreshed = CacheEntry(
            **{**entry.__dict__, "hit_count": entry.hit_count + 1}
        )
        self._entries[key] = refreshed
        return deepcopy(refreshed)

    def set(self, entry: CacheEntry) -> None:
        self.cleanup_expired()
        key = (entry.namespace, entry.key_hash)
        stored = deepcopy(entry)
        stored = CacheEntry(**{**stored.__dict__, "value": deepcopy(stored.value)})
        self._entries[key] = stored
        self._entries.move_to_end(key)
        while len(self._entries) > self.max_entries:
            self._entries.popitem(last=False)

    def delete(self, namespace: str, key_hash: str) -> None:
        self._entries.pop((namespace, key_hash), None)

    def invalidate_namespace(self, namespace: str) -> int:
        keys = [key for key in self._entries if key[0] == namespace]
        for key in keys:
            del self._entries[key]
        return len(keys)

    def cleanup_expired(self) -> int:
        now = self._clock()
        keys = [key for key, entry in self._entries.items() if entry.expires_at <= now]
        for key in keys:
            del self._entries[key]
        return len(keys)

    @property
    def size(self) -> int:
        self.cleanup_expired()
        return len(self._entries)


@dataclass
class SourceVersions:
    product_db: str | None
    pdf_index: str | None
    law_policy: str | None
    router_policy: str = ROUTER_POLICY_VERSION

    def as_dict(self) -> dict[str, str | None]:
        return {
            "product_db": self.product_db,
            "pdf_index": self.pdf_index,
            "law_policy": self.law_policy,
            "router_policy": self.router_policy,
        }

    def combined(self) -> str | None:
        values = self.as_dict()
        if any(value is None for value in values.values()):
            return None
        return stable_hash(values)


def stable_hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def normalize_question(question: str) -> str:
    return " ".join(question.casefold().split())


def fingerprint_paths(paths: list[Path]) -> str | None:
    """시작 시 한 번만 사용할 결정론적 source fingerprint입니다."""
    if not paths or any(not path.is_file() for path in paths):
        return None

    digest = sha256()
    for path in sorted(paths, key=lambda item: str(item).casefold()):
        digest.update(path.name.encode("utf-8"))
        with path.open("rb") as file:
            for block in iter(lambda: file.read(1024 * 1024), b""):
                digest.update(block)
    return digest.hexdigest()


def source_versions_from_agent(agent: Any) -> SourceVersions:
    return SourceVersionTracker.from_agent(agent).versions


class SourceVersionTracker:
    """가벼운 stat 비교 후 변경된 source만 다시 fingerprint하는 version tracker입니다."""

    def __init__(
        self,
        product_paths: Callable[[], list[Path]],
        pdf_paths: Callable[[], list[Path]],
        law_paths: Callable[[], list[Path]],
    ) -> None:
        self._path_factories = {
            "product_db": product_paths,
            "pdf_index": pdf_paths,
            "law_policy": law_paths,
        }
        self._stamps: dict[str, tuple[tuple[str, int, int], ...] | None] = {}
        self.versions = SourceVersions(None, None, None)
        self.refresh(force=True)

    @classmethod
    def from_agent(cls, agent: Any) -> "SourceVersionTracker":
        product_db = getattr(agent, "product_db", None)
        product_path = getattr(product_db, "path", None)

        def product_paths() -> list[Path]:
            if not isinstance(product_path, Path):
                return []
            return [product_path] if product_path.is_file() else sorted(product_path.glob("*.json"))

        retriever = getattr(getattr(agent, "document_chatbot", None), "retriever", None)
        chunks_path = getattr(retriever, "chunks_path", None)

        def pdf_paths() -> list[Path]:
            return [chunks_path] if isinstance(chunks_path, Path) else []

        root = Path(__file__).resolve().parent

        def law_paths() -> list[Path]:
            return [
                root / "law_tool.py",
                root / "law_api_client.py",
                root / "law_reference_resolver.py",
            ]

        tracker = cls(product_paths, pdf_paths, law_paths)
        explicit_version = getattr(product_db, "source_version", None)
        if explicit_version:
            tracker.versions.product_db = str(explicit_version)
        return tracker

    def refresh(self, force: bool = False) -> bool:
        changed = False
        for field, factory in self._path_factories.items():
            paths = factory()
            stamps = self._path_stamps(paths)
            if force or stamps != self._stamps.get(field):
                setattr(self.versions, field, fingerprint_paths(paths))
                self._stamps[field] = stamps
                changed = True
        return changed

    @staticmethod
    def _path_stamps(paths: list[Path]) -> tuple[tuple[str, int, int], ...] | None:
        if not paths or any(not path.is_file() for path in paths):
            return None
        return tuple(
            (str(path), path.stat().st_size, path.stat().st_mtime_ns)
            for path in sorted(paths, key=lambda item: str(item).casefold())
        )


def _source_versions_from_agent_legacy(agent: Any) -> SourceVersions:
    product_db = getattr(agent, "product_db", None)
    product_path = getattr(product_db, "path", None)
    if isinstance(product_path, Path):
        product_files = (
            [product_path]
            if product_path.is_file()
            else sorted(product_path.glob("*.json"))
        )
        product_version = fingerprint_paths(product_files)
    else:
        product_version = None

    retriever = getattr(getattr(agent, "document_chatbot", None), "retriever", None)
    chunks_path = getattr(retriever, "chunks_path", None)
    pdf_version = fingerprint_paths([chunks_path]) if isinstance(chunks_path, Path) else None

    root = Path(__file__).resolve().parent
    law_policy = fingerprint_paths(
        [root / "law_tool.py", root / "law_api_client.py", root / "law_reference_resolver.py"]
    )
    return SourceVersions(
        product_db=product_version,
        pdf_index=pdf_version,
        law_policy=law_policy,
    )


class CacheController:
    """Cache backend과 cache 사용 정책을 분리하는 LangGraph 전용 controller입니다."""

    DEFAULT_TTLS = {
        "faq_answer": 600,
        "route_spec": 900,
        "spec_bundle": 900,
        "product_result": 600,
        "pdf_lookup": 900,
        "law_result": 120,
    }

    def __init__(
        self,
        backend: CacheBackend | None = None,
        source_versions: SourceVersions | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._clock = clock
        self.backend = backend or InMemoryCache(clock=clock)
        self.source_versions = source_versions or SourceVersions(None, None, None)

    def context(self) -> "ToolCacheContext":
        return ToolCacheContext(self)

    def lookup(
        self,
        namespace: str,
        key_payload: dict[str, Any],
        source_version: str | None,
        policy_version: str,
    ) -> tuple[Any | None, str, str]:
        key_hash = stable_hash(key_payload)
        if source_version is None:
            return None, "bypass", key_hash
        entry = self.backend.get(namespace, key_hash)
        if entry is None:
            return None, "miss", key_hash
        if (
            entry.schema_version != CACHE_SCHEMA_VERSION
            or entry.policy_version != policy_version
            or entry.source_version != source_version
        ):
            self.backend.delete(namespace, key_hash)
            return None, "miss", key_hash
        return deepcopy(entry.value), "hit", key_hash

    def store(
        self,
        namespace: str,
        key_payload: dict[str, Any],
        value: Any,
        source_version: str | None,
        policy_version: str,
        evidence_status: list[str] | tuple[str, ...] = (),
        eligible: bool = True,
        ttl_seconds: int | None = None,
    ) -> bool:
        if source_version is None or not eligible:
            return False
        now = self._clock()
        ttl = ttl_seconds or self.DEFAULT_TTLS[namespace]
        entry = CacheEntry(
            namespace=namespace,
            key_hash=stable_hash(key_payload),
            value=deepcopy(value),
            schema_version=CACHE_SCHEMA_VERSION,
            policy_version=policy_version,
            source_version=source_version,
            created_at=now,
            expires_at=now + ttl,
            evidence_status=tuple(evidence_status),
            eligible=eligible,
        )
        self.backend.set(entry)
        return True

    def invalidate_namespace(self, namespace: str) -> int:
        return self.backend.invalidate_namespace(namespace)


class ToolCacheContext:
    """기존 Core가 선택적으로 사용할 Product/PDF/Law 결과 cache wrapper입니다."""

    def __init__(self, controller: CacheController) -> None:
        self.controller = controller
        self.lookup_count = 0
        self.hit_count = 0
        self.types_used: list[str] = []

    def _remember(self, namespace: str, status: str) -> None:
        self.lookup_count += 1
        if status == "hit":
            self.hit_count += 1
            if namespace not in self.types_used:
                self.types_used.append(namespace)

    def product_results(
        self,
        question: str,
        query_spec: Any,
        loader: Callable[[], list[dict[str, Any]]],
    ) -> list[dict[str, Any]]:
        payload = {
            "question": normalize_question(question),
            "query_spec": getattr(query_spec, "__dict__", {}),
        }
        value, status, _ = self.controller.lookup(
            "product_result",
            payload,
            self.controller.source_versions.product_db,
            "product-query-v1",
        )
        self._remember("product_result", status)
        if status == "hit":
            return value
        result = loader()
        self.controller.store(
            "product_result",
            payload,
            result,
            self.controller.source_versions.product_db,
            "product-query-v1",
        )
        return deepcopy(result)

    def pdf_chunks(
        self,
        source_file: str,
        source_page: int,
        loader: Callable[[], list[dict[str, Any]]],
    ) -> list[dict[str, Any]]:
        value, status = self.lookup_pdf_chunks(source_file, source_page)
        if status == "hit":
            return value
        result = loader()
        self.store_pdf_chunks(source_file, source_page, result)
        return deepcopy(result)

    def lookup_pdf_chunks(
        self,
        source_file: str,
        source_page: int,
    ) -> tuple[list[dict[str, Any]] | None, str]:
        payload = {"source_file": source_file, "source_page": source_page}
        value, status, _ = self.controller.lookup(
            "pdf_lookup",
            payload,
            self.controller.source_versions.pdf_index,
            "pdf-direct-lookup-v1",
        )
        self._remember("pdf_lookup", status)
        if status == "hit":
            return value, status
        return None, status

    def store_pdf_chunks(
        self,
        source_file: str,
        source_page: int,
        result: list[dict[str, Any]],
    ) -> None:
        payload = {"source_file": source_file, "source_page": source_page}
        self.controller.store(
            "pdf_lookup",
            payload,
            result,
            self.controller.source_versions.pdf_index,
            "pdf-direct-lookup-v1",
        )

    def law_result(
        self,
        question: str,
        loader: Callable[[], dict[str, Any]],
    ) -> dict[str, Any]:
        payload = {"question": normalize_question(question)}
        value, status, _ = self.controller.lookup(
            "law_result",
            payload,
            self.controller.source_versions.law_policy,
            "law-tool-v1",
        )
        self._remember("law_result", status)
        if status == "hit":
            return value
        result = loader()
        evidence_version = _law_evidence_version(result)
        self.controller.store(
            "law_result",
            payload,
            result,
            self.controller.source_versions.law_policy,
            "law-tool-v1",
            eligible=evidence_version is not None and bool(result.get("success")),
        )
        return deepcopy(result)


def _law_evidence_version(result: dict[str, Any]) -> str | None:
    dates = []
    for source in [*(result.get("primary_sources") or []), *(result.get("references") or [])]:
        date = source.get("article_effective_date") or source.get("effective_date")
        if date:
            dates.append(str(date))
    return stable_hash(sorted(dates)) if dates else None


def evidence_statuses(result: dict[str, Any]) -> list[str]:
    statuses: list[str] = []
    for product in result.get("evidence_status") or []:
        for field in product.get("fields") or []:
            status = field.get("status")
            if status:
                statuses.append(str(status))
    for item in result.get("pdf_evidence") or []:
        status = item.get("status")
        if status:
            statuses.append(str(status))
    law_status = result.get("law_evidence_status")
    if law_status:
        statuses.append(str(law_status))
    return statuses


def faq_eligible(question: str, result: dict[str, Any]) -> bool:
    """개인화·추천·오류·불완전 Evidence는 FAQ answer cache에서 제외합니다."""
    personal_markers = (
        "추천", "나에게", "내게", "제 상황", "개인", "적합", "포트폴리오",
    )
    normalized = normalize_question(question)
    if any(marker in normalized for marker in personal_markers):
        return False
    if result.get("route") != "document" or not result.get("results"):
        return False
    if result.get("errors") or not result.get("answer"):
        return False
    statuses = evidence_statuses(result)
    return not any(status in {"missing", "unresolved", "conflict"} for status in statuses)


__all__ = [
    "CACHE_SCHEMA_VERSION",
    "FAQ_POLICY_VERSION",
    "ROUTER_POLICY_VERSION",
    "CacheBackend",
    "CacheController",
    "CacheEntry",
    "InMemoryCache",
    "SourceVersions",
    "SourceVersionTracker",
    "ToolCacheContext",
    "evidence_statuses",
    "faq_eligible",
    "fingerprint_paths",
    "normalize_question",
    "source_versions_from_agent",
    "stable_hash",
]
