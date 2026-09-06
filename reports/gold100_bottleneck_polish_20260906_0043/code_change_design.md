# Code changes — bottleneck polish

## Targets from 71% residual fails
1. ResponseGuard `system_error` wiping grounded document+law answers (law_source_missing)
2. Correction questions misrouted to product catalog (투자설명서+총보수+맞나요)
3. Correction token gap: answers said 불가 without 아닙니다
4. Verification FAIL short-circuit despite retrieved documents

## Changes
- `task_intent.py`: correction before prospectus-attribute collision
- `pension_protocol.py`: allow document sources to satisfy law/product guard; prefer partial grounded success over system_error
- `pension_langgraph_agent.py`: soft-continue to answer when docs/products exist; narrower catalog_style; yes/no+불가 → 아닙니다 opener; soft PASS metadata for grounded partials
- router policy v7; unit test for fee premise correction
- T001–T022 kept green; single Gold re-run

## Constraints
No Excel/evaluator edits, no Test-id hardcoding, HyperCLOVA only, no cherry-picking.
