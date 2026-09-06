from __future__ import annotations

from copy import deepcopy
import re
from typing import Any

from .display_units import format_financial_value, public_source_citation
from .law_tool import LawTool
from .product_db_adapter import (
    JsonProductDBAdapter,
    ProductDBAdapter,
    ProductQuerySpec,
    create_product_db_adapter,
)
from .product_normalizer import ProductNormalizer
from .query_router import QueryRouter, RouteDecision, product_search_hints
from .chatbot_core import PensionChatbot
from .llm_provider import HyperClovaProviderAdapter
from .model_policy import llm_for_role
from .paths import REPO_ROOT
from .risk_policy import label_for_grade


PRODUCTS_PATH = REPO_ROOT / "data" / "structured" / "products"


def _is_product_risk_question(question: str) -> bool:
    q = question or ""
    if any(token in q for token in ("추천", "보여줘", "비교", "이하", "이상")):
        return False
    return any(token in q for token in ("위험은", "투자위험", "위험등급은", "리스크는"))


class PensionAgentCore:
    """
    현재 문서 RAG와 미래 상품 DB 사이를 연결하는 오케스트레이터입니다.
    상품 DB가 도착하면 product_db 인자만 실제 Adapter로 바꾸면 됩니다.
    """

    def __init__(self, product_db: ProductDBAdapter | None = None) -> None:
        self.document_chatbot = PensionChatbot()
        self.answer_provider = HyperClovaProviderAdapter(self.document_chatbot.llm)
        self.normalizer_provider = HyperClovaProviderAdapter(llm_for_role("normalizer"))
        self.product_db = product_db or create_product_db_adapter(
            normalizer=ProductNormalizer.from_environment(self.normalizer_provider),
            fallback_path=PRODUCTS_PATH,
        )
        self.router = QueryRouter(
            product_hints=product_search_hints(getattr(self.product_db, "records", []) or []),
        )
        self.law_tool = LawTool()

    def answer(self, question: str, top_k: int = 5) -> dict:
        question = question.strip()
        if not question:
            raise ValueError("질문이 비어 있습니다.")

        decision = self.router.decide(question)
        return self.run_with_decision(question, decision, top_k=top_k)

    def run_with_decision(
        self,
        question: str,
        decision: RouteDecision,
        top_k: int = 5,
        tool_cache: Any | None = None,
    ) -> dict:
        """Execute the existing dispatch with an already selected route.

        This thin public adapter lets an orchestrator reuse one QueryRouter
        decision without copying Product, Document, or Law business logic.
        """
        question = question.strip()
        if not question:
            raise ValueError("질문이 비어 있습니다.")

        if decision.tools in (["product", "law"], ["law", "product"]):
            return self._answer_with_product_and_law(
                question,
                top_k,
                decision.reason,
                tool_cache=tool_cache,
            )

        if decision.tools == ["document", "law"]:
            return self._answer_with_document_and_law(
                question,
                top_k,
                decision.reason,
                tool_cache=tool_cache,
            )

        if decision.tools == ["law"]:
            return self._answer_with_law(
                question,
                decision.reason,
                tool_cache=tool_cache,
            )

        if decision.route == "document":
            result = self.document_chatbot.answer(question, top_k=top_k)
            result["route"] = "document"
            result["route_reason"] = decision.reason
            result["tools"] = decision.tools
            result["product_db_available"] = self.product_db.available
            return result

        if decision.route == "product":
            if not self.product_db.available:
                return {
                    "question": question,
                    "answer": (
                        "이 질문은 상품 DB 조회가 적합하지만 현재 상품 DB가 아직 연결되지 않았습니다. "
                        "팀원의 상품 DB가 연결되면 구조화 조회로 처리할 수 있습니다."
                    ),
                    "results": [],
                    "product_results": [],
                    "route": "product",
                    "route_reason": decision.reason,
                    "tools": decision.tools,
                    "product_db_available": False,
                    "model": self.document_chatbot.llm.model,
                }

            product_results = self._search_product_results(
                question,
                top_k,
                tool_cache=tool_cache,
            )
            return self._answer_with_product_evidence(
                question,
                product_results,
                top_k,
                decision.reason,
                tool_cache=tool_cache,
            )

        document_result = self.document_chatbot.answer(question, top_k=top_k)

        if not self.product_db.available:
            document_result["answer"] += (
                "\n\n참고: 이 질문에는 상품 DB 조회도 함께 사용하는 것이 적합하지만 "
                "현재 상품 DB가 아직 연결되지 않아 내부 문서 근거만 사용했습니다."
            )
            document_result["route"] = "both"
            document_result["route_reason"] = decision.reason
            document_result["tools"] = decision.tools
            document_result["product_results"] = []
            document_result["product_db_available"] = False
            return document_result

        document_result["product_results"] = self._search_product_results(
            question,
            top_k,
            tool_cache=tool_cache,
        )
        document_result["route"] = "both"
        document_result["route_reason"] = decision.reason
        document_result["tools"] = decision.tools
        document_result["product_db_available"] = True
        return document_result

    def collect_evidence_with_decision(
        self,
        question: str,
        decision: RouteDecision,
        top_k: int = 5,
        tool_cache: Any | None = None,
    ) -> dict[str, Any]:
        """Collect a request-local evidence bundle without changing any LLM method.

        This deliberately does not delegate to the legacy answer helpers because
        those helpers create the final answer.  It reuses their lower-level
        retrieval, Product/PDF preparation, and Law formatting helpers, so no
        mutable global capture hook is needed and concurrent calls stay isolated.
        """
        question = question.strip()
        route, tools = decision.route, list(decision.tools)
        model = self.document_chatbot.llm.model
        base = {"question": question, "route": route, "route_reason": decision.reason,
                "tools": tools, "product_db_available": self.product_db.available,
                "model": model, "results": [], "product_results": []}

        if route == "document":
            contexts = self.document_chatbot.retriever.retrieve(
                question, top_k=top_k, source_group="docs"
            )
            return {"result": {**base, "results": contexts, "used_tools": ["document"]},
                    "answer_kind": "document", "contexts": contexts, "evidence_text": ""}

        if route == "law":
            law_result = self._search_law_result(question, tool_cache=tool_cache)
            text = self._law_result_to_text(law_result)
            result = {**base, "law_result": law_result, "used_tools": ["law"]}
            return {"result": result, "answer_kind": "evidence" if law_result.get("success") and text else None,
                    "contexts": [], "evidence_text": text}

        if route == "document+law":
            contexts = self.document_chatbot.retriever.retrieve(
                question, top_k=top_k, source_group="docs"
            )
            law_result = self._search_law_result(question, tool_cache=tool_cache)
            document_text = self._document_contexts_to_text(contexts)
            law_text = self._law_result_to_text(law_result)
            text = f"[LAW_EVIDENCE]\n{law_text}\n\n[DOCUMENT_EVIDENCE]\n{document_text}"
            return {"result": {**base, "results": contexts, "law_result": law_result, "used_tools": ["document", "law"]},
                    "answer_kind": "evidence" if document_text or law_text else None,
                    "contexts": [], "evidence_text": text}

        product_results: list[dict[str, Any]] = []
        pdf_evidence: list[dict[str, Any]] = []
        evidence_status: list[dict[str, Any]] = []
        structured_text = "No matching structured product records were found."
        pdf_text = "No linked PDF evidence was found."
        if self.product_db.available:
            product_results = self._search_product_results(question, top_k, tool_cache=tool_cache)
            if product_results:
                _, pdf_evidence, evidence_status, structured_text, pdf_text = self._prepare_product_evidence(
                    question, product_results, top_k, tool_cache=tool_cache
                )
        product_result = {**base, "product_results": product_results, "pdf_evidence": pdf_evidence,
                          "evidence_status": evidence_status, "used_tools": ["product", "document"]}
        product_text = f"[STRUCTURED_DB_EVIDENCE]\n{structured_text}\n\n[PDF_EVIDENCE]\n{pdf_text}"
        if route == "product":
            named_miss = False
            if hasattr(self.product_db, "parse_query"):
                spec = self.product_db.parse_query(question, top_k)
                named_miss = bool(getattr(spec, "name_match_required", False) and not product_results)
            if named_miss:
                message = "현재 상품 DB에서 해당 상품을 확인하지 못했습니다. 다른 상품으로 대체하지 않았습니다."
                product_result["answer"] = message
                product_result["final_answer"] = message
                return {"result": product_result, "answer_kind": "direct", "contexts": [], "evidence_text": product_text}
            return {"result": product_result,
                    "answer_kind": "evidence" if product_results else None,
                    "contexts": [], "evidence_text": product_text}

        try:
            law_result = self._search_law_result(question, tool_cache=tool_cache)
        except Exception as exc:
            law_result = {"success": False, "primary_sources": [], "references": [],
                          "message": f"LawTool lookup failed: {type(exc).__name__}"}
        law_text = self._law_result_to_text(law_result)
        result = {**product_result, "route": "product+law", "tools": ["product", "law"],
                  "law_result": law_result, "law_results": law_result,
                  "law_evidence_status": "matched" if law_result.get("success") else "unresolved",
                  "used_tools": ["product", "law"]}
        return {"result": result,
                "answer_kind": "evidence" if product_results or law_result.get("success") else None,
                "contexts": [], "evidence_text": f"{product_text}\n\n[LAW_EVIDENCE]\n{law_text}"}

    def generate_answer_from_collection(
        self, question: str, collection: dict[str, Any]
    ) -> str | None:
        """Generate exactly one final answer from a verified evidence collection."""
        kind = collection.get("answer_kind")
        if kind == "document":
            return self.answer_provider.answer_from_context(
                question, collection.get("contexts") or []
            )
        if kind == "evidence":
            products = (collection.get("result") or {}).get("product_results") or []
            if products and _is_product_risk_question(question):
                return self.compose_product_risk_answer(question, products)
            try:
                return self.answer_provider.answer_from_evidence(
                    question, collection.get("evidence_text") or ""
                )
            except Exception:
                if products:
                    return self.compose_product_answer(question, products)
                raise
        if kind == "direct":
            return str((collection.get("result") or {}).get("answer") or "")
        return None

    @staticmethod
    def compose_product_answer(question: str, products: list[dict[str, Any]]) -> str:
        """Authoritative product facts only. No LLM-invented numbers or substitutes."""
        if not products:
            return "현재 상품 DB에서 해당 상품을 확인하지 못했습니다. 다른 상품으로 대체하지 않았습니다."
        if _is_product_risk_question(question):
            return PensionAgentCore.compose_product_risk_answer(question, products)
        header = "상품 DB에서 확인된 결과입니다."
        if "추천" in question:
            header = (
                "입력한 위험 선호 조건 기준으로 비교 가능한 후보입니다. "
                "개인 투자 적합성이나 수익을 보장하는 추천은 아닙니다."
            )
        elif any(token in question for token in ("보수", "수수료")):
            header = "상품 DB의 총보수 값으로 정렬한 결과입니다."
        elif any(token in question for token in ("단기", "중장기", "장기")) and "솔로몬" in question:
            header = "솔로몬 국공채 계열에서 확인된 상품입니다. 일반 금리 민감도 설명은 기업 근거 없이 단정하지 않습니다."
        lines = [header]
        if not re.search(r"\d+\s*(?:개|종)", question or ""):
            lines.insert(0, f"다음 {len(products)}개를 보여드리겠습니다.")
        for index, product in enumerate(products, start=1):
            parts = [f"{index}. {product.get('product_name') or '이름 없음'}"]
            if product.get("class_name"):
                parts.append(f"클래스 {product['class_name']}")
            if product.get("risk_grade") is not None:
                label = product.get("risk_label") or label_for_grade(product.get("risk_grade"))
                grade_text = f"위험등급 {product['risk_grade']}"
                if label:
                    grade_text += f"({label})"
                parts.append(grade_text)
            if product.get("total_fee") is not None:
                parts.append(
                    "총보수 "
                    + format_financial_value(
                        product["total_fee"],
                        product.get("total_fee_unit"),
                        "fee",
                    )
                )
            if product.get("selected_performance_value") is not None:
                audit = product.get("selected_performance_audit") or {}
                period = product.get("selected_performance_period") or ""
                formatted = format_financial_value(
                    product["selected_performance_value"],
                    product.get("selected_performance_unit") or audit.get("unit"),
                    product.get("selected_performance_metric_type") or "fund_return",
                    status=product.get("selected_performance_status") or audit.get("status"),
                )
                if "단위/스케일 확인" in formatted:
                    parts.append(f"{period} {formatted}".strip())
                else:
                    parts.append(f"{period} 수익률 {formatted}".strip())
            source = product.get("source_file")
            if source:
                parts.append(f"출처 {source}")
            lines.append(" / ".join(parts))
        lines.append(f"근거 출처: {public_source_citation()}.")
        return "\n".join(lines)

    @staticmethod
    def compose_product_risk_answer(question: str, products: list[dict[str, Any]]) -> str:
        """Answer risk questions from risk_ratings + INVESTMENT_RISK narratives only."""
        product = products[0]
        name = product.get("product_name") or "해당 상품"
        grade = product.get("risk_grade")
        label = product.get("risk_label") or label_for_grade(grade)
        lines = []
        if grade is not None:
            grade_text = f"{grade}등급"
            if label:
                grade_text += f"({label})"
            lines.append(f"{name}의 투자위험등급은 {grade_text}입니다.")
        elif label:
            lines.append(f"{name}의 투자위험 구분은 {label}입니다.")
        else:
            lines.append(f"{name}의 투자위험등급을 상품 DB에서 확인하지 못했습니다.")
        risks = [
            item
            for item in product.get("investment_risks") or []
            if isinstance(item, dict) and (item.get("subject") or item.get("text"))
        ]
        if risks:
            lines.append("")
            lines.append("주요 위험:")
            seen: set[str] = set()
            for item in risks:
                subject = str(item.get("subject") or "").strip()
                text = str(item.get("text") or "").strip()
                key = subject or text[:40]
                if key in seen:
                    continue
                seen.add(key)
                if subject and text:
                    lines.append(f"- {subject}: {text}")
                elif subject:
                    lines.append(f"- {subject}")
                else:
                    lines.append(f"- {text}")
        pages = product.get("source_pages") or []
        source = product.get("source_file")
        citation = public_source_citation(source)
        if pages:
            citation += f", {pages[0]}쪽"
        lines.append("")
        lines.append(f"근거 출처: {citation}.")
        return "\n".join(lines)

    def _search_product_results(
        self,
        question: str,
        top_k: int,
        tool_cache: Any | None = None,
    ) -> list[dict[str, Any]]:
        if isinstance(self.product_db, JsonProductDBAdapter):
            query_spec = self.product_db.parse_query(question, limit=top_k)
        else:
            query_spec = ProductQuerySpec(limit=top_k)

        loader = lambda: self.product_db.search(question, limit=top_k)
        if tool_cache is not None:
            return tool_cache.product_results(question, query_spec, loader)
        return loader()

    def _search_law_result(
        self,
        question: str,
        tool_cache: Any | None = None,
    ) -> dict[str, Any]:
        if tool_cache is not None:
            return tool_cache.law_result(question, lambda: self.law_tool.search(question))
        return self.law_tool.search(question)

    def _answer_with_product_evidence(
        self,
        question: str,
        product_results: list[dict[str, Any]],
        top_k: int,
        route_reason: str,
        tool_cache: Any | None = None,
    ) -> dict:
        """확정된 Product DB 결과에만 관련 PDF 원문 근거를 연결합니다."""
        if not product_results:
            return {
                "question": question,
                "answer": self._product_results_to_answer(product_results),
                "results": [],
                "product_results": [],
                "pdf_evidence": [],
                "evidence_status": [],
                "route": "product",
                "route_reason": route_reason,
                "tools": ["product"],
                "used_tools": ["product"],
                "llm_call_count": 0,
                "product_db_available": True,
                "model": self.document_chatbot.llm.model,
            }

        _, pdf_evidence, evidence_status, structured_text, pdf_text = (
            self._prepare_product_evidence(
                question,
                product_results,
                top_k,
                tool_cache=tool_cache,
            )
        )
        evidence_text = (
            f"[STRUCTURED_DB_EVIDENCE]\n{structured_text}"
            f"\n\n[PDF_EVIDENCE]\n{pdf_text}"
        )
        answer = self.answer_provider.answer_from_evidence(
            question,
            evidence_text,
        )

        return {
            "question": question,
            "answer": answer,
            "results": [],
            "product_results": product_results,
            "pdf_evidence": pdf_evidence,
            "evidence_status": evidence_status,
            "route": "product",
            "route_reason": route_reason,
            "tools": ["product"],
            "used_tools": ["product", "document"],
            "llm_call_count": 1,
            "product_db_available": True,
            "model": self.document_chatbot.llm.model,
        }

    def _answer_with_product_and_law(
        self,
        question: str,
        top_k: int,
        route_reason: str,
        tool_cache: Any | None = None,
    ) -> dict:
        """확정 상품/PDF 근거와 LawTool 근거를 모아 LLM을 한 번만 호출합니다."""
        product_results: list[dict[str, Any]] = []
        pdf_evidence: list[dict[str, Any]] = []
        evidence_status: list[dict[str, Any]] = []
        structured_text = "구조화 상품 DB가 연결되지 않았거나 조건에 맞는 상품이 없습니다."
        pdf_text = "선택된 상품 필드에 연결된 PDF 원문 근거를 찾지 못했습니다."
        used_tools: list[str] = []

        if self.product_db.available:
            product_results = self._search_product_results(
                question,
                top_k,
                tool_cache=tool_cache,
            )
            used_tools.append("product")
            if product_results:
                _, pdf_evidence, evidence_status, structured_text, pdf_text = (
                    self._prepare_product_evidence(
                        question,
                        product_results,
                        top_k,
                        tool_cache=tool_cache,
                    )
                )

        try:
            law_result = self._search_law_result(question, tool_cache=tool_cache)
        except Exception:
            law_result = {
                "success": False,
                "topic": None,
                "message": "LawTool 법령 조회를 완료하지 못했습니다.",
                "primary_sources": [],
                "references": [],
            }

        used_tools.append("law")
        law_text = self._law_result_to_text(law_result)
        if not law_text:
            law_text = (
                "LawTool 조회 상태: "
                f"{law_result.get('message') or '법령 근거를 찾지 못했습니다.'}"
            )

        if not product_results and not law_result.get("success"):
            return {
                "question": question,
                "answer": "상품 DB와 법령 근거에서 관련 내용을 찾지 못했습니다.",
                "results": [],
                "product_results": [],
                "pdf_evidence": [],
                "law_result": law_result,
                "law_results": law_result,
                "evidence_status": evidence_status,
                "law_evidence_status": "missing",
                "route": "product+law",
                "route_reason": route_reason,
                "tools": ["product", "law"],
                "used_tools": used_tools,
                "used_evidence_sources": used_tools,
                "llm_call_count": 0,
                "product_db_available": self.product_db.available,
                "model": self.document_chatbot.llm.model,
            }

        evidence_text = (
            f"[STRUCTURED_DB_EVIDENCE]\n{structured_text}"
            f"\n\n[PDF_EVIDENCE]\n{pdf_text}"
            f"\n\n[LAW_EVIDENCE]\n{law_text}"
        )
        answer = self.answer_provider.answer_from_evidence(
            question,
            evidence_text,
        )

        return {
            "question": question,
            "answer": answer,
            "results": [],
            "product_results": product_results,
            "pdf_evidence": pdf_evidence,
            "law_result": law_result,
            "law_results": law_result,
            "evidence_status": evidence_status,
            "law_evidence_status": (
                "matched" if law_result.get("success") else "unresolved"
            ),
            "route": "product+law",
            "route_reason": route_reason,
            "tools": ["product", "law"],
            "used_tools": used_tools,
            "used_evidence_sources": ["product", "pdf", "law"],
            "llm_call_count": 1,
            "product_db_available": self.product_db.available,
            "model": self.document_chatbot.llm.model,
        }

    def _prepare_product_evidence(
        self,
        question: str,
        product_results: list[dict[str, Any]],
        top_k: int,
        tool_cache: Any | None = None,
    ) -> tuple[
        ProductQuerySpec,
        list[dict[str, Any]],
        list[dict[str, Any]],
        str,
        str,
    ]:
        """Product 단독 및 Product+Law 경로가 같은 PDF Evidence 선택을 사용합니다."""
        if isinstance(self.product_db, JsonProductDBAdapter):
            query_spec = self.product_db.parse_query(question, limit=top_k)
        else:
            query_spec = ProductQuerySpec(limit=top_k)

        required_fields = self._required_product_evidence_fields(query_spec)
        selected_evidence = [
            self._select_product_evidence(result, required_fields)
            for result in product_results
        ]
        pdf_evidence, evidence_status = self._resolve_product_pdf_evidence(
            product_results,
            selected_evidence,
            required_fields,
            tool_cache=tool_cache,
        )
        structured_text = self._product_results_to_evidence_text(
            product_results,
            evidence_status,
            query_spec,
        )
        pdf_text = self._pdf_evidence_to_text(pdf_evidence)

        return (
            query_spec,
            pdf_evidence,
            evidence_status,
            structured_text,
            pdf_text,
        )

    @staticmethod
    def _required_product_evidence_fields(
        query_spec: ProductQuerySpec,
    ) -> list[str]:
        fields = ["product_identity", "class"]

        if query_spec.irp_only:
            fields.append("pension_type")
        if query_spec.risk_grade_max is not None or query_spec.risk_grade_min is not None:
            fields.append("risk_grade")
        if query_spec.sort_by == "total_fee":
            fields.append("total_fee")
        if query_spec.sort_by == "performance":
            fields.append("performance")
        if query_spec.online_only:
            fields.append("online")

        return fields

    @staticmethod
    def _select_product_evidence(
        product_result: dict[str, Any],
        required_fields: list[str],
    ) -> list[dict[str, Any]]:
        """JSON의 실제 evidence.field_path만 사용해 질문 관련 근거를 고릅니다."""
        selected: dict[str, dict[str, Any]] = {}

        for evidence in product_result.get("evidence") or []:
            field_path = str(evidence.get("field_path") or "")
            matched_fields = set()

            if "product_identity" in required_fields and field_path.startswith("product"):
                matched_fields.add("product_identity")
            if (
                {"class", "pension_type", "online"}.intersection(required_fields)
                and "classes" in field_path
            ):
                matched_fields.update(
                    {"class", "pension_type", "online"}.intersection(
                        required_fields
                    )
                )
            if "risk_grade" in required_fields and field_path.startswith(
                "risk_ratings"
            ):
                matched_fields.add("risk_grade")
            if "total_fee" in required_fields and field_path.startswith("fees"):
                matched_fields.add("total_fee")
            if "performance" in required_fields and field_path.startswith("performance"):
                matched_fields.add("performance")

            if matched_fields:
                item = deepcopy(evidence)
                item["matched_fields"] = sorted(matched_fields)
                selected[str(item.get("evidence_id"))] = item

        return list(selected.values())

    def _resolve_product_pdf_evidence(
        self,
        product_results: list[dict[str, Any]],
        selected_evidence: list[list[dict[str, Any]]],
        required_fields: list[str],
        tool_cache: Any | None = None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """전역 검색 없이 source_file/page가 일치하는 PDF 청크만 가져옵니다."""
        target_locations = {
            (result.get("source_file"), evidence.get("page"))
            for result, evidence_items in zip(product_results, selected_evidence)
            for evidence in evidence_items
            if result.get("source_file") and evidence.get("page") is not None
        }
        chunks_by_location: dict[tuple[str, int], list[dict[str, Any]]] = {}
        missing_locations = set(target_locations)

        if tool_cache is not None:
            for source_file, source_page in target_locations:
                cached_chunks, status = tool_cache.lookup_pdf_chunks(
                    source_file,
                    source_page,
                )
                if status == "hit" and cached_chunks is not None:
                    chunks_by_location[(source_file, source_page)] = cached_chunks
                    missing_locations.discard((source_file, source_page))

        if missing_locations:
            discovered: dict[tuple[str, int], list[dict[str, Any]]] = {}
            for chunk in self.document_chatbot.retriever.chunks:
                key = (chunk.get("filename"), chunk.get("location"))
                if chunk.get("location_type") == "page" and key in missing_locations:
                    discovered.setdefault(key, []).append(chunk)

            for source_file, source_page in missing_locations:
                chunks = discovered.get((source_file, source_page), [])
                chunks_by_location[(source_file, source_page)] = chunks
                if tool_cache is not None:
                    tool_cache.store_pdf_chunks(source_file, source_page, chunks)

        pdf_evidence: list[dict[str, Any]] = []
        evidence_status: list[dict[str, Any]] = []

        for result, evidence_items in zip(product_results, selected_evidence):
            field_items = []
            grouped_pages: dict[tuple[str, int], list[dict[str, Any]]] = {}
            for evidence in evidence_items:
                page_key = (result.get("source_file"), evidence.get("page"))
                grouped_pages.setdefault(page_key, []).append(evidence)

            for field in required_fields:
                related = [
                    evidence
                    for evidence in evidence_items
                    if field in evidence.get("matched_fields", [])
                ]
                value = self._product_field_value(result, field)

                if value is None:
                    status = "missing"
                    chunk_count = 0
                elif not related:
                    status = "missing"
                    chunk_count = 0
                else:
                    related_chunks = [
                        chunk
                        for evidence in related
                        for chunk in chunks_by_location.get(
                            (result.get("source_file"), evidence.get("page")),
                            [],
                        )
                    ]
                    chunk_count = len(related_chunks)
                    status = "matched" if chunk_count else "unresolved"
                    if status == "matched" and self._has_clear_risk_conflict(
                        field,
                        value,
                        related,
                        related_chunks,
                    ):
                        status = "conflict"

                field_items.append(
                    {
                        "field": field,
                        "value": value,
                        "status": status,
                        "evidence_ids": [
                            evidence.get("evidence_id") for evidence in related
                        ],
                        "pdf_chunk_count": chunk_count,
                    }
                )

            for (source_file, page), page_evidence in grouped_pages.items():
                chunks = chunks_by_location.get((source_file, page), [])
                fields = sorted(
                    {
                        field
                        for evidence in page_evidence
                        for field in evidence.get("matched_fields", [])
                    }
                )
                pdf_evidence.append(
                    {
                        "product_name": result.get("product_name"),
                        "class_name": result.get("class_name"),
                        "source_file": source_file,
                        "source_page": page,
                        "fields": fields,
                        "evidence": [
                            {
                                "evidence_id": evidence.get("evidence_id"),
                                "field_path": evidence.get("field_path"),
                                "section": evidence.get("section"),
                                "source_text": evidence.get("source_text"),
                            }
                            for evidence in page_evidence
                        ],
                        "chunks": [
                            {
                                "chunk_id": chunk.get("chunk_id"),
                                "document_id": chunk.get("document_id"),
                                "filename": chunk.get("filename"),
                                "location_type": chunk.get("location_type"),
                                "location": chunk.get("location"),
                                "text": chunk.get("text"),
                            }
                            for chunk in chunks
                        ],
                        "status": "matched" if chunks else "unresolved",
                    }
                )

            evidence_status.append(
                {
                    "product_name": result.get("product_name"),
                    "class_name": result.get("class_name"),
                    "fields": field_items,
                }
            )

        return pdf_evidence, evidence_status

    @staticmethod
    def _product_field_value(product_result: dict[str, Any], field: str) -> Any:
        values = {
            "product_identity": product_result.get("product_name"),
            "class": product_result.get("class_name"),
            "pension_type": product_result.get("pension_type"),
            "risk_grade": product_result.get("risk_grade"),
            "total_fee": product_result.get("total_fee"),
            "performance": product_result.get("selected_performance_value"),
            "online": product_result.get("is_online"),
        }
        return values.get(field)

    @staticmethod
    def _has_clear_risk_conflict(
        field: str,
        value: Any,
        evidence_items: list[dict[str, Any]],
        chunks: list[dict[str, Any]],
    ) -> bool:
        """대상 Evidence 원문에 등급 하나가 명시된 경우에만 충돌로 표시합니다."""
        if field != "risk_grade" or not isinstance(value, int):
            return False

        # PDF 청크에는 위험등급 분류표의 주변 등급도 포함될 수 있으므로, 해당
        # Evidence가 직접 가리킨 source_text에 단일 등급이 있을 때만 비교합니다.
        text = "\n".join(
            str(item.get("source_text") or "") for item in evidence_items
        )
        found_grades = {
            int(grade)
            for grade in re.findall(r"(\d+)\s*등급", text)
        }
        return len(found_grades) == 1 and value not in found_grades

    def _product_results_to_evidence_text(
        self,
        product_results: list[dict[str, Any]],
        evidence_status: list[dict[str, Any]],
        query_spec: ProductQuerySpec,
    ) -> str:
        statuses_by_product = {
            (item.get("product_name"), item.get("class_name")): item
            for item in evidence_status
        }
        blocks = []

        for index, result in enumerate(product_results, start=1):
            lines = [
                f"[상품 {index}]",
                f"상품명: {result.get('product_name') or '확인되지 않음'}",
                f"상품 클래스: {result.get('class_name') or '확인되지 않음'}",
                f"상품 KOFIA 코드: {result.get('product_kofia_fund_code') or '확인되지 않음'}",
                f"클래스 KOFIA 코드: {result.get('class_kofia_fund_code') or '확인되지 않음'}",
                f"기준일: {result.get('as_of_date') or '확인되지 않음'}",
            ]
            if query_spec.irp_only:
                lines.append(f"연금 유형: {result.get('pension_type') or '확인되지 않음'}")
            if query_spec.risk_grade_max is not None or query_spec.risk_grade_min is not None:
                lines.append(
                    f"위험등급: {result.get('risk_grade') or '확인되지 않음'}"
                )
            if query_spec.sort_by == "total_fee":
                if result.get("total_fee") is None:
                    lines.append("총보수: 확인되지 않음")
                else:
                    lines.append(
                        "총보수: "
                        + format_financial_value(
                            result.get("total_fee"),
                            result.get("total_fee_unit"),
                            "fee",
                        )
                    )
            if query_spec.sort_by == "performance":
                value = result.get("selected_performance_value")
                period = result.get("selected_performance_period") or query_spec.performance_period or "1Y"
                audit = result.get("selected_performance_audit") or {}
                if value is None:
                    lines.append(f"{period} 수익률: 확인되지 않음")
                else:
                    formatted = format_financial_value(
                        value,
                        result.get("selected_performance_unit") or audit.get("unit"),
                        result.get("selected_performance_metric_type") or "fund_return",
                        status=result.get("selected_performance_status") or audit.get("status"),
                    )
                    lines.append(f"{period} 수익률: {formatted}")
                    if audit.get("status"):
                        lines.append(f"수익률 검증 상태: {audit.get('status')} ({audit.get('reason')})")
            if query_spec.online_only:
                lines.append(f"판매 채널: {result.get('channel') or '확인되지 않음'}")

            status = statuses_by_product.get(
                (result.get("product_name"), result.get("class_name")),
                {},
            )
            for item in status.get("fields", []):
                lines.append(
                    f"근거 상태({item['field']}): {item['status']}"
                )
            blocks.append("\n".join(lines))

        return "\n\n".join(blocks)

    @staticmethod
    def _pdf_evidence_to_text(pdf_evidence: list[dict[str, Any]]) -> str:
        if not pdf_evidence:
            return "선택된 상품 필드에 연결된 PDF 원문 근거를 찾지 못했습니다."

        blocks = []
        for index, item in enumerate(pdf_evidence, start=1):
            lines = [
                f"[PDF 근거 {index}]",
                f"상품명: {item.get('product_name') or '확인되지 않음'}",
                f"상품 클래스: {item.get('class_name') or '확인되지 않음'}",
                f"출처 파일: {item.get('source_file') or '확인되지 않음'}",
                f"페이지: {item.get('source_page') or '확인되지 않음'}",
                f"대상 필드: {', '.join(item.get('fields') or [])}",
                f"PDF 연결 상태: {item.get('status')}",
            ]
            for evidence in item.get("evidence") or []:
                lines.append(
                    f"Evidence {evidence.get('evidence_id')}: "
                    f"{evidence.get('field_path')} / {evidence.get('section')}"
                )
                lines.append(f"원문: {evidence.get('source_text') or '내용 없음'}")
            for chunk in item.get("chunks") or []:
                lines.append(
                    f"PDF 청크: {chunk.get('filename')} / "
                    f"{chunk.get('location_type')} {chunk.get('location')}"
                )
                lines.append(f"내용: {chunk.get('text') or '내용 없음'}")
            blocks.append("\n".join(lines))

        return "\n\n".join(blocks)

    @staticmethod
    def _product_results_to_answer(product_results: list[dict[str, Any]]) -> str:
        """구조화 조회값만 사용해 Product 단독 경로의 결과를 표시합니다."""
        if not product_results:
            return "구조화 상품 DB에서 질문 조건에 맞는 상품 클래스를 찾지 못했습니다."

        lines = [
            f"구조화 상품 DB에서 조건에 맞는 상품 클래스 {len(product_results)}건을 찾았습니다."
        ]
        for index, result in enumerate(product_results, start=1):
            product_name = result.get("product_name") or "상품명 확인되지 않음"
            class_name = result.get("class_name") or "클래스명 확인되지 않음"
            lines.append(f"{index}. {product_name} / {class_name}")

            details = []
            if result.get("risk_grade") is not None:
                details.append(f"위험등급 {result['risk_grade']}등급")
            if result.get("total_fee") is not None:
                details.append(
                    "총보수 "
                    + format_financial_value(
                        result["total_fee"],
                        result.get("total_fee_unit"),
                        "fee",
                    )
                )
            if result.get("selected_performance_value") is not None:
                audit = result.get("selected_performance_audit") or {}
                details.append(
                    f"{result.get('selected_performance_period') or '1Y'} 수익률 "
                    + format_financial_value(
                        result["selected_performance_value"],
                        result.get("selected_performance_unit") or audit.get("unit"),
                        result.get("selected_performance_metric_type") or "fund_return",
                        status=result.get("selected_performance_status") or audit.get("status"),
                    )
                )
            if result.get("pension_type"):
                details.append(f"연금 유형 {result['pension_type']}")
            if result.get("channel"):
                details.append(f"판매 채널 {result['channel']}")
            if details:
                lines.append("   " + " / ".join(details))

            source_file = result.get("source_file") or "출처 파일 확인되지 않음"
            source_pages = ", ".join(
                str(page) for page in result.get("source_pages") or []
            )
            evidence_ids = ", ".join(result.get("evidence_ids") or [])
            lines.append(
                f"   근거: {source_file}"
                + (f" p.{source_pages}" if source_pages else "")
                + (f" / evidence: {evidence_ids}" if evidence_ids else "")
            )

        return "\n".join(lines)

    def _answer_with_document_and_law(
        self,
        question: str,
        top_k: int,
        route_reason: str,
        tool_cache: Any | None = None,
    ) -> dict:
        """문서 RAG와 LawTool의 근거를 모은 뒤 LLM을 한 번만 호출합니다."""
        contexts = self.document_chatbot.retriever.retrieve(
            question, top_k=top_k, source_group="docs"
        )
        law_result = self._search_law_result(question, tool_cache=tool_cache)

        document_evidence = self._document_contexts_to_text(contexts)
        law_evidence = self._law_result_to_text(law_result)
        evidence_text = (
            f"[LAW_EVIDENCE]\n{law_evidence or '조회된 법령 근거가 없습니다.'}"
            f"\n\n[DOCUMENT_EVIDENCE]\n"
            f"{document_evidence or '검색된 내부 문서 근거가 없습니다.'}"
        )

        if not document_evidence and not law_evidence:
            return {
                "question": question,
                "answer": "제공된 자료와 법령 근거에서 관련 내용을 찾지 못했습니다.",
                "results": [],
                "law_result": law_result,
                "route": "document+law",
                "route_reason": route_reason,
                "tools": ["document", "law"],
                "used_tools": ["document", "law"],
                "product_results": [],
                "product_db_available": self.product_db.available,
                "model": self.document_chatbot.llm.model,
            }

        answer = self.answer_provider.answer_from_evidence(
            question,
            evidence_text,
        )

        return {
            "question": question,
            "answer": answer,
            "results": contexts,
            "law_result": law_result,
            "route": "document+law",
            "route_reason": route_reason,
            "tools": ["document", "law"],
            "used_tools": ["document", "law"],
            "product_results": [],
            "product_db_available": self.product_db.available,
            "model": self.document_chatbot.llm.model,
        }

    def _answer_with_law(
        self,
        question: str,
        route_reason: str,
        tool_cache: Any | None = None,
    ) -> dict:
        law_result = self._search_law_result(question, tool_cache=tool_cache)
        evidence_text = self._law_result_to_text(law_result)

        if not law_result.get("success") or not evidence_text:
            return {
                "question": question,
                "answer": law_result.get(
                    "message", "제공된 법령 근거를 찾지 못했습니다."
                ),
                "results": [],
                "law_result": law_result,
                "route": "law",
                "route_reason": route_reason,
                "tools": ["law"],
                "used_tools": ["law"],
                "product_results": [],
                "product_db_available": self.product_db.available,
                "model": self.document_chatbot.llm.model,
            }

        answer = self.answer_provider.answer_from_evidence(
            question,
            evidence_text,
        )

        return {
            "question": question,
            "answer": answer,
            "results": [],
            "law_result": law_result,
            "route": "law",
            "route_reason": route_reason,
            "tools": ["law"],
            "used_tools": ["law"],
            "product_results": [],
            "product_db_available": self.product_db.available,
            "model": self.document_chatbot.llm.model,
        }

    @staticmethod
    def _law_result_to_text(law_result: dict[str, Any]) -> str:
        """LawTool의 주요 조문과 참조 조문을 LLM용 근거로 변환합니다."""
        blocks: list[str] = []

        for index, source in enumerate(
            law_result.get("primary_sources") or [], start=1
        ):
            lines = [
                f"[Law API 주요 근거 {index}]",
                f"법령명: {source.get('law_name') or '확인되지 않음'}",
                f"조문: 제{source.get('article_no') or '?'}조"
                + (
                    f" ({source.get('article_title')})"
                    if source.get("article_title")
                    else ""
                ),
            ]

            if source.get("effective_date"):
                lines.append(f"법령 시행일자: {source['effective_date']}")
            if source.get("article_effective_date"):
                lines.append(f"조문 시행일자: {source['article_effective_date']}")

            for paragraph in source.get("paragraphs") or []:
                paragraph_no = paragraph.get("paragraph_no") or ""
                paragraph_text = paragraph.get("text") or ""
                lines.append(f"{paragraph_no} {paragraph_text}".strip())

                for item in paragraph.get("items") or []:
                    item_no = item.get("item_no") or ""
                    item_text = item.get("text") or ""
                    lines.append(f"{item_no} {item_text}".strip())

            blocks.append("\n".join(line for line in lines if line))

        for index, reference in enumerate(
            law_result.get("references") or [], start=1
        ):
            article_label = f"제{reference.get('article_no') or '?'}조"
            if reference.get("article_title"):
                article_label += f" ({reference['article_title']})"

            lines = [
                f"[Law API 참조 조문 {index}]",
                f"참조 표현: {reference.get('reference') or '확인되지 않음'}",
                f"법령명: {reference.get('law_name') or '확인되지 않음'}",
                f"조문: {article_label}",
            ]

            location = []
            if reference.get("paragraph_no"):
                location.append(f"제{reference['paragraph_no']}항")
            if reference.get("item_no"):
                location.append(f"제{reference['item_no']}호")
            if location:
                lines.append(f"세부 위치: {''.join(location)}")
            if reference.get("article_effective_date"):
                lines.append(
                    f"조문 시행일자: {reference['article_effective_date']}"
                )
            if reference.get("origin_text"):
                lines.append(f"원문 문맥: {reference['origin_text']}")
            lines.append(f"참조 내용: {reference.get('text') or '내용 없음'}")

            blocks.append("\n".join(lines))

        return "\n\n".join(blocks)

    @staticmethod
    def _document_contexts_to_text(contexts: list[dict[str, Any]]) -> str:
        """Retriever 문서 청크를 공통 evidence 형식으로 변환합니다."""
        blocks = []

        for index, context in enumerate(contexts, start=1):
            blocks.append(
                f"[Document RAG 근거 {index}]\n"
                f"filename: {context.get('filename', '확인되지 않음')}\n"
                f"location_type: {context.get('location_type', '확인되지 않음')}\n"
                f"location: {context.get('location', '확인되지 않음')}\n"
                f"text:\n{context.get('text', '')}"
            )

        return "\n\n".join(blocks)
