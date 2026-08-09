"""Forward-only PostgreSQL migration runner for reviewed raw SQL files."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, Sequence


MIGRATIONS_DIRECTORY = Path(__file__).resolve().parents[1] / "migrations"
MIGRATION_NAME = re.compile(r"^(?P<number>[0-9]{4})_[a-z0-9_]+\.sql$")
ADVISORY_LOCK_ID = 0x4D41524B45544F50  # "MARKETOP" as a signed-safe bigint.

REGISTRY_SETUP_SQL = """
CREATE SCHEMA IF NOT EXISTS marketops;

CREATE TABLE IF NOT EXISTS marketops.schema_migrations (
    migration_name text PRIMARY KEY,
    sha256 bytea NOT NULL,
    applied_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    CONSTRAINT schema_migrations_sha256_32 CHECK (octet_length(sha256) = 32)
);

CREATE OR REPLACE FUNCTION marketops.reject_schema_migration_change()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog
AS $$
BEGIN
    RAISE EXCEPTION USING
        ERRCODE = '55000',
        MESSAGE = 'schema migration registry is immutable';
END;
$$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_catalog.pg_trigger
        WHERE tgrelid = 'marketops.schema_migrations'::regclass
          AND tgname = 'schema_migrations_rows_immutable'
          AND NOT tgisinternal
    ) THEN
        CREATE TRIGGER schema_migrations_rows_immutable
        BEFORE UPDATE OR DELETE ON marketops.schema_migrations
        FOR EACH ROW EXECUTE FUNCTION marketops.reject_schema_migration_change();
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_catalog.pg_trigger
        WHERE tgrelid = 'marketops.schema_migrations'::regclass
          AND tgname = 'schema_migrations_truncate_immutable'
          AND NOT tgisinternal
    ) THEN
        CREATE TRIGGER schema_migrations_truncate_immutable
        BEFORE TRUNCATE ON marketops.schema_migrations
        FOR EACH STATEMENT EXECUTE FUNCTION marketops.reject_schema_migration_change();
    END IF;
END;
$$;

REVOKE ALL ON TABLE marketops.schema_migrations FROM PUBLIC;
REVOKE ALL (migration_name, sha256, applied_at)
    ON TABLE marketops.schema_migrations FROM PUBLIC;
