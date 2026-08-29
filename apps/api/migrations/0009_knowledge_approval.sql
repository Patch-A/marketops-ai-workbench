CREATE TABLE marketops.knowledge_promotion_roots (
    id uuid PRIMARY KEY,
    organization_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    client_id uuid NOT NULL,
    project_id uuid NOT NULL,
    knowledge_id uuid NOT NULL,
    created_by uuid NOT NULL,
    created_at timestamptz NOT NULL,
    CONSTRAINT knowledge_promotion_roots_scope_unique
        UNIQUE (organization_id, workspace_id, client_id, project_id, id),
    CONSTRAINT knowledge_promotion_roots_knowledge_unique
        UNIQUE (organization_id, workspace_id, client_id, project_id, knowledge_id),
    CONSTRAINT knowledge_promotion_roots_knowledge_fk
        FOREIGN KEY (organization_id, workspace_id, client_id, project_id, knowledge_id)
        REFERENCES marketops.knowledge_items (
            organization_id, workspace_id, client_id, project_id, id
        ) ON DELETE RESTRICT
);

CREATE TABLE marketops.knowledge_promotion_versions (
    promotion_id uuid NOT NULL,
    version integer NOT NULL CHECK (version > 0),
    organization_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    client_id uuid NOT NULL,
    project_id uuid NOT NULL,
    knowledge_id uuid NOT NULL,
    action text NOT NULL CHECK (action IN ('approve', 'revise', 'reject', 'revoke', 'elevate')),
    status text NOT NULL CHECK (status IN ('approved', 'rejected', 'revoked')),
    effective_scope text CHECK (effective_scope IN ('project', 'client')),
    content text CHECK (content = btrim(content) AND length(content) BETWEEN 1 AND 4000),
    content_sha256 bytea CHECK (octet_length(content_sha256) = 32),
    reason text CHECK (reason = btrim(reason) AND length(reason) BETWEEN 1 AND 1000),
    request_sha256 bytea NOT NULL CHECK (octet_length(request_sha256) = 32),
    replay_sha256 bytea NOT NULL CHECK (octet_length(replay_sha256) = 32),
    created_by uuid NOT NULL,
    created_at timestamptz NOT NULL,
    PRIMARY KEY (promotion_id, version),
    CONSTRAINT knowledge_promotion_versions_scope_unique
        UNIQUE (organization_id, workspace_id, client_id, project_id, promotion_id, version),
    CONSTRAINT knowledge_promotion_versions_tenant_identity_unique
        UNIQUE (organization_id, workspace_id, client_id, promotion_id, version),
    CONSTRAINT knowledge_promotion_versions_replay_unique
        UNIQUE (promotion_id, request_sha256),
    CONSTRAINT knowledge_promotion_versions_root_fk
        FOREIGN KEY (organization_id, workspace_id, client_id, project_id, promotion_id)
        REFERENCES marketops.knowledge_promotion_roots (
            organization_id, workspace_id, client_id, project_id, id
        ) ON DELETE RESTRICT,
    CONSTRAINT knowledge_promotion_versions_knowledge_fk
        FOREIGN KEY (organization_id, workspace_id, client_id, project_id, knowledge_id)
        REFERENCES marketops.knowledge_items (
            organization_id, workspace_id, client_id, project_id, id
        ) ON DELETE RESTRICT,
    CONSTRAINT knowledge_promotion_versions_shape_check CHECK (
        (status = 'approved' AND effective_scope IS NOT NULL AND content IS NOT NULL AND content_sha256 IS NOT NULL)
        OR (status IN ('rejected', 'revoked') AND effective_scope IS NULL AND content IS NULL AND content_sha256 IS NULL)
    )
);

