-- Phase 5: make the audit resource dimension explicit and keep all
-- application-owned observability sinks tenant-indexed.

BEGIN;

ALTER TABLE IF EXISTS longxin_app.audit_events
    ADD COLUMN IF NOT EXISTS resource JSONB NOT NULL DEFAULT '{}'::jsonb;

CREATE INDEX IF NOT EXISTS ix_audit_events_tenant_action_time
    ON longxin_app.audit_events (tenant_id, action, created_at DESC);

ALTER TABLE IF EXISTS public.project_observability_events
    ADD COLUMN IF NOT EXISTS tenant_id UUID,
    ADD COLUMN IF NOT EXISTS membership_id UUID;

ALTER TABLE IF EXISTS public.skill_observability_events
    ADD COLUMN IF NOT EXISTS tenant_id UUID,
    ADD COLUMN IF NOT EXISTS membership_id UUID;

DO $$
BEGIN
    IF to_regclass('public.project_observability_events') IS NOT NULL THEN
        CREATE INDEX IF NOT EXISTS ix_project_obs_tenant_component_time
            ON public.project_observability_events
            (tenant_id, component, occurred_at DESC);
    END IF;
    IF to_regclass('public.skill_observability_events') IS NOT NULL THEN
        CREATE INDEX IF NOT EXISTS ix_skill_obs_tenant_skill_time
            ON public.skill_observability_events
            (tenant_id, skill_name, occurred_at DESC);
    END IF;
END
$$;

COMMIT;
