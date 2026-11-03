-- One-time, additive backfill from the existing JSONB aggregate store.
-- Prerequisite: 001_sales_normalized_schema.sql has been reviewed and applied.
-- Source rows remain intact. Conflicts are left in the source for investigation;
-- do not cut over until source and destination counts and values have been compared.

BEGIN;

INSERT INTO sales_staff (
  staff_id, username, password_salt, password_hash, role, status, created_at, updated_at
)
SELECT
  item->>'id',
  item->>'username',
  item->>'passwordSalt',
  item->>'passwordHash',
  item->>'role',
  item->>'status',
  to_timestamp(COALESCE(NULLIF(item->>'createdAt', '')::DOUBLE PRECISION, 0) / 1000.0),
  to_timestamp(COALESCE(NULLIF(item->>'updatedAt', '')::DOUBLE PRECISION, 0) / 1000.0)
FROM sales_console_json_store AS source
CROSS JOIN LATERAL jsonb_array_elements(COALESCE(source.value, '[]'::jsonb)) AS rows(item)
WHERE source.key = 'staff'
ON CONFLICT DO NOTHING;

INSERT INTO sales_customers (
  customer_id, system_id, name, environment, protocol, configured_ip, port, base_url,
  internal_base_url, contact, notes, api_token, total_recharged, status, last_seen_ip,
  last_seen_at, last_report, created_at, updated_at
)
SELECT
  item->>'id',
  COALESCE(item->>'systemId', ''),
  COALESCE(item->>'name', ''),
  COALESCE(item->>'environment', 'unclassified'),
  COALESCE(item->>'protocol', 'http'),
  COALESCE(item->>'ip', ''),
  COALESCE(NULLIF(item->>'port', '')::INTEGER, 3000),
  item->>'baseUrl',
  item->>'internalBaseUrl',
  COALESCE(item->>'contact', ''),
  COALESCE(item->>'notes', ''),
  COALESCE(item->>'apiToken', ''),
  COALESCE(NULLIF(item->>'totalRecharged', '')::NUMERIC(18, 2), 0),
  COALESCE(item->>'status', 'active'),
  item->>'lastSeenIP',
  CASE WHEN NULLIF(item->>'lastSeenAt', '') IS NULL THEN NULL
       ELSE to_timestamp((item->>'lastSeenAt')::DOUBLE PRECISION / 1000.0) END,
  item->'lastReport',
  to_timestamp(COALESCE(NULLIF(item->>'createdAt', '')::DOUBLE PRECISION, 0) / 1000.0),
  to_timestamp(COALESCE(NULLIF(item->>'updatedAt', '')::DOUBLE PRECISION, 0) / 1000.0)
FROM sales_console_json_store AS source
CROSS JOIN LATERAL jsonb_array_elements(COALESCE(source.value, '[]'::jsonb)) AS rows(item)
WHERE source.key = 'customers'
ON CONFLICT DO NOTHING;

INSERT INTO sales_recharge_orders (
  order_id, request_id, customer_id, requested_at, method, status, requested_amount, amount,
  tokens, note, code, expires_at, decision_reason, delivered, decision_acknowledged,
  delivered_at, delivery_attempts, last_delivery_at, ack_operation_id,
  redemption_operation_id, ledger_id, created_at, processed_at, processed_by
)
SELECT
  item->>'id',
  item->>'requestID',
  item->>'customerID',
  CASE WHEN NULLIF(item->>'requestedAt', '') IS NULL THEN NULL
       ELSE to_timestamp((item->>'requestedAt')::DOUBLE PRECISION / 1000.0) END,
  item->>'method',
  item->>'status',
  NULLIF(item->>'requestedAmount', '')::NUMERIC(18, 2),
  NULLIF(item->>'amount', '')::NUMERIC(18, 2),
  NULLIF(item->>'tokens', '')::BIGINT,
  item->>'note',
  item->>'code',
  CASE WHEN NULLIF(item->>'expiresAt', '') IS NULL THEN NULL
       ELSE to_timestamp((item->>'expiresAt')::DOUBLE PRECISION / 1000.0) END,
  item->>'decisionReason',
  COALESCE((item->>'delivered')::BOOLEAN, FALSE),
  COALESCE((item->>'decisionAcknowledged')::BOOLEAN, FALSE),
  CASE WHEN NULLIF(item->>'deliveredAt', '') IS NULL THEN NULL
       ELSE to_timestamp((item->>'deliveredAt')::DOUBLE PRECISION / 1000.0) END,
  COALESCE(NULLIF(item->>'deliveryAttempts', '')::INTEGER, 0),
  CASE WHEN NULLIF(item->>'lastDeliveryAt', '') IS NULL THEN NULL
       ELSE to_timestamp((item->>'lastDeliveryAt')::DOUBLE PRECISION / 1000.0) END,
  item->>'ackOperationID',
  item->>'redemptionOperationID',
  item->>'ledgerID',
  to_timestamp(COALESCE(NULLIF(item->>'createdAt', '')::DOUBLE PRECISION, 0) / 1000.0),
  CASE WHEN NULLIF(item->>'processedAt', '') IS NULL THEN NULL
       ELSE to_timestamp((item->>'processedAt')::DOUBLE PRECISION / 1000.0) END,
  item->>'processedBy'
