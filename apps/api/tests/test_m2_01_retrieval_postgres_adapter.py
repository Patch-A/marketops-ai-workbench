from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
MIGRATION = ROOT / "apps" / "api" / "migrations" / "0007_source_retrieval.sql"
ADAPTER = ROOT / "apps" / "api" / "marketops_retrieval" / "postgres.py"


class RetrievalPostgresContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.migration = MIGRATION.read_text(encoding="utf-8")
        cls.adapter = ADAPTER.read_text(encoding="utf-8")

    def test_migration_forces_scope_and_source_integrity(self):
        required = (
            "CREATE TABLE marketops.source_indexes",
            "CREATE TABLE marketops.source_chunks",
            "source_indexes_artifact_version_fk",
            "CREATE CONSTRAINT TRIGGER source_indexes_integrity",
            "version.sha256 = NEW.source_sha256",
            "ALTER TABLE marketops.source_indexes FORCE ROW LEVEL SECURITY",
            "ALTER TABLE marketops.source_chunks FORCE ROW LEVEL SECURITY",
            "workspace_id = marketops.current_workspace_id()",
            "client_id = marketops.current_client_id()",
            "project_id = marketops.current_project_id()",
            "created_by = marketops.current_actor_id()",
            "REVOKE ALL ON marketops.source_indexes FROM PUBLIC",
            "REVOKE ALL ON marketops.source_chunks FROM PUBLIC",
        )
        for marker in required:
            with self.subTest(marker=marker):
                self.assertIn(marker, self.migration)

    def test_withdrawal_is_the_only_mutation_and_controls_chunk_deletion(self):
        required = (
            "OLD.status <> 'ready' OR NEW.status <> 'withdrawn'",
            "source index identity is immutable",
            "NEW.withdrawn_by IS DISTINCT FROM marketops.current_actor_id()",
            "source_indexes_delete_immutable",
            "source chunks change only during authorized index withdrawal",
            "source_chunks_truncate_immutable",
        )
        for marker in required:
            with self.subTest(marker=marker):
                self.assertIn(marker, self.migration)

    def test_adapter_sets_scope_before_queries_and_uses_closed_writes(self):
        required = (
            "set_config('app.workspace_id', $1, true)",
            "set_config('app.client_id', $2, true)",
            "set_config('app.project_id', $3, true)",
            "set_config('app.actor_id', $4, true)",
            "pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended($1, 0))",
            "decode($7, 'hex')",
            "$11::jsonb",
            "status = 'withdrawn'",
            "DELETE FROM marketops.source_chunks WHERE index_id = $1",
            '"source_index.created"',
            '"source_index.withdrawn"',
        )
        for marker in required:
            with self.subTest(marker=marker):
                self.assertIn(marker, self.adapter)
        self.assertNotIn("format(", self.adapter)
        self.assertNotIn("postgresql://", self.adapter)


if __name__ == "__main__":
    unittest.main()
