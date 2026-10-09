-- Upgrade existing installations as well as fresh databases. Keep both
-- constraints in one transaction so no unconstrained intermediate state
-- is visible. Preserve every previously accepted lifecycle value.
DO $migration$
BEGIN
    ALTER TABLE longxin_app.task_runs
        DROP CONSTRAINT IF EXISTS task_runs_status_check;
    ALTER TABLE longxin_app.task_runs
        ADD CONSTRAINT task_runs_status_check
        CHECK (status IN (
            'queued', 'pending', 'running', 'awaiting_input',
            'succeeded', 'success', 'failed', 'canceled', 'cancelled',
            'timed_out'
        ));

    ALTER TABLE longxin_app.task_run_nodes
        DROP CONSTRAINT IF EXISTS task_run_nodes_status_check;
    ALTER TABLE longxin_app.task_run_nodes
        ADD CONSTRAINT task_run_nodes_status_check
        CHECK (status IN (
            'pending', 'running', 'awaiting_input', 'succeeded', 'success',
            'failed', 'canceled', 'cancelled'
        ));
END;
$migration$;
