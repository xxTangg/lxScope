-- lxScope application-owned persistence foundation.
--
-- This migration deliberately lives outside src/agentscope.  AgentScope's
-- own StorageBase tables remain unchanged.  The application layer should use
-- the longxin_app schema through repositories/services and must always apply
-- the current TenantContext when reading tenant-owned rows.
--
-- IDs are supplied by the application.  This keeps the migration independent
-- of optional PostgreSQL extensions such as pgcrypto and makes imports from
-- the current Redis-backed service explicit.

BEGIN;

CREATE SCHEMA IF NOT EXISTS longxin_app;

CREATE TABLE IF NOT EXISTS longxin_app.tenants (
    id UUID PRIMARY KEY,
    code VARCHAR(64) NOT NULL UNIQUE,
    name VARCHAR(128) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'suspended', 'disabled', 'deleted')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS longxin_app.users (
    id UUID PRIMARY KEY,
    username VARCHAR(128) NOT NULL UNIQUE,
    external_user_id VARCHAR(255) UNIQUE,
    email VARCHAR(255),
    password_hash TEXT,
    status VARCHAR(32) NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'locked', 'banned', 'deleted')),
    system_role VARCHAR(32) NOT NULL DEFAULT 'user',
    failed_attempts INTEGER NOT NULL DEFAULT 0 CHECK (failed_attempts >= 0),
    locked_until TIMESTAMPTZ,
    password_expires_at TIMESTAMPTZ,
    temporary_password_expires_at TIMESTAMPTZ,
    token_version INTEGER NOT NULL DEFAULT 0 CHECK (token_version >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Keeps the current string-based auth IDs resolvable while the application
-- gradually moves to UUID users and membership IDs.
ALTER TABLE longxin_app.users
    ADD COLUMN IF NOT EXISTS external_user_id VARCHAR(255);

CREATE UNIQUE INDEX IF NOT EXISTS uq_users_external_user_id
    ON longxin_app.users (external_user_id)
    WHERE external_user_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS longxin_app.tenant_memberships (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL REFERENCES longxin_app.tenants(id),
    user_id UUID NOT NULL REFERENCES longxin_app.users(id),
    role VARCHAR(32) NOT NULL DEFAULT 'member'
        CHECK (role IN ('tenant_owner', 'tenant_admin', 'manager', 'member', 'viewer')),
    status VARCHAR(32) NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'suspended', 'removed')),
    display_name VARCHAR(128),
    department_id UUID,
    joined_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_tenant_memberships_tenant_user UNIQUE (tenant_id, user_id),
    CONSTRAINT uq_tenant_memberships_tenant_id UNIQUE (tenant_id, id)
);

CREATE INDEX IF NOT EXISTS ix_tenant_memberships_user
    ON longxin_app.tenant_memberships (user_id, status);

CREATE TABLE IF NOT EXISTS longxin_app.tenant_settings (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL REFERENCES longxin_app.tenants(id) ON DELETE CASCADE,
    config_key VARCHAR(128) NOT NULL,
    config_value JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_tenant_settings_key UNIQUE (tenant_id, config_key)
);

CREATE TABLE IF NOT EXISTS longxin_app.tasks (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL REFERENCES longxin_app.tenants(id),
    owner_membership_id UUID NOT NULL,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    status VARCHAR(32) NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft', 'active', 'archived')),
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
    generation_status VARCHAR(32) NOT NULL DEFAULT 'idle'
        CHECK (generation_status IN ('idle', 'generating', 'succeeded', 'failed')),
    definition JSONB NOT NULL DEFAULT '{}'::jsonb,
    last_run_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    archived_at TIMESTAMPTZ,
    CONSTRAINT uq_tasks_tenant_id UNIQUE (tenant_id, id),
    CONSTRAINT fk_tasks_owner_membership
        FOREIGN KEY (tenant_id, owner_membership_id)
        REFERENCES longxin_app.tenant_memberships (tenant_id, id)
);

CREATE INDEX IF NOT EXISTS ix_tasks_tenant_created
    ON longxin_app.tasks (tenant_id, created_at DESC);

