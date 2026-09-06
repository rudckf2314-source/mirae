# Code changes and design rationale

## Goal
Improve semantic routing, required-fact planning, and deterministic tax calculation without loosening Gold evaluators or hardcoding Test IDs / expected Excel answers.

## Changes

### 1) Semantic Router (`chatbot/task_intent.py`, `chatbot/query_router.py`)
- Classify work type: procedure / institution / product_search / product_attribute / tax_calculation / correction / compound_holding / action_request.
- Stop treating class-name tokens like `퇴직연금` as product identity hints (`_GENERIC_HINTS`).
- Procedure/education/funding-rule questions prefer `document` (+ `law` when needed), not product catalog dumps.
- Holding-without-name and buy-order requests are handled as clarification / out-of-scope, not arbitrary product picks.
- Bumped `ROUTER_POLICY_VERSION` to invalidate stale route cache.

### 2) Required Fact Planner (`chatbot/required_facts.py`)
- Build a fact list (tool + status) before answering.
- Prefer partial confirmed/unconfirmed disclosure over blanket safe_stop when user inputs are the missing piece.
- Wire plan into LangGraph `query_analysis` metadata (not user-visible).

### 3) Correction + Calculation (`chatbot/calculation_gateway.py`, `calculation_worker.py`, `calculation_verifier.py`, `tax_policy_repository.py`, seed policy)
- Parse `6,000만 원` style amounts; separate `총급여` vs `납입`.
- Policy fields: `annual_contribution_limit=18000000`, `local_tax_surcharge_ratio=0.10` (from Legal DB seed, not Excel answers).
- Effective rates = national × (1 + surcharge) → 13.2% / 16.5%.
- Premise-check answers explicitly correct wrong assumed rates/amounts.
- Verifier recomputes using `effective_rate`.

### 4) Stop-reason separation (`pension_ambiguity.py`, `_safe_stop_node`)
- Internal codes: ACTION_NOT_ALLOWED, NEEDS_CLARIFICATION, EVIDENCE_INSUFFICIENT, NO_MATCHING_PRODUCT, POLICY_BLOCKED, OUT_OF_SCOPE.
- Natural-language guidance only in user text; codes stay in metadata.

## Non-goals / constraints honored
- No Excel / gold evaluator / prior report edits.
- No Test-number exception branches.
- HyperCLOVA-only; secrets not printed.
- Single Gold re-run; no cherry-picking.

## Residual bottlenecks (why <65%)
- Many remaining fails are still `D:correction_missing` on document narratives (tax/legal nuance without deterministic calculator path).
- Some adapter `route_family` expectations remain stricter than valid multi-tool answers.
- Enterprise document coverage gaps for specialized ops (실물이전 코드, VPC, etc.).
- One automated invented-product flag (`hallucination_cases=1`) — treat as signal, not proof of zero hallucination.
