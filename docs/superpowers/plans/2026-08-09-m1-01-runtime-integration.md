# M1-01 Runtime Integration Plan

**Goal:** Turn the admitted M1-01 runtime choices into a reproducible asynchronous HTTP and PostgreSQL import path without claiming M1-01 completion before real PostgreSQL, recovery, and server-backed user-flow evidence pass.

**Baseline commit:** `f9e81810e6e374e9047c36c53c480bcf6fbfd553`

## Product Requirement Cross-Check

**Job statement:** For an independent owner of an activity, brand, or B2B marketing project, retain the approved proposal as a traceable server-side project fact when execution begins, so later WBS, schedule, status, and review work do not depend on browser-local records or repeated document reconciliation.

**User and workflow map:** the primary user is the single project owner; a deployment administrator supplies server configuration but does not make project decisions. The flow is `upload -> validate -> retain immutable files -> create or replay project transaction -> show auditable result -> later extract and review`. Approval of the proposal, correction of extracted work, project changes, external messages, and knowledge promotion remain human-controlled.

**MVP scope:** one authenticated deployment actor, one organization/workspace/client scope, Markdown/CSV/basic DOCX source input, Markdown/basic DOCX proposal input, one approved proposal version, local immutable object adapter, PostgreSQL fact source, stable HTTP errors, and deterministic replay/conflict behavior.

**Explicit non-goals:** proposal generation, WBS extraction, scheduling, generic chat, voice, Feishu/WeCom, automatic market monitoring, ROI prediction, multi-user membership, automatic knowledge reuse, production cloud object storage, and production backup infrastructure. These remain in later milestones or deployment decisions.

**States and safety:** the adapter exposes loading, validation failure, unsupported input, retained-object failure, database rollback, conflict, replay, and success. Original bytes, parser/model output, human approval, project facts, and audit evidence remain separate. No failed or cancelled request may be presented as an accepted project.

## Frozen Work Package Contract

- Task ID: `M1-01`; it remains the only milestone task `in_progress`.
- Frozen input: one server-authenticated deployment actor, organization/workspace/client scope, one project name, one source file, one approved proposal file, one positive proposal version, and one idempotency key.
- Frozen output: two immutable objects plus one transactionally consistent project, two artifacts, two versions, approved-proposal pointer, audit event, and stable replay/conflict result.
- Claim boundary: this package can establish runtime and engineering behavior only. It cannot establish demand, ROI, time savings, repeated use, payment, production readiness, or M1-01 completion by itself.
- Sensitive-data boundary: tests use only synthetic/public fixtures. Credentials, object bytes, DSNs, local databases, reports containing secrets, and customer material stay out of Git and logs.
- Integrator-only paths: `project-status.json`, `docs/PROJECT_STATUS.md`, `.github/workflows/quality.yml`, and final commits.
- Reviewer roles: async contract reviewer, PostgreSQL/RLS reviewer, and dependency/OSS reviewer must be different from the corresponding implementer.

## Four-Quadrant Gate

| Quadrant | Current record | Execution consequence |
| --- | --- | --- |
| Shared known | The product is an independent open-source workbench. M1-01 must retain source files and select an approved proposal version. FastAPI `0.141.1`, Uvicorn `0.52.1`, python-multipart `0.0.32`, asyncpg `0.31.0`, and internal runner `contract-v1` are admitted for implementation only. | Preserve the approved proposal-to-project-state boundary and existing stable domain error codes. |
| User known / agent unknown | No deployment-specific PostgreSQL image variant, authentication provider, production object-store root, TLS policy, or backup target has been supplied. | Use a synthetic single-deployment actor and replaceable adapters. Discover and pin the official Linux/amd64 PostgreSQL image in CI; do not invent production credentials or infrastructure choices. |
| User unknown / agent known | The migration-owned transaction conflicts with atomic migration recording; sync repository code cannot wrap asyncpg; check-then-insert is racy; static RLS checks cannot prove membership authorization; a server API alone does not replace the browser fact source. | Remove file-owned transaction control, make the service genuinely async, arbitrate idempotency with the database uniqueness constraint, narrow RLS claims, and keep frontend cutover as a separate acceptance item. |
| Shared unknown | The fixed Python wheels may not resolve or run together; asyncpg may reject the current multi-command migration; PostgreSQL 18.4 may expose SQL/RLS defects; restart, restore, cleanup, and user-flow behavior remain untested. | Run one-variable experiments for the dependency lock, HTTP adapter, migration runner, repository, RLS matrix, concurrency, restart, restore, cleanup, and browser cutover. Retain commands, hashes, response samples, row snapshots, and failure codes. |

