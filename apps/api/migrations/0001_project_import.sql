BEGIN;

CREATE SCHEMA marketops;

CREATE TABLE marketops.organizations (
    id uuid PRIMARY KEY,
    name text NOT NULL CHECK (length(btrim(name)) > 0),
    created_at timestamptz NOT NULL DEFAULT transaction_timestamp()
);

CREATE TABLE marketops.workspaces (
    id uuid PRIMARY KEY,
    organization_id uuid NOT NULL,
    name text NOT NULL CHECK (length(btrim(name)) > 0),
    kind text NOT NULL CHECK (kind IN ('personal', 'team')),
    created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    CONSTRAINT workspaces_scope_unique UNIQUE (organization_id, id),
    CONSTRAINT workspaces_organization_fk
        FOREIGN KEY (organization_id)
        REFERENCES marketops.organizations (id)
        ON DELETE RESTRICT
);

CREATE TABLE marketops.clients (
    id uuid PRIMARY KEY,
    organization_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    name text NOT NULL CHECK (length(btrim(name)) > 0),
    created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    CONSTRAINT clients_scope_unique UNIQUE (organization_id, workspace_id, id),
    CONSTRAINT clients_workspace_fk
        FOREIGN KEY (organization_id, workspace_id)
        REFERENCES marketops.workspaces (organization_id, id)
        ON DELETE RESTRICT
);

CREATE TABLE marketops.projects (
    id uuid PRIMARY KEY,
    organization_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    client_id uuid NOT NULL,
    name text NOT NULL CHECK (length(btrim(name)) > 0),
    status text NOT NULL DEFAULT 'planning' CHECK (status IN ('planning', 'active', 'archived')),
    import_request_id text NOT NULL CHECK (length(btrim(import_request_id)) > 0),
    import_manifest_sha256 bytea NOT NULL,
    approved_proposal_artifact_id uuid NOT NULL,
    approved_proposal_version_id uuid NOT NULL,
    approved_proposal_number integer NOT NULL CHECK (approved_proposal_number > 0),
    created_by uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    CONSTRAINT projects_scope_unique UNIQUE (organization_id, workspace_id, client_id, id),
    CONSTRAINT projects_import_request_unique UNIQUE (workspace_id, client_id, import_request_id),
    CONSTRAINT projects_manifest_sha256_32
        CHECK (octet_length(import_manifest_sha256) = 32),
    CONSTRAINT projects_client_fk
        FOREIGN KEY (organization_id, workspace_id, client_id)
        REFERENCES marketops.clients (organization_id, workspace_id, id)
        ON DELETE RESTRICT
);

CREATE TABLE marketops.artifacts (
    id uuid PRIMARY KEY,
    organization_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    client_id uuid NOT NULL,
    project_id uuid NOT NULL,
    kind text NOT NULL CHECK (kind IN ('source', 'proposal')),
    created_by uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    CONSTRAINT artifacts_scope_unique
        UNIQUE (organization_id, workspace_id, client_id, project_id, id),
    CONSTRAINT artifacts_project_fk
        FOREIGN KEY (organization_id, workspace_id, client_id, project_id)
        REFERENCES marketops.projects (organization_id, workspace_id, client_id, id)
        ON DELETE RESTRICT
);

CREATE TABLE marketops.artifact_versions (
    id uuid PRIMARY KEY,
    organization_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    client_id uuid NOT NULL,
    project_id uuid NOT NULL,
    artifact_id uuid NOT NULL,
    proposal_version integer CHECK (proposal_version > 0),
    original_filename text NOT NULL CHECK (length(btrim(original_filename)) > 0),
    media_type text NOT NULL CHECK (length(btrim(media_type)) > 0),
    byte_size bigint NOT NULL CHECK (byte_size >= 0),
    storage_key text NOT NULL CHECK (length(btrim(storage_key)) > 0),
    sha256 bytea NOT NULL,
    approval_status text NOT NULL CHECK (
        approval_status IN ('not_applicable', 'pending', 'approved')
    ),
    approved_by uuid,
    approved_at timestamptz,
    created_by uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    CONSTRAINT artifact_versions_scope_unique
        UNIQUE (organization_id, workspace_id, client_id, project_id, artifact_id, id),
    CONSTRAINT artifact_versions_storage_key_unique UNIQUE (storage_key),
    CONSTRAINT artifact_versions_sha256_32 CHECK (octet_length(sha256) = 32),
    CONSTRAINT artifact_versions_approval_consistent CHECK (
        (
            approval_status = 'approved'
            AND proposal_version IS NOT NULL
            AND approved_by IS NOT NULL
            AND approved_at IS NOT NULL
        )
        OR
        (
            approval_status <> 'approved'
            AND approved_by IS NULL
            AND approved_at IS NULL
        )
    ),
    CONSTRAINT artifact_versions_artifact_fk
        FOREIGN KEY (organization_id, workspace_id, client_id, project_id, artifact_id)
        REFERENCES marketops.artifacts (organization_id, workspace_id, client_id, project_id, id)
        ON DELETE RESTRICT
);

