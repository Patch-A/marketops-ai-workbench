CREATE TABLE marketops.source_indexes (
    id uuid PRIMARY KEY,
    organization_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    client_id uuid NOT NULL,
    project_id uuid NOT NULL,
    artifact_version_id uuid NOT NULL,
    source_sha256 bytea NOT NULL CHECK (octet_length(source_sha256) = 32),
    parser_version text NOT NULL CHECK (
        parser_version ~ '^[a-z0-9][a-z0-9._-]{0,63}$'
    ),
    chunker_version text NOT NULL CHECK (
        chunker_version ~ '^[a-z0-9][a-z0-9._-]{0,63}$'
    ),
    status text NOT NULL CHECK (status IN ('ready', 'withdrawn')),
    index_sha256 bytea NOT NULL CHECK (octet_length(index_sha256) = 32),
    created_by uuid NOT NULL,
    created_at timestamptz NOT NULL,
    withdrawn_by uuid,
    withdrawn_at timestamptz,
    CONSTRAINT source_indexes_pipeline_unique
        UNIQUE (artifact_version_id, parser_version, chunker_version),
    CONSTRAINT source_indexes_scope_identity_unique UNIQUE (
        organization_id, workspace_id, client_id, project_id, id,
        artifact_version_id, source_sha256, parser_version, chunker_version,
        created_by
    ),
    CONSTRAINT source_indexes_artifact_version_fk
        FOREIGN KEY (artifact_version_id)
        REFERENCES marketops.artifact_versions (id)
        ON DELETE RESTRICT,
    CONSTRAINT source_indexes_withdrawal_consistent CHECK (
        (
            status = 'ready'
            AND withdrawn_by IS NULL
            AND withdrawn_at IS NULL
        )
        OR (
            status = 'withdrawn'
            AND withdrawn_by IS NOT NULL
            AND withdrawn_at IS NOT NULL
            AND withdrawn_at >= created_at
        )
    )
);

CREATE TABLE marketops.source_chunks (
    id uuid PRIMARY KEY,
    index_id uuid NOT NULL,
    organization_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    client_id uuid NOT NULL,
    project_id uuid NOT NULL,
    artifact_version_id uuid NOT NULL,
    ordinal integer NOT NULL CHECK (ordinal >= 0),
    chunk_text text NOT NULL CHECK (
        length(chunk_text) BETWEEN 1 AND 2000
        AND chunk_text = btrim(chunk_text)
    ),
    content_sha256 bytea NOT NULL CHECK (octet_length(content_sha256) = 32),
    location jsonb NOT NULL CHECK (
        jsonb_typeof(location) = 'object'
        AND location <> '{}'::jsonb
        AND octet_length(location::text) <= 4000
    ),
    source_sha256 bytea NOT NULL CHECK (octet_length(source_sha256) = 32),
    parser_version text NOT NULL CHECK (
        parser_version ~ '^[a-z0-9][a-z0-9._-]{0,63}$'
    ),
    chunker_version text NOT NULL CHECK (
        chunker_version ~ '^[a-z0-9][a-z0-9._-]{0,63}$'
    ),
    created_by uuid NOT NULL,
    created_at timestamptz NOT NULL,
    CONSTRAINT source_chunks_index_ordinal_unique UNIQUE (index_id, ordinal),
    CONSTRAINT source_chunks_index_scope_fk FOREIGN KEY (
        organization_id, workspace_id, client_id, project_id, index_id,
        artifact_version_id, source_sha256, parser_version, chunker_version,
        created_by
    ) REFERENCES marketops.source_indexes (
        organization_id, workspace_id, client_id, project_id, id,
        artifact_version_id, source_sha256, parser_version, chunker_version,
        created_by
    ) ON DELETE RESTRICT
);

CREATE INDEX source_indexes_project_ready_idx ON marketops.source_indexes (
    workspace_id, client_id, project_id, created_by, status, created_at, id
);

CREATE INDEX source_chunks_project_index_idx ON marketops.source_chunks (
    workspace_id, client_id, project_id, created_by, index_id, ordinal
);

