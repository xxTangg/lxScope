-- Phase 4: extend the existing tenant quota ledger with model usage detail.
-- No new quota table is introduced; tenant_accounts/quota_ledger remain the
-- balance and usage source of truth.

BEGIN;

ALTER TABLE longxin_app.quota_ledger
    ADD COLUMN IF NOT EXISTS model VARCHAR(128),
    ADD COLUMN IF NOT EXISTS tokens BIGINT,
    ADD COLUMN IF NOT EXISTS cost NUMERIC(20, 8);

CREATE INDEX IF NOT EXISTS ix_quota_ledger_tenant_usage
    ON longxin_app.quota_ledger (tenant_id, entry_type, created_at DESC);

COMMIT;
