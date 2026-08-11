CREATE TABLE marketops.extraction_runs (
    id uuid PRIMARY KEY,
    organization_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    client_id uuid NOT NULL,
    project_id uuid NOT NULL,
    proposal_artifact_id uuid NOT NULL,
    proposal_version_id uuid NOT NULL,
    proposal_version integer NOT NULL CHECK (proposal_version > 0),
    proposal_sha256 bytea NOT NULL CHECK (octet_length(proposal_sha256) = 32),
    candidate_count integer NOT NULL CHECK (candidate_count > 0),
    created_by uuid NOT NULL,
    created_at timestamptz NOT NULL,
    CONSTRAINT extraction_runs_scope_unique
        UNIQUE (organization_id, workspace_id, client_id, project_id, id),
    CONSTRAINT extraction_runs_project_fk
        FOREIGN KEY (organization_id, workspace_id, client_id, project_id)
        REFERENCES marketops.projects (organization_id, workspace_id, client_id, id)
        ON DELETE RESTRICT,
    CONSTRAINT extraction_runs_proposal_fk
        FOREIGN KEY (
            organization_id,
            workspace_id,
            client_id,
            project_id,
            proposal_artifact_id,
            proposal_version_id
        )
        REFERENCES marketops.artifact_versions (
            organization_id,
            workspace_id,
            client_id,
            project_id,
            artifact_id,
            id
        )
        ON DELETE RESTRICT
);

CREATE TABLE marketops.extraction_candidates (
    id uuid NOT NULL,
    organization_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    client_id uuid NOT NULL,
    project_id uuid NOT NULL,
    run_id uuid NOT NULL,
    ordinal integer NOT NULL CHECK (ordinal > 0),
    kind text NOT NULL CHECK (kind IN ('deliverable', 'milestone', 'constraint', 'assumption')),
    candidate_text text NOT NULL CHECK (length(btrim(candidate_text)) > 0),
    classification text NOT NULL CHECK (classification IN ('fact', 'hypothesis')),
    confidence double precision NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    source_version_id uuid NOT NULL,
    source_sha256 bytea NOT NULL CHECK (octet_length(source_sha256) = 32),
    source_location jsonb NOT NULL CHECK (jsonb_typeof(source_location) = 'object'),
    section_path jsonb NOT NULL CHECK (jsonb_typeof(section_path) = 'array'),
    source_quote text NOT NULL CHECK (length(btrim(source_quote)) > 0),
    review_status text NOT NULL DEFAULT 'pending' CHECK (review_status = 'pending'),
    created_by uuid NOT NULL,
    created_at timestamptz NOT NULL,
    PRIMARY KEY (run_id, id),
    CONSTRAINT extraction_candidates_scope_unique
        UNIQUE (organization_id, workspace_id, client_id, project_id, run_id, id),
    CONSTRAINT extraction_candidates_ordinal_unique UNIQUE (run_id, ordinal),
    CONSTRAINT extraction_candidates_run_fk
        FOREIGN KEY (organization_id, workspace_id, client_id, project_id, run_id)
        REFERENCES marketops.extraction_runs (
            organization_id, workspace_id, client_id, project_id, id
        )
        ON DELETE RESTRICT,
    CONSTRAINT extraction_candidates_source_fk
        FOREIGN KEY (source_version_id)
        REFERENCES marketops.artifact_versions (id)
        ON DELETE RESTRICT
);

CREATE TABLE marketops.review_snapshots (
    id uuid PRIMARY KEY,
    organization_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    client_id uuid NOT NULL,
    project_id uuid NOT NULL,
    run_id uuid NOT NULL,
    review_version integer NOT NULL CHECK (review_version > 0),
    created_by uuid NOT NULL,
    created_at timestamptz NOT NULL,
    CONSTRAINT review_snapshots_scope_unique
        UNIQUE (organization_id, workspace_id, client_id, project_id, run_id, id),
    CONSTRAINT review_snapshots_version_unique UNIQUE (run_id, review_version),
    CONSTRAINT review_snapshots_run_fk
        FOREIGN KEY (organization_id, workspace_id, client_id, project_id, run_id)
        REFERENCES marketops.extraction_runs (
            organization_id, workspace_id, client_id, project_id, id
        )
        ON DELETE RESTRICT
);