## Corrected Security Boundary

- PostgreSQL RLS enforces organization/workspace/client scope for project summaries and organization/workspace/client/project scope for artifacts, versions, and audit data.
- The server-authenticated actor setting must be present for application access and must match actor-attributed writes.
- Actor membership and role authorization are application-layer responsibilities until a membership relation is implemented. A valid but unauthorized actor ID cannot be distinguished by the current database schema, so tests must not claim otherwise.
- Same-client project summaries may be listed by an authenticated deployment actor; raw artifacts, versions, and audit rows remain project-scoped. This supports the workbench project list while preventing cross-project document retrieval.
- The application role is `NOSUPERUSER`, `NOBYPASSRLS`, cannot assume the migration owner, cannot mutate the migration registry, and receives only the DML/function privileges needed by M1-01.

## Work Packages

### WP1: Correct Migration And Runner Contract

Owned paths: `apps/api/migrations/**`, `apps/api/marketops_import/migrations.py`, `apps/api/tests/test_migration_runner.py`, `scripts/check_m1_01_postgres_contract.py`.

- [x] Remove migration-owned `BEGIN/COMMIT`; the runner owns the only transaction.
- [x] Require actor presence on application policies and preserve `created_by` on project updates.
- [x] Implement ordered raw-byte migrations, exact SHA-256, `pg_advisory_xact_lock`, an immutable registry, drift/missing-prefix rejection, intact SQL execution, and forward-only behavior.
- [x] Add failure tests for concurrency, drift, partial failure, registry failure, and forbidden semicolon splitting.

Package evidence: 29/29 runner tests and 27 migration plus 17 runner mutation guards passed. Independent final review returned `CLEAN APPROVE` after testing rewrite rules, inheritance, trigger column restrictions, table/column/schema ACL drift, exact insert command tags, SQL-standard `BEGIN ATOMIC`, and post-grant replay. Real PostgreSQL 18.4 catalog and transaction behavior remains part of WP5.

Acceptance:

```powershell
python scripts/check_m1_01_postgres_contract.py
python -m unittest apps.api.tests.test_migration_runner -v
```

### WP2: Genuine Async Domain Contract

Owned paths: `apps/api/marketops_import/service.py`, `apps/api/tests/test_project_import_service.py`.

- [x] Convert object storage, repository transaction, and service orchestration to async protocols without thread/event-loop bridges around asyncpg.
- [x] Keep file validation, manifest hashing, operation order, error codes, and cancellation behavior stable.
- [x] Replace check-then-insert with a repository claim result backed by the unique idempotency constraint; same manifest replays, different manifest conflicts.
- [x] Preserve rollback, audit atomicity, object-write ordering, and server-only scope tests.

Package evidence: 34/34 service tests passed on 2026-08-09, including cancellation, commit failure, concurrent same/different manifest arbitration, invalid claim, tenant-scope, and rollback paths. A non-implementer returned `CLEAN APPROVE`. This closes WP2 only; it is not M1-01 completion evidence.

Acceptance:

```powershell
python -m unittest apps.api.tests.test_project_import_service -v
```

### WP3: Reproducible Python Runtime

Owned paths: `.python-version`, `requirements/**`, `scripts/check_m1_01_python_lock.py`, `validation/results/m1-01-python-runtime-lock.json`, `third_party/licenses/python/**`, `sbom/m1-01-python-runtime.cdx.json`, `THIRD_PARTY_NOTICES.md`.

- [x] Freeze Python `3.12.13`, pip resolver version, Linux x86-64 wheel target, exact URLs, and SHA-256 hashes.
- [x] Reject sdists, extras, unexpected packages, inactive platform dependencies presented as mandatory, and audit/lock drift.
- [x] Inspect the selected wheels, including native `asyncpg` and `pydantic-core` artifacts, and retain required license/NOTICE material.
- [ ] Install from the hashed lock in a clean Linux environment and run `pip check`.