CREATE TABLE marketops.audit_events (
    id uuid PRIMARY KEY,
    organization_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    client_id uuid NOT NULL,
    project_id uuid NOT NULL,
    actor_id uuid NOT NULL,
    action text NOT NULL CHECK (length(btrim(action)) > 0),
    target_type text NOT NULL CHECK (length(btrim(target_type)) > 0),
    target_id uuid NOT NULL,
    event_data jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    CONSTRAINT audit_events_project_fk
        FOREIGN KEY (organization_id, workspace_id, client_id, project_id)
        REFERENCES marketops.projects (organization_id, workspace_id, client_id, id)
        ON DELETE RESTRICT
);

ALTER TABLE marketops.projects
    ADD CONSTRAINT projects_approved_proposal_version_fk
    FOREIGN KEY (
        organization_id,
        workspace_id,
        client_id,
        id,
        approved_proposal_artifact_id,
        approved_proposal_version_id
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
    DEFERRABLE INITIALLY DEFERRED;

CREATE FUNCTION marketops.reject_immutable_row_change()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog
AS $$
BEGIN
    RAISE EXCEPTION USING
        ERRCODE = '55000',
        MESSAGE = format('%s is append-only', TG_TABLE_NAME);
END;
$$;

CREATE TRIGGER artifact_versions_immutable
BEFORE UPDATE OR DELETE ON marketops.artifact_versions
FOR EACH ROW EXECUTE FUNCTION marketops.reject_immutable_row_change();

CREATE TRIGGER artifacts_identity_immutable
BEFORE UPDATE OR DELETE ON marketops.artifacts
FOR EACH ROW EXECUTE FUNCTION marketops.reject_immutable_row_change();

CREATE TRIGGER audit_events_append_only
BEFORE UPDATE OR DELETE ON marketops.audit_events
FOR EACH ROW EXECUTE FUNCTION marketops.reject_immutable_row_change();

CREATE FUNCTION marketops.check_final_approved_proposal()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog
AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM marketops.projects AS project
        WHERE project.id = NEW.id
    ) THEN
        RETURN NEW;
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
        JOIN marketops.artifacts AS artifact
          ON artifact.organization_id = version.organization_id
         AND artifact.workspace_id = version.workspace_id
         AND artifact.client_id = version.client_id
         AND artifact.project_id = version.project_id
         AND artifact.id = version.artifact_id
        WHERE project.id = NEW.id
          AND artifact.kind = 'proposal'
          AND version.proposal_version = project.approved_proposal_number
          AND version.approval_status = 'approved'
          AND version.approved_by = project.created_by
          AND version.approved_at IS NOT NULL
    ) THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = 'approved proposal must be a same-scope proposal with the selected version number';
    END IF;

    RETURN NEW;
END;
$$;

CREATE CONSTRAINT TRIGGER projects_approved_proposal_check
AFTER INSERT OR UPDATE ON marketops.projects
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION marketops.check_final_approved_proposal();

CREATE FUNCTION marketops.current_workspace_id()
RETURNS uuid
LANGUAGE sql
STABLE
PARALLEL SAFE
SET search_path = pg_catalog
RETURN nullif(current_setting('app.workspace_id', true), '')::uuid;

CREATE FUNCTION marketops.current_client_id()
RETURNS uuid
LANGUAGE sql
STABLE
PARALLEL SAFE
SET search_path = pg_catalog
RETURN nullif(current_setting('app.client_id', true), '')::uuid;

CREATE FUNCTION marketops.current_project_id()
RETURNS uuid
LANGUAGE sql
STABLE
PARALLEL SAFE
SET search_path = pg_catalog
RETURN nullif(current_setting('app.project_id', true), '')::uuid;

CREATE FUNCTION marketops.current_actor_id()
RETURNS uuid
LANGUAGE sql
STABLE
PARALLEL SAFE
SET search_path = pg_catalog
RETURN nullif(current_setting('app.actor_id', true), '')::uuid;

