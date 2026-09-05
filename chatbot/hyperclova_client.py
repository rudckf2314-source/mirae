from __future__ import annotations

import json
import os
import re
import uuid
from typing import Any

import httpx


class HyperClovaLLM:
    """Direct CLOVA Studio v3 client for chat and document extraction."""

    def __init__(self, *, api_key=None, model=None, base_url=None, timeout=None) -> None:
        api_key = api_key if api_key is not None else os.getenv("CLOVA_STUDIO_API_KEY")
        if not api_key:
            raise RuntimeError("CLOVA_STUDIO_API_KEY가 .env에 없습니다.")
        self.api_key = api_key
        self.model = model or os.getenv("CLOVA_MODEL", "HCX-005")
        self.base_url = (base_url or os.getenv("CLOVA_BASE_URL", "https://clovastudio.stream.ntruss.com")).rstrip("/")
        self.timeout = float(timeout if timeout is not None else os.getenv("LLM_TIMEOUT", "120"))

    def _chat(self, system: str, user: str, max_tokens: int = 1200) -> str:
        return self.chat_messages([{"role": "system", "content": system},
                                   {"role": "user", "content": user}], max_tokens=max_tokens)

    def chat_messages(self, messages: list[dict], *, max_tokens=1200, temperature=0.0, stop=None) -> str:
        url = f"{self.base_url}/v3/chat-completions/{self.model}"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "X-NCP-CLOVASTUDIO-REQUEST-ID": str(uuid.uuid4()),
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        payload = {
            "messages": messages,
            "topP": 0.8,
            "topK": 0,
            "maxTokens": min(max_tokens, 4096) if self.model in {"HCX-005", "HCX-DASH-002"} else max_tokens,
            "temperature": temperature,
            "repetitionPenalty": 1.05,
            "stop": stop or [],
        }
        if self.model == "HCX-007":
            # Specifications are bounded JSON tasks; disable hidden reasoning
            # to keep latency predictable and reserve the budget for output.
            payload.pop("maxTokens")
            payload.pop("stop")
            payload["maxCompletionTokens"] = min(max_tokens, 32768)
            payload["thinking"] = {"effort": "none"}
        with httpx.Client(timeout=self.timeout, trust_env=os.getenv("PENSION_DISABLE_ENV_PROXY") != "1") as client:
            response = client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()
        # CLOVA Studio has changed response envelopes across API generations;
        # accept the documented/common forms without hiding an invalid payload.
        candidates: list[Any] = [
            data.get("result", {}).get("message", {}).get("content") if isinstance(data.get("result"), dict) else None,
            data.get("message", {}).get("content") if isinstance(data.get("message"), dict) else None,
            data.get("content"),
        ]
        for value in candidates:
            if isinstance(value, str) and value.strip():
                return value.strip()
            if isinstance(value, list):
                texts = [str(item.get("text")) for item in value if isinstance(item, dict) and item.get("text")]
                if texts:
                    return "\n".join(texts).strip()
        raise RuntimeError(f"HyperCLOVA 응답에서 content를 찾지 못했습니다: keys={sorted(data.keys())}")

    @staticmethod
    def _evidence_prompt() -> str:
        return """
당신은 미래에셋 연금 Agent의 답변 생성기입니다.
기업이 제공한 투자설명서·기초자료와 그 자료에서 정규화한 DB를 1차 근거로 사용합니다.
제공된 근거에 없는 상품 수치, 가입조건, 법적 결론을 만들지 마세요.
외부 공식 법령 근거가 함께 제공된 경우 최신 법적 사실 확인용으로만 사용하고,
기업 자료와 표현이 다르면 어느 쪽도 숨기지 말고 차이를 분리해서 설명하세요.
구조화 상품 DB 값은 상품 사실 조회에 사용하고 PDF 근거는 원문 추적에 사용하세요.
내부 검증 코드명이나 FAIL 사유를 사용자에게 그대로 노출하지 마세요.
한국어로 자연스럽고 짧게 답하고, 실제 사용한 출처만 마지막에 적으세요.
""".strip()


    def structured(self, system: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Return a JSON object through the same HyperCLOVA X connection."""
        raw = self._chat(
            system + "\n반드시 JSON object 하나만 출력하고 코드블록은 사용하지 마세요.",
            json.dumps(payload, ensure_ascii=False),
            max_tokens=1600,
        )
        text = raw.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I | re.S).strip()
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            start, end = text.find("{"), text.rfind("}")
            if start < 0 or end <= start:
                raise RuntimeError("HyperCLOVA 구조화 응답에서 JSON object를 찾지 못했습니다.")
            value = json.loads(text[start:end + 1])
        if not isinstance(value, dict):
            raise RuntimeError("HyperCLOVA 구조화 응답이 JSON object가 아닙니다.")
        return value

    def generate(self, question: str, contexts: list[dict[str, Any]]) -> str:
        blocks = []
        for i, item in enumerate(contexts, 1):
            blocks.append(
                f"[ENTERPRISE_DOCUMENT {i}]\n출처: {item.get('filename')} / {item.get('location_type')} {item.get('location')}\n{item.get('text','')}"
            )
        return self._chat(self._evidence_prompt(), f"질문:\n{question}\n\n기업 제공 근거:\n" + "\n\n".join(blocks))

    def generate_from_evidence(self, question: str, evidence_text: str) -> str:
        if not evidence_text.strip():
            raise ValueError("답변 생성에 사용할 근거가 비어 있습니다.")
        return self._chat(self._evidence_prompt(), f"질문:\n{question}\n\n검증된 근거:\n{evidence_text}")
