CREATE TABLE marketops.project_capsules (
    id uuid PRIMARY KEY,
    organization_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    client_id uuid NOT NULL,
    project_id uuid NOT NULL,
    proposal_artifact_id uuid NOT NULL,
    proposal_version_id uuid NOT NULL,
    proposal_version integer NOT NULL CHECK (proposal_version > 0),
    proposal_sha256 bytea NOT NULL CHECK (octet_length(proposal_sha256) = 32),
    plan_id uuid NOT NULL,
    plan_version_id uuid NOT NULL,
    plan_version integer NOT NULL CHECK (plan_version > 0),
    plan_digest bytea NOT NULL CHECK (octet_length(plan_digest) = 32),
    approval_id uuid NOT NULL,
    schedule_snapshot_id uuid NOT NULL,
    schedule_digest bytea NOT NULL CHECK (octet_length(schedule_digest) = 32),
    capsule_version integer NOT NULL CHECK (capsule_version > 0),
    status text NOT NULL CHECK (status = 'ready'),
    capsule_digest bytea NOT NULL CHECK (octet_length(capsule_digest) = 32),
    payload jsonb NOT NULL CHECK (
        jsonb_typeof(payload) = 'object'
        AND octet_length(payload::text) <= 1048576
    ),
    outcome_count integer NOT NULL CHECK (outcome_count >= 0),
    retrospective_count integer NOT NULL CHECK (retrospective_count >= 0),
    knowledge_count integer NOT NULL CHECK (knowledge_count >= 0),
    created_by uuid NOT NULL,
    created_at timestamptz NOT NULL,
    CONSTRAINT project_capsules_scope_unique
        UNIQUE (organization_id, workspace_id, client_id, project_id, id),
    CONSTRAINT project_capsules_version_unique UNIQUE (project_id, capsule_version),
    CONSTRAINT project_capsules_digest_unique UNIQUE (project_id, capsule_digest),
    CONSTRAINT project_capsules_project_fk
        FOREIGN KEY (organization_id, workspace_id, client_id, project_id)
        REFERENCES marketops.projects (organization_id, workspace_id, client_id, id)
        ON DELETE RESTRICT,
    CONSTRAINT project_capsules_proposal_fk
        FOREIGN KEY (
            organization_id, workspace_id, client_id, project_id,
            proposal_artifact_id, proposal_version_id
        )
        REFERENCES marketops.artifact_versions (
            organization_id, workspace_id, client_id, project_id, artifact_id, id
        )
        ON DELETE RESTRICT,
    CONSTRAINT project_capsules_plan_version_fk
        FOREIGN KEY (
            organization_id, workspace_id, client_id, project_id, plan_id, plan_version_id
        )
        REFERENCES marketops.wbs_plan_versions (
            organization_id, workspace_id, client_id, project_id, plan_id, id
        )
        ON DELETE RESTRICT,
    CONSTRAINT project_capsules_approval_fk
        FOREIGN KEY (
            organization_id, workspace_id, client_id, project_id, approval_id
        )
        REFERENCES marketops.wbs_plan_approvals (
            organization_id, workspace_id, client_id, project_id, id
        )
        ON DELETE RESTRICT,
    CONSTRAINT project_capsules_schedule_fk
        FOREIGN KEY (
            organization_id, workspace_id, client_id, project_id, schedule_snapshot_id
        )
        REFERENCES marketops.schedule_snapshots (
            organization_id, workspace_id, client_id, project_id, id
        )
        ON DELETE RESTRICT
);