ALTER TABLE marketops.organizations ENABLE ROW LEVEL SECURITY;
ALTER TABLE marketops.organizations FORCE ROW LEVEL SECURITY;
ALTER TABLE marketops.workspaces ENABLE ROW LEVEL SECURITY;
ALTER TABLE marketops.workspaces FORCE ROW LEVEL SECURITY;
ALTER TABLE marketops.clients ENABLE ROW LEVEL SECURITY;
ALTER TABLE marketops.clients FORCE ROW LEVEL SECURITY;
ALTER TABLE marketops.projects ENABLE ROW LEVEL SECURITY;
ALTER TABLE marketops.projects FORCE ROW LEVEL SECURITY;
ALTER TABLE marketops.artifacts ENABLE ROW LEVEL SECURITY;
ALTER TABLE marketops.artifacts FORCE ROW LEVEL SECURITY;
ALTER TABLE marketops.artifact_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE marketops.artifact_versions FORCE ROW LEVEL SECURITY;
ALTER TABLE marketops.audit_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE marketops.audit_events FORCE ROW LEVEL SECURITY;

CREATE POLICY organizations_scope ON marketops.organizations
USING (
    EXISTS (
        SELECT 1
        FROM marketops.workspaces AS workspace
        WHERE workspace.organization_id = organizations.id
          AND workspace.id = marketops.current_workspace_id()
    )
)
WITH CHECK (
    EXISTS (
        SELECT 1
        FROM marketops.workspaces AS workspace
        WHERE workspace.organization_id = organizations.id
          AND workspace.id = marketops.current_workspace_id()
    )
);

CREATE POLICY workspaces_scope ON marketops.workspaces
USING (id = marketops.current_workspace_id())
WITH CHECK (id = marketops.current_workspace_id());

CREATE POLICY clients_scope ON marketops.clients
USING (
    workspace_id = marketops.current_workspace_id()
    AND id = marketops.current_client_id()
)
WITH CHECK (
    workspace_id = marketops.current_workspace_id()
    AND id = marketops.current_client_id()
);

CREATE POLICY projects_scope_select ON marketops.projects
FOR SELECT
USING (
    workspace_id = marketops.current_workspace_id()
    AND client_id = marketops.current_client_id()
);

CREATE POLICY projects_scope_insert ON marketops.projects
FOR INSERT
WITH CHECK (
    workspace_id = marketops.current_workspace_id()
    AND client_id = marketops.current_client_id()
    AND id = marketops.current_project_id()
    AND created_by = marketops.current_actor_id()
);

CREATE POLICY projects_scope_update ON marketops.projects
FOR UPDATE
USING (
    workspace_id = marketops.current_workspace_id()
    AND client_id = marketops.current_client_id()
    AND id = marketops.current_project_id()
)
WITH CHECK (
    workspace_id = marketops.current_workspace_id()
    AND client_id = marketops.current_client_id()
    AND id = marketops.current_project_id()
);

CREATE POLICY projects_scope_delete ON marketops.projects
FOR DELETE
USING (
    workspace_id = marketops.current_workspace_id()
    AND client_id = marketops.current_client_id()
    AND id = marketops.current_project_id()
);

CREATE POLICY artifacts_scope ON marketops.artifacts
USING (
    workspace_id = marketops.current_workspace_id()
    AND client_id = marketops.current_client_id()
    AND project_id = marketops.current_project_id()
)
WITH CHECK (
    workspace_id = marketops.current_workspace_id()
    AND client_id = marketops.current_client_id()
    AND project_id = marketops.current_project_id()
    AND created_by = marketops.current_actor_id()
);

CREATE POLICY artifact_versions_scope ON marketops.artifact_versions
USING (
    workspace_id = marketops.current_workspace_id()
    AND client_id = marketops.current_client_id()
    AND project_id = marketops.current_project_id()
)
WITH CHECK (
    workspace_id = marketops.current_workspace_id()
    AND client_id = marketops.current_client_id()
    AND project_id = marketops.current_project_id()
    AND created_by = marketops.current_actor_id()
);

CREATE POLICY audit_events_scope ON marketops.audit_events
USING (
    workspace_id = marketops.current_workspace_id()
    AND client_id = marketops.current_client_id()
    AND project_id = marketops.current_project_id()
)
WITH CHECK (
    workspace_id = marketops.current_workspace_id()
    AND client_id = marketops.current_client_id()
    AND project_id = marketops.current_project_id()
    AND actor_id = marketops.current_actor_id()
);

REVOKE ALL ON SCHEMA marketops FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA marketops FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA marketops FROM PUBLIC;

COMMIT;
