from __future__ import annotations

import io
import json
import os
import sys
import zipfile
import time
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
for path in (ROOT, SRC):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from config.settings import get_settings  # noqa: E402
from processing.progress import ProgressEvent  # noqa: E402
from schemas.product import CanonicalProduct  # noqa: E402
from services.extraction_service import ExtractionService, ProcessResult  # noqa: E402

PROCESS_STEPS = [
    ("upload", "STEP 1  PDF 업로드"),
    ("hash_check", "STEP 2  SHA-256 중복 검사"),
    ("guardrail", "STEP 2.5  투자설명서 가드레일"),
    ("parsing", "STEP 3  PDF Parsing"),
    ("section_detection", "STEP 4  Section Detection"),
    ("chunking", "STEP 5  Chunk 생성"),
    ("extracting", "STEP 6  HyperCLOVA X LLM Extraction"),
    ("merging", "STEP 7  JSON Merge"),
    ("validating", "STEP 8  Validation"),
    ("verifying", "STEP 9  Verification"),
    ("saving", "STEP 10  Cache Save"),
    ("standardizing", "STEP 11  Schema JSON"),
    ("database", "STEP 12  PostgreSQL Save"),
    ("complete", "STEP 13  Complete"),
]


def get_service() -> ExtractionService:
    if "extraction_service" not in st.session_state:
        st.session_state.extraction_service = ExtractionService(settings=get_settings())
    return st.session_state.extraction_service


def init_state() -> None:
    st.session_state.setdefault("selected_document_id", None)
    st.session_state.setdefault("last_result", None)
    st.session_state.setdefault("last_results", [])
    st.session_state.setdefault("last_events", [])
    st.session_state.setdefault("ui_mode", "cached")
    st.session_state.setdefault("chat_messages", [])
    st.session_state.setdefault("chat_session_context", None)
    st.session_state.setdefault("chat_history", [])


def get_chat_agent():
    if "chat_agent" not in st.session_state:
        os.environ.setdefault("PENSION_AGENT_MODE", "langgraph")
        from chatbot.agent_core import PensionAgentCore
        from chatbot.pension_langgraph_agent import PensionLangGraphAgent

        core = PensionAgentCore()
        st.session_state.chat_core = core
        st.session_state.chat_agent = PensionLangGraphAgent(core)
    return st.session_state.chat_agent


def _source_preview(payload: dict) -> str:
    metadata = payload.get("metadata") or {}
    route = payload.get("route") or metadata.get("route") or "-"
    backend = payload.get("product_db_backend") or getattr(
        getattr(st.session_state.get("chat_core"), "product_db", None),
        "backend",
        "-",
    )
    sources = payload.get("sources") or []
    bits = [f"경로: {route}"]
    if "product" in str(route):
        bits.append(f"상품 스키마: {backend}")
    if sources:
        domains = {}
        for item in sources:
            domain = item.get("domain") or "?"
            domains[domain] = domains.get(domain, 0) + 1
        bits.append("근거: " + ", ".join(f"{k} {v}건" for k, v in sorted(domains.items())))
    if metadata.get("evidence_policy") == "NOT_REQUIRED":
        bits.append("대화 예시(근거 조회 불필요)")
    return " · ".join(bits)