REVOKE ALL ON FUNCTION marketops.reject_schema_migration_change() FROM PUBLIC;
""".strip()

LOCK_SQL = "SELECT pg_advisory_xact_lock($1)"
READ_REGISTRY_SQL = """
SELECT migration_name, encode(sha256, 'hex') AS sha256
FROM marketops.schema_migrations
ORDER BY migration_name
""".strip()
INSERT_REGISTRY_SQL = """
INSERT INTO marketops.schema_migrations (migration_name, sha256)
VALUES ($1, $2)
""".strip()

REGISTRY_ATTESTATION_SQL = r"""
SELECT
    current_user AS current_user,
    pg_catalog.pg_get_userbyid(namespace.nspowner) AS schema_owner,
    pg_catalog.pg_get_userbyid(registry.relowner) AS table_owner,
    pg_catalog.pg_get_userbyid(trigger_function.proowner) AS function_owner,
    registry.relkind::text AS relkind,
    registry.relpersistence::text AS relpersistence,
    registry.relhasrules AS has_rules,
    registry.relhassubclass AS has_subclass,
    registry.relrowsecurity AS rls_enabled,
    registry.relforcerowsecurity AS force_rls_enabled,
    (
        SELECT count(*)::integer
        FROM pg_catalog.pg_inherits AS inheritance_record
        WHERE inheritance_record.inhparent = registry.oid
           OR inheritance_record.inhrelid = registry.oid
    ) AS inheritance_edge_count,
    (
        SELECT pg_catalog.array_agg(
            pg_catalog.format(
                '%s:%s:%s',
                attribute.attname,
                pg_catalog.format_type(attribute.atttypid, attribute.atttypmod),
                CASE
                    WHEN attribute.attnotnull THEN 'not-null'
                    ELSE 'nullable'
                END
            )
            ORDER BY attribute.attnum
        )
        FROM pg_catalog.pg_attribute AS attribute
        WHERE attribute.attrelid = registry.oid
          AND attribute.attnum > 0
          AND NOT attribute.attisdropped
    ) AS columns,
    (
        SELECT pg_catalog.array_agg(
            constraint_record.conname || ':' ||
            pg_catalog.pg_get_constraintdef(constraint_record.oid, false) || ':' ||
            CASE
                WHEN constraint_record.convalidated THEN 'validated'
                ELSE 'not-validated'
            END
            ORDER BY constraint_record.conname
        )
        FROM pg_catalog.pg_constraint AS constraint_record
        WHERE constraint_record.conrelid = registry.oid
    ) AS constraints,
    (
        SELECT count(*)::integer
        FROM pg_catalog.pg_trigger AS trigger_record
        WHERE trigger_record.tgrelid = registry.oid
          AND NOT trigger_record.tgisinternal
    ) AS trigger_count,
    (
        SELECT count(*)::integer
        FROM pg_catalog.pg_trigger AS trigger_record
        JOIN pg_catalog.pg_proc AS trigger_proc
          ON trigger_proc.oid = trigger_record.tgfoid
        JOIN pg_catalog.pg_namespace AS trigger_namespace
          ON trigger_namespace.oid = trigger_proc.pronamespace
        WHERE trigger_record.tgrelid = registry.oid
          AND NOT trigger_record.tgisinternal
          AND trigger_record.tgname = 'schema_migrations_rows_immutable'
          AND trigger_record.tgenabled = 'O'
          AND trigger_record.tgtype = 27
          AND trigger_record.tgattr = ''::pg_catalog.int2vector
          AND trigger_record.tgqual IS NULL
          AND trigger_record.tgnargs = 0
          AND trigger_namespace.nspname = 'marketops'
          AND trigger_proc.proname = 'reject_schema_migration_change'
          AND trigger_proc.oid = trigger_function.oid
    ) AS row_trigger_count,
    (
        SELECT count(*)::integer
        FROM pg_catalog.pg_trigger AS trigger_record
        JOIN pg_catalog.pg_proc AS trigger_proc
          ON trigger_proc.oid = trigger_record.tgfoid
        JOIN pg_catalog.pg_namespace AS trigger_namespace
          ON trigger_namespace.oid = trigger_proc.pronamespace
        WHERE trigger_record.tgrelid = registry.oid
          AND NOT trigger_record.tgisinternal
          AND trigger_record.tgname = 'schema_migrations_truncate_immutable'
          AND trigger_record.tgenabled = 'O'
          AND trigger_record.tgtype = 34
          AND trigger_record.tgqual IS NULL
          AND trigger_record.tgnargs = 0
          AND trigger_namespace.nspname = 'marketops'
          AND trigger_proc.proname = 'reject_schema_migration_change'
          AND trigger_proc.oid = trigger_function.oid
    ) AS truncate_trigger_count,
    (
        SELECT count(*)::integer
        FROM pg_catalog.aclexplode(
            COALESCE(
                namespace.nspacl,
                pg_catalog.acldefault('n', namespace.nspowner)
            )
        ) AS acl
        WHERE acl.grantee = 0
    ) AS public_schema_privilege_count,
    (
        SELECT COALESCE(
            pg_catalog.array_agg(
                pg_catalog.format(
                    '%s:%s:%s',
                    pg_catalog.pg_get_userbyid(acl.grantee),
                    acl.privilege_type,
                    CASE WHEN acl.is_grantable THEN 'grantable' ELSE 'not-grantable' END
                )
                ORDER BY
                    pg_catalog.pg_get_userbyid(acl.grantee),
                    acl.privilege_type,
                    acl.is_grantable
            ),
            ARRAY[]::text[]
        )
        FROM pg_catalog.aclexplode(
            COALESCE(
                namespace.nspacl,
                pg_catalog.acldefault('n', namespace.nspowner)
            )
        ) AS acl
        WHERE acl.grantee <> 0
          AND acl.grantee <> namespace.nspowner
    ) AS non_owner_schema_privileges,
    (
        SELECT count(*)::integer
        FROM pg_catalog.aclexplode(
            COALESCE(registry.relacl, pg_catalog.acldefault('r', registry.relowner))
        ) AS acl
        WHERE acl.grantee = 0
    ) AS public_table_privilege_count,
    (
        SELECT count(*)::integer
        FROM pg_catalog.aclexplode(
            COALESCE(registry.relacl, pg_catalog.acldefault('r', registry.relowner))
        ) AS acl
        WHERE acl.grantee <> registry.relowner
    ) AS non_owner_table_privilege_count,
    (
        SELECT count(*)::integer
        FROM pg_catalog.pg_attribute AS privileged_attribute
        CROSS JOIN LATERAL pg_catalog.aclexplode(
            privileged_attribute.attacl
        ) AS acl
        WHERE privileged_attribute.attrelid = registry.oid
          AND privileged_attribute.attnum > 0
          AND NOT privileged_attribute.attisdropped
          AND privileged_attribute.attacl IS NOT NULL
          AND acl.grantee <> registry.relowner
    ) AS non_owner_column_privilege_count,
    (
        SELECT count(*)::integer
        FROM pg_catalog.aclexplode(
            COALESCE(
                trigger_function.proacl,
                pg_catalog.acldefault('f', trigger_function.proowner)
            )
        ) AS acl
        WHERE acl.grantee = 0
    ) AS public_function_privilege_count,
    (
        SELECT count(*)::integer
        FROM pg_catalog.aclexplode(
            COALESCE(
                trigger_function.proacl,
                pg_catalog.acldefault('f', trigger_function.proowner)
            )
        ) AS acl
        WHERE acl.grantee <> trigger_function.proowner
    ) AS non_owner_function_privilege_count,
    pg_catalog.has_table_privilege(registry.oid, 'SELECT,INSERT')
        AS owner_can_read_and_insert
