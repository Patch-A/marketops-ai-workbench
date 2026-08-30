from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
MIGRATION = ROOT / "apps" / "api" / "migrations" / "0008_project_learning.sql"
ADAPTER = ROOT / "apps" / "api" / "marketops_learning" / "postgres.py"
APPLICATION = ROOT / "apps" / "api" / "marketops_learning" / "application.py"


class LearningPostgresContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.migration = MIGRATION.read_text(encoding="utf-8")
        cls.adapter = ADAPTER.read_text(encoding="utf-8")
        cls.application = APPLICATION.read_text(encoding="utf-8")

    def test_migration_is_append_only_project_scoped_and_closed(self) -> None:
        for table in (
            "project_capsules",
            "project_outcomes",
            "project_retrospectives",
            "knowledge_items",
            "knowledge_item_versions",
            "knowledge_item_evidence",
        ):
            with self.subTest(table=table):
                self.assertIn(f"CREATE TABLE marketops.{table}", self.migration)
                self.assertIn(
                    f"ALTER TABLE marketops.{table} FORCE ROW LEVEL SECURITY",
                    self.migration,
                )
                self.assertIn(f"REVOKE ALL ON marketops.{table} FROM PUBLIC", self.migration)
                self.assertIn(f"{table}_immutable", self.migration)
                self.assertIn(f"{table}_truncate_immutable", self.migration)
        for marker in (
            "source_type IN ('artifact_version', 'task_execution', 'schedule_snapshot')",
            "classification = 'outcome_observation'",
            "classification <> 'non_reusable_note' OR reusable_candidate = false",
            "scope = 'project'",
            "status = 'candidate'",
            "binding_sha256 bytea NOT NULL",
        ):
            self.assertIn(marker, self.migration)

    def test_adapter_resolves_sources_inside_transaction_scope(self) -> None:
        for marker in (
            "set_config('app.workspace_id',$1,true)",
            "set_config('app.client_id',$2,true)",
            "set_config('app.project_id',$3,true)",
            "set_config('app.actor_id',$4,true)",
            "FROM marketops.artifact_versions WHERE id=$1",
            "FROM marketops.wbs_task_execution_updates WHERE id=$1",
            "FROM marketops.schedule_snapshots WHERE id=$1",
            "source.source_id == schedule_id",
            "FeedbackSourceReference(source.source_type,source.source_id,project_id,binding)",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.adapter)
        self.assertNotIn("postgresql://", self.adapter)

    def test_finalization_is_atomic_replay_safe_and_audit_redacted(self) -> None:
        for marker in (
            "async with self._transaction(scope,project_id)",
            "pg_catalog.pg_advisory_xact_lock",
            "capsule_digest=decode($6,'hex')",
            "INSERT INTO marketops.project_capsules",
            "INSERT INTO marketops.project_outcomes",
            "INSERT INTO marketops.project_retrospectives",
            "INSERT INTO marketops.knowledge_items",
            "INSERT INTO marketops.knowledge_item_versions",
            "INSERT INTO marketops.knowledge_item_evidence",
            "'project_capsule.finalized'",
            "'outcomeCount'",
            "'retrospectiveCount'",
            "'knowledgeCount'",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.adapter)
        audit_sql = self.adapter.split("_INSERT_AUDIT_SQL", 1)[1].split("_INSERT_OUTCOME_SQL", 1)[0]
        self.assertNotIn("finding", audit_sql)
        self.assertNotIn("actual_value", audit_sql)

    def test_application_and_repository_expose_only_atomic_finalization(self) -> None:
        self.assertIn("async def finalize_capsule", self.application)
        self.assertNotIn("async def record_outcome", self.application)
        self.assertNotIn("async def record_retrospective", self.application)
        for method in (
            "read_capsule",
            "list_capsules",
            "list_knowledge",
            "read_knowledge",
        ):
            with self.subTest(method=method):
                self.assertIn(f"async def {method}", self.adapter)
        self.assertIn("transaction(readonly=True)", self.adapter)
        self.assertIn("_KNOWLEDGE_EVIDENCE_SQL", self.adapter)
        self.assertIn("_KNOWLEDGE_VERSIONS_SQL", self.adapter)


if __name__ == "__main__":
    unittest.main()