CREATE FUNCTION marketops.check_source_index_integrity()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog
AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM marketops.artifact_versions AS version
        WHERE version.id = NEW.artifact_version_id
          AND version.organization_id = NEW.organization_id
          AND version.workspace_id = NEW.workspace_id
          AND version.client_id = NEW.client_id
          AND version.project_id = NEW.project_id
          AND version.sha256 = NEW.source_sha256
          AND version.created_by = NEW.created_by
    ) THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = 'source index must match its artifact version scope and hash';
    END IF;
    RETURN NEW;
END;
$$;

CREATE CONSTRAINT TRIGGER source_indexes_integrity
AFTER INSERT OR UPDATE ON marketops.source_indexes
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION marketops.check_source_index_integrity();

CREATE FUNCTION marketops.enforce_source_index_transition()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog
AS $$
BEGIN
    IF OLD.status <> 'ready' OR NEW.status <> 'withdrawn' THEN
        RAISE EXCEPTION USING ERRCODE = '55000', MESSAGE = 'invalid source index transition';
    END IF;
    IF ROW(
        NEW.id, NEW.organization_id, NEW.workspace_id, NEW.client_id,
        NEW.project_id, NEW.artifact_version_id, NEW.source_sha256,
        NEW.parser_version, NEW.chunker_version, NEW.index_sha256,
        NEW.created_by, NEW.created_at
    ) IS DISTINCT FROM ROW(
        OLD.id, OLD.organization_id, OLD.workspace_id, OLD.client_id,
        OLD.project_id, OLD.artifact_version_id, OLD.source_sha256,
        OLD.parser_version, OLD.chunker_version, OLD.index_sha256,
        OLD.created_by, OLD.created_at
    ) THEN
        RAISE EXCEPTION USING ERRCODE = '55000', MESSAGE = 'source index identity is immutable';
    END IF;
    IF NEW.withdrawn_by IS DISTINCT FROM marketops.current_actor_id() THEN
        RAISE EXCEPTION USING ERRCODE = '42501', MESSAGE = 'source index withdrawal actor mismatch';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER source_indexes_controlled_update
BEFORE UPDATE ON marketops.source_indexes
FOR EACH ROW EXECUTE FUNCTION marketops.enforce_source_index_transition();

CREATE TRIGGER source_indexes_delete_immutable
BEFORE DELETE OR TRUNCATE ON marketops.source_indexes
FOR EACH STATEMENT EXECUTE FUNCTION marketops.reject_immutable_row_change();

CREATE FUNCTION marketops.enforce_source_chunk_change()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog
AS $$
BEGIN
    IF TG_OP = 'DELETE' AND EXISTS (
        SELECT 1
        FROM marketops.source_indexes AS source_index
        WHERE source_index.id = OLD.index_id
          AND source_index.status = 'withdrawn'
          AND source_index.withdrawn_by = marketops.current_actor_id()
    ) THEN
        RETURN OLD;
    END IF;
    RAISE EXCEPTION USING
        ERRCODE = '55000',
        MESSAGE = 'source chunks change only during authorized index withdrawal';
END;
$$;

CREATE TRIGGER source_chunks_controlled_change
BEFORE UPDATE OR DELETE ON marketops.source_chunks
FOR EACH ROW EXECUTE FUNCTION marketops.enforce_source_chunk_change();

CREATE TRIGGER source_chunks_truncate_immutable
BEFORE TRUNCATE ON marketops.source_chunks
FOR EACH STATEMENT EXECUTE FUNCTION marketops.reject_immutable_row_change();

ALTER TABLE marketops.source_indexes ENABLE ROW LEVEL SECURITY;
ALTER TABLE marketops.source_indexes FORCE ROW LEVEL SECURITY;
ALTER TABLE marketops.source_chunks ENABLE ROW LEVEL SECURITY;
ALTER TABLE marketops.source_chunks FORCE ROW LEVEL SECURITY;

CREATE POLICY source_indexes_actor_scope ON marketops.source_indexes
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

CREATE POLICY source_chunks_actor_scope ON marketops.source_chunks
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

REVOKE ALL ON marketops.source_indexes FROM PUBLIC;
REVOKE ALL ON marketops.source_chunks FROM PUBLIC;
REVOKE ALL ON FUNCTION marketops.check_source_index_integrity() FROM PUBLIC;
REVOKE ALL ON FUNCTION marketops.enforce_source_index_transition() FROM PUBLIC;
REVOKE ALL ON FUNCTION marketops.enforce_source_chunk_change() FROM PUBLIC;
