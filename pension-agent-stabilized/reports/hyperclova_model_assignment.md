# HyperCLOVA X role allocation

Verified 2026-09-05 using the configured CLOVA Studio key: HCX-007, HCX-005, and HCX-DASH-002 each returned HTTP 200 / API status 20000 and nonempty content. The probe used synthetic input only. See hyperclova_availability.json. This establishes account access, not an application quality benchmark.

| Role | Model | Rationale |
| --- | --- | --- |
| Execution specification supervisor | HCX-007, thinking none | Complex instruction following for multi-tool execution specifications; bounded JSON output and predictable latency |
| Document/product/law conversational answers | HCX-005 | Instruction following and 128k context for evidence-grounded explanations |
| Pension type normalizer | HCX-DASH-002 | Small fixed-label classification task; lightweight model |
| PDF extraction and semantic review | HCX-005 | Long document context and detailed extraction; current pipeline supplies parsed text, not PDF images |
| Router, retrieval, calculation, verification | No LLM | Existing deterministic logic, database queries and checks |

Settings: CLOVA_ANSWER_MODEL, CLOVA_SUPERVISOR_MODEL, CLOVA_NORMALIZER_MODEL, CLOVA_EXTRACTION_MODEL. These role settings supersede CLOVA_MODEL in application role wiring. PENSION_NORMALIZER_MODEL, if set, must match CLOVA_NORMALIZER_MODEL. Restart running app processes to load changes.

HCX-007 requests use maxCompletionTokens and thinking.effort=none; HCX-005/DASH-002 use maxTokens capped at 4096. No other provider fallback exists. Supervisor cache keys include the supervisor model even before lazy initialization.

Validation: five offline unit tests passed, syntax compilation passed. Application-wide integration tests remain limited by the broken Python 3.12 virtual environment. Assignments are initial engineering choices, not empirically proven optima; compare the existing conversation and gold100 datasets before tuning further.

Sources:
- https://guide.ncloud-docs.com/docs/en/clovastudio-model
- https://api.ncloud-docs.com/docs/clovastudio-chatcompletionsv3-thinking
