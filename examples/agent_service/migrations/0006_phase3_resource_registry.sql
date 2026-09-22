-- Phase 3: application-owned resource ownership and tenant visibility.
-- AgentScope Core remains user-scoped; this table is the lxScope business
-- mapping used before an application service delegates to Core.

BEGIN;

CREATE TABLE IF NOT EXISTS longxin_app.resource_registry (
    resource_type VARCHAR(32) NOT NULL,
    resource_id VARCHAR(255) NOT NULL,
    scope VARCHAR(16) NOT NULL,
    tenant_id UUID,
    owner_membership_id VARCHAR(255),
    visibility VARCHAR(16) NOT NULL DEFAULT 'all',
    status VARCHAR(16) NOT NULL DEFAULT 'active',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT resource_registry_scope_check
        CHECK (scope IN ('platform', 'tenant', 'personal')),
    CONSTRAINT resource_registry_visibility_check
        CHECK (visibility IN ('all', 'selected', 'none')),
    CONSTRAINT resource_registry_status_check
        CHECK (status IN ('active', 'disabled', 'removed')),
    CONSTRAINT resource_registry_owner_check
        CHECK (
            (scope = 'platform' AND tenant_id IS NULL)
            OR (scope = 'tenant' AND tenant_id IS NOT NULL)
            OR (scope = 'personal' AND owner_membership_id IS NOT NULL)
        )
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_resource_registry_platform
    ON longxin_app.resource_registry (resource_type, resource_id)
    WHERE scope = 'platform';

CREATE UNIQUE INDEX IF NOT EXISTS uq_resource_registry_tenant
    ON longxin_app.resource_registry (resource_type, resource_id, tenant_id)
    WHERE scope = 'tenant';

CREATE UNIQUE INDEX IF NOT EXISTS uq_resource_registry_personal
    ON longxin_app.resource_registry (resource_type, resource_id, owner_membership_id)
    WHERE scope = 'personal';

CREATE INDEX IF NOT EXISTS ix_resource_registry_tenant_type_status
    ON longxin_app.resource_registry (tenant_id, resource_type, status);

COMMIT;