CREATE TABLE marketops.knowledge_citations (
    id uuid PRIMARY KEY,
    organization_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    client_id uuid NOT NULL,
    project_id uuid NOT NULL,
    source_project_id uuid NOT NULL,
    source_knowledge_id uuid NOT NULL,
    source_promotion_id uuid NOT NULL,
    source_promotion_version integer NOT NULL CHECK (source_promotion_version > 0),
    source_scope text NOT NULL CHECK (source_scope IN ('project', 'client')),
    content text NOT NULL CHECK (content = btrim(content) AND length(content) BETWEEN 1 AND 4000),
    content_sha256 bytea NOT NULL CHECK (octet_length(content_sha256) = 32),
    reason text NOT NULL CHECK (reason = btrim(reason) AND length(reason) BETWEEN 1 AND 1000),
    citation_sha256 bytea NOT NULL CHECK (octet_length(citation_sha256) = 32),
    created_by uuid NOT NULL,
    created_at timestamptz NOT NULL,
    CONSTRAINT knowledge_citations_target_scope_unique
        UNIQUE (organization_id, workspace_id, client_id, project_id, id),
    CONSTRAINT knowledge_citations_replay_unique
        UNIQUE (project_id, source_promotion_id, source_promotion_version, citation_sha256),
    CONSTRAINT knowledge_citations_target_project_fk
        FOREIGN KEY (organization_id, workspace_id, client_id, project_id)
        REFERENCES marketops.projects (organization_id, workspace_id, client_id, id)
        ON DELETE RESTRICT,
    CONSTRAINT knowledge_citations_source_promotion_fk
        FOREIGN KEY (
            organization_id, workspace_id, client_id,
            source_promotion_id, source_promotion_version
        )
        REFERENCES marketops.knowledge_promotion_versions (
            organization_id, workspace_id, client_id, promotion_id, version
        )
        ON DELETE RESTRICT
);

CREATE INDEX knowledge_promotion_versions_head_idx ON marketops.knowledge_promotion_versions (
    promotion_id, version DESC
);
CREATE INDEX knowledge_citations_target_project_idx ON marketops.knowledge_citations (
    workspace_id, client_id, project_id, created_by, created_at DESC
);

CREATE FUNCTION marketops.check_knowledge_promotion_transition()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog
AS $$
DECLARE
    root_record record;
    previous_record record;
    candidate_record record;
BEGIN
    SELECT * INTO root_record FROM marketops.knowledge_promotion_roots
    WHERE id = NEW.promotion_id
      AND organization_id = NEW.organization_id AND workspace_id = NEW.workspace_id
      AND client_id = NEW.client_id AND project_id = NEW.project_id;
    SELECT * INTO candidate_record FROM marketops.knowledge_items
    WHERE id = NEW.knowledge_id
      AND organization_id = NEW.organization_id AND workspace_id = NEW.workspace_id
      AND client_id = NEW.client_id AND project_id = NEW.project_id;
    SELECT * INTO previous_record FROM marketops.knowledge_promotion_versions
    WHERE promotion_id = NEW.promotion_id AND version < NEW.version
    ORDER BY version DESC LIMIT 1;

    IF root_record.id IS NULL OR candidate_record.id IS NULL
       OR root_record.knowledge_id IS DISTINCT FROM NEW.knowledge_id
       OR candidate_record.created_by IS DISTINCT FROM NEW.created_by THEN
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'knowledge promotion source is invalid';
    END IF;
    IF previous_record.promotion_id IS NULL THEN
        IF NEW.version <> 1 OR NEW.action NOT IN ('approve', 'revise', 'reject')
           OR (NEW.action = 'approve' AND (NEW.status <> 'approved' OR NEW.effective_scope <> 'project'
               OR NEW.content IS DISTINCT FROM candidate_record.content OR NEW.content_sha256 IS DISTINCT FROM candidate_record.content_sha256
               OR NEW.reason IS NOT NULL))
           OR (NEW.action = 'revise' AND (NEW.status <> 'approved' OR NEW.effective_scope <> 'project'
               OR NEW.content IS NULL OR NEW.content = candidate_record.content OR NEW.reason IS NULL))
           OR (NEW.action = 'reject' AND (NEW.status <> 'rejected' OR NEW.reason IS NULL)) THEN
            RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'initial knowledge promotion is invalid';
        END IF;
    ELSIF previous_record.status <> 'approved' OR NEW.version <> previous_record.version + 1
       OR (NEW.action = 'revise' AND (NEW.status <> 'approved' OR NEW.effective_scope IS DISTINCT FROM previous_record.effective_scope
           OR NEW.content IS NULL OR NEW.content = previous_record.content OR NEW.reason IS NULL))
       OR (NEW.action = 'elevate' AND (NEW.status <> 'approved' OR previous_record.effective_scope <> 'project'
           OR NEW.effective_scope <> 'client' OR NEW.content IS DISTINCT FROM previous_record.content
           OR NEW.content_sha256 IS DISTINCT FROM previous_record.content_sha256 OR NEW.reason IS NULL))
       OR (NEW.action = 'revoke' AND (NEW.status <> 'revoked' OR NEW.reason IS NULL))
       OR NEW.action NOT IN ('revise', 'elevate', 'revoke') THEN
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'knowledge promotion transition is invalid';
    END IF;
    RETURN NEW;
