-- Phase 2: tenant membership roles are mapped to permissions in the
-- application database.  Authorization must not depend on role-name checks.

BEGIN;

ALTER TABLE IF EXISTS longxin_app.tenant_memberships
    DROP CONSTRAINT IF EXISTS tenant_memberships_role_check;

ALTER TABLE IF EXISTS longxin_app.tenant_memberships
    ADD CONSTRAINT tenant_memberships_role_check
    CHECK (role IN (
        'tenant_owner',
        'tenant_admin',
        'skill_admin',
        'manager',
        'member',
        'viewer'
    ));

ALTER TABLE IF EXISTS longxin_app.tenant_memberships
    DROP CONSTRAINT IF EXISTS tenant_memberships_status_check;

ALTER TABLE IF EXISTS longxin_app.tenant_memberships
    ADD CONSTRAINT tenant_memberships_status_check
    CHECK (status IN ('active', 'disabled', 'removed', 'suspended'));

CREATE TABLE IF NOT EXISTS longxin_app.role_permission_mapping (
    role VARCHAR(32) NOT NULL,
    permission VARCHAR(128) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_role_permission_mapping_role_permission
        UNIQUE (role, permission)
);

INSERT INTO longxin_app.role_permission_mapping (role, permission)
VALUES
    ('tenant_owner', 'tenant:manage'),
    ('tenant_owner', 'skill:manage'),
    ('tenant_owner', 'task:use'),
    ('tenant_admin', 'tenant:manage'),
    ('tenant_admin', 'task:use'),
    ('skill_admin', 'skill:manage'),
    ('manager', 'task:use'),
    ('member', 'task:use'),
    ('viewer', 'task:use')
ON CONFLICT (role, permission) DO NOTHING;

COMMIT;
