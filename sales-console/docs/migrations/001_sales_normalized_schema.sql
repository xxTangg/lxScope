-- Additive PostgreSQL schema proposal for sales-console.
-- This file is intentionally not applied by the refactor task. It creates new
-- tables beside sales_console_json_store and never drops or rewrites source data.

BEGIN;

CREATE TABLE IF NOT EXISTS sales_console_schema_migrations (
  version INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  checksum TEXT NOT NULL,
  applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS sales_staff (
  staff_id TEXT PRIMARY KEY,
  username TEXT NOT NULL UNIQUE,
  password_salt TEXT NOT NULL,
  password_hash TEXT NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('admin', 'operator')),
  status TEXT NOT NULL CHECK (status IN ('active', 'disabled')),
  created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS sales_customers (
  customer_id TEXT PRIMARY KEY,
  system_id TEXT NOT NULL DEFAULT '',
  name TEXT NOT NULL,
  environment TEXT NOT NULL CHECK (environment IN ('production', 'test', 'unclassified')),
  protocol TEXT NOT NULL CHECK (protocol IN ('http', 'https')),
  configured_ip TEXT NOT NULL DEFAULT '',
  port INTEGER NOT NULL CHECK (port BETWEEN 1 AND 65535),
  base_url TEXT,
  internal_base_url TEXT,
  contact TEXT NOT NULL DEFAULT '',
  notes TEXT NOT NULL DEFAULT '',
  api_token TEXT NOT NULL,
  total_recharged NUMERIC(18, 2) NOT NULL DEFAULT 0,
  status TEXT NOT NULL CHECK (status IN ('active', 'disabled')),
  last_seen_ip TEXT,
  last_seen_at TIMESTAMPTZ,
  last_report JSONB,
  created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS sales_customers_system_id_uq
  ON sales_customers (system_id)
  WHERE system_id <> '';

CREATE TABLE IF NOT EXISTS sales_recharge_orders (
  order_id TEXT PRIMARY KEY,
  request_id TEXT,
  customer_id TEXT NOT NULL,
  requested_at TIMESTAMPTZ,
  method TEXT NOT NULL CHECK (method IN ('code', 'online')),
  status TEXT NOT NULL CHECK (status IN ('pending', 'issued', 'approved', 'rejected')),
  requested_amount NUMERIC(18, 2),
  amount NUMERIC(18, 2),
  tokens BIGINT,
  note TEXT,
  code TEXT,
  expires_at TIMESTAMPTZ,
  decision_reason TEXT,
  delivered BOOLEAN NOT NULL DEFAULT FALSE,
  decision_acknowledged BOOLEAN NOT NULL DEFAULT FALSE,
  delivered_at TIMESTAMPTZ,
  delivery_attempts INTEGER NOT NULL DEFAULT 0,
  last_delivery_at TIMESTAMPTZ,
  ack_operation_id TEXT,
  redemption_operation_id TEXT,
  ledger_id TEXT,
  created_at TIMESTAMPTZ NOT NULL,
  processed_at TIMESTAMPTZ,
  processed_by TEXT
);

CREATE INDEX IF NOT EXISTS sales_recharge_orders_customer_status_created_idx
  ON sales_recharge_orders (customer_id, status, created_at DESC);

CREATE TABLE IF NOT EXISTS sales_usage_reports (
  report_id TEXT PRIMARY KEY,
  customer_id TEXT NOT NULL,
  system_id TEXT NOT NULL,
  pool_tokens BIGINT NOT NULL CHECK (pool_tokens >= 0),
  total_recharged NUMERIC(18, 2) NOT NULL CHECK (total_recharged >= 0),
  cumulative_consumed BIGINT,
  cumulative_credits BIGINT,
  app_version TEXT NOT NULL DEFAULT '',
  client_reported_at TIMESTAMPTZ,
  reported_at TIMESTAMPTZ NOT NULL,
  observed_ip TEXT
);

CREATE INDEX IF NOT EXISTS sales_usage_reports_customer_reported_idx
  ON sales_usage_reports (customer_id, reported_at DESC);

CREATE TABLE IF NOT EXISTS sales_console_settings (
  settings_id SMALLINT PRIMARY KEY CHECK (settings_id = 1),
  token_exchange_rate INTEGER NOT NULL CHECK (token_exchange_rate > 0),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS sales_audit_events (
  event_id TEXT PRIMARY KEY,
  occurred_at TIMESTAMPTZ NOT NULL,
  actor TEXT NOT NULL DEFAULT '',
  method TEXT NOT NULL DEFAULT '',
  path TEXT NOT NULL DEFAULT '',
  source_ip TEXT
);

CREATE INDEX IF NOT EXISTS sales_audit_events_occurred_idx
  ON sales_audit_events (occurred_at DESC);

CREATE TABLE IF NOT EXISTS sales_idempotency_records (
  scope TEXT NOT NULL,
  idempotency_key TEXT NOT NULL,
  request_hash TEXT NOT NULL,
  status_code INTEGER NOT NULL,
  response_body JSONB,
  operation_id TEXT,
  created_at TIMESTAMPTZ NOT NULL,
  expires_at TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (scope, idempotency_key)
);

ALTER TABLE sales_idempotency_records
  ADD COLUMN IF NOT EXISTS operation_id TEXT;

CREATE INDEX IF NOT EXISTS sales_idempotency_records_expiry_idx
  ON sales_idempotency_records (expires_at);

CREATE TABLE IF NOT EXISTS sales_sessions (
  session_token_hash TEXT PRIMARY KEY,
  staff_id TEXT NOT NULL,
  expires_at TIMESTAMPTZ NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS sales_sessions_expiry_idx ON sales_sessions (expires_at);

CREATE TABLE IF NOT EXISTS sales_release_artifacts (
  artifact_type TEXT NOT NULL CHECK (artifact_type IN ('app', 'core')),
  version TEXT NOT NULL,
  file_name TEXT NOT NULL,
  sha256 TEXT NOT NULL,
  size_bytes BIGINT NOT NULL CHECK (size_bytes >= 0),
  uploaded_at TIMESTAMPTZ NOT NULL,
  uploaded_by TEXT NOT NULL,
  PRIMARY KEY (artifact_type, version)
);

CREATE TABLE IF NOT EXISTS sales_upgrade_tasks (
  task_id TEXT PRIMARY KEY,
  artifact_type TEXT NOT NULL CHECK (artifact_type IN ('app', 'core')),
  version TEXT NOT NULL,
  customer_ids JSONB NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'completed', 'failed')),
  result JSONB,
  attempt_count INTEGER NOT NULL DEFAULT 0,
  available_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  lease_owner TEXT,
  lease_expires_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS sales_upgrade_tasks_claim_idx
  ON sales_upgrade_tasks (status, available_at, lease_expires_at);

COMMIT;
