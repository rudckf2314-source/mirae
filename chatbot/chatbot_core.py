from __future__ import annotations

import os

from dotenv import load_dotenv


from .paths import REPO_ROOT
from .retriever import ChunkRetriever
from .hyperclova_client import HyperClovaLLM
from .model_policy import llm_for_role

CHUNKS_PATH = REPO_ROOT / "data" / "chatbot" / "chunks" / "chunks.jsonl"

load_dotenv(REPO_ROOT / ".env")


class PensionChatbot:
    def __init__(self) -> None:
        self.retriever = ChunkRetriever(CHUNKS_PATH)
        self.llm = llm_for_role("answer")

    def answer(self, question: str, top_k: int = 5) -> dict:
        question = question.strip()
        if not question:
            raise ValueError("질문이 비어 있습니다.")

        contexts = self.retriever.retrieve(question, top_k=top_k, source_group="docs")

        if not contexts:
            return {
                "question": question,
                "answer": "제공된 자료에서 관련 근거를 찾지 못했습니다.",
                "results": [],
                "model": self.llm.model,
            }

        answer = self.llm.generate(question, contexts)

        return {
            "question": question,
            "answer": answer,
            "results": contexts,
            "model": self.llm.model,
        }