def render_chat() -> None:
    st.title("연금 상담")
    st.caption("기업 제공 투자설명서·연금 안내자료를 최우선 근거로 답하고, 필요한 경우에만 공식 외부 근거를 보완합니다.")


    with st.sidebar:
        st.markdown("### AI 미래에셋")
        if st.button("＋ 새 채팅", use_container_width=True):
            if st.session_state.chat_messages:
                st.session_state.chat_history.append({
                    "saved_at": time.strftime("%Y-%m-%d %H:%M"),
                    "messages": list(st.session_state.chat_messages),
                })
            st.session_state.chat_messages = []
            st.session_state.chat_session_context = None
            st.rerun()
        st.markdown("#### 메뉴")
        st.caption("채팅 내역")
        for idx, item in enumerate(reversed(st.session_state.chat_history[-8:]), start=1):
            first = next((m.get("content", "") for m in item.get("messages", []) if m.get("role") == "user"), "새 대화")
            st.write(f"{idx}. {first[:28]}")

    if not st.session_state.chat_messages:
        with st.chat_message("assistant"):
            st.write("안녕하세요! 연금에 대해 어떤 점이 궁금하세요? 상품을 찾거나 투자 계획을 세우는 것도 함께 도와드릴게요.")

    for item in st.session_state.chat_messages:
        with st.chat_message(item["role"]):
            st.write(item["content"])
            _render_chat_sources(item.get("sources") or [])

    question = st.chat_input("메시지를 입력해 주세요")
    if not question:
        return

    st.session_state.chat_messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.write(question)
    with st.chat_message("assistant"):
        with st.spinner("답변을 준비하고 있어요..."):
            try:
                agent = get_chat_agent()
                payload = agent.respond(question, session_context=st.session_state.get("chat_session_context"))
            except Exception as exc:
                payload = {"answer": "답변 처리 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.", "metadata": {"ui_error_type": type(exc).__name__}}

        answer = payload.get("answer") or "답변을 만들지 못했습니다."
        meta_payload = payload.get("metadata") or {}
        previous = dict(st.session_state.get("chat_session_context") or {})
        updates = dict(meta_payload.get("context_updates") or {})
        confirmed = dict(previous.get("confirmed_constraints") or {})
        confirmed.update(updates.get("confirmed_constraints") or {})
        route = meta_payload.get("route") or previous.get("active_intent")
        topic = updates.get("last_topic") or previous.get("last_topic")
        upper_q = question.upper()
        for candidate in ("IRP", "DC", "DB", "연금저축"):
            if candidate.upper() in upper_q:
                topic = candidate
                break

        st.session_state.chat_session_context = {
            "session_id": previous.get("session_id") or "streamlit",
            "pending_question_id": payload.get("question_id") or previous.get("pending_question_id"),
            "confirmed_constraints": confirmed,
            "missing_fields": updates.get("missing_fields", meta_payload.get("missing_fields", previous.get("missing_fields", []))),
            "pending_question": updates.get("pending_question", previous.get("pending_question")),
            "active_intent": route,
            "last_topic": topic,
            "last_assistant_action": updates.get("last_assistant_action") or ("CLARIFY" if payload.get("status") == "clarify" else "ANSWER"),
            "last_candidates": updates.get("last_candidates", previous.get("last_candidates", [])),
            "selected_product": updates.get("selected_product", previous.get("selected_product")),
            "pending_task": updates.get("pending_task", previous.get("pending_task")),
            "expires_at": time.time() + 1800,
        }
        st.write(answer)
        _render_chat_sources(payload.get("sources") or [])
    st.session_state.chat_messages.append({"role": "assistant", "content": answer, "sources": payload.get("sources") or []})


def _render_chat_sources(sources: list) -> None:
    if sources:
        with st.expander("참고한 자료", expanded=False):
            for src in sources:
                from chatbot.public_language import source_label
                st.write(f"- {src.get('label') or source_label(src)}" + (f" / {src.get('source_page')}쪽" if src.get('source_page') else ""))


def _load_inventory() -> dict:
    settings = get_settings()
    snapshot = {
        "db_ok": False,
        "counts": {},
        "documents": [],
        "error": None,
    }
    if settings.database_url:
        try:
            from database import PostgresStandardStore

            store = PostgresStandardStore(settings.database_url)
            snapshot["counts"] = store.table_counts()
            snapshot["documents"] = store.list_standard_documents()
            snapshot["db_ok"] = True
        except Exception as exc:
            snapshot["error"] = str(exc)
    if not snapshot["documents"]:
        standard_dir = ROOT / "data" / "standard_json"
        files = sorted(standard_dir.glob("*.schema_v0.1.json")) if standard_dir.is_dir() else []
        docs = []
        for path in files:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            docs.append(
                {
                    "document_id": path.stem.replace(".schema_v0.1", ""),
                    "filename": (payload.get("source_document") or {}).get("filename") or path.name,
                    "standard_json": payload,
                    "updated_at": None,
                }
            )
        snapshot["documents"] = docs
    summary_path = ROOT / "data" / "chatbot" / "chunks" / "chunks_summary.json"
    if summary_path.exists():
        snapshot["chunks"] = json.loads(summary_path.read_text(encoding="utf-8"))
    else:
        snapshot["chunks"] = {}
    return snapshot


