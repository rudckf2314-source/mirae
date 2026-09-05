from __future__ import annotations

import os
import re
from typing import Any

import requests
from dotenv import load_dotenv
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .paths import REPO_ROOT

load_dotenv(REPO_ROOT / ".env")


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _article_label(number: Any, branch: Any = None) -> str:
    no = str(number or "").strip()
    br = str(branch or "").strip()
    if no.startswith("제"):
        return no
    if br and br not in {"0", "00"}:
        return f"제{no}조의{br}"
    return f"제{no}조"


class LawAPIClient:
    """National Law Information Center API client used by sync jobs.

    Chat serving is DB-first.  This client is intentionally kept as the ingestion
    adapter so an API outage does not directly break end-user questions.
    """

    SEARCH_URL = "https://www.law.go.kr/DRF/lawSearch.do"
    SERVICE_URL = "https://www.law.go.kr/DRF/lawService.do"

    def __init__(self, oc: str | None = None):
        self.oc = oc or os.getenv("LAW_API_OC")
        self.session = requests.Session()
        self.session.trust_env = os.getenv("PENSION_DISABLE_ENV_PROXY") != "1"
        retry = Retry(total=3, connect=3, read=3, status=3, backoff_factor=0.4,
                      status_forcelist=(429, 500, 502, 503, 504), allowed_methods=frozenset({"GET"}))
        self.session.mount("https://", HTTPAdapter(max_retries=retry))
        if not self.oc:
            raise RuntimeError(".env에 LAW_API_OC가 없습니다.")

    def _get_json(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        response = self.session.get(url, params=params, timeout=float(os.getenv("LAW_API_TIMEOUT", "20")))
        response.raise_for_status()
        return response.json()

    def search_law(self, query: str) -> list[dict[str, Any]]:
        params = {
            "OC": self.oc,
            "target": os.getenv("LAW_API_SEARCH_TARGET", "law"),
            "type": "JSON",
            "search": 1,
            "query": query,
            "display": 50,
        }
        data = self._get_json(self.SEARCH_URL, params)
        root = data.get("LawSearch") or data.get("lawSearch") or data
        laws = root.get("law", []) if isinstance(root, dict) else []
        return _as_list(laws)

    def find_exact_law(self, law_name: str) -> dict[str, Any] | None:
        target = re.sub(r"\s+", "", law_name)
        for law in self.search_law(law_name):
            name = str(law.get("법령명한글") or law.get("법령명_한글") or "")
            if re.sub(r"\s+", "", name) == target:
                return law
        return None

    def get_law_body(self, law_id: str) -> dict[str, Any]:
        params = {
            "OC": self.oc,
            "target": os.getenv("LAW_API_SERVICE_TARGET", "law"),
            "type": "JSON",
            "ID": law_id,
        }
        return self._get_json(self.SERVICE_URL, params)

    def get_normalized_law(self, law_name: str) -> dict[str, Any] | None:
        law_info = self.find_exact_law(law_name)
        if not law_info:
            return None
        law_id = str(law_info.get("법령ID") or "")
        if not law_id:
            return None
        data = self.get_law_body(law_id)
        law = data.get("법령", data)
        basic = law.get("기본정보", {}) if isinstance(law, dict) else {}
        raw_articles = ((law.get("조문") or {}).get("조문단위", [])) if isinstance(law, dict) else []
        articles = []
        for article in _as_list(raw_articles):
            if not isinstance(article, dict) or article.get("조문여부") not in (None, "조문"):
                continue
            article_no = _article_label(article.get("조문번호"), article.get("조문가지번호"))
            text_parts: list[str] = []
            content = str(article.get("조문내용") or "").strip()
            if content:
                text_parts.append(content)
            for paragraph in _as_list(article.get("항")):
                if not isinstance(paragraph, dict):
                    continue
                ptext = str(paragraph.get("항내용") or "").strip()
                if ptext:
                    text_parts.append(ptext)
                for item in _as_list(paragraph.get("호")):
                    if isinstance(item, dict):
                        itext = str(item.get("호내용") or "").strip()
                        if itext:
                            text_parts.append(itext)
            articles.append({
                "article_no": article_no,
                "article_title": article.get("조문제목"),
                "article_text": "\n".join(dict.fromkeys(text_parts)).strip(),
                "article_effective_date": article.get("조문시행일자"),
            })
        ministry = basic.get("소관부처")
        if isinstance(ministry, dict):
            ministry = ministry.get("content")
        return {
            "law_id": basic.get("법령ID") or law_id,
            "law_serial": basic.get("법령일련번호") or law_info.get("법령일련번호"),
            "law_name": basic.get("법령명_한글") or basic.get("법령명한글") or law_name,
            "law_short_name": basic.get("법령명약칭"),
            "ministry": ministry,
            "promulgation_date": basic.get("공포일자") or law_info.get("공포일자"),
            "effective_date": basic.get("시행일자") or law_info.get("시행일자"),
            "articles": articles,
        }

    def get_article(self, law_name: str, article_number: str) -> dict[str, Any] | None:
        normalized = self.get_normalized_law(law_name)
        if not normalized:
            return None
        target = str(article_number).replace("제", "").replace("조", "")
        for article in normalized["articles"]:
            compact = str(article["article_no"]).replace("제", "").replace("조", "")
            if compact == target:
                return {
                    "source_type": "law_api",
                    "law_id": normalized.get("law_id"),
                    "law_name": normalized.get("law_name"),
                    "law_short_name": normalized.get("law_short_name"),
                    "ministry": normalized.get("ministry"),
                    "promulgation_date": normalized.get("promulgation_date"),
                    "effective_date": normalized.get("effective_date"),
                    "article_no": article.get("article_no"),
                    "article_title": article.get("article_title"),
                    "article_effective_date": article.get("article_effective_date"),
                    "paragraphs": [{"paragraph_no": None, "text": article.get("article_text"), "items": []}],
                    "origin_text": article.get("article_text"),
                }
        return None
