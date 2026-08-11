from __future__ import annotations

import unittest

from scripts.run_m1_01_postgres_gate import (
    APPLICATION_ROLE,
    EXPECTED_COLUMN_UPDATE_PRIVILEGES,
    EXPECTED_FUNCTION_EXECUTE,
    EXPECTED_RELATION_PRIVILEGES,
    _validate_application_attestation,
    ensure_login_role,
    validate_server_log_safety,
    validate_postgres_image,
)


def baseline_role() -> dict[str, object]:
    return {
        "rolname": APPLICATION_ROLE,
        "rolcanlogin": True,
        "rolsuper": False,
        "rolcreatedb": False,
        "rolcreaterole": False,
        "rolinherit": False,
        "rolreplication": False,
        "rolbypassrls": False,
    }


def rows_for_pairs(pairs: frozenset[tuple[str, str]]) -> list[dict[str, object]]:
    return [
        {
            "object_name": object_name,
            "privilege": privilege,
            "granted": True,
            "grantable": False,
        }
        for object_name, privilege in sorted(pairs)
    ]


def column_rows() -> list[dict[str, object]]:
    rows = [
        {
            "object_name": object_name,
            "column_name": "id",
            "privilege": privilege,
            "granted": True,
            "grantable": False,
        }
        for object_name, privilege in sorted(EXPECTED_RELATION_PRIVILEGES)
    ]
    rows.extend(
        {
            "object_name": object_name,
            "column_name": column_name,
            "privilege": "UPDATE",
            "granted": True,
            "grantable": False,
        }
        for object_name, column_name in sorted(EXPECTED_COLUMN_UPDATE_PRIVILEGES)
    )
    return rows


def valid_attestation(**overrides):
    values = {
        "role": baseline_role(),
        "memberships": [],
        "schema_privileges": [
            {"privilege": "CREATE", "granted": False, "grantable": False},
            {"privilege": "USAGE", "granted": True, "grantable": False},
        ],
        "relation_privileges": rows_for_pairs(EXPECTED_RELATION_PRIVILEGES),
        "column_privileges": column_rows(),
        "sequence_privileges": [],
        "function_privileges": [
            {
                "function_signature": signature,
                "privilege": "EXECUTE",
                "granted": True,
                "grantable": False,
            }
            for signature in sorted(EXPECTED_FUNCTION_EXECUTE)
        ],
    }
    values.update(overrides)
    return _validate_application_attestation(**values)


class FakeRoleConnection:
    def __init__(self, exists: bool):
        self.exists = exists
        self.statements: list[str] = []

    async def fetchval(self, query: str, *args):
        if "quote_literal" in query:
            return "'test-password'"
        if "SELECT EXISTS" in query:
            return self.exists
        raise AssertionError(f"unexpected fetchval query: {query}")

    async def execute(self, statement: str):
        self.statements.append(statement)


class RuntimeGateLogSafetyTests(unittest.TestCase):
    def test_restrictive_failure_logging_is_attested(self):
        self.assertEqual(
            validate_server_log_safety("terse", "panic"),
            {"errorVerbosity": "terse", "minimumErrorStatement": "panic"},
        )

    def test_context_or_error_statement_logging_fails_closed(self):
        for verbosity, threshold in (("default", "panic"), ("terse", "error")):
            with self.subTest(verbosity=verbosity, threshold=threshold):
                with self.assertRaisesRegex(RuntimeError, "could expose"):
                    validate_server_log_safety(verbosity, threshold)


class RuntimeGateRoleProvisioningTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_role_explicitly_disables_replication(self):
        connection = FakeRoleConnection(exists=False)

        await ensure_login_role(connection, APPLICATION_ROLE, "test-password")

        self.assertEqual(len(connection.statements), 1)
        self.assertIn("CREATE ROLE marketops_app", connection.statements[0])
        self.assertIn("NOREPLICATION", connection.statements[0])

    async def test_alter_role_explicitly_disables_replication(self):
        connection = FakeRoleConnection(exists=True)

        await ensure_login_role(connection, APPLICATION_ROLE, "test-password")

        self.assertEqual(len(connection.statements), 1)
        self.assertIn("ALTER ROLE marketops_app", connection.statements[0])
        self.assertIn("NOREPLICATION", connection.statements[0])


class RuntimeGateImagePinTests(unittest.TestCase):
    pinned = (
        "postgres@sha256:"
        "a02db8cac496f15b094798a38254f14d6e00741f709360e5e00bb6668ea31636"
    )

    def test_reviewed_service_digest_passes(self):
        validate_postgres_image(self.pinned, self.pinned)

    def test_mutable_tag_fails_closed(self):
        with self.assertRaisesRegex(RuntimeError, "immutable canonical digest"):
            validate_postgres_image("postgres:18.4", self.pinned)

    def test_unreviewed_canonical_digest_fails_closed(self):
        unreviewed = "postgres@sha256:" + "0" * 64

        with self.assertRaisesRegex(RuntimeError, "reviewed service image digest"):
            validate_postgres_image(unreviewed, unreviewed)

    def test_container_digest_mismatch_fails_closed(self):
        other = "postgres@sha256:" + "0" * 64

        with self.assertRaisesRegex(RuntimeError, "service-container RepoDigest"):
            validate_postgres_image(self.pinned, other)


