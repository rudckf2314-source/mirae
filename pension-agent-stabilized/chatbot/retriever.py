from __future__ import annotations

import argparse
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Optional

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import linear_kernel

QUERY_EXPANSIONS = {
    "DB": ["확정급여형", "Defined Benefit", "급여 산정", "사용자 운용"],
    "DC": ["확정기여형", "Defined Contribution", "부담금", "근로자 운용"],
    "IRP": ["개인형퇴직연금", "개인형 퇴직연금", "퇴직급여 이전"],
    "연금저축": ["연금저축계좌"],
    "세액공제": ["세액 공제", "공제 한도", "공제율"],
    "중도인출": ["중도 인출", "중도인출 사유", "인출 요건"],
    "연금수령": ["연금 수령", "수령 요건"],
}

DOCS_HINTS = {"DB", "DC", "IRP", "퇴직연금", "연금저축", "세액공제", "연금소득세", "중도인출", "퇴직급여", "연금수령", "이전"}
PRODUCT_HINTS = {"상품", "펀드", "ETF", "ETN", "리츠", "보수", "수수료", "수익률", "위험등급", "위험 등급", "클래스", "운용사", "AUM"}
QUESTION_LIST_MARKERS = ("질문 리스트", "질문리스트")
_TOKEN_RE = re.compile(r"[A-Za-z]+|[가-힣]{2,}|\d+(?:\.\d+)?")


def _tokens(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN_RE.findall(text or "")]


def expand_query(question: str) -> str:
    parts = [question]
    for key, expansions in QUERY_EXPANSIONS.items():
        if key.lower() in question.lower():
            parts.extend(expansions)
    return " ".join(parts)


def query_variants(question: str) -> list[str]:
    """Deterministic RQ-RAG-style refinement without another LLM call."""
    q = " ".join((question or "").split())
    variants = [q, expand_query(q)]
    clauses = [c.strip() for c in re.split(r"[?!.]|(?:\s+그리고\s+)|(?:\s+또\s+)|(?:\s+하면서\s+)|(?:\s+및\s+)", q) if len(c.strip()) >= 4]
    if len(clauses) > 1:
        variants.extend(clauses[:4])
    if "비교" in q or "차이" in q:
        accounts = [x for x in ("DB", "DC", "IRP", "연금저축") if x.lower() in q.lower()]
        variants.extend([f"{a} 정의 운용 주체 산정 방식 제한" for a in accounts])
    if any(k in q for k in ("세액공제", "세금", "절세")):
        variants.append(f"{q} 공제 대상 공제 한도 공제율 적용 조건")
    if "중도인출" in q:
        variants.append(f"{q} 허용 사유 요건 절차")
    return list(dict.fromkeys(v for v in variants if v))[:7]


def query_terms(question: str) -> list[str]:
    return [term for term in _tokens(question) if len(term) >= 2]


def source_multiplier(question: str, source_group: str) -> float:
    upper_q = question.upper()
    docs_signal = any(h.upper() in upper_q for h in DOCS_HINTS)
    product_signal = any(h.upper() in upper_q for h in PRODUCT_HINTS)
    if docs_signal and not product_signal:
        return 1.18 if source_group == "docs" else 0.88
    if product_signal and not docs_signal:
        return 1.10 if source_group == "investment" else 1.0
    return 1.0


