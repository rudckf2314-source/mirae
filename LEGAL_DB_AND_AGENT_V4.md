# Pension Agent v4 — Legal DB serving architecture

## Goal

The National Law Information Center Open API is an **ingestion/synchronization source**, not a chat-time dependency.
The agent answers legal/tax questions from a local guarded Legal DB and sends only the retrieved legal evidence to the response layer.

```text
National Law Information Center Open API
        -> pension_legal_guardrail_v0.1.json (DENY by default)
        -> scripts/sync_legal_db.py
        -> data/legal/pension_legal.db
             - legal_sources
             - legal_articles
             - legal_sync_runs
             - tax_policy_rules
        -> LegalRetriever / LawTool (DB first)
        -> Rule Engine / EvidenceHub / Verifier
        -> HyperCLOVA X response
```

## Source priority

1. Enterprise-provided documents/product data
2. Guarded official Legal DB for legal conclusions and tax rules
3. Optional external API fallback only when explicitly enabled

`LAW_QUERY_FALLBACK_API=0` is the default so a live API outage does not break chat serving.

## Guardrail

`config/pension_legal_guardrail_v0.1.json` is authoritative for retrieval scope.
Unknown topics/laws/articles and unregistered cross-references fail closed.
The LLM is not allowed to invent law names, article numbers, or API URLs.

## Initial DB

This package includes a small current official-law snapshot sufficient for core regression work, including:
- Income Tax Act Article 59-3 (pension-account tax credit)
- Income Tax Act Enforcement Decree Article 40-2
- Employee Retirement Benefit Security Act Articles 13, 19, 22, 24
- Enforcement Decree Article 18
- normalized 2026 pension tax-credit rule linked to Income Tax Act Article 59-3

This snapshot was seeded from independently checked official law.go.kr pages because this build environment cannot reach the external Open API endpoint. It is **not represented as a successful full API sync**.

## Full guarded API sync on the deployment machine

Put `LAW_API_OC` only in local `.env`, then run:

```bash
python scripts/sync_legal_db.py --fail-on-partial
```

or Windows:

```bat
scripts\run_legal_sync.bat
```

The job:
- loads the guardrail registry
- fetches each registered law
- stores all articles for FULL sources
- stores only allowlisted articles for PARTIAL sources
- records a sync run without logging API secrets
- deterministically refreshes the normalized pension tax-credit policy only if all required statutory values can be parsed; otherwise it fails closed and retains the prior verified rule.

## Scheduling

Recommended contest MVP: once per day. Laws are versioned and the serving path does not need a live API call per question.
Use Windows Task Scheduler or cron to run `scripts/run_legal_sync.*`.

## Tests

```bash
python scripts/seed_legal_snapshot.py
python -m compileall -q chatbot scripts
pytest -q TEST/test_conversation_regression.py TEST/test_agent_patch_regression.py TEST/test_legal_db_v4.py
```

The live 22-question suite must still be rerun on the user's environment with PostgreSQL + HyperCLOVA X. Unit PASS is not a claim of live Agent PASS.