Package evidence: the static lock audit covers 15 exact wheels, 15 retained licenses, seven fail-closed gates, and 19 weakening mutations; an independent reviewer returned `CLEAN APPROVE` for the evidence design. All committed gates remain `blocked`, the default acceptance command exits nonzero, and only explicit `--static` exits zero. Clean Linux installation, imports, and `pip check` still require GitHub Actions evidence.

Acceptance:

```powershell
python scripts/check_m1_01_python_lock.py
```

### WP4: HTTP, Storage, And Asyncpg Adapters

Owned paths: `apps/api/main.py`, `apps/api/marketops_import/http.py`, `apps/api/marketops_import/storage.py`, `apps/api/marketops_import/postgres.py`, `apps/api/tests/test_project_import_http.py`, `apps/api/tests/postgres/**`.

- [x] Stream multipart input to server-created temporary files with 25 MiB limits and unconditional cleanup.
- [x] Resolve scope only from a server authentication dependency; reject extra multipart fields and normalize all failures to the frozen envelope.
- [x] Use transaction-local RLS settings, parameterized SQL, explicit row mapping, database-backed idempotency arbitration, and stable driver error translation.
- [x] Verify success, replay, conflict, malformed input, cancellation, pool reuse, rollback, and no sensitive error leakage in HTTP, local-storage, and fake-adapter tests.

Package evidence: an independent reviewer returned `CLEAN APPROVE` for the local/static/fake-adapter scope after 15 HTTP tests, 15 executed storage/adapter tests, and 17 OpenAPI guards with 30 mutations. One symlink test is unavailable on the current Windows host; the trusted-root TOCTOU boundary is explicit. The four live PostgreSQL tests still require WP5 and cannot be counted as passed locally.

Frozen HTTP mapping:

| Status | Condition |
| --- | --- |
| `400` | Malformed multipart, duplicate/extra fields, or domain `INVALID_INPUT` |
| `401` | Missing or invalid deployment bearer token; return `WWW-Authenticate: Bearer` and `AUTHORIZATION_REQUIRED` |
| `403` | An authenticated request lacks a valid server-supplied scope (`AUTHORIZATION_REQUIRED`) |
| `409` | `IDEMPOTENCY_CONFLICT` |
| `413` | `PAYLOAD_TOO_LARGE` |
| `415` | `UNSUPPORTED_FORMAT` or `INVALID_MEDIA_TYPE` |
| `422` | `INVALID_DOCUMENT` or `APPROVAL_REQUIRED` |
| `500` | Object integrity/write, server ID/clock, database, or unexpected adapter failures |

The OpenAPI contract and checker must add `401`; FastAPI's default validation envelope must not escape the adapter.

Acceptance:

```powershell
python -m unittest apps.api.tests.test_project_import_http -v
python -m unittest discover -s apps/api/tests/postgres -p 'test_*.py' -v
```

### WP5: PostgreSQL 18.4 And Operations Gate

Integrator-owned CI path: `.github/workflows/quality.yml`. Evidence paths will be frozen before results are committed.

- [ ] Resolve the official `postgres:18.4` Linux/amd64 manifest in a bootstrap CI run, record upstream source and platform, then pin the service by immutable digest.
- [ ] Assert server version `18.4`, role attributes, grants, migration checksum, forced RLS, tenant/project rejection, pool-scope reset, deferred constraints, append-only behavior, and concurrent idempotency.
- [ ] Test committed restart persistence separately from uncommitted connection loss.
- [ ] Test database plus object-store backup/restore and a dry-run-first, race-safe orphan cleanup policy.
- [ ] Switch the browser import path to the server API and verify the retained project survives a browser refresh without localStorage/IndexedDB as the fact source.

The PostgreSQL image digest is currently unverified because direct Docker Hub registry access timed out. A mutable tag must not be accepted as final evidence.

## Completion Gate

`M1-01` may be marked `completed` only after WP1-WP5 pass on the same reviewed commit, an independent reviewer reports no blocking isolation/credential/license issue, the full quality suite and GitHub CI pass, and the registry contains concrete evidence with `selfCheck.status: "passed"`. Until then, `project-status.json` and `docs/PROJECT_STATUS.md` remain unchanged.
