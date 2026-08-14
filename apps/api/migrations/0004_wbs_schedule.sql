CREATE TABLE marketops.wbs_plans (
    id uuid PRIMARY KEY,
    organization_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    client_id uuid NOT NULL,
    project_id uuid NOT NULL,
    proposal_artifact_id uuid NOT NULL,
    proposal_version_id uuid NOT NULL,
    proposal_sha256 bytea NOT NULL CHECK (octet_length(proposal_sha256) = 32),
    source_review_run_id uuid NOT NULL,
    source_review_snapshot_id uuid NOT NULL,
    source_review_version integer NOT NULL CHECK (source_review_version > 0),
    created_by uuid NOT NULL,
    created_at timestamptz NOT NULL,
    CONSTRAINT wbs_plans_scope_unique
        UNIQUE (organization_id, workspace_id, client_id, project_id, id),
    CONSTRAINT wbs_plans_source_unique
        UNIQUE (
            workspace_id, client_id, project_id, created_by,
            source_review_run_id, source_review_version
        ),
    CONSTRAINT wbs_plans_project_fk
        FOREIGN KEY (organization_id, workspace_id, client_id, project_id)
        REFERENCES marketops.projects (organization_id, workspace_id, client_id, id)
        ON DELETE RESTRICT,
    CONSTRAINT wbs_plans_proposal_fk
        FOREIGN KEY (
            organization_id, workspace_id, client_id, project_id,
            proposal_artifact_id, proposal_version_id
        )
        REFERENCES marketops.artifact_versions (
            organization_id, workspace_id, client_id, project_id,
            artifact_id, id
        )
        ON DELETE RESTRICT,
    CONSTRAINT wbs_plans_review_run_fk
        FOREIGN KEY (
            organization_id, workspace_id, client_id, project_id, source_review_run_id
        )
        REFERENCES marketops.extraction_runs (
            organization_id, workspace_id, client_id, project_id, id
        )
        ON DELETE RESTRICT,
    CONSTRAINT wbs_plans_review_snapshot_fk
        FOREIGN KEY (
            organization_id, workspace_id, client_id, project_id,
            source_review_run_id, source_review_snapshot_id
        )
        REFERENCES marketops.review_snapshots (
            organization_id, workspace_id, client_id, project_id, run_id, id
        )
        ON DELETE RESTRICT
);

CREATE TABLE marketops.wbs_plan_versions (
    id uuid PRIMARY KEY,
    organization_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    client_id uuid NOT NULL,
    project_id uuid NOT NULL,
    plan_id uuid NOT NULL,
    plan_version integer NOT NULL CHECK (plan_version > 0),
    status text NOT NULL CHECK (status = 'draft'),
    schema_version integer NOT NULL CHECK (schema_version = 1),
    plan_payload jsonb NOT NULL CHECK (jsonb_typeof(plan_payload) = 'object'),
    plan_digest bytea NOT NULL CHECK (octet_length(plan_digest) = 32),
    task_count integer NOT NULL CHECK (task_count > 0),
    created_by uuid NOT NULL,
    created_at timestamptz NOT NULL,
    CONSTRAINT wbs_plan_versions_scope_unique
        UNIQUE (organization_id, workspace_id, client_id, project_id, id),
    CONSTRAINT wbs_plan_versions_plan_scope_unique
        UNIQUE (organization_id, workspace_id, client_id, project_id, plan_id, id),
    CONSTRAINT wbs_plan_versions_number_unique UNIQUE (plan_id, plan_version),
    CONSTRAINT wbs_plan_versions_plan_fk
        FOREIGN KEY (organization_id, workspace_id, client_id, project_id, plan_id)
        REFERENCES marketops.wbs_plans (
            organization_id, workspace_id, client_id, project_id, id
        )
        ON DELETE RESTRICT
);

