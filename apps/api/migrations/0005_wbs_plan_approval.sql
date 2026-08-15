CREATE TABLE marketops.wbs_plan_approvals (
    id uuid PRIMARY KEY,
    organization_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    client_id uuid NOT NULL,
    project_id uuid NOT NULL,
    plan_id uuid NOT NULL,
    plan_version_id uuid NOT NULL,
    plan_version integer NOT NULL CHECK (plan_version > 0),
    schedule_snapshot_id uuid NOT NULL,
    plan_digest bytea NOT NULL CHECK (octet_length(plan_digest) = 32),
    schedule_digest bytea NOT NULL CHECK (octet_length(schedule_digest) = 32),
    reason text NOT NULL CHECK (
        length(btrim(reason)) BETWEEN 1 AND 1000
        AND reason = btrim(reason)
    ),
    approved_by uuid NOT NULL,
    approved_at timestamptz NOT NULL,
    CONSTRAINT wbs_plan_approvals_scope_unique
        UNIQUE (organization_id, workspace_id, client_id, project_id, id),
    CONSTRAINT wbs_plan_approvals_version_unique UNIQUE (plan_version_id),
    CONSTRAINT wbs_plan_approvals_target_unique
        UNIQUE (plan_version_id, schedule_snapshot_id),
    CONSTRAINT wbs_plan_approvals_plan_version_fk
        FOREIGN KEY (
            organization_id, workspace_id, client_id, project_id,
            plan_id, plan_version_id
        )
        REFERENCES marketops.wbs_plan_versions (
            organization_id, workspace_id, client_id, project_id,
            plan_id, id
        )
        ON DELETE RESTRICT,
    CONSTRAINT wbs_plan_approvals_schedule_fk
        FOREIGN KEY (
            organization_id, workspace_id, client_id, project_id,
            schedule_snapshot_id
        )
        REFERENCES marketops.schedule_snapshots (
            organization_id, workspace_id, client_id, project_id, id
        )
        ON DELETE RESTRICT
);

CREATE FUNCTION marketops.check_wbs_plan_approval_integrity()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog
AS $$
DECLARE
    version_record record;
    schedule_record record;
BEGIN
    SELECT * INTO version_record
    FROM marketops.wbs_plan_versions
    WHERE organization_id = NEW.organization_id
      AND workspace_id = NEW.workspace_id
      AND client_id = NEW.client_id
      AND project_id = NEW.project_id
      AND plan_id = NEW.plan_id
      AND id = NEW.plan_version_id;

    SELECT * INTO schedule_record
    FROM marketops.schedule_snapshots
    WHERE organization_id = NEW.organization_id
      AND workspace_id = NEW.workspace_id
      AND client_id = NEW.client_id
      AND project_id = NEW.project_id
      AND plan_id = NEW.plan_id
      AND plan_version_id = NEW.plan_version_id
      AND id = NEW.schedule_snapshot_id;

    IF version_record.id IS NULL
       OR schedule_record.id IS NULL
       OR version_record.plan_version IS DISTINCT FROM NEW.plan_version
       OR schedule_record.plan_version IS DISTINCT FROM NEW.plan_version
       OR version_record.plan_digest IS DISTINCT FROM NEW.plan_digest
       OR schedule_record.plan_digest IS DISTINCT FROM NEW.plan_digest
       OR schedule_record.schedule_digest IS DISTINCT FROM NEW.schedule_digest
       OR schedule_record.status IS DISTINCT FROM 'ready'
       OR version_record.created_by IS DISTINCT FROM NEW.approved_by
       OR schedule_record.created_by IS DISTINCT FROM NEW.approved_by THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = 'WBS plan approval target is inconsistent or not ready';
    END IF;
    RETURN NEW;
END;
$$;

CREATE CONSTRAINT TRIGGER wbs_plan_approvals_integrity
AFTER INSERT ON marketops.wbs_plan_approvals
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION marketops.check_wbs_plan_approval_integrity();

CREATE TRIGGER wbs_plan_approvals_immutable
BEFORE UPDATE OR DELETE ON marketops.wbs_plan_approvals
FOR EACH ROW EXECUTE FUNCTION marketops.reject_immutable_row_change();

CREATE TRIGGER wbs_plan_approvals_truncate_immutable
BEFORE TRUNCATE ON marketops.wbs_plan_approvals
FOR EACH STATEMENT EXECUTE FUNCTION marketops.reject_immutable_row_change();

ALTER TABLE marketops.wbs_plan_approvals ENABLE ROW LEVEL SECURITY;
ALTER TABLE marketops.wbs_plan_approvals FORCE ROW LEVEL SECURITY;

CREATE POLICY wbs_plan_approvals_actor_scope ON marketops.wbs_plan_approvals
USING (
    marketops.current_actor_id() IS NOT NULL
    AND workspace_id = marketops.current_workspace_id()
    AND client_id = marketops.current_client_id()
    AND project_id = marketops.current_project_id()
    AND approved_by = marketops.current_actor_id()
)
WITH CHECK (
    marketops.current_actor_id() IS NOT NULL
    AND workspace_id = marketops.current_workspace_id()
    AND client_id = marketops.current_client_id()
    AND project_id = marketops.current_project_id()
    AND approved_by = marketops.current_actor_id()
);

REVOKE ALL ON marketops.wbs_plan_approvals FROM PUBLIC;
REVOKE ALL ON FUNCTION marketops.check_wbs_plan_approval_integrity() FROM PUBLIC;
