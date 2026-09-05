# Improvement Report v4 — Guarded Legal DB

## Implemented

- Added deny-by-default legal retrieval guardrail integration.
- Added local SQLite legal serving DB with source/article/sync/policy tables.
- Added DB-first `LegalRetriever` and updated `LawTool` so chat does not require a live law API call.
- Added National Law Information Center Open API sync job with retries and secret-safe reporting.
- Added deterministic tax-credit policy normalization from Income Tax Act Article 59-3; fails closed if required numbers are missing.
- Added preloaded official-law snapshot for core 2026 regression use.
- Added deterministic 2026 pension tax-credit calculation path backed by Legal DB evidence.
- EvidenceHub now preserves `legal_db` source type/channel metadata.
- Fixed live follow-up state propagation by preserving `confirmed_constraints` in context updates.
- Added distinctive catalog product aliases (e.g. `솔로몬`) for product-first routing.
- Changed `.env.example` default provider to HyperCLOVA because contest evaluation requires HyperCLOVA X.

## Verification actually run in this build environment

- `python -m compileall -q chatbot scripts` — PASS
- `python scripts/seed_legal_snapshot.py` — PASS; 25 registered sources, 7 seeded articles, 1 verified normalized policy
- Existing unit/regression suite + new legal DB tests — **18 PASS**

## Not claimed

- A full live National Law Information Center API sync was **not** executed here: this container cannot resolve/reach the external API endpoint.
- The user's PostgreSQL + HyperCLOVA X live T001–T022 suite was **not** rerun here.

Run those on the deployment/local environment using the included scripts before treating the build as final.
