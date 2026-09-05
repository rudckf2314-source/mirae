CREATE TABLE IF NOT EXISTS source_documents (
    id BIGSERIAL PRIMARY KEY,
    document_id TEXT NOT NULL UNIQUE,
    filename TEXT NOT NULL,
    document_type TEXT NOT NULL,
    as_of_date DATE,
    effective_date DATE,
    revision_date DATE,
    page_count INTEGER,
    file_hash TEXT UNIQUE,
    schema_version TEXT NOT NULL,
    standard_json JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS products (
    id BIGSERIAL PRIMARY KEY,
    source_document_id BIGINT NOT NULL REFERENCES source_documents(id) ON DELETE CASCADE,
    product_key TEXT NOT NULL,
    official_name TEXT NOT NULL,
    kofia_fund_code TEXT,
    manager_name TEXT,
    legal_form TEXT,
    asset_type TEXT,
    is_open_end BOOLEAN,
    is_additional BOOLEAN,
    is_class_type BOOLEAN,
    is_master_feeder BOOLEAN,
    is_convertible BOOLEAN,
    is_high_complexity_product BOOLEAN,
    inception_date DATE
);

CREATE TABLE IF NOT EXISTS risk_ratings (
    id BIGSERIAL PRIMARY KEY,
    source_document_id BIGINT NOT NULL REFERENCES source_documents(id) ON DELETE CASCADE,
    grade INTEGER NOT NULL CHECK (grade BETWEEN 1 AND 6),
    label TEXT,
    method TEXT,
    as_of_date DATE,
    evidence_ids JSONB NOT NULL DEFAULT '[]'::jsonb
);

CREATE TABLE IF NOT EXISTS product_classes (
    id BIGSERIAL PRIMARY KEY,
    source_document_id BIGINT NOT NULL REFERENCES source_documents(id) ON DELETE CASCADE,
    class_key TEXT NOT NULL,
    class_name TEXT NOT NULL,
    kofia_fund_code TEXT,
    sales_charge_type TEXT,
    channel TEXT,
    pension_type TEXT,
    eligibility_text TEXT,
    is_online BOOLEAN,
    is_cdsc_class BOOLEAN,
    is_conversion_enabled BOOLEAN,
    inception_date DATE,
    evidence_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    UNIQUE(source_document_id, class_key)
);

CREATE TABLE IF NOT EXISTS sales_charges (
    id BIGSERIAL PRIMARY KEY,
    source_document_id BIGINT NOT NULL REFERENCES source_documents(id) ON DELETE CASCADE,
    class_key TEXT NOT NULL,
    charge_type TEXT NOT NULL,
    rate NUMERIC(20,8),
    rate_min NUMERIC(20,8),
    rate_max NUMERIC(20,8),
    rate_unit TEXT NOT NULL,
    rate_condition TEXT NOT NULL,
    base_amount TEXT,
    timing TEXT,
    condition_text TEXT,
    evidence_ids JSONB NOT NULL DEFAULT '[]'::jsonb
);

CREATE TABLE IF NOT EXISTS fees (
    id BIGSERIAL PRIMARY KEY,
    source_document_id BIGINT NOT NULL REFERENCES source_documents(id) ON DELETE CASCADE,
    class_key TEXT NOT NULL,
    fee_type TEXT NOT NULL,
    rate NUMERIC(20,8),
    unit TEXT NOT NULL,
    as_of_date DATE,
    effective_from DATE,
    effective_to DATE,
    evidence_ids JSONB NOT NULL DEFAULT '[]'::jsonb
);

CREATE TABLE IF NOT EXISTS class_transition_rules (
    id BIGSERIAL PRIMARY KEY,
    source_document_id BIGINT NOT NULL REFERENCES source_documents(id) ON DELETE CASCADE,
    from_class TEXT NOT NULL,
    to_class TEXT NOT NULL,
    automatic BOOLEAN NOT NULL,
    trigger_type TEXT,
    minimum_holding_months INTEGER,
    condition_text TEXT,
    evidence_ids JSONB NOT NULL DEFAULT '[]'::jsonb
);

CREATE TABLE IF NOT EXISTS fund_conversion_rules (
    id BIGSERIAL PRIMARY KEY,
    source_document_id BIGINT NOT NULL REFERENCES source_documents(id) ON DELETE CASCADE,
    source_class TEXT,
    target_product_name TEXT NOT NULL,
    target_class TEXT,
    conversion_allowed BOOLEAN NOT NULL,
    conversion_fee_rate DOUBLE PRECISION,
    conversion_count_limit INTEGER,
    condition_text TEXT,
    evidence_ids JSONB NOT NULL DEFAULT '[]'::jsonb
);

CREATE TABLE IF NOT EXISTS master_feeder_relations (
    id BIGSERIAL PRIMARY KEY,
    source_document_id BIGINT NOT NULL REFERENCES source_documents(id) ON DELETE CASCADE,
    master_product_name TEXT NOT NULL,
    minimum_investment_ratio DOUBLE PRECISION,
    maximum_investment_ratio DOUBLE PRECISION,
    ratio_unit TEXT NOT NULL,
    evidence_ids JSONB NOT NULL DEFAULT '[]'::jsonb
);

CREATE TABLE IF NOT EXISTS hedging_policies (
    id BIGSERIAL PRIMARY KEY,
    source_document_id BIGINT NOT NULL REFERENCES source_documents(id) ON DELETE CASCADE,
    subject TEXT NOT NULL,
    fund_name TEXT,
    is_hedged BOOLEAN,
    hedge_ratio_min_pct DOUBLE PRECISION,
    hedge_ratio_max_pct DOUBLE PRECISION,
    hedge_from_currency TEXT,
    hedge_to_currency TEXT,
    residual_fx_exposure TEXT,
    policy_text TEXT,
    as_of_date DATE,
    status TEXT NOT NULL,
    evidence_ids JSONB NOT NULL DEFAULT '[]'::jsonb
);

CREATE TABLE IF NOT EXISTS performance (
    id BIGSERIAL PRIMARY KEY,
    source_document_id BIGINT NOT NULL REFERENCES source_documents(id) ON DELETE CASCADE,
    class_key TEXT,
    metric TEXT NOT NULL,
    period TEXT NOT NULL,
    return_type TEXT,
    value DOUBLE PRECISION,
    unit TEXT NOT NULL,
    as_of_date DATE,
    period_start DATE,
    period_end DATE,
    evidence_ids JSONB NOT NULL DEFAULT '[]'::jsonb
);

CREATE TABLE IF NOT EXISTS capital_flows (
    id BIGSERIAL PRIMARY KEY,
    source_document_id BIGINT NOT NULL REFERENCES source_documents(id) ON DELETE CASCADE,
    class_key TEXT,
    period_start DATE NOT NULL,
    period_end DATE NOT NULL,
    opening_units DOUBLE PRECISION,
    opening_amount DOUBLE PRECISION,
    subscription_units DOUBLE PRECISION,
    subscription_amount DOUBLE PRECISION,
    redemption_units DOUBLE PRECISION,
    redemption_amount DOUBLE PRECISION,
    ending_units DOUBLE PRECISION,
    ending_amount DOUBLE PRECISION,
    unit_scale TEXT,
    units_scale TEXT,
    amount_scale TEXT,
    currency TEXT NOT NULL,
    evidence_ids JSONB NOT NULL DEFAULT '[]'::jsonb
);

CREATE TABLE IF NOT EXISTS financial_metrics (
    id BIGSERIAL PRIMARY KEY,
    source_document_id BIGINT NOT NULL REFERENCES source_documents(id) ON DELETE CASCADE,
    class_key TEXT,
    metric_type TEXT NOT NULL,
    raw_value DOUBLE PRECISION,
    raw_unit TEXT,
    normalized_value_krw DOUBLE PRECISION,
    as_of_date DATE,
    period_start DATE,
    period_end DATE,
    evidence_ids JSONB NOT NULL DEFAULT '[]'::jsonb
);

CREATE TABLE IF NOT EXISTS narratives (
    id BIGSERIAL PRIMARY KEY,
    source_document_id BIGINT NOT NULL REFERENCES source_documents(id) ON DELETE CASCADE,
    narrative_type TEXT NOT NULL,
    subject TEXT NOT NULL,
    text TEXT NOT NULL,
    evidence_ids JSONB NOT NULL DEFAULT '[]'::jsonb
);

CREATE TABLE IF NOT EXISTS evidence (
    evidence_id TEXT PRIMARY KEY,
    source_document_id BIGINT NOT NULL REFERENCES source_documents(id) ON DELETE CASCADE,
    field_path TEXT NOT NULL,
    page INTEGER NOT NULL,
    section TEXT,
    source_text TEXT NOT NULL,
    table_markdown TEXT,
    source_hash TEXT,
    row_index INTEGER,
    column_name TEXT,
    raw_cell_text TEXT,
    extraction_method TEXT NOT NULL,
    confidence DOUBLE PRECISION
);

CREATE TABLE IF NOT EXISTS field_status (
    id BIGSERIAL PRIMARY KEY,
    source_document_id BIGINT NOT NULL REFERENCES source_documents(id) ON DELETE CASCADE,
    field_path TEXT NOT NULL,
    status TEXT NOT NULL,
    UNIQUE(source_document_id, field_path)
);

CREATE TABLE IF NOT EXISTS extraction_issues (
    id BIGSERIAL PRIMARY KEY,
    source_document_id BIGINT NOT NULL REFERENCES source_documents(id) ON DELETE CASCADE,
    field_path TEXT NOT NULL,
    issue_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    message TEXT NOT NULL,
    page INTEGER
);

CREATE TABLE IF NOT EXISTS investment_profiles (
    id BIGSERIAL PRIMARY KEY,
    source_document_id BIGINT NOT NULL REFERENCES source_documents(id) ON DELETE CASCADE,
    primary_asset TEXT,
    investment_regions JSONB NOT NULL DEFAULT '[]'::jsonb,
    investment_countries JSONB NOT NULL DEFAULT '[]'::jsonb,
    investment_sectors JSONB NOT NULL DEFAULT '[]'::jsonb,
    investment_styles JSONB NOT NULL DEFAULT '[]'::jsonb,
    benchmark_name TEXT,
    equity_ratio_min NUMERIC(20,8), equity_ratio_max NUMERIC(20,8),
    bond_ratio_min NUMERIC(20,8), bond_ratio_max NUMERIC(20,8),
    overseas_asset_ratio_min NUMERIC(20,8), overseas_asset_ratio_max NUMERIC(20,8),
    derivative_usage BOOLEAN,
    recommended_horizon TEXT,
    principal_loss_possible BOOLEAN,
    evidence_ids JSONB NOT NULL DEFAULT '[]'::jsonb
);

CREATE TABLE IF NOT EXISTS liquidity_rules (
    id BIGSERIAL PRIMARY KEY,
    source_document_id BIGINT NOT NULL REFERENCES source_documents(id) ON DELETE CASCADE,
    class_key TEXT,
    transaction_type TEXT NOT NULL,
    cutoff_time TEXT,
    pricing_day_offset INTEGER,
    payment_day_offset INTEGER,
    redemption_fee NUMERIC(20,8),
    restriction_text TEXT,
    evidence_ids JSONB NOT NULL DEFAULT '[]'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_products_fund_code ON products(kofia_fund_code);
CREATE INDEX IF NOT EXISTS idx_products_product_key ON products(product_key);
CREATE INDEX IF NOT EXISTS idx_products_name ON products(official_name);
CREATE INDEX IF NOT EXISTS idx_classes_key ON product_classes(class_key);
CREATE INDEX IF NOT EXISTS idx_fees_class_key ON fees(class_key);
CREATE INDEX IF NOT EXISTS idx_performance_class_key ON performance(class_key);
CREATE INDEX IF NOT EXISTS idx_narratives_type ON narratives(narrative_type);
CREATE INDEX IF NOT EXISTS idx_field_status_status ON field_status(status);
