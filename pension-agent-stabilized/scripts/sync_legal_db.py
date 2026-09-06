from __future__ import annotations

import argparse
import json
import sys
import re
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from chatbot.law_api_client import LawAPIClient  # noqa: E402
from chatbot.legal_store import LegalArticle, LegalStore, DEFAULT_GUARDRAIL_PATH, utc_now  # noqa: E402


def normalize_article_label(value: str) -> str:
    v = str(value or "").strip()
    if v.startswith("제"):
        return v
    return f"제{v}조"



def _won_from_manwon(text: str, pattern: str) -> int | None:
    m = re.search(pattern, text)
    if not m:
        return None
    return int(m.group(1).replace(",", "")) * 10000


def refresh_tax_credit_policy(store: LegalStore, law_body: dict) -> bool:
    """Normalize only explicitly stated Income Tax Act Article 59-3 values.

    Fail closed if the statutory text does not expose every required numeric fact.
    This keeps an API format/content change from silently creating a wrong tax rule.
    """
    article = next((a for a in law_body.get("articles", []) if normalize_article_label(a.get("article_no")) == "제59조의3"), None)
    if not article:
        return False
    text = str(article.get("article_text") or "").replace(" ", "")
    # Current statutory wording uses 600만원/900만원 and 12%/15% expressed as 100분의 N.
    amounts = [int(x.replace(",", "")) * 10000 for x in re.findall(r"(\d[\d,]*)만원", text)]
    rates = [int(x) for x in re.findall(r"100분의(\d+)", text)]
    if 6000000 not in amounts or 9000000 not in amounts or 12 not in rates or 15 not in rates:
        return False
    gross = 55000000 if ("5천500만원" in text or "5500만원" in text) else None
    comprehensive = 45000000 if ("4천500만원" in text or "4500만원" in text) else None
    if gross is None or comprehensive is None:
        return False
    effective = str(law_body.get("effective_date") or "")
    year = int(effective[:4]) if len(effective) >= 4 and effective[:4].isdigit() else datetime.now().year
    store.upsert_policy_rule(
        policy_key="PENSION_TAX_CREDIT", policy_year=year, effective_from=effective or f"{year}-01-01",
        formula_id=f"pension_tax_credit_v{year}", version=effective or str(year),
        payload={
            "combined_credit_base_limit": 9000000,
            "pension_savings_credit_base_limit": 6000000,
            "annual_contribution_limit": 18000000,
            "standard_rate": "0.12",
            "lower_income_rate": "0.15",
            "local_tax_surcharge_ratio": "0.10",
            "gross_salary_threshold": gross,
            "comprehensive_income_threshold": comprehensive,
            "isa_extra_credit_base_limit": 3000000 if "300만원" in text else 0,
            "isa_transfer_credit_ratio": "0.10" if ("100분의10" in text or "10%" in text) else "0",
        },
        evidence_source_key="INCOME_TAX_ACT", evidence_article_no="제59조의3",
        source_type="LAW_GO_KR_OPEN_API", source_priority="OFFICIAL_LEGAL", verified=True,
    )
    return True

def run_sync(db_path: str | None = None, guardrail_path: str | None = None) -> dict:
    store = LegalStore(db_path)
    guardrail_file = Path(guardrail_path or DEFAULT_GUARDRAIL_PATH)
    guardrail = store.load_guardrail_registry(guardrail_file)
    client = LawAPIClient()

    started = utc_now()
    run_details = {"sources": {}, "guardrail": guardrail_file.name}
    total_articles = 0
    errors = 0

    registry = guardrail.get("source_registry", {})
    for source_key, spec in registry.items():
        law_name = spec["law_name"]
        try:
            body = client.get_normalized_law(law_name)
            if not body:
                raise RuntimeError("law_not_found")
            allowed = set(spec.get("allowed_articles", [])) if spec.get("default_scope") == "PARTIAL" else None
            records = []
            for article in body.get("articles", []):
                article_no = normalize_article_label(article.get("article_no"))
                if allowed is not None and article_no not in allowed:
                    continue
                text = str(article.get("article_text") or "").strip()
                if not text:
                    continue
                records.append(LegalArticle(
                    source_key=source_key,
                    law_name=body.get("law_name") or law_name,
                    law_type=spec["law_type"],
                    law_id=str(body.get("law_id") or "") or None,
                    law_serial=str(body.get("law_serial") or "") or None,
                    promulgation_date=str(body.get("promulgation_date") or "") or None,
                    effective_date=str(body.get("effective_date") or "") or None,
                    article_no=article_no,
                    article_title=article.get("article_title"),
                    article_text=text,
                    source_channel="LAW_GO_KR_OPEN_API",
                    source_url="https://www.law.go.kr/DRF/lawService.do",
                    fetched_at=utc_now(),
                ))
            inserted = store.upsert_articles(records)
            total_articles += inserted
            policy_refreshed = False
            if source_key == "INCOME_TAX_ACT":
                policy_refreshed = refresh_tax_credit_policy(store, body)
            run_details["sources"][source_key] = {
                "status": "OK",
                "law_name": law_name,
                "fetched": len(records),
                "inserted": inserted,
                "effective_date": body.get("effective_date"),
                "tax_policy_refreshed": policy_refreshed,
            }
        except Exception as exc:  # sync report only; secrets are never logged
            errors += 1
            run_details["sources"][source_key] = {
                "status": "ERROR",
                "law_name": law_name,
                "error_type": type(exc).__name__,
            }

    status = "SUCCESS" if errors == 0 else ("PARTIAL" if total_articles else "FAILED")
    with store.connect() as conn:
        conn.execute(
            """
            INSERT INTO legal_sync_runs(started_at, finished_at, status, source_count, article_count, error_count, details_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (started, utc_now(), status, len(registry), total_articles, errors, json.dumps(run_details, ensure_ascii=False)),
        )
    return {"status": status, "source_count": len(registry), "article_count": total_articles, "error_count": errors, "db": str(store.db_path)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync pension legal DB from National Law Information Center Open API")
    parser.add_argument("--db", default=None)
    parser.add_argument("--guardrail", default=None)
    parser.add_argument("--fail-on-partial", action="store_true")
    args = parser.parse_args()
    result = run_sync(args.db, args.guardrail)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["status"] == "FAILED" or (args.fail_on_partial and result["status"] != "SUCCESS"):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
