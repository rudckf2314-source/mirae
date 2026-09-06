from __future__ import annotations

import os

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .agent_core import PensionAgentCore
from .paths import REPO_ROOT
from .pension_langgraph_agent import PensionLangGraphAgent
from .public_language import public_text

STATIC_DIR = REPO_ROOT / "static"

app = FastAPI(title="Mirae Asset Pension Agent - DB Ready")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
agent = PensionAgentCore()
mode = os.getenv("PENSION_AGENT_MODE", "legacy").strip().lower()
if mode not in {"legacy", "langgraph"}:
    raise RuntimeError("PENSION_AGENT_MODE must be 'legacy' or 'langgraph'")
langgraph_agent = PensionLangGraphAgent(agent) if mode == "langgraph" else None


class ChatRequest(BaseModel):
    question: str
    top_k: int = 5
    session_context: dict | None = None
    question_id: str | None = None


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
    question = request.question.strip()

    if not question:
        raise HTTPException(status_code=400, detail="질문을 입력해주세요.")

    try:
        if langgraph_agent is not None:
            return langgraph_agent.respond(question, top_k=request.top_k, session_context=request.session_context, question_id=request.question_id)
        response = agent.answer(question=question, top_k=max(1, min(request.top_k, 10)))
        response["answer"] = public_text(response.get("answer"))
        return response
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
