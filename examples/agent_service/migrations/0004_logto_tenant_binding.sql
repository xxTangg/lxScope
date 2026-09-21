-- Map provisioned lxScope tenants to external Logto Organizations.
--
-- The internal UUID tenant_id remains the application boundary.  An
-- external organization is only a lookup key; this migration does not
-- provision tenants from Logto.

BEGIN;

ALTER TABLE IF EXISTS longxin_app.tenants
    ADD COLUMN IF NOT EXISTS identity_provider VARCHAR(32) NOT NULL DEFAULT 'local',
    ADD COLUMN IF NOT EXISTS external_org_id VARCHAR(255);

CREATE UNIQUE INDEX IF NOT EXISTS uq_tenants_identity_provider_external_org
    ON longxin_app.tenants (identity_provider, external_org_id)
    WHERE external_org_id IS NOT NULL;

COMMIT;