FROM pg_catalog.pg_namespace AS namespace
JOIN pg_catalog.pg_class AS registry
  ON registry.relnamespace = namespace.oid
JOIN pg_catalog.pg_proc AS trigger_function
  ON trigger_function.pronamespace = namespace.oid
 AND trigger_function.proname = 'reject_schema_migration_change'
 AND trigger_function.pronargs = 0
WHERE namespace.nspname = 'marketops'
  AND registry.relname = 'schema_migrations'
""".strip()

EXPECTED_REGISTRY_COLUMNS = (
    "migration_name:text:not-null",
    "sha256:bytea:not-null",
    "applied_at:timestamp with time zone:not-null",
)
EXPECTED_REGISTRY_CONSTRAINTS = (
    "schema_migrations_pkey:PRIMARY KEY (migration_name):validated",
    "schema_migrations_sha256_32:CHECK ((octet_length(sha256) = 32)):validated",
)
# Callers name application roles explicitly. The runner itself derives the only
# allowed ACL shape: non-grantable USAGE on the application schema.
DEFAULT_ALLOWED_SCHEMA_USAGE_ROLES: tuple[str, ...] = ()
ROLE_NAME = re.compile(r"[a-z_][a-z0-9_]{0,62}")

SINGLE_WORD_TRANSACTION_COMMANDS = frozenset(
    {"abort", "begin", "commit", "end", "release", "rollback", "savepoint"}
)


class Transaction(Protocol):
    async def __aenter__(self) -> Any: ...

    async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> Any: ...


class MigrationConnection(Protocol):
    def transaction(self) -> Transaction: ...

    async def execute(self, query: str, *args: Any) -> Any: ...

    async def fetch(self, query: str, *args: Any) -> Sequence[Any]: ...


class MigrationError(RuntimeError):
    """Base error for a rejected or failed migration run."""


class InvalidMigrationSetError(MigrationError):
    """The local migration directory is not a valid forward-only sequence."""


class MigrationHistoryError(MigrationError):
    """The registry is not an exact prefix of the local migration set."""


class MigrationDriftError(MigrationError):
    """An applied migration's raw-byte checksum no longer matches."""


