-- Keep already-applied 0002 installations compatible with the transitional
-- string user IDs used by the current Redis-backed auth service.

ALTER TABLE IF EXISTS longxin_app.users
    ADD COLUMN IF NOT EXISTS external_user_id VARCHAR(255);

CREATE UNIQUE INDEX IF NOT EXISTS uq_users_external_user_id
    ON longxin_app.users (external_user_id)
    WHERE external_user_id IS NOT NULL;
