CREATE TABLE marketops.wbs_task_execution_updates (
    id uuid PRIMARY KEY,
    organization_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    client_id uuid NOT NULL,
    project_id uuid NOT NULL,
    plan_id uuid NOT NULL,
    plan_version_id uuid NOT NULL,
    task_id text NOT NULL,
    sequence_no integer NOT NULL CHECK (sequence_no > 0),
    status text NOT NULL CHECK (
        status IN ('not_started', 'in_progress', 'blocked', 'completed', 'cancelled')
    ),
    blocker_reason text,
    actual_start date,
    actual_finish date,
    note text,
    updated_by uuid NOT NULL,
    updated_at timestamptz NOT NULL,
    UNIQUE (plan_version_id, task_id, sequence_no),
    FOREIGN KEY (plan_version_id, task_id)
    REFERENCES marketops.wbs_tasks (plan_version_id, task_id)
    ON DELETE RESTRICT,
    CHECK (
        status <> 'blocked'
        OR (
            blocker_reason IS NOT NULL
            AND length(btrim(blocker_reason)) BETWEEN 1 AND 2000
            AND blocker_reason = btrim(blocker_reason)
        )
    ),
    CHECK (status = 'blocked' OR blocker_reason IS NULL),
    CHECK (status <> 'in_progress' OR actual_start IS NOT NULL),
    CHECK (
        status <> 'completed'
        OR (actual_start IS NOT NULL AND actual_finish IS NOT NULL)
    ),
    CHECK (
        note IS NULL
        OR (
            length(btrim(note)) BETWEEN 1 AND 4000
            AND note = btrim(note)
        )
    ),
    CHECK (
        actual_finish IS NULL
        OR actual_start IS NULL
        OR actual_finish >= actual_start
    )
);

CREATE INDEX wbs_task_execution_updates_latest_idx
    ON marketops.wbs_task_execution_updates (
        plan_version_id, task_id, sequence_no DESC
    );

CREATE TRIGGER wbs_task_execution_updates_immutable
BEFORE UPDATE OR DELETE ON marketops.wbs_task_execution_updates
FOR EACH ROW EXECUTE FUNCTION marketops.reject_immutable_row_change();

CREATE TRIGGER wbs_task_execution_updates_truncate_immutable
BEFORE TRUNCATE ON marketops.wbs_task_execution_updates
FOR EACH STATEMENT EXECUTE FUNCTION marketops.reject_immutable_row_change();

ALTER TABLE marketops.wbs_task_execution_updates ENABLE ROW LEVEL SECURITY;
ALTER TABLE marketops.wbs_task_execution_updates FORCE ROW LEVEL SECURITY;

CREATE POLICY wbs_task_execution_updates_actor_scope
ON marketops.wbs_task_execution_updates
USING (
    marketops.current_actor_id() IS NOT NULL
    AND workspace_id = marketops.current_workspace_id()
    AND client_id = marketops.current_client_id()
    AND project_id = marketops.current_project_id()
    AND updated_by = marketops.current_actor_id()
)
WITH CHECK (
    marketops.current_actor_id() IS NOT NULL
    AND workspace_id = marketops.current_workspace_id()
    AND client_id = marketops.current_client_id()
    AND project_id = marketops.current_project_id()
    AND updated_by = marketops.current_actor_id()
);

REVOKE ALL ON marketops.wbs_task_execution_updates FROM PUBLIC;
