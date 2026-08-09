# M1-01 Server Import Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the browser-only import fact source with a workspace-scoped, auditable server import core while keeping M1-01 open until real PostgreSQL 18.4 integration passes.

**Architecture:** PostgreSQL owns project, artifact-version, approval, and audit facts. Original bytes are written to an immutable object adapter before the database transaction; the transaction then records both versions, selects the approved proposal, and writes the audit event atomically. HTTP remains an adapter over the service contract and cannot supply authorization scope.

**Tech Stack:** PostgreSQL 18.4 SQL, Python 3.12+ standard library for deterministic contract checks, later FastAPI/DB driver only after dependency review.

---

## Frozen Work Package Contract

- Task ID: `M1-01`
- Baseline commit: `4a09278c1e70117d463da9a8aa4a4b6a9ab7e38c`
- Branch: `codex/m1-01-server-import`
- Frozen input: one workspace, one client, one server-authenticated actor, one source file, one approved proposal file, a positive proposal version, and an idempotency key.
- Frozen output: one project, two artifacts, two immutable versions, one approved-proposal pointer, and one append-only audit event.
- Forbidden input: client-controlled authorization scope, unsupported formats, mismatched hashes, mutable version replacement, or real customer files in tests.
- Integrator-only paths: `project-status.json`, `docs/PROJECT_STATUS.md`, `.github/workflows/quality.yml`, and final commits.
- Completion boundary: static and in-memory checks are package evidence only. M1-01 completion also requires real PostgreSQL 18.4 migration, RLS, concurrency, recovery, and independent review evidence.

### Intentional M1-01 reductions

- `Project.type` and `Project.stage` remain outside this import slice because the current UI and approved M1-01 acceptance do not yet define their allowed values. `status=planning` is retained; type/stage must be frozen before M1-02 extraction writes them.
- `Artifact.current_version` is deferred because M1-01 creates one immutable version per source/proposal artifact and the project stores an explicit approved proposal version pointer. It must be added before mutable draft/version workflows.
- The audit table currently uses one structured event payload rather than generic `before/after` columns. The import service must write the project/version IDs and manifest hash in the same transaction; general before/after diffs are required before M1-04.
- Authentication membership is not implemented. The only accepted P0 context is a server-injected deployment actor; any client-supplied tenant scope remains forbidden.

### Task 1: PostgreSQL Schema and Security Contract

**Files:**
- Create: `apps/api/migrations/0001_project_import.sql`
- Create: `scripts/check_m1_01_postgres_contract.py`
- Test: `scripts/check_m1_01_postgres_contract.py`

- [x] **Step 1: Write the failing contract checker**

The checker must require: organization/workspace/client/project/artifact/version/audit tables; scoped composite foreign keys; 32-byte SHA-256 checks; an idempotency uniqueness constraint; immutable version and audit triggers; a deferred approved-proposal constraint; `ENABLE` and `FORCE ROW LEVEL SECURITY`; fail-closed scope helpers; policies for every tenant table; and `REVOKE ... FROM PUBLIC`.

- [x] **Step 2: Verify RED**

Run: `python scripts/check_m1_01_postgres_contract.py`

Expected: failure because `apps/api/migrations/0001_project_import.sql` does not exist.

- [x] **Step 3: Add the minimum migration**

The migration must use UUID identifiers, `timestamptz`, composite scope keys, `ON DELETE RESTRICT` for retained versions, and append-only triggers. RLS settings must come from transaction-local `app.workspace_id`, `app.client_id`, `app.project_id`, and `app.actor_id`; absent settings return no tenant rows.

- [x] **Step 4: Verify GREEN and mutation coverage**

Run: `python scripts/check_m1_01_postgres_contract.py`

Expected: pass, including internally generated mutations that remove or weaken each security guard and must be rejected by the checker.

### Task 2: Transactional Import Service Contract