CREATE TABLE marketops.project_outcomes (
    id uuid NOT NULL,
    organization_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    client_id uuid NOT NULL,
    project_id uuid NOT NULL,
    capsule_id uuid NOT NULL,
    metric text NOT NULL CHECK (
        metric = btrim(metric) AND length(metric) BETWEEN 1 AND 200
    ),
    planned_value text CHECK (
        planned_value IS NULL
        OR (planned_value = btrim(planned_value) AND length(planned_value) BETWEEN 1 AND 1000)
    ),
    actual_value text NOT NULL CHECK (
        actual_value = btrim(actual_value) AND length(actual_value) BETWEEN 1 AND 1000
    ),
    unit text CHECK (
        unit IS NULL OR (unit = btrim(unit) AND length(unit) BETWEEN 1 AND 80)
    ),
    classification text NOT NULL CHECK (classification = 'outcome_observation'),
    source_type text NOT NULL CHECK (
        source_type IN ('artifact_version', 'task_execution', 'schedule_snapshot')
    ),
    source_id uuid NOT NULL,
    source_binding_sha256 bytea NOT NULL CHECK (octet_length(source_binding_sha256) = 32),
    created_by uuid NOT NULL,
    created_at timestamptz NOT NULL,
    CONSTRAINT project_outcomes_capsule_id_unique UNIQUE (capsule_id, id),
    CONSTRAINT project_outcomes_scope_unique
        UNIQUE (organization_id, workspace_id, client_id, project_id, capsule_id, id),
    CONSTRAINT project_outcomes_capsule_fk
        FOREIGN KEY (organization_id, workspace_id, client_id, project_id, capsule_id)
        REFERENCES marketops.project_capsules (
            organization_id, workspace_id, client_id, project_id, id
        )
        ON DELETE RESTRICT
);

CREATE TABLE marketops.project_retrospectives (
    id uuid NOT NULL,
    organization_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    client_id uuid NOT NULL,
    project_id uuid NOT NULL,
    capsule_id uuid NOT NULL,
    finding text NOT NULL CHECK (
        finding = btrim(finding) AND length(finding) BETWEEN 1 AND 4000
    ),
    classification text NOT NULL CHECK (
        classification IN (
            'success_pattern', 'failure_counterexample', 'risk_check',
            'process_observation', 'non_reusable_note'
        )
    ),
    reusable_candidate boolean NOT NULL,
    evidence jsonb NOT NULL CHECK (
        jsonb_typeof(evidence) = 'array'
        AND jsonb_array_length(evidence) BETWEEN 1 AND 100
        AND octet_length(evidence::text) <= 40000
    ),
    evidence_sha256 bytea NOT NULL CHECK (octet_length(evidence_sha256) = 32),
    created_by uuid NOT NULL,
    created_at timestamptz NOT NULL,
    CONSTRAINT project_retrospectives_capsule_id_unique UNIQUE (capsule_id, id),
    CONSTRAINT project_retrospectives_scope_unique
        UNIQUE (organization_id, workspace_id, client_id, project_id, capsule_id, id),
    CONSTRAINT project_retrospectives_non_reusable_check CHECK (
        classification <> 'non_reusable_note' OR reusable_candidate = false
    ),
    CONSTRAINT project_retrospectives_capsule_fk
        FOREIGN KEY (organization_id, workspace_id, client_id, project_id, capsule_id)
        REFERENCES marketops.project_capsules (
            organization_id, workspace_id, client_id, project_id, id
        )
        ON DELETE RESTRICT
);

CREATE TABLE marketops.knowledge_items (
    id uuid PRIMARY KEY,
    organization_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    client_id uuid NOT NULL,
    project_id uuid NOT NULL,
    capsule_id uuid NOT NULL,
    ordinal integer NOT NULL CHECK (ordinal > 0),
    scope text NOT NULL CHECK (scope = 'project'),
    type text NOT NULL CHECK (
        type IN ('observed_outcome', 'observed_task_duration', 'retrospective_finding')
    ),
    status text NOT NULL CHECK (status = 'candidate'),
    classification text NOT NULL CHECK (
        classification IN (
            'outcome_observation', 'observed_duration', 'success_pattern',
            'failure_counterexample', 'risk_check', 'process_observation'
        )
    ),
    content text NOT NULL CHECK (
        content = btrim(content) AND length(content) BETWEEN 1 AND 4000
    ),
    content_sha256 bytea NOT NULL CHECK (octet_length(content_sha256) = 32),
    confidence double precision NOT NULL CHECK (confidence = 1.0),
    created_by uuid NOT NULL,
    created_at timestamptz NOT NULL,
    CONSTRAINT knowledge_items_scope_unique
        UNIQUE (organization_id, workspace_id, client_id, project_id, id),
    CONSTRAINT knowledge_items_capsule_ordinal_unique UNIQUE (capsule_id, ordinal),
    CONSTRAINT knowledge_items_capsule_fk
        FOREIGN KEY (organization_id, workspace_id, client_id, project_id, capsule_id)
        REFERENCES marketops.project_capsules (
            organization_id, workspace_id, client_id, project_id, id
        )
        ON DELETE RESTRICT
);

