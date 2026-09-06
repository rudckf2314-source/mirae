from __future__ import annotations

import os
import uuid

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .agent_core import PensionAgentCore
from .paths import REPO_ROOT
from .pension_langgraph_agent import PensionLangGraphAgent
from .public_language import public_text
from .public_security import public_payload
from .submission_contract import to_submission

STATIC_DIR = REPO_ROOT / "static"

app = FastAPI(title="Mirae Asset Pension Agent - DB Ready")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
agent = PensionAgentCore()
mode = os.getenv("PENSION_AGENT_MODE", "langgraph").strip().lower()
if mode not in {"legacy", "langgraph"}:
    raise RuntimeError("PENSION_AGENT_MODE must be 'legacy' or 'langgraph'")
langgraph_agent = PensionLangGraphAgent(agent) if mode == "langgraph" else None


class ChatRequest(BaseModel):
    question: str
    top_k: int = 5
    session_context: dict | None = None
    question_id: str | None = None


def _respond(question: str, top_k: int = 5, session_context: dict | None = None, question_id: str | None = None) -> dict:
    question_id = question_id or str(uuid.uuid4())
    question = question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="질문을 입력해 주세요.")
    try:
        if langgraph_agent is not None:
            return public_payload(langgraph_agent.respond(question, top_k=top_k, session_context=session_context, question_id=question_id))
        response = agent.answer(question=question, top_k=max(1, min(top_k, 10)))
        response["answer"] = public_text(response.get("answer"))
        response["question_id"] = question_id
        return public_payload(response)
    except HTTPException:
        raise
    except Exception as exc:
        # Keep provider configuration, local paths, and internal exceptions in
        # server-side diagnostics rather than the public HTTP response.
        raise HTTPException(status_code=500, detail={"message": "요청을 안전하게 처리하지 못했습니다.", "question_id": question_id, "error_code": type(exc).__name__}) from None


@app.get("/")
def home():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
def health():
    return {
        "status": "ok",
        "chunks": len(agent.document_chatbot.retriever.chunks),
        "model": agent.document_chatbot.llm.model,
        "product_db_available": agent.product_db.available,
        "product_db_backend": getattr(agent.product_db, "backend", "json"),
        "product_db_records": getattr(agent.product_db, "record_count", 0),
        "architecture": "document_tool + product_db_adapter + router",
        "mode": mode,
    }


@app.get("/ready")
def ready():
    return {"status": "ready" if agent.product_db.available and bool(agent.document_chatbot.retriever.chunks) else "not_ready", "product_db_available": agent.product_db.available, "pdf_chunk_count": len(agent.document_chatbot.retriever.chunks), "law_tool_initialized": agent.law_tool is not None, "mode": mode}


@app.post("/api/search")
def search(request: ChatRequest):
    return _respond(request.question, request.top_k, request.session_context, request.question_id)


@app.get("/answer")
def answer(question: str, question_id: str | None = None, top_k: int = 5):
    """Official query-string contract; POST /api/search remains compatible."""
    return to_submission(question, _respond(question, top_k=top_k, question_id=question_id))