CREATE TABLE marketops.review_snapshot_items (
    organization_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    client_id uuid NOT NULL,
    project_id uuid NOT NULL,
    run_id uuid NOT NULL,
    snapshot_id uuid NOT NULL,
    candidate_id uuid NOT NULL,
    ordinal integer NOT NULL CHECK (ordinal > 0),
    status text NOT NULL CHECK (status IN ('pending', 'approve', 'modify', 'reject')),
    replacement_text text,
    created_by uuid NOT NULL,
    created_at timestamptz NOT NULL,
    PRIMARY KEY (snapshot_id, candidate_id),
    CONSTRAINT review_snapshot_items_ordinal_unique UNIQUE (snapshot_id, ordinal),
    CONSTRAINT review_snapshot_items_replacement_consistent CHECK (
        (status = 'modify' AND replacement_text IS NOT NULL AND length(btrim(replacement_text)) > 0)
        OR (status <> 'modify' AND replacement_text IS NULL)
    ),
    CONSTRAINT review_snapshot_items_snapshot_fk
        FOREIGN KEY (
            organization_id, workspace_id, client_id, project_id, run_id, snapshot_id
        )
        REFERENCES marketops.review_snapshots (
            organization_id, workspace_id, client_id, project_id, run_id, id
        )
        ON DELETE RESTRICT,
    CONSTRAINT review_snapshot_items_candidate_fk
        FOREIGN KEY (
            organization_id, workspace_id, client_id, project_id, run_id, candidate_id
        )
        REFERENCES marketops.extraction_candidates (
            organization_id, workspace_id, client_id, project_id, run_id, id
        )
        ON DELETE RESTRICT
);

CREATE TABLE marketops.review_decisions (
    id uuid PRIMARY KEY,
    organization_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    client_id uuid NOT NULL,
    project_id uuid NOT NULL,
    run_id uuid NOT NULL,
    review_version integer NOT NULL CHECK (review_version > 1),
    candidate_id uuid NOT NULL,
    action text NOT NULL CHECK (action IN ('approve', 'modify', 'reject')),
    reason text NOT NULL CHECK (length(btrim(reason)) > 0),
    comment text,
    replacement_text text,
    actor_id uuid NOT NULL,
    created_at timestamptz NOT NULL,
    CONSTRAINT review_decisions_version_unique UNIQUE (run_id, review_version),
    CONSTRAINT review_decisions_action_consistent CHECK (
        (action = 'modify' AND replacement_text IS NOT NULL AND length(btrim(replacement_text)) > 0)
        OR (action <> 'modify' AND replacement_text IS NULL)
    ),
    CONSTRAINT review_decisions_snapshot_fk
        FOREIGN KEY (run_id, review_version)
        REFERENCES marketops.review_snapshots (run_id, review_version)
        ON DELETE RESTRICT,
    CONSTRAINT review_decisions_candidate_fk
        FOREIGN KEY (
            organization_id, workspace_id, client_id, project_id, run_id, candidate_id
        )
        REFERENCES marketops.extraction_candidates (
            organization_id, workspace_id, client_id, project_id, run_id, id
        )
        ON DELETE RESTRICT
);

CREATE FUNCTION marketops.check_extraction_run_integrity()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog
AS $$
DECLARE
    checked_run_id uuid;
    run_record marketops.extraction_runs%ROWTYPE;