CREATE TABLE marketops.wbs_tasks (
    organization_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    client_id uuid NOT NULL,
    project_id uuid NOT NULL,
    plan_id uuid NOT NULL,
    plan_version_id uuid NOT NULL,
    source_review_run_id uuid NOT NULL,
    task_id text NOT NULL CHECK (length(btrim(task_id)) > 0),
    ordinal integer NOT NULL CHECK (ordinal > 0),
    candidate_id uuid NOT NULL,
    kind text NOT NULL CHECK (kind IN ('deliverable', 'milestone')),
    title text NOT NULL CHECK (length(btrim(title)) > 0),
    duration_workdays integer NOT NULL CHECK (duration_workdays > 0),
    predecessors jsonb NOT NULL CHECK (jsonb_typeof(predecessors) = 'array'),
    owner_role text NOT NULL,
    planned_start date,
    planned_finish date,
    hard_deadline date,
    approved_buffer_workdays integer NOT NULL CHECK (approved_buffer_workdays >= 0),
    is_locked boolean NOT NULL,
    execution_status text NOT NULL CHECK (
        execution_status IN ('not_started', 'in_progress', 'blocked', 'completed', 'cancelled')
    ),
    source_version_id uuid NOT NULL,
    source_sha256 bytea NOT NULL CHECK (octet_length(source_sha256) = 32),
    task_payload jsonb NOT NULL CHECK (jsonb_typeof(task_payload) = 'object'),
    created_by uuid NOT NULL,
    created_at timestamptz NOT NULL,
    PRIMARY KEY (plan_version_id, task_id),
    CONSTRAINT wbs_tasks_ordinal_unique UNIQUE (plan_version_id, ordinal),
    CONSTRAINT wbs_tasks_candidate_unique UNIQUE (plan_version_id, candidate_id),
    CONSTRAINT wbs_tasks_plan_version_fk
        FOREIGN KEY (
            organization_id, workspace_id, client_id, project_id, plan_id, plan_version_id
        )
        REFERENCES marketops.wbs_plan_versions (
            organization_id, workspace_id, client_id, project_id, plan_id, id
        )
        ON DELETE RESTRICT,
    CONSTRAINT wbs_tasks_source_candidate_fk
        FOREIGN KEY (
            organization_id, workspace_id, client_id, project_id,
            source_review_run_id, candidate_id
        )
        REFERENCES marketops.extraction_candidates (
            organization_id, workspace_id, client_id, project_id, run_id, id
        )
        ON DELETE RESTRICT,
    CONSTRAINT wbs_tasks_source_version_fk
        FOREIGN KEY (source_version_id)
        REFERENCES marketops.artifact_versions (id)
        ON DELETE RESTRICT
);

CREATE TABLE marketops.schedule_snapshots (
    id uuid PRIMARY KEY,
    organization_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    client_id uuid NOT NULL,
    project_id uuid NOT NULL,
    plan_id uuid NOT NULL,
    plan_version_id uuid NOT NULL,
    plan_version integer NOT NULL CHECK (plan_version > 0),
    project_start date NOT NULL,
    holidays jsonb NOT NULL CHECK (jsonb_typeof(holidays) = 'array'),
    status text NOT NULL CHECK (status IN ('ready', 'needs_review')),
    plan_digest bytea NOT NULL CHECK (octet_length(plan_digest) = 32),
    schedule_digest bytea NOT NULL CHECK (octet_length(schedule_digest) = 32),
    schedule_payload jsonb NOT NULL CHECK (jsonb_typeof(schedule_payload) = 'object'),
    created_by uuid NOT NULL,
    created_at timestamptz NOT NULL,
    CONSTRAINT schedule_snapshots_scope_unique
        UNIQUE (organization_id, workspace_id, client_id, project_id, id),
    CONSTRAINT schedule_snapshots_digest_unique UNIQUE (plan_version_id, schedule_digest),
    CONSTRAINT schedule_snapshots_plan_version_fk
        FOREIGN KEY (
            organization_id, workspace_id, client_id, project_id, plan_id, plan_version_id
        )
        REFERENCES marketops.wbs_plan_versions (
            organization_id, workspace_id, client_id, project_id, plan_id, id
        )
        ON DELETE RESTRICT
);

CREATE FUNCTION marketops.check_wbs_plan_integrity()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog
AS $$
DECLARE
    run_record record;
    snapshot_record record;