CREATE TABLE marketops.knowledge_item_versions (
    knowledge_id uuid NOT NULL,
    version integer NOT NULL CHECK (version = 1),
    organization_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    client_id uuid NOT NULL,
    project_id uuid NOT NULL,
    capsule_id uuid NOT NULL,
    content text NOT NULL CHECK (
        content = btrim(content) AND length(content) BETWEEN 1 AND 4000
    ),
    content_sha256 bytea NOT NULL CHECK (octet_length(content_sha256) = 32),
    evidence_digest bytea NOT NULL CHECK (octet_length(evidence_digest) = 32),
    created_by uuid NOT NULL,
    created_at timestamptz NOT NULL,
    PRIMARY KEY (knowledge_id, version),
    CONSTRAINT knowledge_item_versions_scope_fk
        FOREIGN KEY (
            organization_id, workspace_id, client_id, project_id, knowledge_id
        )
        REFERENCES marketops.knowledge_items (
            organization_id, workspace_id, client_id, project_id, id
        )
        ON DELETE RESTRICT,
    CONSTRAINT knowledge_item_versions_capsule_fk
        FOREIGN KEY (
            organization_id, workspace_id, client_id, project_id, capsule_id
        )
        REFERENCES marketops.project_capsules (
            organization_id, workspace_id, client_id, project_id, id
        )
        ON DELETE RESTRICT
);

CREATE TABLE marketops.knowledge_item_evidence (
    knowledge_id uuid NOT NULL,
    ordinal integer NOT NULL CHECK (ordinal > 0),
    organization_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    client_id uuid NOT NULL,
    project_id uuid NOT NULL,
    source_type text NOT NULL CHECK (
        source_type IN (
            'artifact_version', 'task', 'task_execution', 'plan_version',
            'plan_approval', 'schedule_snapshot', 'outcome', 'retrospective', 'capsule'
        )
    ),
    source_id text NOT NULL CHECK (
        source_id = btrim(source_id) AND length(source_id) BETWEEN 1 AND 300
    ),
    binding_sha256 bytea NOT NULL CHECK (octet_length(binding_sha256) = 32),
    created_by uuid NOT NULL,
    created_at timestamptz NOT NULL,
    PRIMARY KEY (knowledge_id, ordinal),
    CONSTRAINT knowledge_item_evidence_scope_fk
        FOREIGN KEY (
            organization_id, workspace_id, client_id, project_id, knowledge_id
        )
        REFERENCES marketops.knowledge_items (
            organization_id, workspace_id, client_id, project_id, id
        )
        ON DELETE RESTRICT
);

CREATE INDEX project_capsules_project_idx ON marketops.project_capsules (
    workspace_id, client_id, project_id, created_by, capsule_version DESC
);
CREATE INDEX project_outcomes_capsule_idx ON marketops.project_outcomes (capsule_id, id);
CREATE INDEX project_retrospectives_capsule_idx ON marketops.project_retrospectives (capsule_id, id);
CREATE INDEX knowledge_items_project_idx ON marketops.knowledge_items (
    workspace_id, client_id, project_id, created_by, capsule_id, ordinal
);

CREATE FUNCTION marketops.check_project_capsule_integrity()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog
AS $$
DECLARE
    project_record record;
    proposal_record record;
    plan_version_record record;
    approval_record record;
    schedule_record record;