class RegistryContractError(MigrationError):
    """The database migration registry does not match its frozen contract."""


@dataclass(frozen=True)
class Migration:
    name: str
    raw_sql: bytes
    sha256: str

    def sql_text(self) -> str:
        try:
            return self.raw_sql.decode("utf-8")
        except UnicodeDecodeError as error:
            raise InvalidMigrationSetError(
                f"migration {self.name!r} is not valid UTF-8"
            ) from error


def _statement_leading_words(sql: str) -> tuple[tuple[str, ...], ...]:
    """Lex SQL enough to inspect statement prefixes without rewriting SQL."""
    statements: list[tuple[str, ...]] = []
    words: list[str] = []
    index = 0
    length = len(sql)
    atomic_depth = 0
    case_depth = 0
    previous_word = ""
    create_routine_statement = False

    def finish_statement() -> None:
        nonlocal words, previous_word, create_routine_statement
        if words:
            statements.append(tuple(words))
        words = []
        previous_word = ""
        create_routine_statement = False

    while index < length:
        character = sql[index]
        following = sql[index + 1] if index + 1 < length else ""

        if character.isspace():
            index += 1
            continue
        if character == "-" and following == "-":
            line_endings = tuple(
                position
                for position in (
                    sql.find("\n", index + 2),
                    sql.find("\r", index + 2),
                )
                if position >= 0
            )
            index = length if not line_endings else min(line_endings) + 1
            continue
        if character == "/" and following == "*":
            depth = 1
            index += 2
            while index < length and depth:
                if sql.startswith("/*", index):
                    depth += 1
                    index += 2
                elif sql.startswith("*/", index):
                    depth -= 1
                    index += 2
                else:
                    index += 1
            if depth:
                raise InvalidMigrationSetError("unterminated block comment in migration")
            continue
        if character in ("'", '"'):
            quote = character
            escape_backslash = (
                quote == "'"
                and index > 0
                and sql[index - 1] in ("e", "E")
                and (index < 2 or not (sql[index - 2].isalnum() or sql[index - 2] in ("_", "$")))
            )
            index += 1
            while index < length:
                if sql[index] == "\\" and escape_backslash:
                    index += 2
                    continue
                if sql[index] == quote:
                    if index + 1 < length and sql[index + 1] == quote:
                        index += 2
                        continue
                    index += 1
                    break
                index += 1
            else:
                raise InvalidMigrationSetError("unterminated quoted value in migration")
            continue
        if character == "$":
            delimiter_match = re.match(r"\$(?:[A-Za-z_][A-Za-z0-9_]*)?\$", sql[index:])
            if delimiter_match is not None:
                delimiter = delimiter_match.group(0)
                body_start = index + len(delimiter)
                body_end = sql.find(delimiter, body_start)
                if body_end < 0:
                    raise InvalidMigrationSetError(
                        "unterminated dollar-quoted body in migration"
                    )
                index = body_end + len(delimiter)
                continue
        if character == ";":
            if atomic_depth == 0:
                finish_statement()
            index += 1
            continue
        if character.isalpha() or character == "_":
            word_end = index + 1
            while word_end < length and (
                sql[word_end].isalnum() or sql[word_end] in ("_", "$")
            ):
                word_end += 1
            word = sql[index:word_end].lower()
            if atomic_depth > 0:
                if word == "case":
                    case_depth += 1
                elif word == "end":
                    if case_depth > 0:
                        case_depth -= 1
                    else:
                        atomic_depth -= 1
                elif word == "atomic" and previous_word == "begin":
                    atomic_depth += 1
                previous_word = word
                index = word_end
                continue

            if (
                word == "atomic"
                and previous_word == "begin"
                and create_routine_statement
            ):
                atomic_depth = 1
                previous_word = word
                index = word_end
                continue

            if len(words) < 5:
                words.append(word)
            if words[0:1] == ["create"] and word in {"function", "procedure"}:
                create_routine_statement = True
            previous_word = word
            index = word_end
            continue
        index += 1

    if atomic_depth:
        raise InvalidMigrationSetError("unterminated BEGIN ATOMIC body in migration")
    finish_statement()
    return tuple(statements)