BEGIN
    SELECT * INTO run_record
    FROM marketops.extraction_runs
    WHERE organization_id = NEW.organization_id
      AND workspace_id = NEW.workspace_id
      AND client_id = NEW.client_id
      AND project_id = NEW.project_id
      AND id = NEW.source_review_run_id;
    SELECT * INTO snapshot_record
    FROM marketops.review_snapshots
    WHERE organization_id = NEW.organization_id
      AND workspace_id = NEW.workspace_id
      AND client_id = NEW.client_id
      AND project_id = NEW.project_id
      AND run_id = NEW.source_review_run_id
      AND id = NEW.source_review_snapshot_id
      AND review_version = NEW.source_review_version;
    IF run_record.id IS NULL OR snapshot_record.id IS NULL
       OR run_record.proposal_artifact_id <> NEW.proposal_artifact_id
       OR run_record.proposal_version_id <> NEW.proposal_version_id
       OR run_record.proposal_sha256 <> NEW.proposal_sha256
       OR run_record.created_by <> NEW.created_by THEN
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'WBS source identity is inconsistent';
    END IF;
    IF NOT EXISTS (
        SELECT 1
        FROM marketops.projects AS project
        JOIN marketops.artifact_versions AS version
          ON version.organization_id = project.organization_id
         AND version.workspace_id = project.workspace_id
         AND version.client_id = project.client_id
         AND version.project_id = project.id
         AND version.artifact_id = project.approved_proposal_artifact_id
         AND version.id = project.approved_proposal_version_id
        WHERE project.organization_id = NEW.organization_id
          AND project.workspace_id = NEW.workspace_id
          AND project.client_id = NEW.client_id
          AND project.id = NEW.project_id
          AND version.artifact_id = NEW.proposal_artifact_id
          AND version.id = NEW.proposal_version_id
          AND version.sha256 = NEW.proposal_sha256
          AND version.approval_status = 'approved'
          AND version.proposal_version = project.approved_proposal_number
    ) THEN
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'WBS source is not the current approved proposal';
    END IF;
    IF EXISTS (
        SELECT 1 FROM marketops.review_snapshot_items
        WHERE snapshot_id = NEW.source_review_snapshot_id AND status = 'pending'
    ) THEN
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'WBS source review is incomplete';
    END IF;
    RETURN NEW;
END;
$$;

CREATE CONSTRAINT TRIGGER wbs_plans_integrity
AFTER INSERT ON marketops.wbs_plans
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION marketops.check_wbs_plan_integrity();

CREATE FUNCTION marketops.check_wbs_plan_version_integrity()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog
AS $$
DECLARE
    version_record record;
    plan_record record;
    actual_count integer;
BEGIN
    SELECT * INTO version_record
    FROM marketops.wbs_plan_versions
    WHERE id = CASE WHEN TG_TABLE_NAME = 'wbs_plan_versions' THEN NEW.id ELSE NEW.plan_version_id END;
    IF version_record IS NULL THEN RETURN NEW; END IF;
    SELECT * INTO plan_record FROM marketops.wbs_plans WHERE id = version_record.plan_id;
    IF plan_record IS NULL
       OR version_record.plan_payload->>'projectId' IS DISTINCT FROM version_record.project_id::text
       OR version_record.plan_payload->>'sourceReviewRunId' IS DISTINCT FROM plan_record.source_review_run_id::text
       OR version_record.plan_payload->>'sourceReviewSnapshotId' IS DISTINCT FROM plan_record.source_review_snapshot_id::text
       OR (version_record.plan_payload->>'sourceReviewVersion')::integer IS DISTINCT FROM plan_record.source_review_version
       OR version_record.plan_payload->'proposal'->>'versionId' IS DISTINCT FROM plan_record.proposal_version_id::text
       OR decode(version_record.plan_payload->'proposal'->>'sha256', 'hex') IS DISTINCT FROM plan_record.proposal_sha256
       OR jsonb_array_length(version_record.plan_payload->'tasks') IS DISTINCT FROM version_record.task_count THEN
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'WBS plan version payload is inconsistent';
    END IF;
    IF version_record.plan_version > 1 AND NOT EXISTS (
        SELECT 1 FROM marketops.wbs_plan_versions
        WHERE plan_id = version_record.plan_id AND plan_version = version_record.plan_version - 1
    ) THEN
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'WBS plan versions must be contiguous';
    END IF;
    SELECT count(*) INTO actual_count
    FROM marketops.wbs_tasks WHERE plan_version_id = version_record.id;
    IF actual_count <> version_record.task_count THEN
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'WBS task batch is incomplete';
    END IF;
    IF EXISTS (
        SELECT 1
        FROM marketops.wbs_tasks AS task
        WHERE task.plan_version_id = version_record.id
          AND (
              task.source_review_run_id IS DISTINCT FROM plan_record.source_review_run_id
              OR task.task_payload->>'taskId' IS DISTINCT FROM task.task_id
              OR task.task_payload->>'candidateId' IS DISTINCT FROM task.candidate_id::text
              OR task.task_payload->>'kind' IS DISTINCT FROM task.kind
              OR task.task_payload->>'title' IS DISTINCT FROM task.title
              OR task.task_payload->'sourceCitation'->>'sourceVersionId' IS DISTINCT FROM task.source_version_id::text
              OR decode(task.task_payload->'sourceCitation'->>'sourceSha256', 'hex') IS DISTINCT FROM task.source_sha256
              OR NOT EXISTS (
                  SELECT 1
                  FROM marketops.extraction_candidates AS candidate
                  JOIN marketops.review_snapshots AS snapshot
                    ON snapshot.organization_id = task.organization_id
                   AND snapshot.workspace_id = task.workspace_id
                   AND snapshot.client_id = task.client_id
                   AND snapshot.project_id = task.project_id
                   AND snapshot.run_id = plan_record.source_review_run_id
                   AND snapshot.id = plan_record.source_review_snapshot_id
                  JOIN marketops.review_snapshot_items AS item
                    ON item.organization_id = snapshot.organization_id
                   AND item.workspace_id = snapshot.workspace_id
                   AND item.client_id = snapshot.client_id
                   AND item.project_id = snapshot.project_id
                   AND item.run_id = snapshot.run_id
                   AND item.snapshot_id = snapshot.id
                   AND item.candidate_id = candidate.id
                  WHERE candidate.organization_id = task.organization_id
                    AND candidate.workspace_id = task.workspace_id
                    AND candidate.client_id = task.client_id
                    AND candidate.project_id = task.project_id
                    AND candidate.run_id = plan_record.source_review_run_id
                    AND candidate.id = task.candidate_id
                    AND item.status IN ('approve', 'modify')
                    AND candidate.kind IN ('deliverable', 'milestone')
                    AND task.kind IS NOT DISTINCT FROM candidate.kind
                    AND task.source_version_id IS NOT DISTINCT FROM candidate.source_version_id
                    AND task.source_sha256 IS NOT DISTINCT FROM candidate.source_sha256
                    AND EXISTS (
                        SELECT 1
                        FROM jsonb_array_elements(version_record.plan_payload->'tasks') AS payload_task
                        WHERE payload_task = task.task_payload
                    )
              )
          )
    ) THEN
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'WBS task is not backed by an accepted review candidate';
    END IF;
    RETURN NEW;
