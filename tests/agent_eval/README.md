# Pension Agent live regression

This package drives the **same public path** as production chat:

- FastAPI `POST /api/search` (`chatbot/web.py`)
- Streamlit `agent.respond(...)` (`app.py`)
- `PensionLangGraphAgent.respond`

It does not reimplement routing or retrieval. Multi-turn cases keep one `session_id` and update `SessionContext` the same way Streamlit does.

## Run

From the repo root, with `.venv` active:

```bat
python -m tests.agent_eval.run_eval
```

Defaults to T001–T006.

```bat
python -m tests.agent_eval.run_eval --all
python -m tests.agent_eval.run_eval --case T002
python -m tests.agent_eval.run_eval --ids T001,T002,T003,T004,T005,T006,T007,T008,T009,T010,T011,T012,T013,T014,T015
python -m tests.agent_eval.run_eval --category recommendation
python -m tests.agent_eval.run_eval --all --repeat 2
python -m tests.agent_eval.run_eval --llm-judge
```

`--all` includes T001–T015 plus official P0 items T016–T020 and official multi-turn/compound T021–T022.

`AGENT_EVAL_LLM_JUDGE` defaults to `false`. The optional judge only scores usefulness / naturalness / sufficiency. It does not score DB facts, sources, or routing.

## Reports

Written under `reports/agent_eval/`:

- `latest_results.json`
- `latest_results.csv`
- `latest_summary.md`
- `reproducibility.md` (when `--repeat 2`)

SKIP is used when the NVIDIA key is missing or the agent cannot start. That is not reported as PASS or disguised as FAIL.

Do not print `.env` secrets. This runner only checks that a key exists.