BEGIN
    IF TG_TABLE_NAME = 'extraction_runs' THEN
        checked_run_id := NEW.id;
    ELSE
        checked_run_id := NEW.run_id;
    END IF;

    SELECT * INTO run_record
    FROM marketops.extraction_runs
    WHERE id = checked_run_id;
    IF NOT FOUND THEN
        RETURN NEW;
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM marketops.projects AS project
        JOIN marketops.artifacts AS artifact
          ON artifact.organization_id = project.organization_id
         AND artifact.workspace_id = project.workspace_id
         AND artifact.client_id = project.client_id
         AND artifact.project_id = project.id
         AND artifact.id = project.approved_proposal_artifact_id
        JOIN marketops.artifact_versions AS version
          ON version.organization_id = artifact.organization_id
         AND version.workspace_id = artifact.workspace_id
         AND version.client_id = artifact.client_id
         AND version.project_id = artifact.project_id
         AND version.artifact_id = artifact.id
         AND version.id = project.approved_proposal_version_id
        WHERE project.id = run_record.project_id
          AND project.organization_id = run_record.organization_id
          AND project.workspace_id = run_record.workspace_id
          AND project.client_id = run_record.client_id
          AND artifact.kind = 'proposal'
          AND artifact.id = run_record.proposal_artifact_id
          AND version.id = run_record.proposal_version_id
          AND version.proposal_version = run_record.proposal_version
          AND version.approval_status = 'approved'
          AND version.sha256 = run_record.proposal_sha256
    ) THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = 'extraction run source is not the current approved proposal';
    END IF;

    IF (
        SELECT count(*)
        FROM marketops.extraction_candidates AS candidate
        WHERE candidate.run_id = checked_run_id
    ) <> run_record.candidate_count THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = 'extraction run candidate batch is incomplete';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM marketops.extraction_candidates AS candidate
        WHERE candidate.run_id = checked_run_id
          AND (
              candidate.source_version_id <> run_record.proposal_version_id
              OR candidate.source_sha256 <> run_record.proposal_sha256
          )
    ) THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = 'candidate citation differs from the extraction run source';
    END IF;
    RETURN NEW;
END;
$$;

CREATE CONSTRAINT TRIGGER extraction_runs_integrity
AFTER INSERT ON marketops.extraction_runs
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION marketops.check_extraction_run_integrity();

CREATE CONSTRAINT TRIGGER extraction_candidates_integrity
AFTER INSERT ON marketops.extraction_candidates
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION marketops.check_extraction_run_integrity();

CREATE FUNCTION marketops.check_review_snapshot_integrity()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog
AS $$
DECLARE
    checked_run_id uuid;
    checked_version integer;
    checked_snapshot_id uuid;
    expected_count integer;
    changed_count integer;
BEGIN
    IF TG_TABLE_NAME = 'review_snapshots' THEN
        checked_run_id := NEW.run_id;
        checked_version := NEW.review_version;
        checked_snapshot_id := NEW.id;
    ELSIF TG_TABLE_NAME = 'review_snapshot_items' THEN
        checked_snapshot_id := NEW.snapshot_id;
        SELECT run_id, review_version
        INTO checked_run_id, checked_version
        FROM marketops.review_snapshots
        WHERE id = checked_snapshot_id;
    ELSE
        checked_run_id := NEW.run_id;
        checked_version := NEW.review_version;
        SELECT id INTO checked_snapshot_id
        FROM marketops.review_snapshots
        WHERE run_id = checked_run_id
          AND review_version = checked_version;
    END IF;

    SELECT candidate_count INTO expected_count
    FROM marketops.extraction_runs
    WHERE id = checked_run_id;
    IF expected_count IS NULL OR checked_snapshot_id IS NULL THEN
        RETURN NEW;
    END IF;

    IF (
        SELECT count(*)
        FROM marketops.review_snapshot_items
        WHERE snapshot_id = checked_snapshot_id
    ) <> expected_count THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = 'review snapshot is incomplete';
    END IF;

    IF checked_version = 1 THEN
        IF EXISTS (
            SELECT 1 FROM marketops.review_snapshot_items
            WHERE snapshot_id = checked_snapshot_id
              AND (status <> 'pending' OR replacement_text IS NOT NULL)
        ) OR EXISTS (
            SELECT 1 FROM marketops.review_decisions
            WHERE run_id = checked_run_id AND review_version = 1
        ) THEN
            RAISE EXCEPTION USING
                ERRCODE = '23514',
                MESSAGE = 'initial review snapshot must be fully pending';
        END IF;
        RETURN NEW;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM marketops.review_snapshots
        WHERE run_id = checked_run_id
          AND review_version = checked_version - 1
    ) THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = 'review snapshot versions must be contiguous';
    END IF;

    SELECT count(*) INTO changed_count
    FROM marketops.review_snapshot_items AS current_item
    JOIN marketops.review_snapshots AS previous_snapshot
      ON previous_snapshot.run_id = checked_run_id
     AND previous_snapshot.review_version = checked_version - 1
    JOIN marketops.review_snapshot_items AS previous_item
      ON previous_item.snapshot_id = previous_snapshot.id
     AND previous_item.candidate_id = current_item.candidate_id
    WHERE current_item.snapshot_id = checked_snapshot_id
      AND (
          current_item.status IS DISTINCT FROM previous_item.status
          OR current_item.replacement_text IS DISTINCT FROM previous_item.replacement_text
      );

    IF changed_count <> 1 OR NOT EXISTS (
        SELECT 1
        FROM marketops.review_decisions AS decision
        JOIN marketops.review_snapshot_items AS current_item
          ON current_item.snapshot_id = checked_snapshot_id
         AND current_item.candidate_id = decision.candidate_id
        JOIN marketops.review_snapshots AS previous_snapshot
          ON previous_snapshot.run_id = checked_run_id
         AND previous_snapshot.review_version = checked_version - 1
        JOIN marketops.review_snapshot_items AS previous_item
          ON previous_item.snapshot_id = previous_snapshot.id
         AND previous_item.candidate_id = decision.candidate_id
        WHERE decision.run_id = checked_run_id
          AND decision.review_version = checked_version
          AND current_item.status = decision.action
          AND current_item.replacement_text IS NOT DISTINCT FROM decision.replacement_text
          AND (
              current_item.status IS DISTINCT FROM previous_item.status
              OR current_item.replacement_text IS DISTINCT FROM previous_item.replacement_text
          )
    ) OR (
        SELECT count(*) FROM marketops.review_decisions
        WHERE run_id = checked_run_id AND review_version = checked_version
    ) <> 1 THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = 'review snapshot must match exactly one candidate decision';
    END IF;
    RETURN NEW;