def validate_no_transaction_control(sql: str, migration_name: str) -> None:
    for words in _statement_leading_words(sql):
        first = words[0]
        forbidden = first in SINGLE_WORD_TRANSACTION_COMMANDS
        forbidden = forbidden or (
            first in {"prepare", "start"} and words[1:2] == ("transaction",)
        )
        forbidden = forbidden or (
            first == "set"
            and (
                words[1:2] == ("transaction",)
                or words[1:5] == ("session", "characteristics", "as", "transaction")
            )
        )
        if forbidden:
            raise InvalidMigrationSetError(
                f"migration {migration_name!r} contains top-level transaction control"
            )


def discover_migrations(directory: Path = MIGRATIONS_DIRECTORY) -> tuple[Migration, ...]:
    """Load a contiguous, lexically ordered set and hash its exact file bytes."""
    paths = sorted(directory.glob("*.sql"), key=lambda path: path.name)
    if not paths:
        raise InvalidMigrationSetError(f"no migrations found in {directory}")

    migrations: list[Migration] = []
    for expected_number, path in enumerate(paths, start=1):
        match = MIGRATION_NAME.fullmatch(path.name)
        if match is None or int(match.group("number")) != expected_number:
            raise InvalidMigrationSetError(
                "migration files must form a contiguous sequence starting at 0001"
            )
        raw_sql = path.read_bytes()
        try:
            sql_text = raw_sql.decode("utf-8")
        except UnicodeDecodeError as error:
            raise InvalidMigrationSetError(
                f"migration {path.name!r} is not valid UTF-8"
            ) from error
        validate_no_transaction_control(sql_text, path.name)
        migrations.append(
            Migration(
                name=path.name,
                raw_sql=raw_sql,
                sha256=hashlib.sha256(raw_sql).hexdigest(),
            )
        )
    return tuple(migrations)


def _registry_value(row: Any, key: str, index: int) -> str:
    try:
        return str(row[key])
    except (KeyError, TypeError):
        return str(row[index])


def _row_value(row: Any, key: str) -> Any:
    try:
        return row[key]
    except (KeyError, TypeError) as error:
        raise RegistryContractError(
            f"registry attestation omitted required field {key!r}"
        ) from error


