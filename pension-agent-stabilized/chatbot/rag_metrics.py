from __future__ import annotations

import math
from dataclasses import dataclass, asdict
from typing import Iterable, Sequence


@dataclass(frozen=True)
class RetrievalMetrics:
    recall_at_k: float
    mrr_at_k: float
    ndcg_at_k: float
    queries: int

    def to_dict(self) -> dict[str, float | int]:
        return asdict(self)


def _dcg(rels: Sequence[int]) -> float:
    return sum((2**rel - 1) / math.log2(i + 2) for i, rel in enumerate(rels))


def evaluate_rankings(rankings: Iterable[Sequence[str]], relevant: Iterable[set[str]], k: int = 5) -> RetrievalMetrics:
    rankings = list(rankings)
    relevant = list(relevant)
    if len(rankings) != len(relevant):
        raise ValueError("rankings and relevant must have the same length")
    recalls, rrs, ndcgs = [], [], []
    for ranked, gold in zip(rankings, relevant):
        top = list(ranked)[:k]
        hits = [1 if item in gold else 0 for item in top]
        recalls.append(0.0 if not gold else len(set(top) & gold) / len(gold))
        first = next((i for i, hit in enumerate(hits, start=1) if hit), None)
        rrs.append(0.0 if first is None else 1.0 / first)
        ideal = [1] * min(len(gold), k) + [0] * max(0, k - len(gold))
        denom = _dcg(ideal)
        ndcgs.append(0.0 if denom == 0 else _dcg(hits) / denom)
    n = len(rankings)
    return RetrievalMetrics(
        recall_at_k=round(sum(recalls) / n, 4) if n else 0.0,
        mrr_at_k=round(sum(rrs) / n, 4) if n else 0.0,
        ndcg_at_k=round(sum(ndcgs) / n, 4) if n else 0.0,
        queries=n,
    )
