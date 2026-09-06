# Improvement Report v6 - Research-Grounded RAG + LangGraph

## Implemented
- BM25 + char/word TF-IDF retrieval branches.
- Multi-query deterministic refinement for comparison, tax, rule and multi-clause questions.
- Reciprocal Rank Fusion (RRF) across query variants and retrieval branches.
- Source/content-aware reranking and duplicate-location/text suppression.
- Query analyzer now emits `required_evidence` and `retrieval_queries`.
- Evidence coverage now records diagnostic fact coverage while retaining domain coverage as the hard gate.
- Retrieval metric utilities: Recall@K, MRR@K, nDCG@K.
- HyperCLOVA X structured JSON support for the LangGraph supervisor.
- `COMPETITION_MODE=1` defaults to HyperCLOVA X and blocks other LLM providers.
- Added v6 architecture tests and retrieval unit tests.

## Safety/competition rationale
No additional generative model is introduced. BM25/TF-IDF/RRF are deterministic IR algorithms. HyperCLOVA X remains the only LLM in competition mode. Legal DB deny-by-default, deterministic calculation, evidence hub, rule verification, numeric grounding, response guard, and source precedence remain intact.

## Claims deliberately NOT made
This release does not claim higher final-answer accuracy until a gold retrieval/factuality benchmark is run. Existing 20/20 orchestration evaluation does not measure natural-language factuality. v6 therefore ships measurement utilities and an ablation plan rather than inventing improvement numbers.

## Next benchmark gate
Build at least 80-150 gold queries split across DB/DC/IRP/연금저축, tax, procedure, product comparison/recommendation, false-premise, multi-hop, follow-up, and unanswerable cases. Label relevant chunk/article/product IDs and expected answer facts. Compare v5 vs v6 under identical Top-K and HyperCLOVA model/version.