def validate_registry_contract(
    rows: Sequence[Any],
    *,
    allowed_schema_usage_roles: Sequence[str] = DEFAULT_ALLOWED_SCHEMA_USAGE_ROLES,
) -> None:
    if len(rows) != 1:
        raise RegistryContractError("registry attestation must return exactly one row")
    row = rows[0]
    current_user = _row_value(row, "current_user")
    if not isinstance(current_user, str) or not current_user:
        raise RegistryContractError("registry attestation returned no current user")
    if isinstance(allowed_schema_usage_roles, (str, bytes)):
        raise RegistryContractError("schema usage allowlist must contain role names")
    roles = tuple(allowed_schema_usage_roles)
    if len(set(roles)) != len(roles):
        raise RegistryContractError("schema usage allowlist contains duplicate roles")
    for role in roles:
        if (
            not isinstance(role, str)
            or ROLE_NAME.fullmatch(role) is None
            or role == "public"
            or role == current_user
        ):
            raise RegistryContractError("schema usage allowlist contains an unsafe role")
    expected_schema_privileges = tuple(
        f"{role}:USAGE:not-grantable" for role in sorted(roles)
    )
    exact_values = {
        "schema_owner": current_user,
        "table_owner": current_user,
        "function_owner": current_user,
        "relkind": "r",
        "relpersistence": "p",
        "has_rules": False,
        "has_subclass": False,
        "rls_enabled": False,
        "force_rls_enabled": False,
        "inheritance_edge_count": 0,
        "trigger_count": 2,
        "row_trigger_count": 1,
        "truncate_trigger_count": 1,
        "public_schema_privilege_count": 0,
        "public_table_privilege_count": 0,
        "non_owner_table_privilege_count": 0,
        "non_owner_column_privilege_count": 0,
        "public_function_privilege_count": 0,
        "non_owner_function_privilege_count": 0,
        "owner_can_read_and_insert": True,
    }
    for key, expected in exact_values.items():
        if _row_value(row, key) != expected:
            raise RegistryContractError(f"registry attestation failed for {key}")

    schema_privileges = _row_value(row, "non_owner_schema_privileges")
    if (
        not isinstance(schema_privileges, (list, tuple))
        or tuple(schema_privileges) != expected_schema_privileges
    ):
        raise RegistryContractError(
            "registry schema privileges do not match the frozen role allowlist"
        )

    columns = _row_value(row, "columns")
    if not isinstance(columns, (list, tuple)) or tuple(columns) != EXPECTED_REGISTRY_COLUMNS:
        raise RegistryContractError(
            "registry columns do not match the frozen shape: "
            f"expected={EXPECTED_REGISTRY_COLUMNS!r}, observed={columns!r}"
        )
    constraints = _row_value(row, "constraints")
    if (
        not isinstance(constraints, (list, tuple))
        or tuple(constraints) != EXPECTED_REGISTRY_CONSTRAINTS
    ):
        raise RegistryContractError(
            "registry constraints do not match the frozen shape: "
            f"expected={EXPECTED_REGISTRY_CONSTRAINTS!r}, observed={constraints!r}"
        )


def validate_history(migrations: Sequence[Migration], rows: Sequence[Any]) -> int:
    """Return the applied prefix length or reject missing files, gaps, and drift."""
    if len(rows) > len(migrations):
        raise MigrationHistoryError("registry contains migrations missing locally")

    for index, row in enumerate(rows):
        applied_name = _registry_value(row, "migration_name", 0)
        applied_sha256 = _registry_value(row, "sha256", 1).lower()
        expected = migrations[index]
        if applied_name != expected.name:
            raise MigrationHistoryError(
                "registry must be an exact prefix of the ordered local migrations"
            )
        if applied_sha256 != expected.sha256:
            raise MigrationDriftError(
                f"applied migration {expected.name!r} differs from its raw SQL file"
            )
    return len(rows)


async def run_migrations(
    connection: MigrationConnection,
    directory: Path = MIGRATIONS_DIRECTORY,
    *,
    allowed_schema_usage_roles: Sequence[str] = DEFAULT_ALLOWED_SCHEMA_USAGE_ROLES,
) -> tuple[str, ...]:
    """Apply pending migrations and registry records in one locked transaction."""
    migrations = discover_migrations(directory)
    applied_now: list[str] = []

    async with connection.transaction():
        await connection.execute(LOCK_SQL, ADVISORY_LOCK_ID)
        await connection.execute(REGISTRY_SETUP_SQL)
        attestation = await connection.fetch(REGISTRY_ATTESTATION_SQL)
        validate_registry_contract(
            attestation,
            allowed_schema_usage_roles=allowed_schema_usage_roles,
        )
        rows = await connection.fetch(READ_REGISTRY_SQL)
        applied_count = validate_history(migrations, rows)

        for migration in migrations[applied_count:]:
            await connection.execute(migration.sql_text())
            insert_result = await connection.execute(
                INSERT_REGISTRY_SQL,
                migration.name,
                bytes.fromhex(migration.sha256),
            )
            if insert_result != "INSERT 0 1":
                raise RegistryContractError(
                    "migration registry insert did not affect exactly one row"
                )
            applied_now.append(migration.name)

    return tuple(applied_now)