def render_inventory() -> None:
    st.title("저장된 데이터")
    st.caption("사용자가 챗봇이 참조하는 적재 현황을 볼 수 있습니다. 투자설명서 사실은 Postgres Standard JSON, 제도 안내는 청크 파일입니다.")
    snapshot = _load_inventory()
    counts = snapshot.get("counts") or {}
    chunks = snapshot.get("chunks") or {}
    docs = snapshot.get("documents") or []

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("투자설명서", counts.get("source_documents", len(docs)))
    m2.metric("상품 클래스", counts.get("product_classes", 0))
    m3.metric("위험등급", counts.get("risk_ratings", 0))
    m4.metric("보수 레코드", counts.get("fees", 0))
    m5, m6, m7, m8 = st.columns(4)
    m5.metric("서술(위험/목적/전략)", counts.get("narratives", 0))
    m6.metric("근거 조각", counts.get("evidence", 0))
    m7.metric("안내 청크", (chunks.get("by_source_group") or {}).get("docs", 0))
    m8.metric("설명서 원문 청크", (chunks.get("by_source_group") or {}).get("investment", 0))
    if snapshot.get("error"):
        st.warning(f"Postgres 연결 실패, 로컬 Schema JSON으로 표시합니다: {snapshot['error']}")
    if snapshot.get("db_ok"):
        st.success("Postgres `source_documents.standard_json`에서 읽었습니다.")

    st.subheader("투자설명서 스키마 목록")
    rows = []
    for item in docs:
        payload = item.get("standard_json") or {}
        product = payload.get("product") or {}
        ratings = payload.get("risk_ratings") or []
        grade = ratings[0].get("grade") if ratings else None
        rows.append(
            {
                "document_id": item.get("document_id"),
                "파일명": item.get("filename"),
                "상품명": product.get("official_name"),
                "운용사": product.get("manager_name"),
                "위험등급": grade,
                "클래스": len(payload.get("classes") or []),
                "보수": len(payload.get("fees") or []),
                "위험서술": len(
                    [n for n in (payload.get("narratives") or []) if n.get("narrative_type") == "INVESTMENT_RISK"]
                ),
            }
        )
    if rows:
        st.dataframe(rows, use_container_width=True, hide_index=True)
    else:
        st.info("저장된 투자설명서 스키마가 없습니다. ‘투자설명서 적재’에서 PDF를 올리세요.")

    if not docs:
        return
    labels = {f"{item.get('filename')}  ({item.get('document_id')})": item for item in docs}
    chosen = st.selectbox("스키마 JSON 보기", ["선택하세요"] + list(labels.keys()))
    if chosen == "선택하세요":
        return
    item = labels[chosen]
    payload = item.get("standard_json") or {}
    st.json(payload)
    st.download_button(
        "Schema JSON 다운로드",
        data=json.dumps(payload, ensure_ascii=False, indent=2),
        file_name=f"{item.get('document_id')}.schema_v0.1.json",
        mime="application/json",
    )


def collect_upload_items(uploaded_files) -> list[tuple[bytes, str]]:
    items: list[tuple[bytes, str]] = []
    for uploaded in uploaded_files or []:
        data = uploaded.getvalue()
        name = uploaded.name
        if name.lower().endswith(".zip"):
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                for info in archive.infolist():
                    if info.is_dir() or not info.filename.lower().endswith(".pdf"):
                        continue
                    items.append((archive.read(info), Path(info.filename).name))
        elif name.lower().endswith(".pdf"):
            items.append((data, name))
    return items


