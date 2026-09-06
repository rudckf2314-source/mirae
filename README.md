# Mirae Asset Pension Agent

HyperCLOVA X-only pension assistant. The FastAPI service retains the existing
LangGraph path, product DB, legal DB, and Streamlit UI.

## Run locally

```bat
py -3.13 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
copy .env.example .env
run.bat
```

Set `CLOVA_STUDIO_API_KEY` in `.env`. `LAW_API_OC` is optional for normal chat:
legal serving is DB-first and `LAW_QUERY_FALLBACK_API=0` by default. Do not put
secrets in source control.

- API health: `GET /health`
- Existing chat API: `POST /api/search`
- Official query contract: `GET /answer?question=...&question_id=...&top_k=5`
- Streamlit UI: `.venv\Scripts\python.exe -m streamlit run app.py`

## Tests

Run all automated regression tests with one command:

```bat
run_tests.bat
```

Gold-100 has live HyperCLOVA calls and is intentionally separate from the
offline regression command. Validate its input without making a model call:

```bat
.venv\Scripts\python.exe -m tests.gold100.run_gold100 --dry-run --out-dir C:\tmp\gold100-dry-run
```

## Docker

```bat
docker build -t mirae-pension-agent .
docker run --rm -p 8000:8000 --env-file .env mirae-pension-agent
```

The provided `docker-compose.yml` starts PostgreSQL only; start it before the
application when using a PostgreSQL product store.
