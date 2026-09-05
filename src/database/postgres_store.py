from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from schemas.product_schema import ProductExtraction
from database.safety import assert_persistence_safe


class PostgresStandardStore:
    """Transactional PostgreSQL loader for ProductExtraction v0.1.

    The full validated standard JSON is retained in source_documents.standard_json,
    while query-critical child entities are also normalized into SQL tables.
    """

    CHILD_TABLES = (
        "extraction_issues", "field_status", "evidence", "narratives",
        "liquidity_rules", "investment_profiles",
        "financial_metrics", "capital_flows", "performance", "hedging_policies",
        "master_feeder_relations", "fund_conversion_rules", "class_transition_rules",
        "fees", "sales_charges", "product_classes", "risk_ratings", "products",
    )

    def __init__(self, database_url: str, migration_path: Path | None = None):
        if not database_url:
            raise ValueError("DATABASE_URL이 비어 있습니다.")
        self.database_url = database_url
        self.migration_path = migration_path or Path(__file__).resolve().parents[2] / "sql" / "001_init.sql"
        self._schema_ready = False

    @staticmethod
    def _psycopg():
        try:
            import psycopg
        except ImportError as exc:
            raise RuntimeError("PostgreSQL 저장을 사용하려면 psycopg[binary]를 설치하세요.") from exc
        return psycopg

    def ensure_schema(self) -> None:
        if self._schema_ready:
            return
        psycopg = self._psycopg()
        sql_dir = self.migration_path.parent
        migration_files = sorted(sql_dir.glob("*.sql"))
        with psycopg.connect(self.database_url) as conn:
            with conn.cursor() as cur:
                for path in migration_files:
                    cur.execute(path.read_text(encoding="utf-8"))
            conn.commit()
        self._schema_ready = True

    def save(self, document_id: str, product: ProductExtraction, *, unsafe: bool = False) -> int:
        validated = ProductExtraction.model_validate(product.model_dump())
        if not unsafe:
            assert_persistence_safe(validated)
        psycopg = self._psycopg()
        payload = validated.model_dump(mode="json")
        self.ensure_schema()
        with psycopg.connect(self.database_url) as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO source_documents
                          (document_id, filename, document_type, as_of_date, effective_date,
                           revision_date, page_count, file_hash, schema_version, standard_json, updated_at)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,NOW())
                        ON CONFLICT (document_id) DO UPDATE SET
                          filename=EXCLUDED.filename,
                          document_type=EXCLUDED.document_type,
                          as_of_date=EXCLUDED.as_of_date,
                          effective_date=EXCLUDED.effective_date,
                          revision_date=EXCLUDED.revision_date,
                          page_count=EXCLUDED.page_count,
                          file_hash=EXCLUDED.file_hash,
                          schema_version=EXCLUDED.schema_version,
                          standard_json=EXCLUDED.standard_json,
                          updated_at=NOW()
                        RETURNING id
                        """,
                        (
                            document_id,
                            payload["source_document"]["filename"],
                            payload["source_document"]["document_type"],
                            payload["source_document"].get("as_of_date"),
                            payload["source_document"].get("effective_date"),
                            payload["source_document"].get("revision_date"),
                            payload["source_document"].get("page_count"),
                            payload["source_document"].get("file_hash"),
                            payload["schema_version"],
                            json.dumps(payload, ensure_ascii=False),
                        ),
                    )
                    source_id = cur.fetchone()[0]
                    for table in self.CHILD_TABLES:
                        cur.execute(f"DELETE FROM {table} WHERE source_document_id = %s", (source_id,))
                    self._insert_all(cur, source_id, payload)
            return source_id

    def ping(self) -> bool:
        psycopg = self._psycopg()
        with psycopg.connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                return cur.fetchone()[0] == 1

    def _insert_all(self, cur, sid: int, p: dict[str, Any]) -> None:
        prod = p["product"]
        cur.execute(
            """INSERT INTO products
            (source_document_id,product_key,official_name,kofia_fund_code,manager_name,legal_form,asset_type,
             is_open_end,is_additional,is_class_type,is_master_feeder,is_convertible,
             is_high_complexity_product,inception_date)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (sid, prod["product_key"], prod["official_name"], prod.get("kofia_fund_code"), prod.get("manager_name"),
             prod.get("legal_form"), prod.get("asset_type"), prod.get("is_open_end"),
             prod.get("is_additional"), prod.get("is_class_type"), prod.get("is_master_feeder"),
             prod.get("is_convertible"), prod.get("is_high_complexity_product"), prod.get("inception_date")),
        )
        self._rows(cur, "risk_ratings", sid, p["risk_ratings"],
                   ["grade","label","method","as_of_date","evidence_ids"])
        self._rows(cur, "product_classes", sid, self._unique_class_rows(p["classes"]),
                   ["class_key","class_name","kofia_fund_code","sales_charge_type","channel","pension_type",
                    "eligibility_text","is_online","is_cdsc_class","is_conversion_enabled","inception_date","evidence_ids"])
        self._rows(cur, "sales_charges", sid, p["sales_charges"],
                   ["class_key","charge_type","rate","rate_min","rate_max","rate_unit","rate_condition",
                    "base_amount","timing","condition_text","evidence_ids"])
        self._rows(cur, "fees", sid, p["fees"],
                   ["class_key","fee_type","rate","unit","as_of_date","effective_from","effective_to","evidence_ids"])
        self._rows(cur, "class_transition_rules", sid, p["class_transition_rules"],
                   ["from_class","to_class","automatic","trigger_type","minimum_holding_months","condition_text","evidence_ids"])
        self._rows(cur, "fund_conversion_rules", sid, p["fund_conversion_rules"],
                   ["source_class","target_product_name","target_class","conversion_allowed","conversion_fee_rate",
                    "conversion_count_limit","condition_text","evidence_ids"])
        self._rows(cur, "master_feeder_relations", sid, p["master_feeder_relations"],
                   ["master_product_name","minimum_investment_ratio","maximum_investment_ratio","ratio_unit","evidence_ids"])
        self._rows(cur, "hedging_policies", sid, p["hedging_policies"],
                   ["subject","fund_name","is_hedged","hedge_ratio_min_pct","hedge_ratio_max_pct","hedge_from_currency",
                    "hedge_to_currency","residual_fx_exposure","policy_text","as_of_date","status","evidence_ids"])
        self._rows(cur, "performance", sid, p["performance"],
                   ["class_key","metric","period","return_type","value","unit","as_of_date","period_start","period_end","evidence_ids"])
        self._rows(cur, "capital_flows", sid, p["capital_flows"],
                   ["class_key","period_start","period_end","opening_units","opening_amount","subscription_units",
                    "subscription_amount","redemption_units","redemption_amount","ending_units","ending_amount",
                    "unit_scale","units_scale","amount_scale","currency","evidence_ids"])
        self._rows(cur, "financial_metrics", sid, p["financial_metrics"],
                   ["class_key","metric_type","raw_value","raw_unit","normalized_value_krw","as_of_date","period_start","period_end","evidence_ids"])
        if p.get("investment_profile"):
            self._rows(cur, "investment_profiles", sid, [p["investment_profile"]],
                       ["primary_asset","investment_regions","investment_countries","investment_sectors",
                        "investment_styles","benchmark_name","equity_ratio_min","equity_ratio_max",
                        "bond_ratio_min","bond_ratio_max","overseas_asset_ratio_min","overseas_asset_ratio_max",
                        "derivative_usage","recommended_horizon","principal_loss_possible","evidence_ids"])
        self._rows(cur, "liquidity_rules", sid, p.get("liquidity_rules", []),
                   ["class_key","transaction_type","cutoff_time","pricing_day_offset",
                    "payment_day_offset","redemption_fee","restriction_text","evidence_ids"])
        self._rows(cur, "narratives", sid, p["narratives"],
                   ["narrative_type","subject","text","evidence_ids"])
        for ev in p["evidence"]:
            cur.execute(
                """INSERT INTO evidence
                (evidence_id,source_document_id,field_path,page,section,source_text,table_markdown,source_hash,
                 row_index,column_name,raw_cell_text,extraction_method,confidence)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (ev["evidence_id"], sid, ev["field_path"], ev["page"], ev.get("section"),
                 ev["source_text"], ev.get("table_markdown"), ev.get("source_hash"),
                 ev.get("row_index"), ev.get("column_name"), ev.get("raw_cell_text"),
                 ev["extraction_method"], ev.get("confidence")),
            )
        for field_path, status in p["field_status"].items():
            cur.execute("INSERT INTO field_status(source_document_id,field_path,status) VALUES (%s,%s,%s)",
                        (sid, field_path, status))
        self._rows(cur, "extraction_issues", sid, p["extraction_issues"],
                   ["field_path","issue_type","severity","message","page"])

    @staticmethod
    def _unique_class_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Guarantee UNIQUE(source_document_id, class_key) for frozen Standard JSON."""
        seen: dict[str, int] = {}
        unique_rows: list[dict[str, Any]] = []
        for row in rows:
            key = row.get("class_key") or ""
            if key in seen:
                seen[key] += 1
                row = {**row, "class_key": f"{key}__{seen[key] + 1}"}
            else:
                seen[key] = 0
            unique_rows.append(row)
        return unique_rows

    def list_standard_documents(self) -> list[dict[str, Any]]:
        """Read persisted Standard JSON for chatbot / query consumers."""
        self.ensure_schema()
        psycopg = self._psycopg()
        with psycopg.connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT document_id, filename, standard_json, updated_at
                    FROM source_documents
                    ORDER BY document_id
                    """
                )
                rows = cur.fetchall()
        return [
            {
                "document_id": document_id,
                "filename": filename,
                "standard_json": payload,
                "updated_at": updated_at,
            }
            for document_id, filename, payload, updated_at in rows
        ]

    def table_counts(self) -> dict[str, int]:
        self.ensure_schema()
        names = [
            "source_documents",
            "products",
            "product_classes",
            "risk_ratings",
            "fees",
            "sales_charges",
            "performance",
            "narratives",
            "evidence",
        ]
        psycopg = self._psycopg()
        counts: dict[str, int] = {}
        with psycopg.connect(self.database_url) as conn:
            with conn.cursor() as cur:
                for name in names:
                    cur.execute(f"SELECT COUNT(*) FROM {name}")
                    counts[name] = int(cur.fetchone()[0])
        return counts

    def get_standard_json(self, document_id: str) -> dict[str, Any] | None:
        self.ensure_schema()
        psycopg = self._psycopg()
        with psycopg.connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT standard_json FROM source_documents WHERE document_id = %s",
                    (document_id,),
                )
                row = cur.fetchone()
        return row[0] if row else None

    @staticmethod
    def _rows(cur, table: str, sid: int, rows: list[dict], fields: list[str]) -> None:
        if not rows:
            return
        cols = ",".join(["source_document_id", *fields])
        placeholders = ["%s"]
        json_fields = {"evidence_ids", "investment_regions", "investment_countries", "investment_sectors", "investment_styles"}
        placeholders.extend("%s::jsonb" if field in json_fields else "%s" for field in fields)
        sql = f"INSERT INTO {table} ({cols}) VALUES ({','.join(placeholders)})"
        for row in rows:
            values = [sid]
            for field in fields:
                value = row.get(field)
                if field in json_fields:
                    value = json.dumps(value or [], ensure_ascii=False)
                values.append(value)
            cur.execute(sql, values)
