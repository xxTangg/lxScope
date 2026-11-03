-- Application-owned PostgreSQL schema for the Skill analysis page.
--
-- This table is intentionally separate from AgentScope's generic Storage
-- schema.  It is append-only and contains bounded dimensions/counters only;
-- prompts, Skill Markdown, model output, and credentials must never be put
-- into it.

CREATE TABLE IF NOT EXISTS skill_observability_events (
    event_id BIGSERIAL PRIMARY KEY,
    event_name VARCHAR(64) NOT NULL,
    result VARCHAR(32) NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL,
    user_id VARCHAR(255) NOT NULL,
    agent_id VARCHAR(255) NOT NULL,
    session_id VARCHAR(255),
    skill_name VARCHAR(255),
    error_code VARCHAR(128),
    duration_seconds DOUBLE PRECISION,
    skill_count INTEGER,
    listed_skill_count INTEGER,
    skill_tool_available BOOLEAN,
    before_count INTEGER,
    visible_count INTEGER,
    after_count INTEGER,
    removed_count INTEGER,
    restored_count INTEGER,
    installed_count INTEGER,
    failure_count INTEGER
);

CREATE INDEX IF NOT EXISTS ix_skill_obs_user_occurred_at
    ON skill_observability_events (user_id, occurred_at DESC);

CREATE INDEX IF NOT EXISTS ix_skill_obs_event_occurred_at
    ON skill_observability_events (event_name, occurred_at DESC);

CREATE INDEX IF NOT EXISTS ix_skill_obs_skill_occurred_at
    ON skill_observability_events (skill_name, occurred_at DESC);

CREATE INDEX IF NOT EXISTS ix_skill_obs_session_occurred_at
    ON skill_observability_events (session_id, occurred_at DESC);