END;
$$;

CREATE CONSTRAINT TRIGGER wbs_plan_versions_integrity
AFTER INSERT ON marketops.wbs_plan_versions
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION marketops.check_wbs_plan_version_integrity();

CREATE CONSTRAINT TRIGGER wbs_tasks_integrity
AFTER INSERT ON marketops.wbs_tasks
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION marketops.check_wbs_plan_version_integrity();

CREATE FUNCTION marketops.check_schedule_snapshot_integrity()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog
AS $$
DECLARE
    version_record record;
BEGIN
    SELECT * INTO version_record
    FROM marketops.wbs_plan_versions
    WHERE id = NEW.plan_version_id
      AND plan_id = NEW.plan_id;
    IF version_record IS NULL
       OR version_record.plan_version IS DISTINCT FROM NEW.plan_version
       OR version_record.plan_digest IS DISTINCT FROM NEW.plan_digest
       OR NEW.schedule_payload->>'planDigest' IS DISTINCT FROM encode(NEW.plan_digest, 'hex')
       OR NEW.schedule_payload->>'scheduleDigest' IS DISTINCT FROM encode(NEW.schedule_digest, 'hex')
       OR NEW.schedule_payload->>'status' IS DISTINCT FROM NEW.status
       OR NEW.schedule_payload->>'planVersion' IS DISTINCT FROM NEW.plan_version::text
       OR NEW.schedule_payload->>'projectStart' IS DISTINCT FROM NEW.project_start::text
       OR NEW.schedule_payload->'calendar'->'holidays' IS DISTINCT FROM NEW.holidays THEN
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'schedule snapshot is inconsistent with its plan version';
    END IF;
    RETURN NEW;
END;
$$;

CREATE CONSTRAINT TRIGGER schedule_snapshots_integrity
AFTER INSERT ON marketops.schedule_snapshots
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION marketops.check_schedule_snapshot_integrity();

CREATE TRIGGER wbs_plans_immutable
BEFORE UPDATE OR DELETE ON marketops.wbs_plans
FOR EACH ROW EXECUTE FUNCTION marketops.reject_immutable_row_change();
CREATE TRIGGER wbs_plans_truncate_immutable
BEFORE TRUNCATE ON marketops.wbs_plans
FOR EACH STATEMENT EXECUTE FUNCTION marketops.reject_immutable_row_change();
CREATE TRIGGER wbs_plan_versions_immutable
BEFORE UPDATE OR DELETE ON marketops.wbs_plan_versions
FOR EACH ROW EXECUTE FUNCTION marketops.reject_immutable_row_change();
CREATE TRIGGER wbs_plan_versions_truncate_immutable
BEFORE TRUNCATE ON marketops.wbs_plan_versions
FOR EACH STATEMENT EXECUTE FUNCTION marketops.reject_immutable_row_change();
CREATE TRIGGER wbs_tasks_immutable
BEFORE UPDATE OR DELETE ON marketops.wbs_tasks
FOR EACH ROW EXECUTE FUNCTION marketops.reject_immutable_row_change();
CREATE TRIGGER wbs_tasks_truncate_immutable
BEFORE TRUNCATE ON marketops.wbs_tasks
FOR EACH STATEMENT EXECUTE FUNCTION marketops.reject_immutable_row_change();
CREATE TRIGGER schedule_snapshots_immutable
BEFORE UPDATE OR DELETE ON marketops.schedule_snapshots
FOR EACH ROW EXECUTE FUNCTION marketops.reject_immutable_row_change();
CREATE TRIGGER schedule_snapshots_truncate_immutable
BEFORE TRUNCATE ON marketops.schedule_snapshots
FOR EACH STATEMENT EXECUTE FUNCTION marketops.reject_immutable_row_change();

