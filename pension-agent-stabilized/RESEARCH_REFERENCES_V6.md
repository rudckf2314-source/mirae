# Research references used for v6 design

This file records design influence, not a claim that every paper was reproduced verbatim.

1. Lewis et al. (2020), Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks. https://arxiv.org/abs/2005.11401
   - Foundation: explicit non-parametric evidence + generator separation.
2. Chan et al. (2024), RQ-RAG: Learning to Refine Queries for Retrieval Augmented Generation. https://arxiv.org/abs/2404.00610
   - Applied: query rewriting/decomposition/disambiguation concept. v6 uses deterministic refinements to avoid extra LLM calls.
3. Ammann et al. (2025), Question Decomposition for Retrieval-Augmented Generation. https://aclanthology.org/2025.acl-srw.32/
   - Applied: sub-question evidence coverage for multi-hop/comparison queries.
4. Thakur et al. (2021), BEIR. https://arxiv.org/abs/2104.08663
   - Applied: keep BM25 as a strong lexical baseline; evaluate retrieval separately from generation.
5. Karpukhin et al. (2020), Dense Passage Retrieval. https://arxiv.org/abs/2004.04906
   - Design influence: semantic retrieval branch. A learned dense branch is intentionally not enabled in competition v6 until model-use rules are confirmed.
6. Santhanam et al. (2022), ColBERTv2. https://aclanthology.org/2022.naacl-main.272/
   - Design influence: late-interaction reranking is a candidate ablation, not default runtime.
7. Yan et al. (2024), Corrective Retrieval Augmented Generation. https://arxiv.org/abs/2401.15884
   - Applied conceptually: retrieval quality must be checked before generation; v6 uses evidence coverage/safe-stop rather than unrestricted web fallback.
8. Asai et al. (2023/2024), Self-RAG. https://arxiv.org/abs/2310.11511
   - Applied conceptually: generation is followed by critique/grounding, with abstention when evidence is insufficient.
9. Yu et al. (2024), RankRAG. https://arxiv.org/abs/2407.02485
   - Design influence: ranking quality is central. v6 implements model-free RRF now; learned ranking is an optional later ablation.
10. Liu et al. (2024), Lost in the Middle. https://aclanthology.org/2024.tacl-1.9/
    - Applied conceptually: avoid indiscriminately increasing context size; retrieve a focused Top-K and deduplicate evidence.
11. Xu et al. (2024), RECOMP. https://arxiv.org/abs/2310.04408
    - Design influence: selective/context compression is planned after evidence-span labeling; not enabled blindly.
12. Es et al. (2023), RAGAS. https://arxiv.org/abs/2309.15217
    - Applied: evaluate retrieval and faithfulness as separate dimensions.
13. Saad-Falcon et al. (2024), ARES. https://aclanthology.org/2024.naacl-long.20/
    - Applied: evaluation dimensions include context relevance, answer faithfulness, answer relevance.
14. Peng et al. (2024/2025), Graph Retrieval-Augmented Generation: A Survey. https://arxiv.org/abs/2408.08921
    - Design influence: graph retrieval is reserved for relationship-heavy pension/tax/entity queries and must prove benefit by ablation.