BEGIN
    SELECT * INTO project_record
    FROM marketops.projects
    WHERE organization_id = NEW.organization_id
      AND workspace_id = NEW.workspace_id
      AND client_id = NEW.client_id
      AND id = NEW.project_id;

    SELECT * INTO proposal_record
    FROM marketops.artifact_versions
    WHERE organization_id = NEW.organization_id
      AND workspace_id = NEW.workspace_id
      AND client_id = NEW.client_id
      AND project_id = NEW.project_id
      AND artifact_id = NEW.proposal_artifact_id
      AND id = NEW.proposal_version_id;

    SELECT * INTO plan_version_record
    FROM marketops.wbs_plan_versions
    WHERE organization_id = NEW.organization_id
      AND workspace_id = NEW.workspace_id
      AND client_id = NEW.client_id
      AND project_id = NEW.project_id
      AND plan_id = NEW.plan_id
      AND id = NEW.plan_version_id;

    SELECT * INTO approval_record
    FROM marketops.wbs_plan_approvals
    WHERE organization_id = NEW.organization_id
      AND workspace_id = NEW.workspace_id
      AND client_id = NEW.client_id
      AND project_id = NEW.project_id
      AND id = NEW.approval_id;

    SELECT * INTO schedule_record
    FROM marketops.schedule_snapshots
    WHERE organization_id = NEW.organization_id
      AND workspace_id = NEW.workspace_id
      AND client_id = NEW.client_id
      AND project_id = NEW.project_id
      AND id = NEW.schedule_snapshot_id;

    IF project_record.id IS NULL
       OR proposal_record.id IS NULL
       OR plan_version_record.id IS NULL
       OR approval_record.id IS NULL
       OR schedule_record.id IS NULL
       OR proposal_record.proposal_version IS DISTINCT FROM NEW.proposal_version
       OR proposal_record.sha256 IS DISTINCT FROM NEW.proposal_sha256
       OR proposal_record.approval_status IS DISTINCT FROM 'approved'
       OR project_record.approved_proposal_artifact_id IS DISTINCT FROM NEW.proposal_artifact_id
       OR project_record.approved_proposal_version_id IS DISTINCT FROM NEW.proposal_version_id
       OR project_record.approved_proposal_number IS DISTINCT FROM NEW.proposal_version
       OR plan_version_record.plan_version IS DISTINCT FROM NEW.plan_version
       OR plan_version_record.plan_digest IS DISTINCT FROM NEW.plan_digest
       OR approval_record.plan_id IS DISTINCT FROM NEW.plan_id
       OR approval_record.plan_version_id IS DISTINCT FROM NEW.plan_version_id
       OR approval_record.plan_version IS DISTINCT FROM NEW.plan_version
       OR approval_record.plan_digest IS DISTINCT FROM NEW.plan_digest
       OR approval_record.schedule_snapshot_id IS DISTINCT FROM NEW.schedule_snapshot_id
       OR approval_record.schedule_digest IS DISTINCT FROM NEW.schedule_digest
       OR schedule_record.plan_id IS DISTINCT FROM NEW.plan_id
       OR schedule_record.plan_version_id IS DISTINCT FROM NEW.plan_version_id
       OR schedule_record.plan_version IS DISTINCT FROM NEW.plan_version
       OR schedule_record.plan_digest IS DISTINCT FROM NEW.plan_digest
       OR schedule_record.schedule_digest IS DISTINCT FROM NEW.schedule_digest
       OR schedule_record.status IS DISTINCT FROM 'ready'
       OR proposal_record.created_by IS DISTINCT FROM NEW.created_by
       OR project_record.created_by IS DISTINCT FROM NEW.created_by
       OR plan_version_record.created_by IS DISTINCT FROM NEW.created_by
       OR approval_record.approved_by IS DISTINCT FROM NEW.created_by
       OR schedule_record.created_by IS DISTINCT FROM NEW.created_by THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = 'project capsule source bindings are inconsistent or not approved';
    END IF;
    RETURN NEW;