class RuntimeGatePrivilegeAttestationTests(unittest.TestCase):
    def test_exact_privilege_matrix_passes(self):
        evidence = valid_attestation()

        self.assertEqual(evidence["memberOf"], [])
        self.assertEqual(evidence["schemaPrivileges"], ["USAGE"])
        self.assertEqual(
            evidence["functionExecute"], sorted(EXPECTED_FUNCTION_EXECUTE)
        )
        self.assertEqual(
            evidence["columnUpdatePrivileges"],
            [
                "marketops.extraction_runs.created_by",
                "marketops.projects.created_by",
            ],
        )

    def test_replication_role_fails_closed(self):
        role = baseline_role()
        role["rolreplication"] = True

        with self.assertRaisesRegex(RuntimeError, "attributes drifted"):
            valid_attestation(role=role)

    def test_any_member_of_edge_fails_closed(self):
        with self.assertRaisesRegex(RuntimeError, "unexpected memberships"):
            valid_attestation(memberships=[{"granted_role": "reporting_owner"}])

    def test_schema_create_fails_closed(self):
        schema_privileges = [
            {"privilege": "CREATE", "granted": True, "grantable": False},
            {"privilege": "USAGE", "granted": True, "grantable": False},
        ]

        with self.assertRaisesRegex(RuntimeError, "schema privileges"):
            valid_attestation(schema_privileges=schema_privileges)

    def test_schema_grant_option_fails_closed(self):
        schema_privileges = [
            {"privilege": "CREATE", "granted": False, "grantable": False},
            {"privilege": "USAGE", "granted": True, "grantable": True},
        ]

        with self.assertRaisesRegex(RuntimeError, "schema.*grant options"):
            valid_attestation(schema_privileges=schema_privileges)

    def test_extra_relation_privilege_fails_closed(self):
        relation_privileges = rows_for_pairs(
            EXPECTED_RELATION_PRIVILEGES | {("artifacts", "UPDATE")}
        )

        with self.assertRaisesRegex(RuntimeError, "unexpected=.*UPDATE"):
            valid_attestation(relation_privileges=relation_privileges)

    def test_missing_relation_privilege_fails_closed(self):
        relation_privileges = rows_for_pairs(
            EXPECTED_RELATION_PRIVILEGES - {("projects", "INSERT")}
        )

        with self.assertRaisesRegex(RuntimeError, "missing=.*INSERT"):
            valid_attestation(relation_privileges=relation_privileges)

    def test_relation_grant_option_fails_closed(self):
        relation_privileges = rows_for_pairs(EXPECTED_RELATION_PRIVILEGES)
        relation_privileges[0]["grantable"] = True

        with self.assertRaisesRegex(RuntimeError, "relation.*grant options"):
            valid_attestation(relation_privileges=relation_privileges)

    def test_column_only_update_fails_closed(self):
        column_privileges = column_rows()
        column_privileges.append(
            {
                "object_name": "audit_events",
                "column_name": "created_by",
                "privilege": "UPDATE",
                "granted": True,
                "grantable": False,
            }
        )

        with self.assertRaisesRegex(RuntimeError, "column UPDATE privileges"):
            valid_attestation(column_privileges=column_privileges)

    def test_missing_required_column_lock_privilege_fails_closed(self):
        column_privileges = [
            row
            for row in column_rows()
            if not (
                row["object_name"] == "extraction_runs"
                and row["privilege"] == "UPDATE"
            )
        ]

        with self.assertRaisesRegex(RuntimeError, "column UPDATE privileges"):
            valid_attestation(column_privileges=column_privileges)

    def test_sequence_privilege_fails_closed(self):
        sequence_privileges = [
            {
                "object_name": "unexpected_sequence",
                "privilege": "USAGE",
                "granted": True,
                "grantable": False,
            }
        ]

        with self.assertRaisesRegex(RuntimeError, "sequence privileges"):
            valid_attestation(sequence_privileges=sequence_privileges)

    def test_column_grant_option_fails_closed(self):
        column_privileges = column_rows()
        column_privileges[0]["grantable"] = True

        with self.assertRaisesRegex(RuntimeError, "column.*grant options"):
            valid_attestation(column_privileges=column_privileges)

    def test_sequence_grant_option_fails_closed(self):
        sequence_privileges = [
            {
                "object_name": "unexpected_sequence",
                "privilege": "USAGE",
                "granted": False,
                "grantable": True,
            }
        ]

        with self.assertRaisesRegex(RuntimeError, "sequence.*grant options"):
            valid_attestation(sequence_privileges=sequence_privileges)

    def test_unexpected_function_execute_fails_closed(self):
        function_privileges = [
            {
                "function_signature": signature,
                "privilege": "EXECUTE",
                "granted": True,
                "grantable": False,
            }
            for signature in sorted(EXPECTED_FUNCTION_EXECUTE | {"reject_immutable_row_change()"})
        ]

        with self.assertRaisesRegex(RuntimeError, "function EXECUTE"):
            valid_attestation(function_privileges=function_privileges)

    def test_function_grant_option_fails_closed(self):
        function_privileges = [
            {
                "function_signature": signature,
                "privilege": "EXECUTE",
                "granted": True,
                "grantable": signature == "current_actor_id()",
            }
            for signature in sorted(EXPECTED_FUNCTION_EXECUTE)
        ]

        with self.assertRaisesRegex(RuntimeError, "function.*grant options"):
            valid_attestation(function_privileges=function_privileges)


if __name__ == "__main__":
    unittest.main()