END;
$$;

CREATE CONSTRAINT TRIGGER review_snapshots_integrity
AFTER INSERT ON marketops.review_snapshots
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION marketops.check_review_snapshot_integrity();

CREATE CONSTRAINT TRIGGER review_snapshot_items_integrity
AFTER INSERT ON marketops.review_snapshot_items
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION marketops.check_review_snapshot_integrity();

CREATE CONSTRAINT TRIGGER review_decisions_integrity
AFTER INSERT ON marketops.review_decisions
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION marketops.check_review_snapshot_integrity();

CREATE TRIGGER extraction_runs_immutable
BEFORE UPDATE OR DELETE ON marketops.extraction_runs
FOR EACH ROW EXECUTE FUNCTION marketops.reject_immutable_row_change();
CREATE TRIGGER extraction_runs_truncate_immutable
BEFORE TRUNCATE ON marketops.extraction_runs
FOR EACH STATEMENT EXECUTE FUNCTION marketops.reject_immutable_row_change();

CREATE TRIGGER extraction_candidates_immutable
BEFORE UPDATE OR DELETE ON marketops.extraction_candidates
FOR EACH ROW EXECUTE FUNCTION marketops.reject_immutable_row_change();
CREATE TRIGGER extraction_candidates_truncate_immutable
BEFORE TRUNCATE ON marketops.extraction_candidates
FOR EACH STATEMENT EXECUTE FUNCTION marketops.reject_immutable_row_change();

CREATE TRIGGER review_snapshots_immutable
BEFORE UPDATE OR DELETE ON marketops.review_snapshots
FOR EACH ROW EXECUTE FUNCTION marketops.reject_immutable_row_change();
CREATE TRIGGER review_snapshots_truncate_immutable
BEFORE TRUNCATE ON marketops.review_snapshots
FOR EACH STATEMENT EXECUTE FUNCTION marketops.reject_immutable_row_change();

CREATE TRIGGER review_snapshot_items_immutable
BEFORE UPDATE OR DELETE ON marketops.review_snapshot_items
FOR EACH ROW EXECUTE FUNCTION marketops.reject_immutable_row_change();
CREATE TRIGGER review_snapshot_items_truncate_immutable
BEFORE TRUNCATE ON marketops.review_snapshot_items
FOR EACH STATEMENT EXECUTE FUNCTION marketops.reject_immutable_row_change();

CREATE TRIGGER review_decisions_immutable
BEFORE UPDATE OR DELETE ON marketops.review_decisions
FOR EACH ROW EXECUTE FUNCTION marketops.reject_immutable_row_change();
CREATE TRIGGER review_decisions_truncate_immutable
BEFORE TRUNCATE ON marketops.review_decisions
FOR EACH STATEMENT EXECUTE FUNCTION marketops.reject_immutable_row_change();