def render_cached_table(service: ExtractionService) -> str | None:
    records = service.list_cached()
    st.subheader("Cached Documents")
    st.metric("Cached documents", len(records))
    if not records:
        st.info("아직 저장된 투자설명서가 없습니다. 새 PDF를 업로드하거나 preload_cache.py를 실행하세요.")
        return None

    rows = []
    for item in records:
        rows.append(
            {
                "파일명": item.get("file_name"),
                "처리상태": item.get("status"),
                "상품명": item.get("product_name"),
                "위험등급": item.get("risk_grade"),
                "처리일": (item.get("processed_at") or "")[:19].replace("T", " "),
                "document_id": item.get("document_id"),
            }
        )
    st.dataframe(rows, use_container_width=True, hide_index=True)

    options = {f"{item['file_name']}  ({item['document_id']})": item["document_id"] for item in records}
    labels = ["선택하세요"] + list(options.keys())
    chosen = st.selectbox("문서 선택", labels, key="cached_doc_choice")
    if chosen == "선택하세요":
        return None
    st.session_state.selected_document_id = options[chosen]
    st.session_state.ui_mode = "cached"
    return options[chosen]


def evidence_map(product: CanonicalProduct) -> dict[str, dict]:
    return {item.chunk_id: item.model_dump() for item in product.evidence}


def render_evidence_block(title: str, value: str, refs: list[str], lookup: dict[str, dict]) -> None:
    st.markdown(f"**{title}**")
    st.write(value if value else "-")
    if not refs:
        st.caption("Evidence 없음")
        return
    for ref in refs:
        item = lookup.get(ref)
        with st.expander(f"Evidence: {ref}", expanded=False):
            if not item:
                st.warning("연결된 chunk를 찾을 수 없습니다.")
                continue
            st.write(f"File: {item['file_name']}")
            st.write(f"Page: {item['page_start']}" + ("" if item["page_start"] == item["page_end"] else f"-{item['page_end']}"))
            st.write(f"Chunk: {item['chunk_id']}")
            st.text(item["source_text"])


def render_product_info(product: CanonicalProduct) -> None:
    info = product.product
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("위험등급", info.risk.grade if info.risk.grade is not None else "-")
    col2.metric("Class 수", len(product.classes))
    col3.metric("Fee 수", len(product.fees))
    col4.metric("Evidence 수", len(product.evidence))
    st.write(f"**상품명:** {info.name or '-'}")
    st.write(f"**운용사:** {info.manager or '-'}")
    st.write(f"**상품분류:** {info.asset_type or '-'}")
    if info.classification:
        st.write("**분류 태그:** " + ", ".join(info.classification))
    if info.fund_code:
        st.write(f"**펀드코드:** {info.fund_code}")
    st.write(f"**작성기준일:** {product.document.as_of_date or '-'}")
    st.write(f"**효력발생일:** {product.document.effective_date or '-'}")
    st.write("**투자목적**")
    st.write(info.investment_objective.text or "-")
    st.write("**투자전략**")
    st.write(info.investment_strategy.text or "-")


def render_evidence_tab(product: CanonicalProduct) -> None:
    lookup = evidence_map(product)
    info = product.product
    render_evidence_block(
        "위험등급",
        f"{info.risk.grade}등급 / {info.risk.label}" if info.risk.grade is not None else "-",
        info.risk.evidence_refs,
        lookup,
    )
    render_evidence_block("투자목적", info.investment_objective.text or "-", info.investment_objective.evidence_refs, lookup)
    render_evidence_block("투자전략", info.investment_strategy.text or "-", info.investment_strategy.evidence_refs, lookup)
    for item in info.investment_risks:
        render_evidence_block(item.name or "투자위험", item.description or "-", item.evidence_refs, lookup)
    for item in product.classes:
        render_evidence_block(
            f"Class {item.class_name or '-'}",
            item.description or item.inception_date or "-",
            item.evidence_refs,
            lookup,
        )
    for item in product.fees:
        value = (
            f"{item.fee_type}: {item.rate}{item.unit or ''}"
            if item.rate is not None
            else f"{item.fee_type}: -"
        )
        if item.condition:
            value += f" ({item.condition})"
        elif item.note:
            value += f" ({item.note})"
        render_evidence_block(f"Fee {item.class_name or '-'}", value, item.evidence_refs, lookup)
    for item in product.performance:
        label = item.subject or item.class_name or "-"
        render_evidence_block(
            f"Performance {label} {item.period or ''} ({item.metric_type or '-'})",
            f"{item.return_rate}{item.unit or ''}",
            item.evidence_refs,
            lookup,
        )