def content_multiplier(question: str, text: str) -> float:
    multiplier = 1.0
    question_marks = text.count("?") + text.count("？")
    numbered_questions = len(re.findall(r"[①②③④⑤⑥⑦⑧⑨⑩]", text))
    if any(marker in text for marker in QUESTION_LIST_MARKERS):
        multiplier *= 0.45
    elif question_marks >= 3 or numbered_questions >= 4:
        multiplier *= 0.62
    terms = query_terms(question)
    if terms:
        covered = sum(1 for term in terms if term in text.lower())
        multiplier *= 1.0 + 0.35 * (covered / len(terms))
    upper_q, upper_text = question.upper(), text.upper()
    # Preserve exact domain anchors in a hybrid system. Query expansion can
    # otherwise over-promote generic chunks (e.g. "운용", "부담금").
    anchors = [a for a in ("DB", "DC", "IRP", "연금저축", "세액공제", "중도인출", "연금소득세", "퇴직소득세") if a.upper() in upper_q]
    for anchor in anchors:
        if anchor.upper() in upper_text:
            multiplier *= 1.12
        else:
            multiplier *= 0.72
    if "DB" in upper_q and "DC" in upper_q:
        if "DB" in upper_text and "DC" in upper_text:
            multiplier *= 1.25
        if "확정급여형" in text and "확정기여형" in text:
            multiplier *= 1.20
    if any(marker in text for marker in ("란", "이란", "의미", "말합니다", "합니다", "계산", "산정", "적립", "운용", "부담금")):
        multiplier *= 1.05
    return multiplier


class _BM25Index:
    def __init__(self, texts: list[str], k1: float = 1.5, b: float = 0.75) -> None:
        self.k1, self.b = k1, b
        self.docs = [_tokens(t) for t in texts]
        self.lengths = np.asarray([len(x) for x in self.docs], dtype=float)
        self.avgdl = float(self.lengths.mean()) if len(self.lengths) else 1.0
        self.tf = [Counter(d) for d in self.docs]
        df: Counter[str] = Counter()
        for doc in self.docs:
            df.update(set(doc))
        n = max(len(self.docs), 1)
        self.idf = {term: math.log(1.0 + (n - freq + 0.5) / (freq + 0.5)) for term, freq in df.items()}

    def scores(self, query: str) -> np.ndarray:
        terms = _tokens(query)
        out = np.zeros(len(self.docs), dtype=float)
        if not terms:
            return out
        for i, tf in enumerate(self.tf):
            dl = self.lengths[i] or 1.0
            score = 0.0
            for term in terms:
                f = tf.get(term, 0)
                if not f:
                    continue
                denom = f + self.k1 * (1 - self.b + self.b * dl / max(self.avgdl, 1.0))
                score += self.idf.get(term, 0.0) * (f * (self.k1 + 1)) / denom
            out[i] = score
        return out


def _top_indices(scores: np.ndarray, n: int) -> list[int]:
    if not len(scores) or n <= 0:
        return []
    n = min(n, len(scores))
    idx = np.argpartition(scores, -n)[-n:]
    return [int(i) for i in idx[np.argsort(scores[idx])[::-1]] if scores[int(i)] > 0]


def _rrf(rankings: list[list[int]], k: int = 60) -> dict[int, float]:
    scores: defaultdict[int, float] = defaultdict(float)
    for ranking in rankings:
        for rank, idx in enumerate(ranking, start=1):
            scores[idx] += 1.0 / (k + rank)
    return dict(scores)


