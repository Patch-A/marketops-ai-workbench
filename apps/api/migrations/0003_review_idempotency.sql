CREATE TABLE marketops.extraction_run_requests (
    id uuid PRIMARY KEY,
    organization_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    client_id uuid NOT NULL,
    project_id uuid NOT NULL,
    idempotency_key text NOT NULL CHECK (
        length(idempotency_key) BETWEEN 8 AND 200
        AND idempotency_key = btrim(idempotency_key)
    ),
    expected_proposal_version_id uuid NOT NULL,
    expected_proposal_sha256 bytea NOT NULL CHECK (
        octet_length(expected_proposal_sha256) = 32
    ),
    run_id uuid NOT NULL,
    created_by uuid NOT NULL,
    created_at timestamptz NOT NULL,
    CONSTRAINT extraction_run_requests_scope_unique
        UNIQUE (organization_id, workspace_id, client_id, project_id, id),
    CONSTRAINT extraction_run_requests_idempotency_unique
        UNIQUE (
            workspace_id, client_id, project_id, created_by, idempotency_key
        ),
    CONSTRAINT extraction_run_requests_project_fk
        FOREIGN KEY (organization_id, workspace_id, client_id, project_id)
        REFERENCES marketops.projects (organization_id, workspace_id, client_id, id)
        ON DELETE RESTRICT,
    CONSTRAINT extraction_run_requests_run_fk
        FOREIGN KEY (organization_id, workspace_id, client_id, project_id, run_id)
        REFERENCES marketops.extraction_runs (
            organization_id, workspace_id, client_id, project_id, id
        )
        ON DELETE RESTRICT
        DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT extraction_run_requests_source_fk
        FOREIGN KEY (expected_proposal_version_id)
        REFERENCES marketops.artifact_versions (id)
        ON DELETE RESTRICT
);

CREATE FUNCTION marketops.check_extraction_run_request_integrity()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog
AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM marketops.extraction_runs AS run
        WHERE run.organization_id = NEW.organization_id
          AND run.workspace_id = NEW.workspace_id
          AND run.client_id = NEW.client_id
          AND run.project_id = NEW.project_id
          AND run.id = NEW.run_id
          AND run.proposal_version_id = NEW.expected_proposal_version_id
          AND run.proposal_sha256 = NEW.expected_proposal_sha256
          AND run.created_by = NEW.created_by
    ) THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = 'extraction run request differs from its review run';
    END IF;
    RETURN NEW;
END;
$$;

CREATE CONSTRAINT TRIGGER extraction_run_requests_integrity
AFTER INSERT ON marketops.extraction_run_requests
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION marketops.check_extraction_run_request_integrity();

CREATE TRIGGER extraction_run_requests_immutable
BEFORE UPDATE OR DELETE ON marketops.extraction_run_requests
FOR EACH ROW EXECUTE FUNCTION marketops.reject_immutable_row_change();
CREATE TRIGGER extraction_run_requests_truncate_immutable
BEFORE TRUNCATE ON marketops.extraction_run_requests
FOR EACH STATEMENT EXECUTE FUNCTION marketops.reject_immutable_row_change();

ALTER TABLE marketops.extraction_run_requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE marketops.extraction_run_requests FORCE ROW LEVEL SECURITY;

CREATE POLICY extraction_run_requests_scope ON marketops.extraction_run_requests
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

REVOKE ALL ON marketops.extraction_run_requests FROM PUBLIC;
REVOKE ALL ON FUNCTION marketops.check_extraction_run_request_integrity() FROM PUBLIC;

DROP POLICY extraction_runs_scope ON marketops.extraction_runs;
CREATE POLICY extraction_runs_actor_scope ON marketops.extraction_runs
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

DROP POLICY extraction_candidates_scope ON marketops.extraction_candidates;
CREATE POLICY extraction_candidates_actor_scope ON marketops.extraction_candidates
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

DROP POLICY review_snapshots_scope ON marketops.review_snapshots;
CREATE POLICY review_snapshots_actor_scope ON marketops.review_snapshots
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

DROP POLICY review_snapshot_items_scope ON marketops.review_snapshot_items;
CREATE POLICY review_snapshot_items_actor_scope ON marketops.review_snapshot_items
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

DROP POLICY review_decisions_scope ON marketops.review_decisions;
CREATE POLICY review_decisions_actor_scope ON marketops.review_decisions
USING (
    marketops.current_actor_id() IS NOT NULL
    AND workspace_id = marketops.current_workspace_id()
    AND client_id = marketops.current_client_id()
    AND project_id = marketops.current_project_id()
    AND actor_id = marketops.current_actor_id()
)
WITH CHECK (
    marketops.current_actor_id() IS NOT NULL
    AND workspace_id = marketops.current_workspace_id()
    AND client_id = marketops.current_client_id()
    AND project_id = marketops.current_project_id()
    AND actor_id = marketops.current_actor_id()
);

DROP POLICY audit_events_scope ON marketops.audit_events;
CREATE POLICY audit_events_actor_scope ON marketops.audit_events
USING (
    marketops.current_actor_id() IS NOT NULL
    AND workspace_id = marketops.current_workspace_id()
    AND client_id = marketops.current_client_id()
    AND project_id = marketops.current_project_id()
    AND actor_id = marketops.current_actor_id()
)
WITH CHECK (
    marketops.current_actor_id() IS NOT NULL
    AND workspace_id = marketops.current_workspace_id()
    AND client_id = marketops.current_client_id()
    AND project_id = marketops.current_project_id()
    AND actor_id = marketops.current_actor_id()
);