END;
$$;

CREATE FUNCTION marketops.check_knowledge_citation_integrity()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog
AS $$
DECLARE
    source_record record;
    target_project_record record;
BEGIN
    SELECT version_record.*, root_record.project_id AS source_project_id, root_record.knowledge_id AS source_knowledge_id
    INTO source_record
    FROM marketops.knowledge_promotion_versions AS version_record
    JOIN marketops.knowledge_promotion_roots AS root_record ON root_record.id = version_record.promotion_id
    WHERE version_record.promotion_id = NEW.source_promotion_id
      AND version_record.version = NEW.source_promotion_version;
    SELECT * INTO target_project_record
    FROM marketops.projects
    WHERE id = NEW.project_id
      AND organization_id = NEW.organization_id
      AND workspace_id = NEW.workspace_id
      AND client_id = NEW.client_id;
    IF source_record.promotion_id IS NULL
       OR source_record.version <> (SELECT max(version) FROM marketops.knowledge_promotion_versions WHERE promotion_id = NEW.source_promotion_id)
       OR source_record.status <> 'approved'
       OR source_record.project_id IS DISTINCT FROM NEW.source_project_id
       OR source_record.knowledge_id IS DISTINCT FROM NEW.source_knowledge_id
       OR source_record.created_by IS DISTINCT FROM NEW.created_by
       OR target_project_record.id IS NULL
       OR target_project_record.created_by IS DISTINCT FROM NEW.created_by
       OR source_record.effective_scope IS DISTINCT FROM NEW.source_scope
       OR source_record.content IS DISTINCT FROM NEW.content
       OR source_record.content_sha256 IS DISTINCT FROM NEW.content_sha256
       OR (NEW.source_scope = 'project' AND NEW.project_id IS DISTINCT FROM NEW.source_project_id)
       OR (NEW.source_scope = 'client' AND (NEW.organization_id IS DISTINCT FROM source_record.organization_id
           OR NEW.workspace_id IS DISTINCT FROM source_record.workspace_id OR NEW.client_id IS DISTINCT FROM source_record.client_id)) THEN
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'knowledge citation source is not currently eligible';
    END IF;
    RETURN NEW;
END;
$$;

CREATE CONSTRAINT TRIGGER knowledge_promotion_versions_integrity
AFTER INSERT ON marketops.knowledge_promotion_versions DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION marketops.check_knowledge_promotion_transition();
CREATE CONSTRAINT TRIGGER knowledge_citations_integrity
AFTER INSERT ON marketops.knowledge_citations DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION marketops.check_knowledge_citation_integrity();