**Files:**
- Create: `apps/api/marketops_import/__init__.py`
- Create: `apps/api/marketops_import/service.py`
- Create: `apps/api/tests/test_project_import_service.py`

- [x] **Step 1: Write failing service tests**

Tests must cover valid import, same-key/same-manifest idempotent replay, same-key/different-manifest conflict, unsupported format, hash mismatch, object-write failure before database begin, database rollback after durable objects, audit atomicity, and server-supplied scope.

- [x] **Step 2: Verify RED**

Run: `python -m unittest discover -s apps/api/tests -p 'test_*.py' -v`

Expected: import failure because the service module does not exist.

- [x] **Step 3: Implement the minimum service**

Define immutable request/result dataclasses, `ObjectStore` and `ImportRepository` protocols, stable error codes, streaming SHA-256 verification, canonical manifest hashing, and this order: validate -> persist both immutable objects -> begin repository transaction -> apply server scope -> replay/conflict check -> write project facts -> write audit -> commit. The request type must not contain workspace/client/actor scope.

- [x] **Step 4: Verify GREEN**

Run: `python -m unittest discover -s apps/api/tests -p 'test_*.py' -v`

Expected: all service contract tests pass with no network or third-party dependency.

### Task 3: HTTP Contract and Dependency Gate

**Files:**
- Create: `apps/api/openapi/project-import.openapi.yaml`
- Create: `scripts/check_m1_01_openapi_contract.py`
- Modify: `docs/OPEN_SOURCE_REVIEW.md`
- Modify: `THIRD_PARTY_NOTICES.md` only after a runtime dependency is adopted.

- [x] **Step 1: Write a failing OpenAPI checker**

Require an idempotency header, multipart source/proposal parts, a positive approved version, stable `400/403/409/413/415/422/500` responses, and responses that expose artifact/version IDs but never storage credentials. Authorization scope must be absent from request bodies.

- [x] **Step 2: Verify RED**

Run: `python scripts/check_m1_01_openapi_contract.py`

Expected: failure because the OpenAPI document does not exist.

- [x] **Step 3: Add the API contract**

Freeze the route and DTOs without adding a third-party runtime.

- [x] **Step 4: Verify the dependency-neutral contract**

Run the OpenAPI checker, server service tests, documentation checks, and the existing dependency decision check.

- [x] **Step 5: Complete the runtime dependency decision**

Review FastAPI, the PostgreSQL driver, and migration tooling for fixed versions, licenses, transitive dependencies, maintenance, Python 3.12 support, and replacement boundaries before adding runtime code.

### Task 4: Real PostgreSQL Integration Gate

**Files:**
- Create after environment decision: `apps/api/tests/postgres/test_project_import.sql`
- Modify after environment decision: `.github/workflows/quality.yml`
- Update after all checks pass: `docs/M1_01_PROJECT_IMPORT_SPIKE.md`

- [ ] Run the migration against PostgreSQL 18.4 with a pinned image digest.
- [ ] Verify separate owner/application roles, `FORCE RLS`, and cross-workspace/client/project rejection for SELECT/INSERT/UPDATE/DELETE.
- [ ] Verify deferred approved-version validation at COMMIT, append-only triggers, transaction rollback, and concurrent idempotency races.
- [ ] Verify restart persistence, backup/restore, and unreferenced-object cleanup behavior.
- [ ] Request independent spec and code-quality review.
- [ ] Only after all acceptance evidence passes, update `project-status.json`, render `docs/PROJECT_STATUS.md`, and run the full quality suite.

## Self-Review

- Spec coverage: project creation, immutable retention, approved version selection, audit, isolation, retry, and failure evidence are mapped to Tasks 1-4.
- Dependency boundary: no unreviewed runtime package is introduced in Tasks 1-2.
- Claim boundary: static SQL and in-memory adapters cannot close M1-01; Task 4 is mandatory.
- Sensitive-data boundary: all fixtures remain synthetic and no object bytes or credentials enter logs, queues, Git, or audit payloads.