def render_document_tabs(product: CanonicalProduct, parsed, result: ProcessResult | None = None) -> None:
    tabs = st.tabs(
        ["상품정보", "Canonical JSON", "Schema JSON", "DB 저장", "Evidence", "Validation", "Verification", "Detected Tables", "Raw Parsed Text"]
    )
    with tabs[0]:
        render_product_info(product)
    with tabs[1]:
        payload = product.model_dump()
        st.json(payload)
        st.download_button(
            "Canonical JSON 다운로드",
            data=json.dumps(payload, ensure_ascii=False, indent=2),
            file_name=f"{product.document.document_id}.json",
            mime="application/json",
        )
    with tabs[2]:
        if result and result.standardized is not None:
            payload = result.standardized.model_dump(mode="json")
            st.json(payload)
            st.download_button(
                "Schema JSON 다운로드",
                data=json.dumps(payload, ensure_ascii=False, indent=2),
                file_name=f"{product.document.document_id}.schema_v0.1.json",
                mime="application/json",
            )
            if result.standard_json_path:
                st.caption(f"저장 경로: {result.standard_json_path}")
        else:
            st.info("이 화면에서 아직 Schema JSON 변환 결과가 없습니다.")
    with tabs[3]:
        if result and result.db_saved:
            st.success("PostgreSQL 저장 완료")
        elif result and result.db_error:
            st.warning(result.db_error)
        else:
            st.info("DATABASE_URL을 설정하면 PDF 처리 후 PostgreSQL에 자동 저장됩니다.")

    with tabs[4]:
        render_evidence_tab(product)
    with tabs[5]:
        report = product.extraction.validation
        cols = st.columns(5)
        cols[0].metric("Schema", report.schema_status)
        cols[1].metric("Evidence", report.evidence_status)
        cols[2].metric("Completeness", report.completeness_status)
        cols[3].metric("Consistency", report.consistency_status)
        cols[4].metric("Final Status", product.extraction.status.upper())
        st.write("**Missing Fields**")
        st.json(product.extraction.missing_fields)
        st.write("**Warnings**")
        if product.extraction.warnings:
            for warning in product.extraction.warnings:
                st.warning(warning)
        else:
            st.json([])
    with tabs[6]:
        report = product.extraction.verification
        cols = st.columns(5)
        cols[0].metric("Status", report.status)
        cols[1].metric("Checked", report.checked)
        cols[2].metric("PASS", report.pass_count)
        cols[3].metric("WARNING", report.warning_count)
        cols[4].metric("FAIL", report.fail_count)
        st.caption("Verification은 추출값을 수정하지 않습니다. extraction.status와 별도입니다.")
        rows = [
            {
                "field": item.field_path,
                "status": item.status,
                "verdict": item.verdict,
                "method": item.method,
                "value": item.extracted_value,
                "reason": item.reason,
            }
            for item in report.items
        ]
        if rows:
            st.dataframe(rows, use_container_width=True)
        else:
            st.info("Verification 결과가 없습니다.")
    with tabs[7]:
        tables = getattr(parsed, "tables", None) if parsed is not None else None
        if not tables:
            st.info("탐지된 표가 없습니다.")
        else:
            for table in tables:
                with st.expander(
                    f"p.{table.page_number} / {table.section_type} / {table.table_id}",
                    expanded=False,
                ):
                    st.write(f"method: {table.extraction_method}")
                    st.write("headers: " + ", ".join(table.headers))
                    display_rows = [
                        dict(zip(table.headers, row + [""] * max(0, len(table.headers) - len(row))))
                        if table.headers
                        else {"row": " | ".join(row)}
                        for row in table.rows
                    ]
                    if display_rows:
                        st.dataframe(display_rows, use_container_width=True)
                    else:
                        st.info("행이 없습니다.")
    with tabs[8]:
        if parsed is None:
            st.info("Parsed text 캐시가 없습니다.")
        else:
            for page in parsed.pages:
                with st.expander(f"p.{page.page_number}", expanded=page.page_number == 1):
                    st.text(page.text)


