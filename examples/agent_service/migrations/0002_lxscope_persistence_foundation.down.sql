-- Rollback for 0002_lxscope_persistence_foundation.sql.
-- Run only after the application has stopped writing to longxin_app.

BEGIN;

DROP INDEX IF EXISTS public.ix_project_obs_tenant_time;
DROP INDEX IF EXISTS public.ix_skill_obs_tenant_time;

ALTER TABLE IF EXISTS public.project_observability_events
    DROP COLUMN IF EXISTS membership_id,
    DROP COLUMN IF EXISTS tenant_id;

ALTER TABLE IF EXISTS public.skill_observability_events
    DROP COLUMN IF EXISTS membership_id,
    DROP COLUMN IF EXISTS tenant_id;

DROP TABLE IF EXISTS longxin_app.system_configs;
DROP TABLE IF EXISTS longxin_app.integration_configs;
DROP TABLE IF EXISTS longxin_app.resource_grants;
DROP TABLE IF EXISTS longxin_app.resource_publications;
DROP TABLE IF EXISTS longxin_app.audit_events;
DROP TABLE IF EXISTS longxin_app.idempotency_records;
DROP TABLE IF EXISTS longxin_app.quota_ledger;
DROP TABLE IF EXISTS longxin_app.recharge_orders;
DROP TABLE IF EXISTS longxin_app.plan_orders;
DROP TABLE IF EXISTS longxin_app.tenant_accounts;
DROP TABLE IF EXISTS longxin_app.task_artifacts;
DROP TABLE IF EXISTS longxin_app.task_events;
DROP TABLE IF EXISTS longxin_app.task_run_nodes;
DROP TABLE IF EXISTS longxin_app.task_runs;
DROP TABLE IF EXISTS longxin_app.tasks;
DROP TABLE IF EXISTS longxin_app.tenant_settings;
DROP TABLE IF EXISTS longxin_app.tenant_memberships;
DROP TABLE IF EXISTS longxin_app.users;
DROP TABLE IF EXISTS longxin_app.tenants;
DROP TABLE IF EXISTS longxin_app.schema_migrations;
DROP SCHEMA IF EXISTS longxin_app;

COMMIT;
