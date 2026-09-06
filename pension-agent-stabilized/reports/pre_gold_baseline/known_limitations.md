# Known limitations (pre Gold Set)

Do not hide these before expanding to 80–150 Gold questions.

- **TDF naming alias**: Catalog uses 라이프사이클-style names; literal `TDF` product names are not present as separate rows.
- **Performance scale / provenance**: Some `fund_return` / `1Y` Standard JSON values are contaminated by fund-code tables (e.g. raw `98776.0`). Audited as `SOURCE_CONFLICT` / scale anomaly; LLM must not invent rescale (`98776 → 9.8776%`). Ranking prefers verified rows when available.
- **SOURCE_CONFLICT volume**: ~42 of 162 `fund_return`/`1Y` samples conflict with source evidence text; treat as data-quality debt for Gold Set labeling.
- **Enterprise evidence gaps**: Pure enterprise-concept questions still depend on RAG chunk coverage. Product-fact answers attach prospectus PDF as document-domain evidence when linked; absence of enterprise narrative is still possible for some topics.
- **Product class coverage**: Limited to classes in Standard JSON / Postgres; not every share class has complete fee / performance / PDF linkage.
- **Risk filter ≠ suitability**: Semantic risk buckets filter candidates by stated preference. No enterprise suitability matrix was found in provided docs. User copy should remain “입력한 위험 선호 조건 기준으로 비교 가능한 후보”, not personal suitability certification.
- **Composer fallback**: When HyperCLOVA narrative generation fails, product-fact answers fall back to deterministic DB composer (units formatted for display only).
- **Regression ≠ generalization**: 22/22 × 2 proves stability of the live suite only. Gold Set evaluation is required before claiming broader quality.