END;
$$;

CREATE CONSTRAINT TRIGGER project_capsules_integrity
AFTER INSERT ON marketops.project_capsules
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION marketops.check_project_capsule_integrity();

CREATE TRIGGER project_capsules_immutable
BEFORE UPDATE OR DELETE ON marketops.project_capsules
FOR EACH ROW EXECUTE FUNCTION marketops.reject_immutable_row_change();
CREATE TRIGGER project_capsules_truncate_immutable
BEFORE TRUNCATE ON marketops.project_capsules
FOR EACH STATEMENT EXECUTE FUNCTION marketops.reject_immutable_row_change();
CREATE TRIGGER project_outcomes_immutable
BEFORE UPDATE OR DELETE ON marketops.project_outcomes
FOR EACH ROW EXECUTE FUNCTION marketops.reject_immutable_row_change();
CREATE TRIGGER project_outcomes_truncate_immutable
BEFORE TRUNCATE ON marketops.project_outcomes
FOR EACH STATEMENT EXECUTE FUNCTION marketops.reject_immutable_row_change();
CREATE TRIGGER project_retrospectives_immutable
BEFORE UPDATE OR DELETE ON marketops.project_retrospectives
FOR EACH ROW EXECUTE FUNCTION marketops.reject_immutable_row_change();
CREATE TRIGGER project_retrospectives_truncate_immutable
BEFORE TRUNCATE ON marketops.project_retrospectives
FOR EACH STATEMENT EXECUTE FUNCTION marketops.reject_immutable_row_change();
CREATE TRIGGER knowledge_items_immutable
BEFORE UPDATE OR DELETE ON marketops.knowledge_items
FOR EACH ROW EXECUTE FUNCTION marketops.reject_immutable_row_change();
CREATE TRIGGER knowledge_items_truncate_immutable
BEFORE TRUNCATE ON marketops.knowledge_items
FOR EACH STATEMENT EXECUTE FUNCTION marketops.reject_immutable_row_change();
CREATE TRIGGER knowledge_item_versions_immutable
BEFORE UPDATE OR DELETE ON marketops.knowledge_item_versions
FOR EACH ROW EXECUTE FUNCTION marketops.reject_immutable_row_change();
CREATE TRIGGER knowledge_item_versions_truncate_immutable
BEFORE TRUNCATE ON marketops.knowledge_item_versions
FOR EACH STATEMENT EXECUTE FUNCTION marketops.reject_immutable_row_change();
CREATE TRIGGER knowledge_item_evidence_immutable
BEFORE UPDATE OR DELETE ON marketops.knowledge_item_evidence
FOR EACH ROW EXECUTE FUNCTION marketops.reject_immutable_row_change();
CREATE TRIGGER knowledge_item_evidence_truncate_immutable
BEFORE TRUNCATE ON marketops.knowledge_item_evidence
FOR EACH STATEMENT EXECUTE FUNCTION marketops.reject_immutable_row_change();

ALTER TABLE marketops.project_capsules ENABLE ROW LEVEL SECURITY;
ALTER TABLE marketops.project_capsules FORCE ROW LEVEL SECURITY;
ALTER TABLE marketops.project_outcomes ENABLE ROW LEVEL SECURITY;
ALTER TABLE marketops.project_outcomes FORCE ROW LEVEL SECURITY;
ALTER TABLE marketops.project_retrospectives ENABLE ROW LEVEL SECURITY;
ALTER TABLE marketops.project_retrospectives FORCE ROW LEVEL SECURITY;
ALTER TABLE marketops.knowledge_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE marketops.knowledge_items FORCE ROW LEVEL SECURITY;
ALTER TABLE marketops.knowledge_item_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE marketops.knowledge_item_versions FORCE ROW LEVEL SECURITY;
ALTER TABLE marketops.knowledge_item_evidence ENABLE ROW LEVEL SECURITY;
ALTER TABLE marketops.knowledge_item_evidence FORCE ROW LEVEL SECURITY;

