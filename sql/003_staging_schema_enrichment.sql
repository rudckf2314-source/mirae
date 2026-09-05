ALTER TABLE source_documents ADD COLUMN IF NOT EXISTS revision_date DATE;
ALTER TABLE evidence ADD COLUMN IF NOT EXISTS table_markdown TEXT;
ALTER TABLE evidence ADD COLUMN IF NOT EXISTS source_hash TEXT;
ALTER TABLE evidence ADD COLUMN IF NOT EXISTS row_index INTEGER;
ALTER TABLE evidence ADD COLUMN IF NOT EXISTS column_name TEXT;
ALTER TABLE evidence ADD COLUMN IF NOT EXISTS raw_cell_text TEXT;
ALTER TABLE products ADD COLUMN IF NOT EXISTS product_key TEXT;

ALTER TABLE sales_charges ALTER COLUMN rate TYPE NUMERIC(20,8) USING rate::numeric;
ALTER TABLE sales_charges ALTER COLUMN rate_min TYPE NUMERIC(20,8) USING rate_min::numeric;
ALTER TABLE sales_charges ALTER COLUMN rate_max TYPE NUMERIC(20,8) USING rate_max::numeric;
ALTER TABLE fees ALTER COLUMN rate TYPE NUMERIC(20,8) USING rate::numeric;

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

CREATE INDEX IF NOT EXISTS idx_products_product_key ON products(product_key);