def render_process_result(result: ProcessResult) -> None:
    if result.error:
        st.error(result.error)
        return
    if result.product is None:
        st.error("처리 결과가 없습니다.")
        return
    if result.duplicate:
        st.success("이미 처리된 문서입니다. 기존 JSON을 불러왔습니다.")
    product = result.product
    summary = product.to_summary()
    st.subheader("처리 결과")
    cols = st.columns(4)
    cols[0].metric("상품명", summary["product_name"] or "-")
    cols[1].metric("운용사", summary["manager"] or "-")
    cols[2].metric("위험등급", summary["risk_grade"] if summary["risk_grade"] is not None else "-")
    cols[3].metric("상품분류", summary["asset_type"] or "-")
    cols2 = st.columns(4)
    cols2[0].metric("Class 수", summary["class_count"])
    cols2[1].metric("Fee 수", summary["fee_count"])
    cols2[2].metric("Performance 수", summary["performance_count"])
    cols2[3].metric("Evidence 수", summary["evidence_count"])
    render_document_tabs(product, result.parsed, result)


def run_pipeline(service: ExtractionService, items: list[tuple[bytes, str]]) -> None:
    events: list[ProgressEvent] = []
    results: list[ProcessResult] = []
    step_labels = {key: label for key, label in PROCESS_STEPS}

    with st.status("PDF 분석 진행 중", expanded=True) as status:
        def callback(event: ProgressEvent) -> None:
            events.append(event)
            label = step_labels.get(event.step, event.step)
            st.write(f"{label}  —  {event.message}")

        for pdf_bytes, file_name in items:
            st.write(f"### {file_name}")
            try:
                result = service.process_pdf(pdf_bytes, file_name=file_name, progress_callback=callback)
                results.append(result)
            except Exception as exc:
                results.append(ProcessResult(product=None, parsed=None, cached=False, error=f"{file_name}: {exc}"))
                st.error(f"{file_name}: {exc}")
        status.update(label="처리 완료", state="complete")

    st.session_state.last_events = events
    st.session_state.last_results = results
    st.session_state.last_result = results[-1] if results else None
    for result in reversed(results):
        if result.product is not None:
            st.session_state.selected_document_id = result.product.document.document_id
            break
    st.session_state.ui_mode = "result"


def render_ingest() -> None:
    service = get_service()
    st.title("투자설명서 적재")
    st.caption(
        "PDF는 투자설명서 구조일 때만 Canonical JSON → Schema v0.1 → PostgreSQL에 적재됩니다. "
        "안내문·계약서·일반 PDF는 가드레일에서 차단합니다."
    )

    left, right = st.columns([0.95, 1.15], gap="large")
    selected_id = None
    with left:
        selected_id = render_cached_table(service)

    with right:
        st.subheader("투자설명서 PDF 업로드")
        st.info("받는 형식: 집합투자증권 투자설명서 PDF(또는 그 PDF가 든 ZIP). 그 외 문서는 저장하지 않습니다.")
        uploaded = st.file_uploader(
            "PDF 또는 ZIP 업로드 (다중 가능)",
            type=["pdf", "zip"],
            accept_multiple_files=True,
        )
        start = st.button("스키마 적재 시작", type="primary", disabled=not uploaded)
        if start:
            items = collect_upload_items(uploaded)
            if not items:
                st.warning("처리할 PDF가 없습니다.")
            else:
                run_pipeline(service, items)

    if st.session_state.last_results and st.session_state.ui_mode == "result":
        st.divider()
        for result in st.session_state.last_results:
            render_process_result(result)
    elif selected_id:
        product = service.get_product(selected_id)
        parsed = service.get_parsed(selected_id)
        if product:
            st.divider()
            render_document_tabs(product, parsed, None)


def main() -> None:
    st.set_page_config(page_title="미래에셋 연금 에이전트", layout="wide")
    init_state()
    page = st.sidebar.radio("메뉴", ["챗봇", "저장된 데이터", "투자설명서 적재"], index=0)
    if page == "챗봇":
        render_chat()
    elif page == "저장된 데이터":
        render_inventory()
    else:
        render_ingest()


if __name__ == "__main__":
    main()