ALTER TABLE marketops.extraction_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE marketops.extraction_runs FORCE ROW LEVEL SECURITY;
ALTER TABLE marketops.extraction_candidates ENABLE ROW LEVEL SECURITY;
ALTER TABLE marketops.extraction_candidates FORCE ROW LEVEL SECURITY;
ALTER TABLE marketops.review_snapshots ENABLE ROW LEVEL SECURITY;
ALTER TABLE marketops.review_snapshots FORCE ROW LEVEL SECURITY;
ALTER TABLE marketops.review_snapshot_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE marketops.review_snapshot_items FORCE ROW LEVEL SECURITY;
ALTER TABLE marketops.review_decisions ENABLE ROW LEVEL SECURITY;
ALTER TABLE marketops.review_decisions FORCE ROW LEVEL SECURITY;

CREATE POLICY extraction_runs_scope ON marketops.extraction_runs
USING (
    marketops.current_actor_id() IS NOT NULL
    AND workspace_id = marketops.current_workspace_id()
    AND client_id = marketops.current_client_id()
    AND project_id = marketops.current_project_id()
)
WITH CHECK (
    marketops.current_actor_id() IS NOT NULL
    AND workspace_id = marketops.current_workspace_id()
    AND client_id = marketops.current_client_id()
    AND project_id = marketops.current_project_id()
    AND created_by = marketops.current_actor_id()
);

CREATE POLICY extraction_candidates_scope ON marketops.extraction_candidates
USING (
    marketops.current_actor_id() IS NOT NULL
    AND workspace_id = marketops.current_workspace_id()
    AND client_id = marketops.current_client_id()
    AND project_id = marketops.current_project_id()
)
WITH CHECK (
    marketops.current_actor_id() IS NOT NULL
    AND workspace_id = marketops.current_workspace_id()
    AND client_id = marketops.current_client_id()
    AND project_id = marketops.current_project_id()
    AND created_by = marketops.current_actor_id()
);

CREATE POLICY review_snapshots_scope ON marketops.review_snapshots
USING (
    marketops.current_actor_id() IS NOT NULL
    AND workspace_id = marketops.current_workspace_id()
    AND client_id = marketops.current_client_id()
    AND project_id = marketops.current_project_id()
)
WITH CHECK (
    marketops.current_actor_id() IS NOT NULL
    AND workspace_id = marketops.current_workspace_id()
    AND client_id = marketops.current_client_id()
    AND project_id = marketops.current_project_id()
    AND created_by = marketops.current_actor_id()
);

CREATE POLICY review_snapshot_items_scope ON marketops.review_snapshot_items
USING (
    marketops.current_actor_id() IS NOT NULL
    AND workspace_id = marketops.current_workspace_id()
    AND client_id = marketops.current_client_id()
    AND project_id = marketops.current_project_id()
)
WITH CHECK (
    marketops.current_actor_id() IS NOT NULL
    AND workspace_id = marketops.current_workspace_id()
    AND client_id = marketops.current_client_id()
    AND project_id = marketops.current_project_id()
    AND created_by = marketops.current_actor_id()
);

CREATE POLICY review_decisions_scope ON marketops.review_decisions
USING (
    marketops.current_actor_id() IS NOT NULL
    AND workspace_id = marketops.current_workspace_id()
    AND client_id = marketops.current_client_id()
    AND project_id = marketops.current_project_id()
)
WITH CHECK (
    marketops.current_actor_id() IS NOT NULL
    AND workspace_id = marketops.current_workspace_id()
    AND client_id = marketops.current_client_id()
    AND project_id = marketops.current_project_id()
    AND actor_id = marketops.current_actor_id()
);

REVOKE ALL ON marketops.extraction_runs FROM PUBLIC;
REVOKE ALL ON marketops.extraction_candidates FROM PUBLIC;
REVOKE ALL ON marketops.review_snapshots FROM PUBLIC;
REVOKE ALL ON marketops.review_snapshot_items FROM PUBLIC;
REVOKE ALL ON marketops.review_decisions FROM PUBLIC;
REVOKE ALL ON FUNCTION marketops.check_extraction_run_integrity() FROM PUBLIC;
REVOKE ALL ON FUNCTION marketops.check_review_snapshot_integrity() FROM PUBLIC;