class ChunkRetriever:
    """Hybrid lexical retriever: char/word TF-IDF + BM25 + multi-query RRF.

    It keeps the v4 API intact and introduces no additional generative model.
    The design is competition-safe: all LLM calls remain in HyperCLOVA-X paths.
    """

    def __init__(self, chunks_path: str | Path) -> None:
        self.chunks_path = Path(chunks_path)
        self.chunks = self._load_chunks()
        texts = [chunk["text"] for chunk in self.chunks]
        self.char_vectorizer = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 5), min_df=1, sublinear_tf=True, norm="l2")
        self.word_vectorizer = TfidfVectorizer(analyzer="word", token_pattern=r"(?u)\b\w+\b", ngram_range=(1, 2), min_df=1, sublinear_tf=True, norm="l2")
        self.char_matrix = self.char_vectorizer.fit_transform(texts)
        self.word_matrix = self.word_vectorizer.fit_transform(texts)
        self.bm25 = _BM25Index(texts)

    def _load_chunks(self) -> list[dict]:
        rows = []
        with self.chunks_path.open("r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    rows.append(json.loads(line))
        if not rows:
            raise ValueError("No chunks found.")
        return rows

    def retrieve(self, question: str, top_k: int = 5, source_group: Optional[str] = None) -> list[dict]:
        question = question.strip()
        if not question:
            return []
        variants = query_variants(question)
        candidate_n = min(len(self.chunks), max(top_k * 16, 80))
        rankings: list[list[int]] = []
        component_scores: dict[int, dict[str, float]] = defaultdict(dict)

        for qi, variant in enumerate(variants):
            expanded = expand_query(variant)
            char = linear_kernel(self.char_vectorizer.transform([expanded]), self.char_matrix).ravel()
            word = linear_kernel(self.word_vectorizer.transform([expanded]), self.word_matrix).ravel()
            bm25 = self.bm25.scores(expanded)
            denseish = 0.65 * char + 0.35 * word
            for name, scores in ((f"tfidf_{qi}", denseish), (f"bm25_{qi}", bm25)):
                ranking = _top_indices(scores, candidate_n)
                rankings.append(ranking)
                for idx in ranking[:candidate_n]:
                    component_scores[idx][name] = float(scores[idx])

        fusion = _rrf(rankings)
        reranked: list[tuple[float, float, int]] = []
        for idx, rrf_score in fusion.items():
            chunk = self.chunks[idx]
            if source_group and chunk.get("source_group") != source_group:
                continue
            final = rrf_score * source_multiplier(question, chunk.get("source_group", "")) * content_multiplier(question, chunk.get("text", ""))
            reranked.append((final, rrf_score, idx))
        reranked.sort(key=lambda x: x[0], reverse=True)

        results, seen_locations, seen_text_prefix = [], set(), set()
        for final_score, rrf_score, idx in reranked:
            chunk = self.chunks[idx]
            location_key = (chunk["filename"], chunk["location_type"], str(chunk["location"]))
            prefix = re.sub(r"\s+", " ", chunk.get("text", ""))[:180]
            if location_key in seen_locations or prefix in seen_text_prefix:
                continue
            seen_locations.add(location_key)
            seen_text_prefix.add(prefix)
            comps = component_scores.get(idx, {})
            results.append({
                "score": round(final_score, 6),
                "base_score": round(rrf_score, 6),
                "retrieval_method": "multi_query_rrf_bm25_tfidf",
                "query_variant_count": len(variants),
                "component_max_tfidf": round(max((v for k, v in comps.items() if k.startswith("tfidf")), default=0.0), 6),
                "component_max_bm25": round(max((v for k, v in comps.items() if k.startswith("bm25")), default=0.0), 6),
                "chunk_id": chunk["chunk_id"], "document_id": chunk["document_id"],
                "source_group": chunk["source_group"], "source_label": chunk["source_label"],
                "source_priority": "ENTERPRISE_PRIMARY", "filename": chunk["filename"],
                "location_type": chunk["location_type"], "location": chunk["location"], "text": chunk["text"],
            })
            if len(results) >= top_k:
                break
        return results


def print_results(results: list[dict]) -> None:
    if not results:
        print("\n관련 근거를 찾지 못했습니다.")
        return
    print("\n=== 검색 결과 ===")
    for rank, result in enumerate(results, start=1):
        print(f"\n[{rank}위] score={result['score']} method={result.get('retrieval_method')}")
        print(f"출처: {result['filename']} / {result['location_type']} {result['location']}")
        print("-" * 70)
        print(result["text"][:1200])


def main() -> None:
    parser = argparse.ArgumentParser(); parser.add_argument("--chunks", required=True); parser.add_argument("--top-k", type=int, default=5); args = parser.parse_args()
    print("검색 데이터를 불러오는 중입니다..."); retriever = ChunkRetriever(args.chunks); print(f"준비 완료: {len(retriever.chunks)} chunks")
    while True:
        question = input("\n질문 > ").strip()
        if question.lower() in {"exit", "quit", "q"}: break
        print_results(retriever.retrieve(question, top_k=args.top_k))

if __name__ == "__main__":
    main()