CREATE INDEX IF NOT EXISTS ix_tasks_owner
    ON longxin_app.tasks (tenant_id, owner_membership_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS longxin_app.task_runs (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL,
    task_id UUID NOT NULL,
    triggered_by_membership_id UUID,
    task_revision INTEGER NOT NULL CHECK (task_revision >= 1),
    status VARCHAR(32) NOT NULL
        CHECK (status IN ('queued', 'pending', 'running', 'succeeded', 'success', 'failed', 'canceled', 'cancelled', 'timed_out')),
    input JSONB,
    output JSONB,
    snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
    error_code VARCHAR(64),
    error_message TEXT,
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_task_runs_tenant_id UNIQUE (tenant_id, id),
    CONSTRAINT fk_task_runs_task
        FOREIGN KEY (tenant_id, task_id)
        REFERENCES longxin_app.tasks (tenant_id, id),
    CONSTRAINT fk_task_runs_triggered_by
        FOREIGN KEY (tenant_id, triggered_by_membership_id)
        REFERENCES longxin_app.tenant_memberships (tenant_id, id)
);

CREATE INDEX IF NOT EXISTS ix_task_runs_task_created
    ON longxin_app.task_runs (tenant_id, task_id, created_at DESC);

CREATE INDEX IF NOT EXISTS ix_task_runs_status
    ON longxin_app.task_runs (tenant_id, status, created_at DESC);

CREATE TABLE IF NOT EXISTS longxin_app.task_run_nodes (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL,
    run_id UUID NOT NULL,
    node_key VARCHAR(128) NOT NULL,
    node_type VARCHAR(32) NOT NULL,
    agent_name VARCHAR(255),
    tool_name VARCHAR(255),
    status VARCHAR(32) NOT NULL
        CHECK (status IN ('pending', 'running', 'succeeded', 'success', 'failed', 'canceled', 'cancelled')),
    input JSONB,
    output JSONB,
    error_message TEXT,
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT fk_task_run_nodes_run
        FOREIGN KEY (tenant_id, run_id)
        REFERENCES longxin_app.task_runs (tenant_id, id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS ix_task_run_nodes_run
    ON longxin_app.task_run_nodes (tenant_id, run_id, created_at);

CREATE TABLE IF NOT EXISTS longxin_app.task_events (
    id BIGSERIAL PRIMARY KEY,
    tenant_id UUID NOT NULL,
    run_id UUID NOT NULL,
    sequence BIGINT NOT NULL CHECK (sequence >= 1),
    event_type VARCHAR(64) NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_task_events_run_sequence UNIQUE (run_id, sequence),
    CONSTRAINT fk_task_events_run
        FOREIGN KEY (tenant_id, run_id)
        REFERENCES longxin_app.task_runs (tenant_id, id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS ix_task_events_run_sequence
    ON longxin_app.task_events (tenant_id, run_id, sequence);

CREATE TABLE IF NOT EXISTS longxin_app.task_artifacts (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL,
    run_id UUID NOT NULL,
    artifact_type VARCHAR(64) NOT NULL,
    file_name VARCHAR(255),
    storage_uri TEXT NOT NULL,
    mime_type VARCHAR(128),
    size_bytes BIGINT CHECK (size_bytes IS NULL OR size_bytes >= 0),
    checksum VARCHAR(128),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT fk_task_artifacts_run
        FOREIGN KEY (tenant_id, run_id)
        REFERENCES longxin_app.task_runs (tenant_id, id)
);

CREATE INDEX IF NOT EXISTS ix_task_artifacts_run
    ON longxin_app.task_artifacts (tenant_id, run_id, created_at DESC);

CREATE TABLE IF NOT EXISTS longxin_app.tenant_accounts (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL UNIQUE REFERENCES longxin_app.tenants(id),
    plan_code VARCHAR(64),
    status VARCHAR(32) NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'suspended', 'closed')),
    token_balance BIGINT NOT NULL DEFAULT 0 CHECK (token_balance >= 0),
    version BIGINT NOT NULL DEFAULT 0 CHECK (version >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS longxin_app.plan_orders (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL REFERENCES longxin_app.tenants(id),
    plan_code VARCHAR(64) NOT NULL,
    order_type VARCHAR(32) NOT NULL DEFAULT 'activation'
        CHECK (order_type IN ('activation', 'renewal', 'upgrade', 'downgrade')),
    status VARCHAR(32) NOT NULL
        CHECK (status IN ('pending', 'approved', 'rejected', 'cancelled')),
    requested_by_membership_id UUID,
    reviewed_by_membership_id UUID,
    note TEXT,
    decision_reason TEXT,
    allocated_tokens BIGINT NOT NULL DEFAULT 0,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT fk_plan_orders_requested_by
        FOREIGN KEY (tenant_id, requested_by_membership_id)
        REFERENCES longxin_app.tenant_memberships (tenant_id, id),
    CONSTRAINT fk_plan_orders_reviewed_by
        FOREIGN KEY (tenant_id, reviewed_by_membership_id)
        REFERENCES longxin_app.tenant_memberships (tenant_id, id)
);

CREATE INDEX IF NOT EXISTS ix_plan_orders_tenant_created
    ON longxin_app.plan_orders (tenant_id, created_at DESC);

CREATE INDEX IF NOT EXISTS ix_plan_orders_status
    ON longxin_app.plan_orders (tenant_id, status, created_at DESC);

CREATE TABLE IF NOT EXISTS longxin_app.recharge_orders (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL REFERENCES longxin_app.tenants(id),
    external_system VARCHAR(64),
    external_order_id VARCHAR(128),
    amount NUMERIC(18, 2) CHECK (amount IS NULL OR amount >= 0),
    credits BIGINT CHECK (credits IS NULL OR credits >= 0),
    status VARCHAR(32) NOT NULL
        CHECK (status IN ('pending', 'approved', 'rejected', 'cancelled', 'completed')),
    requested_by_membership_id UUID,
    reviewed_by_membership_id UUID,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT fk_recharge_orders_requested_by
        FOREIGN KEY (tenant_id, requested_by_membership_id)
        REFERENCES longxin_app.tenant_memberships (tenant_id, id),
    CONSTRAINT fk_recharge_orders_reviewed_by
        FOREIGN KEY (tenant_id, reviewed_by_membership_id)
        REFERENCES longxin_app.tenant_memberships (tenant_id, id)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_recharge_orders_external
    ON longxin_app.recharge_orders (external_system, external_order_id)
    WHERE external_system IS NOT NULL AND external_order_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS ix_recharge_orders_tenant_created
    ON longxin_app.recharge_orders (tenant_id, created_at DESC);

CREATE TABLE IF NOT EXISTS longxin_app.quota_ledger (
    id BIGSERIAL PRIMARY KEY,
    tenant_id UUID NOT NULL REFERENCES longxin_app.tenants(id),
    membership_id UUID,
    entry_type VARCHAR(64) NOT NULL,
    delta_tokens BIGINT NOT NULL,
    balance_after BIGINT NOT NULL CHECK (balance_after >= 0),
    source_type VARCHAR(64),
    source_id VARCHAR(128),
    idempotency_key VARCHAR(128),
    created_by_membership_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT fk_quota_ledger_membership
        FOREIGN KEY (tenant_id, membership_id)
        REFERENCES longxin_app.tenant_memberships (tenant_id, id),
    CONSTRAINT fk_quota_ledger_created_by
        FOREIGN KEY (tenant_id, created_by_membership_id)
        REFERENCES longxin_app.tenant_memberships (tenant_id, id)
);

CREATE INDEX IF NOT EXISTS ix_quota_ledger_tenant_time
    ON longxin_app.quota_ledger (tenant_id, created_at DESC);

CREATE UNIQUE INDEX IF NOT EXISTS uq_quota_ledger_idempotency
    ON longxin_app.quota_ledger (tenant_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS longxin_app.idempotency_records (
    id UUID PRIMARY KEY,
    tenant_id UUID REFERENCES longxin_app.tenants(id),
    namespace VARCHAR(64) NOT NULL,
    idempotency_key VARCHAR(128) NOT NULL,
    request_hash VARCHAR(128),
    response_code INTEGER,
    response_body JSONB,
    status VARCHAR(32) NOT NULL
        CHECK (status IN ('processing', 'completed', 'failed', 'expired')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_idempotency_global
    ON longxin_app.idempotency_records (namespace, idempotency_key)
    WHERE tenant_id IS NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_idempotency_tenant
    ON longxin_app.idempotency_records (tenant_id, namespace, idempotency_key)
    WHERE tenant_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS ix_idempotency_expiry
    ON longxin_app.idempotency_records (expires_at)
    WHERE expires_at IS NOT NULL;

CREATE TABLE IF NOT EXISTS longxin_app.audit_events (
    id BIGSERIAL PRIMARY KEY,
    tenant_id UUID REFERENCES longxin_app.tenants(id),
    actor_user_id UUID,
    actor_membership_id UUID,
    actor_type VARCHAR(32),
    action VARCHAR(128) NOT NULL,
    resource_type VARCHAR(64),
    resource_id VARCHAR(128),
    request_id VARCHAR(128),
    trace_id VARCHAR(128),
    result VARCHAR(32),
    ip_address INET,
    user_agent TEXT,
    summary TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT fk_audit_events_membership
        FOREIGN KEY (tenant_id, actor_membership_id)
        REFERENCES longxin_app.tenant_memberships (tenant_id, id)
);

CREATE INDEX IF NOT EXISTS ix_audit_events_tenant_time
    ON longxin_app.audit_events (tenant_id, created_at DESC);

CREATE INDEX IF NOT EXISTS ix_audit_events_actor
    ON longxin_app.audit_events (tenant_id, actor_membership_id, created_at DESC);

CREATE INDEX IF NOT EXISTS ix_audit_events_resource
    ON longxin_app.audit_events (tenant_id, resource_type, resource_id);

CREATE INDEX IF NOT EXISTS ix_audit_events_trace
    ON longxin_app.audit_events (trace_id);

CREATE TABLE IF NOT EXISTS longxin_app.resource_publications (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL REFERENCES longxin_app.tenants(id),
    resource_type VARCHAR(32) NOT NULL
        CHECK (resource_type IN ('skill', 'mcp', 'knowledge_base', 'agent', 'workflow')),
    source_record_id VARCHAR(128) NOT NULL,
    source_owner_membership_id UUID,
    name VARCHAR(255) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'disabled', 'deleted')),
    visibility VARCHAR(32) NOT NULL DEFAULT 'private'
        CHECK (visibility IN ('private', 'tenant', 'department', 'custom')),
    created_by_membership_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_resource_publication_source
        UNIQUE (tenant_id, resource_type, source_record_id),
    CONSTRAINT uq_resource_publication_tenant_id
        UNIQUE (tenant_id, id),
    CONSTRAINT fk_resource_publications_owner
        FOREIGN KEY (tenant_id, source_owner_membership_id)
        REFERENCES longxin_app.tenant_memberships (tenant_id, id),
    CONSTRAINT fk_resource_publications_created_by
        FOREIGN KEY (tenant_id, created_by_membership_id)
        REFERENCES longxin_app.tenant_memberships (tenant_id, id)
);

CREATE INDEX IF NOT EXISTS ix_resource_publications_tenant_visibility
    ON longxin_app.resource_publications (tenant_id, visibility, updated_at DESC);

CREATE TABLE IF NOT EXISTS longxin_app.resource_grants (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL REFERENCES longxin_app.tenants(id),
    publication_id UUID NOT NULL,
    subject_type VARCHAR(32) NOT NULL
        CHECK (subject_type IN ('user', 'membership', 'department', 'role', 'tenant')),
    subject_id UUID NOT NULL,
    permission VARCHAR(32) NOT NULL DEFAULT 'use',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_resource_grant UNIQUE (tenant_id, publication_id, subject_type, subject_id, permission),
    CONSTRAINT fk_resource_grants_publication
        FOREIGN KEY (tenant_id, publication_id)
        REFERENCES longxin_app.resource_publications (tenant_id, id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS longxin_app.integration_configs (
    id UUID PRIMARY KEY,
    tenant_id UUID REFERENCES longxin_app.tenants(id),
    integration_type VARCHAR(64) NOT NULL,
    name VARCHAR(128) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'disabled', 'error')),
    config JSONB NOT NULL DEFAULT '{}'::jsonb,
    secret_ref VARCHAR(255),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_integration_configs_tenant
    ON longxin_app.integration_configs (tenant_id, integration_type, name)
    WHERE tenant_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_integration_configs_global
    ON longxin_app.integration_configs (integration_type, name)
    WHERE tenant_id IS NULL;

CREATE TABLE IF NOT EXISTS longxin_app.system_configs (
    config_key VARCHAR(128) PRIMARY KEY,
    config_value JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Existing observability sinks are application-owned but predate the tenant
-- model.  Keep the columns nullable during the migration window so old rows
-- remain readable; new repository writes must populate both dimensions.
ALTER TABLE IF EXISTS public.skill_observability_events
    ADD COLUMN IF NOT EXISTS tenant_id UUID,
    ADD COLUMN IF NOT EXISTS membership_id UUID;

ALTER TABLE IF EXISTS public.project_observability_events
    ADD COLUMN IF NOT EXISTS tenant_id UUID,
    ADD COLUMN IF NOT EXISTS membership_id UUID;

DO $$
BEGIN
    IF to_regclass('public.skill_observability_events') IS NOT NULL THEN
        CREATE INDEX IF NOT EXISTS ix_skill_obs_tenant_time
            ON public.skill_observability_events (tenant_id, occurred_at DESC);
    END IF;
    IF to_regclass('public.project_observability_events') IS NOT NULL THEN
        CREATE INDEX IF NOT EXISTS ix_project_obs_tenant_time
            ON public.project_observability_events (tenant_id, occurred_at DESC);
    END IF;
END
$$;

COMMIT;
