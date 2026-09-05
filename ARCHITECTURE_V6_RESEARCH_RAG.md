# v6 Research-Grounded LangGraph Architecture

## Goal
v6 keeps the v5 LangGraph safety gates and upgrades retrieval/evaluation without adding a second generative LLM. `COMPETITION_MODE=1` defaults to HyperCLOVA X only.

## Runtime flow

User -> Conversation Resolver -> Query Analysis -> Router/Supervisor -> Domain Worker

- Document Worker -> multi-query refinement -> TF-IDF(char/word) + BM25 -> Reciprocal Rank Fusion -> heuristic source/content rerank -> Evidence Hub
- Product Worker -> structured Product DB + linked PDF evidence
- Law Worker -> local Legal DB + deny-by-default guardrail
- Calculation Worker -> deterministic Python calculation + policy verification

Evidence Hub -> Evidence Coverage -> Rule Verifier -> HyperCLOVA X Answer -> Claim Grounding -> Response Guard

## v6 research-derived changes

1. Query refinement/decomposition: deterministic query variants and evidence requirements are derived before retrieval. Complex comparisons/tax/rule questions get focused variants.
2. Hybrid lexical retrieval: exact-term-sensitive BM25 is fused with Korean char/word TF-IDF using RRF. This is robust for pension/legal terms and paraphrases without introducing a prohibited LLM.
3. Evidence coverage diagnostics: hard gate remains domain completeness; fact coverage is reported separately so brittle phrase matching cannot incorrectly block a valid answer.
4. Retrieval metrics: `Recall@K`, `MRR@K`, `nDCG@K` utilities were added for a real labeled retrieval benchmark.
5. Competition LLM guard: HyperCLOVA X is the default; `COMPETITION_MODE=1` rejects other LLM providers. The LangGraph supervisor can now request structured JSON directly from HyperCLOVA X.

## Not enabled by default

- Cross-encoder reranking / ColBERT: useful research directions, but adding another learned model may need competition-rule confirmation and a domain benchmark.
- GraphRAG: retained as a phase-2 extension for relationship-heavy queries; not forced into the critical path before proving gains.
- Web corrective retrieval: not used in competition mode because enterprise-provided evidence must remain primary and source policy is strict.

## Required evaluation before claiming performance gain

Create a gold set with query -> relevant chunk IDs / legal article / product IDs, then compare v5 and v6 with the same data and Top-K. Report Recall@1/3/5, MRR@5, nDCG@5, final factual accuracy, citation coverage, abstention precision, latency, and LLM calls. Only retain an optional technique if the ablation shows net benefit.