ALTER TABLE marketops.wbs_plans ENABLE ROW LEVEL SECURITY;
ALTER TABLE marketops.wbs_plans FORCE ROW LEVEL SECURITY;
ALTER TABLE marketops.wbs_plan_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE marketops.wbs_plan_versions FORCE ROW LEVEL SECURITY;
ALTER TABLE marketops.wbs_tasks ENABLE ROW LEVEL SECURITY;
ALTER TABLE marketops.wbs_tasks FORCE ROW LEVEL SECURITY;
ALTER TABLE marketops.schedule_snapshots ENABLE ROW LEVEL SECURITY;
ALTER TABLE marketops.schedule_snapshots FORCE ROW LEVEL SECURITY;

CREATE POLICY wbs_plans_actor_scope ON marketops.wbs_plans
USING (
    marketops.current_actor_id() IS NOT NULL
    AND workspace_id = marketops.current_workspace_id()
    AND client_id = marketops.current_client_id()
    AND project_id = marketops.current_project_id()
    AND created_by = marketops.current_actor_id()
)
WITH CHECK (
    marketops.current_actor_id() IS NOT NULL
    AND workspace_id = marketops.current_workspace_id()
    AND client_id = marketops.current_client_id()
    AND project_id = marketops.current_project_id()
    AND created_by = marketops.current_actor_id()
);

CREATE POLICY wbs_plan_versions_actor_scope ON marketops.wbs_plan_versions
USING (
    marketops.current_actor_id() IS NOT NULL
    AND workspace_id = marketops.current_workspace_id()
    AND client_id = marketops.current_client_id()
    AND project_id = marketops.current_project_id()
    AND created_by = marketops.current_actor_id()
)
WITH CHECK (
    marketops.current_actor_id() IS NOT NULL
    AND workspace_id = marketops.current_workspace_id()
    AND client_id = marketops.current_client_id()
    AND project_id = marketops.current_project_id()
    AND created_by = marketops.current_actor_id()
);

CREATE POLICY wbs_tasks_actor_scope ON marketops.wbs_tasks
USING (
    marketops.current_actor_id() IS NOT NULL
    AND workspace_id = marketops.current_workspace_id()
    AND client_id = marketops.current_client_id()
    AND project_id = marketops.current_project_id()
    AND created_by = marketops.current_actor_id()
)
WITH CHECK (
    marketops.current_actor_id() IS NOT NULL
    AND workspace_id = marketops.current_workspace_id()
    AND client_id = marketops.current_client_id()
    AND project_id = marketops.current_project_id()
    AND created_by = marketops.current_actor_id()
);

CREATE POLICY schedule_snapshots_actor_scope ON marketops.schedule_snapshots
USING (
    marketops.current_actor_id() IS NOT NULL
    AND workspace_id = marketops.current_workspace_id()
    AND client_id = marketops.current_client_id()
    AND project_id = marketops.current_project_id()
    AND created_by = marketops.current_actor_id()
)
WITH CHECK (
    marketops.current_actor_id() IS NOT NULL
    AND workspace_id = marketops.current_workspace_id()
    AND client_id = marketops.current_client_id()
    AND project_id = marketops.current_project_id()
    AND created_by = marketops.current_actor_id()
);

REVOKE ALL ON marketops.wbs_plans FROM PUBLIC;
REVOKE ALL ON marketops.wbs_plan_versions FROM PUBLIC;
REVOKE ALL ON marketops.wbs_tasks FROM PUBLIC;
REVOKE ALL ON marketops.schedule_snapshots FROM PUBLIC;
REVOKE ALL ON FUNCTION marketops.check_wbs_plan_integrity() FROM PUBLIC;
REVOKE ALL ON FUNCTION marketops.check_wbs_plan_version_integrity() FROM PUBLIC;
REVOKE ALL ON FUNCTION marketops.check_schedule_snapshot_integrity() FROM PUBLIC;