CREATE TRIGGER knowledge_promotion_roots_immutable BEFORE UPDATE OR DELETE ON marketops.knowledge_promotion_roots FOR EACH ROW EXECUTE FUNCTION marketops.reject_immutable_row_change();
CREATE TRIGGER knowledge_promotion_versions_immutable BEFORE UPDATE OR DELETE ON marketops.knowledge_promotion_versions FOR EACH ROW EXECUTE FUNCTION marketops.reject_immutable_row_change();
CREATE TRIGGER knowledge_citations_immutable BEFORE UPDATE OR DELETE ON marketops.knowledge_citations FOR EACH ROW EXECUTE FUNCTION marketops.reject_immutable_row_change();
CREATE TRIGGER knowledge_promotion_roots_truncate_immutable BEFORE TRUNCATE ON marketops.knowledge_promotion_roots FOR EACH STATEMENT EXECUTE FUNCTION marketops.reject_immutable_row_change();
CREATE TRIGGER knowledge_promotion_versions_truncate_immutable BEFORE TRUNCATE ON marketops.knowledge_promotion_versions FOR EACH STATEMENT EXECUTE FUNCTION marketops.reject_immutable_row_change();
CREATE TRIGGER knowledge_citations_truncate_immutable BEFORE TRUNCATE ON marketops.knowledge_citations FOR EACH STATEMENT EXECUTE FUNCTION marketops.reject_immutable_row_change();

ALTER TABLE marketops.knowledge_promotion_roots ENABLE ROW LEVEL SECURITY;
ALTER TABLE marketops.knowledge_promotion_roots FORCE ROW LEVEL SECURITY;
ALTER TABLE marketops.knowledge_promotion_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE marketops.knowledge_promotion_versions FORCE ROW LEVEL SECURITY;
ALTER TABLE marketops.knowledge_citations ENABLE ROW LEVEL SECURITY;
ALTER TABLE marketops.knowledge_citations FORCE ROW LEVEL SECURITY;

CREATE POLICY knowledge_promotion_roots_actor_scope ON marketops.knowledge_promotion_roots
USING (marketops.current_actor_id() IS NOT NULL AND workspace_id = marketops.current_workspace_id() AND client_id = marketops.current_client_id() AND project_id = marketops.current_project_id() AND created_by = marketops.current_actor_id())
WITH CHECK (marketops.current_actor_id() IS NOT NULL AND workspace_id = marketops.current_workspace_id() AND client_id = marketops.current_client_id() AND project_id = marketops.current_project_id() AND created_by = marketops.current_actor_id());
CREATE POLICY knowledge_promotion_versions_actor_scope ON marketops.knowledge_promotion_versions
USING (marketops.current_actor_id() IS NOT NULL AND workspace_id = marketops.current_workspace_id() AND client_id = marketops.current_client_id() AND project_id = marketops.current_project_id() AND created_by = marketops.current_actor_id())
WITH CHECK (marketops.current_actor_id() IS NOT NULL AND workspace_id = marketops.current_workspace_id() AND client_id = marketops.current_client_id() AND project_id = marketops.current_project_id() AND created_by = marketops.current_actor_id());
CREATE POLICY knowledge_citations_actor_scope ON marketops.knowledge_citations
USING (marketops.current_actor_id() IS NOT NULL AND workspace_id = marketops.current_workspace_id() AND client_id = marketops.current_client_id() AND project_id = marketops.current_project_id() AND created_by = marketops.current_actor_id())
WITH CHECK (marketops.current_actor_id() IS NOT NULL AND workspace_id = marketops.current_workspace_id() AND client_id = marketops.current_client_id() AND project_id = marketops.current_project_id() AND created_by = marketops.current_actor_id());

REVOKE ALL ON marketops.knowledge_promotion_roots FROM PUBLIC;
REVOKE ALL ON marketops.knowledge_promotion_versions FROM PUBLIC;
REVOKE ALL ON marketops.knowledge_citations FROM PUBLIC;
REVOKE ALL ON FUNCTION marketops.check_knowledge_promotion_transition() FROM PUBLIC;
REVOKE ALL ON FUNCTION marketops.check_knowledge_citation_integrity() FROM PUBLIC;