FROM sales_console_json_store AS source
CROSS JOIN LATERAL jsonb_array_elements(COALESCE(source.value, '[]'::jsonb)) AS rows(item)
WHERE source.key = 'orders'
ON CONFLICT DO NOTHING;

INSERT INTO sales_usage_reports (
  report_id, customer_id, system_id, pool_tokens, total_recharged, cumulative_consumed,
  cumulative_credits, app_version, client_reported_at, reported_at, observed_ip
)
SELECT
  COALESCE(NULLIF(item->>'id', ''), md5(item::TEXT || ordinality::TEXT)),
  item->>'customerID',
  COALESCE(item->>'systemId', ''),
  COALESCE(NULLIF(item->>'poolTokens', '')::BIGINT, 0),
  COALESCE(NULLIF(item->>'totalRecharged', '')::NUMERIC(18, 2), 0),
  NULLIF(item->>'cumulativeConsumed', '')::BIGINT,
  NULLIF(item->>'cumulativeCredits', '')::BIGINT,
  COALESCE(item->>'appVersion', ''),
  CASE WHEN NULLIF(item->>'clientReportedAt', '') IS NULL THEN NULL
       ELSE to_timestamp((item->>'clientReportedAt')::DOUBLE PRECISION / 1000.0) END,
  to_timestamp(COALESCE(NULLIF(item->>'reportedAt', '')::DOUBLE PRECISION, 0) / 1000.0),
  item->>'observedIP'
FROM sales_console_json_store AS source
CROSS JOIN LATERAL jsonb_array_elements(COALESCE(source.value, '[]'::jsonb)) WITH ORDINALITY AS rows(item, ordinality)
WHERE source.key = 'reports'
ON CONFLICT DO NOTHING;

INSERT INTO sales_console_settings (settings_id, token_exchange_rate)
SELECT 1, COALESCE(NULLIF(source.value->>'tokenExchangeRate', '')::INTEGER, 41841)
FROM sales_console_json_store AS source
WHERE source.key = 'settings'
ON CONFLICT DO NOTHING;

INSERT INTO sales_audit_events (event_id, occurred_at, actor, method, path, source_ip)
SELECT
  COALESCE(NULLIF(item->>'id', ''), md5(item::TEXT || ordinality::TEXT)),
  to_timestamp(COALESCE(NULLIF(item->>'at', '')::DOUBLE PRECISION, 0) / 1000.0),
  COALESCE(item->>'actor', ''),
  COALESCE(item->>'method', ''),
  COALESCE(item->>'path', ''),
  item->>'sourceIP'
FROM sales_console_json_store AS source
CROSS JOIN LATERAL jsonb_array_elements(COALESCE(source.value, '[]'::jsonb)) WITH ORDINALITY AS rows(item, ordinality)
WHERE source.key = 'audit'
ON CONFLICT DO NOTHING;

INSERT INTO sales_idempotency_records (
  scope, idempotency_key, request_hash, status_code, response_body, operation_id, created_at, expires_at
)
SELECT
  item->>'scope',
  item->>'key',
  item->>'requestHash',
  COALESCE(NULLIF(item->>'statusCode', '')::INTEGER, 0),
  item->'responseBody',
  item->>'operationID',
  to_timestamp(COALESCE(NULLIF(item->>'createdAt', '')::DOUBLE PRECISION, 0) / 1000.0),
  to_timestamp(COALESCE(NULLIF(item->>'expiresAt', '')::DOUBLE PRECISION, 0) / 1000.0)
FROM sales_console_json_store AS source
CROSS JOIN LATERAL jsonb_array_elements(COALESCE(source.value, '[]'::jsonb)) AS rows(item)
WHERE source.key = 'idempotency'
ON CONFLICT DO NOTHING;

COMMIT;