CREATE POLICY project_capsules_actor_scope ON marketops.project_capsules
USING (marketops.current_actor_id() IS NOT NULL AND workspace_id = marketops.current_workspace_id() AND client_id = marketops.current_client_id() AND project_id = marketops.current_project_id() AND created_by = marketops.current_actor_id())
WITH CHECK (marketops.current_actor_id() IS NOT NULL AND workspace_id = marketops.current_workspace_id() AND client_id = marketops.current_client_id() AND project_id = marketops.current_project_id() AND created_by = marketops.current_actor_id());
CREATE POLICY project_outcomes_actor_scope ON marketops.project_outcomes
USING (marketops.current_actor_id() IS NOT NULL AND workspace_id = marketops.current_workspace_id() AND client_id = marketops.current_client_id() AND project_id = marketops.current_project_id() AND created_by = marketops.current_actor_id())
WITH CHECK (marketops.current_actor_id() IS NOT NULL AND workspace_id = marketops.current_workspace_id() AND client_id = marketops.current_client_id() AND project_id = marketops.current_project_id() AND created_by = marketops.current_actor_id());
CREATE POLICY project_retrospectives_actor_scope ON marketops.project_retrospectives
USING (marketops.current_actor_id() IS NOT NULL AND workspace_id = marketops.current_workspace_id() AND client_id = marketops.current_client_id() AND project_id = marketops.current_project_id() AND created_by = marketops.current_actor_id())
WITH CHECK (marketops.current_actor_id() IS NOT NULL AND workspace_id = marketops.current_workspace_id() AND client_id = marketops.current_client_id() AND project_id = marketops.current_project_id() AND created_by = marketops.current_actor_id());
CREATE POLICY knowledge_items_actor_scope ON marketops.knowledge_items
USING (marketops.current_actor_id() IS NOT NULL AND workspace_id = marketops.current_workspace_id() AND client_id = marketops.current_client_id() AND project_id = marketops.current_project_id() AND created_by = marketops.current_actor_id())
WITH CHECK (marketops.current_actor_id() IS NOT NULL AND workspace_id = marketops.current_workspace_id() AND client_id = marketops.current_client_id() AND project_id = marketops.current_project_id() AND created_by = marketops.current_actor_id());
CREATE POLICY knowledge_item_versions_actor_scope ON marketops.knowledge_item_versions
USING (marketops.current_actor_id() IS NOT NULL AND workspace_id = marketops.current_workspace_id() AND client_id = marketops.current_client_id() AND project_id = marketops.current_project_id() AND created_by = marketops.current_actor_id())
WITH CHECK (marketops.current_actor_id() IS NOT NULL AND workspace_id = marketops.current_workspace_id() AND client_id = marketops.current_client_id() AND project_id = marketops.current_project_id() AND created_by = marketops.current_actor_id());
CREATE POLICY knowledge_item_evidence_actor_scope ON marketops.knowledge_item_evidence
USING (marketops.current_actor_id() IS NOT NULL AND workspace_id = marketops.current_workspace_id() AND client_id = marketops.current_client_id() AND project_id = marketops.current_project_id() AND created_by = marketops.current_actor_id())
WITH CHECK (marketops.current_actor_id() IS NOT NULL AND workspace_id = marketops.current_workspace_id() AND client_id = marketops.current_client_id() AND project_id = marketops.current_project_id() AND created_by = marketops.current_actor_id());

REVOKE ALL ON marketops.project_capsules FROM PUBLIC;
REVOKE ALL ON marketops.project_outcomes FROM PUBLIC;
REVOKE ALL ON marketops.project_retrospectives FROM PUBLIC;
REVOKE ALL ON marketops.knowledge_items FROM PUBLIC;
REVOKE ALL ON marketops.knowledge_item_versions FROM PUBLIC;
REVOKE ALL ON marketops.knowledge_item_evidence FROM PUBLIC;
REVOKE ALL ON FUNCTION marketops.check_project_capsule_integrity() FROM PUBLIC;
