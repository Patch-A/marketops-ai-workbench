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
| Shared unknown | The fixed Python runtime, migration, SQL/RLS behavior, normal same-container restart, uncommitted connection-loss rollback, isolated application-level logical backup/restore, and Linux local-adapter orphan cleanup have passed their recorded CI experiments. Power-loss and durable-volume behavior, browser cutover, and production reliability remain untested. | Preserve the completed runtime, recovery, backup/restore, and cleanup evidence; run a separately bounded browser-cutover experiment. Retain commands, hashes, response samples, row snapshots, and failure codes without expanding the proven claim boundary. |

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
- [x] Install from the hashed lock in a clean Linux environment and run `pip check`.

Package evidence: the static lock audit covers 15 exact wheels, 15 retained licenses, seven fail-closed gates, and 19 weakening mutations; an independent reviewer returned `CLEAN APPROVE` for the evidence design. GitHub Actions run [31313732611](https://github.com/Patch-A/marketops-ai-workbench/actions/runs/31313732611) installed the hashed lock in a fresh Linux Python 3.12 environment, verified imports, and passed `pip check` on commit `fde58e91e69d57088a13cd22554b7173210c03ce`. This closes WP3 only; it does not establish recovery, production readiness, or M1-01 completion.

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

- [x] Resolve the official `postgres:18.4` Linux/amd64 manifest in a bootstrap CI run, record upstream source and platform, then pin the service by immutable digest.
- [x] Assert server version `18.4`, role attributes, grants, migration checksum, forced RLS, tenant/project rejection, pool-scope reset, deferred constraints, append-only behavior, and concurrent idempotency.
- [x] Test committed restart persistence separately from uncommitted connection loss.
- [x] Test database plus object-store backup/restore in isolated targets.
- [x] Test a dry-run-first, race-safe orphan cleanup policy.
- [ ] Switch the browser import path to the server API and verify the retained project survives a browser refresh without localStorage/IndexedDB as the fact source.

Bootstrap evidence: GitHub Actions run [31313551057](https://github.com/Patch-A/marketops-ai-workbench/actions/runs/31313551057) passed on remote commit `c6ce370b43ef44dbf5ff0bc5155dd91daf1e5701`. It validated the pinned PostgreSQL RepoDigest `postgres@sha256:a02db8cac496f15b094798a38254f14d6e00741f709360e5e00bb6668ea31636` against the actual `linux/amd64` service container, installed the fresh hashed Python runtime with `pip check`, and passed `15/15` HTTP, `9/9` asyncpg adapter, and `4/4` PostgreSQL/RLS/concurrency tests. Restart persistence, backup/restore, orphan cleanup, browser cutover, production readiness, and the overall M1-01 acceptance remain unverified.

#### WP5A: Restart Persistence And Connection-Loss Experiment

- Task ID: `M1-01`; baseline commit: `fde58e91e69d57088a13cd22554b7173210c03ce`.
- Owned paths: `apps/api/marketops_import/postgres.py`, `apps/api/marketops_import/storage.py`, `apps/api/tests/test_project_import_postgres_adapter.py`, `apps/api/tests/test_project_import_storage.py`, `scripts/run_m1_01_restart_recovery_gate.py`, `apps/api/tests/test_m1_01_restart_recovery_gate.py`, `.github/workflows/quality.yml`, and this plan. The PostgreSQL adapter paths were added after independent review identified asyncpg's closed-connection `InterfaceError` as a retryability gap.
- Forbidden paths: `project-status.json`, `docs/PROJECT_STATUS.md`, database migrations, browser code, real customer files, credentials, local databases, and uploaded object bytes.
- Frozen input: the reviewed PostgreSQL 18.4 Linux/amd64 service image, test-only admin and application DSNs, one committed synthetic import, one distinct uncommitted synthetic import, and a runner-temporary local object-store root.
- Frozen output: after an actual service-container restart, a fresh Python process, pool, and service recover the committed project, both artifact versions, audit event, approved-proposal selection, and both immutable objects; separately terminating the unique backend of an open transaction leaves no project, artifact, version, or audit rows, and the same import can then be retried successfully. Evidence is a non-sensitive JSON record printed by CI.
- Claim boundary: this experiment establishes behavior for one isolated GitHub Actions service container and runner-local object root only. It does not establish host restart recovery, durable-volume configuration, backup/restore, production RPO/RTO, browser recovery, demand, ROI, repeat use, payment, or M1-01 completion.
- Acceptance commands: `python -m unittest apps.api.tests.test_m1_01_restart_recovery_gate -v`; the `m1-01-runtime` GitHub Actions job must then run the gate's `prepare`, `restart`, `verify`, and `connection-loss` phases in separate Python processes with the actual service-container ID, and every phase must exit zero.
- Reviewer role: a non-implementer operations/recovery reviewer must inspect the restart boundary, full-row absence checks, object verification, credential redaction, and CI evidence before WP5A is checked complete.

Package evidence: GitHub Actions run [31315869043](https://github.com/Patch-A/marketops-ai-workbench/actions/runs/31315869043) passed on commit `fdf18a0bbc628c01de3020114878a3b6e84eb8c3`. After a normal restart of the same PostgreSQL 18.4 service container, a fresh Python process, pool, and service replayed the committed import with the original IDs and manifest; the approved proposal remained selected, the database aggregate remained `1 project / 2 artifacts / 2 versions / 1 audit event`, and both runner-local immutable objects passed read-only integrity verification. A separately identified application backend with an open transaction was terminated; before retry all four aggregate counts were zero, the adapter returned a retryable `DATABASE_WRITE_FAILED`, and retrying the same record produced the complete `1/2/2/1` aggregate. Independent review returned `CLEAN APPROVE` after two connection-lifecycle findings and one test-coverage gap were corrected. This closes WP5A only. Object retention before the failed database transaction confirms that orphan cleanup remains necessary; the run does not prove power-loss durability, durable host volumes, backup/restore, node replacement, production RPO/RTO, browser recovery, or M1-01 completion.

#### WP5B: Database And Object-Store Backup/Restore Experiment

- Task ID: `M1-01`; baseline commit: `5eaa577d2b994a0ccaad6d3612c158e528984440`.
- Owned paths: `apps/api/marketops_import/backup.py`, `apps/api/tests/test_project_import_backup.py`, `scripts/run_m1_01_backup_restore_gate.py`, `apps/api/tests/test_m1_01_backup_restore_gate.py`, `.github/workflows/quality.yml`, and this plan.
- Forbidden paths: `project-status.json`, `docs/PROJECT_STATUS.md`, database migrations, browser code, real customer files, credentials, database dumps, uploaded object bytes, and generated backup bundles.
- Frozen input: the reviewed PostgreSQL 18.4 Linux/amd64 service image and migration, test-only admin/application DSNs, one committed synthetic import, its runner-temporary immutable-object root, and runner-temporary backup and isolated-restore locations.
- Frozen output: an atomically published directory bundle containing a strict manifest, one PostgreSQL custom-format data dump, and exactly the immutable objects referenced by the exported database snapshot; a fresh isolated database and empty object root must restore the `1 project / 2 artifacts / 2 versions / 1 audit event` aggregate, selected proposal, row-set hashes, object hashes, RLS/role/grant/migration contract, and fresh application-role replay.
- Consistency protocol: hold a read-only repeatable-read transaction with an exported PostgreSQL snapshot; query `artifact_versions` from that snapshot while PostgreSQL 18.4 `pg_dump` uses the same snapshot; copy only those referenced immutable objects and verify their size and SHA-256 before atomic bundle publication. New commits after the snapshot may be absent, but no object referenced by the snapshot may be absent or altered.
- Restore boundary: restore only into a unique empty database and empty staging object root. Allow only reviewed business-table data in the dump table of contents, run `pg_restore` in one transaction, verify both restored sides and application-role replay, and mark the experiment accepted only after all checks pass. Never overwrite or switch the source database or object root.
- Required failure signals: reject malformed or non-canonical manifests, duplicate or extra paths, missing/tampered/symlinked objects, dump hash or table-of-contents drift, tool/migration version mismatch, non-empty targets, partial database restore, object/database cross-reference mismatch, RLS/role/grant drift, and any application replay mismatch. Errors and evidence must not expose DSNs, passwords, object bytes, or subprocess stderr that may contain credentials.
- Claim boundary: this can establish application-level logical backup/restore for the fixed PostgreSQL 18.4 migration and synthetic CI fixture only. It cannot establish physical crash consistency, hot standby, WAL/PITR, backup authenticity, encryption, off-site retention, production cutover, RPO/RTO, cross-host or cross-version recovery, demand, ROI, repeat use, payment, or M1-01 completion.
- Acceptance commands: `python -m unittest apps.api.tests.test_project_import_backup apps.api.tests.test_m1_01_backup_restore_gate -v`; the `m1-01-runtime` GitHub Actions job must then create the bundle with the PostgreSQL 18.4 service-container tools, restore into isolated targets, run the full verification matrix and bounded negative cases, and emit only a non-sensitive JSON evidence record.
- Reviewer role: a non-implementer backup/recovery reviewer must inspect snapshot lifetime, dump allowlisting, path and symlink handling, single-transaction restore behavior, source immutability, credential redaction, failure-path coverage, and actual CI evidence before WP5B is checked complete.

Package evidence: GitHub Actions run [31317614862](https://github.com/Patch-A/marketops-ai-workbench/actions/runs/31317614862) passed both jobs on commit `25d1192ad11cf4e74b89a35a44104c9d832da618`. The PostgreSQL 18.4 service-container tools exported one repeatable-read snapshot, the dump table of contents contained exactly the seven allowlisted business tables, and an isolated migrated database plus empty object root recovered the selected proposal, `1 project / 2 artifacts / 2 versions / 1 audit event`, exact row-set hashes, two immutable objects, forced RLS, the application-role privilege contract, and a fresh same-ID replay. Tampered dump and object bundles were rejected, and a deliberately failing late restore left every business table empty under `pg_restore --single-transaction`. The source project's scoped aggregate and referenced objects remained intact. Independent evidence review returned `CLEAN APPROVE`. This closes WP5B only for application-level logical backup/restore with the reviewed PostgreSQL 18.4 migration, synthetic fixture, and current local immutable-object adapter. It does not establish physical crash consistency, WAL/PITR, hot standby, authenticity, encryption, off-site retention, production cutover, RPO/RTO, cross-host or cross-version recovery, orphan cleanup, browser recovery, demand, ROI, repeat use, payment, or M1-01 completion.

#### WP5C: Dry-Run-First Race-Safe Orphan Cleanup Experiment

- Task ID: `M1-01`; baseline commit: `ac473de5160cce89d44d6594d4f7ad7ee7b319e6`.
- Owned paths: `apps/api/marketops_import/cleanup.py`, `apps/api/marketops_import/storage.py`, `apps/api/marketops_import/service.py`, `apps/api/tests/test_project_import_cleanup.py`, `apps/api/tests/test_project_import_storage.py`, `apps/api/tests/test_project_import_service.py`, `scripts/run_m1_01_orphan_cleanup_gate.py`, `apps/api/tests/test_m1_01_orphan_cleanup_gate.py`, `.github/workflows/quality.yml`, and this plan.
- Forbidden paths: `project-status.json`, `docs/PROJECT_STATUS.md`, database migrations, `backup.py` and WP5B tests/gate, WP5A restart/recovery tests/gate, browser code, requirements, SBOM and license files, real customer files, credentials, local databases, uploaded object bytes, and generated cleanup plans or quarantine contents.
- Frozen input: the reviewed PostgreSQL 18.4 Linux/amd64 service, the current runner-local immutable-object layout, one committed referenced object group, one failed-transaction orphan group, one object group that becomes referenced during the cleanup race, a plan-after-new object, and a test-only administrator reference reader. The 24-hour observation period is a conservative design default, not a validated customer or legal requirement.
- Frozen output: dry-run changes no payload and emits a canonical, hash-bound plan without DSNs, absolute paths, or object contents. Apply accepts only an unexpired plan plus its separately supplied SHA-256; under an exclusive storage lock it re-reads all database references and file identities, preserves referenced, changed, or plan-after-new objects, and quarantines then removes only unchanged plan candidates whose database-time observation period has elapsed.
- Concurrency protocol: every `LocalObjectStore` import holds a shared cross-process lock from before its first immutable write until the database transaction exits. Dry-run and apply use the matching exclusive lock. Apply waits for active importers, then re-reads references before moving any candidate. File `mtime` is an identity signal only; eligibility starts at the database `observedAt` recorded by dry-run and never uses `mtime` as orphan age.
- Filesystem boundary: apply is supported only on Linux local filesystems with standard-library `flock`, cooperative writers, one canonical object root, and one same-filesystem quarantine. Non-canonical layouts, symlinks, special files, multiple hard links, identity drift, duplicate physical mappings, database/reference failures, and partial validation fail closed before the first move. Windows, NFS, SMB, cloud object stores, multi-node deployment, malicious local writers, and old processes that bypass the lock are outside this experiment.
- Required failure signals: reject a missing, malformed, duplicate, tampered, premature, expired, wrong-root, path-traversing, or stale plan; incomplete/non-canonical references; missing or corrupt referenced objects; candidate identity drift; symlink/hard-link/special/unknown layout entries; concurrent cleanup; database unavailability; and a cleanup cancellation or move/delete failure with an explicit bounded recovery state. A newly committed reference must be retained after apply obtains the lock and rechecks the database.
- Claim boundary: this experiment can establish only that, on the tested Linux local adapter and same filesystem, with all importers obeying the same cooperative lock and a privileged complete-reference reader, plan-listed objects observed unreferenced for the configured interval can be cleaned without deleting the tested concurrent import. It cannot establish Windows or network-filesystem safety, a production least-privilege maintenance role, crash-recoverable quarantine, legal retention, storage quotas, production reliability, M1-01 completion, demand, ROI, repeat use, or willingness to pay.
- Acceptance commands: `python -m unittest apps.api.tests.test_project_import_cleanup apps.api.tests.test_project_import_storage apps.api.tests.test_project_import_service apps.api.tests.test_m1_01_orphan_cleanup_gate -v`; the Linux `m1-01-runtime` GitHub Actions job must run the real PostgreSQL cross-process race gate and its failure matrix, emit non-sensitive JSON evidence, and exit zero.
- Reviewer role: a non-implementer storage/concurrency reviewer must inspect lock lifetime, full-reference query authority, plan canonicalization, database-time eligibility, filesystem identity checks, quarantine failure behavior, sensitive-data redaction, negative tests, and actual CI evidence before WP5C is checked complete.

Package evidence: GitHub Actions run [31320454712](https://github.com/Patch-A/marketops-ai-workbench/actions/runs/31320454712) passed both jobs on commit `4b1fa412905872cf4170f82e6178b3e30082ad41`. The PostgreSQL 18.4 Linux gate observed actual cross-process `flock` contention before releasing the importer, retained two candidates that became database references, deleted exactly two permanent failed-transaction orphans, preserved two objects created after the plan, and verified size and SHA-256 for all four final database references. The failure matrix rejected a symbolic link, FIFO, concurrent cleanup, unavailable reference reader, changed candidate, tampered plan, and wrong plan hash; partial delete recovery and cancellation propagation also preserved their recorded boundaries. The default 24-hour observation policy was tested with an accelerated 200 ms CI interval and database timestamps, so this run does not establish real-world retention duration or legal policy. Independent code review and evidence review both returned clean approval. An earlier run, `31320165577`, was rejected as final evidence after automatic PostgreSQL container logs exposed synthetic COPY-row identifiers during the WP5B failure injection. The accepted run attested `log_error_verbosity=terse` and `log_min_error_statement=panic`; all 87 `Stop containers` lines had zero matches for error context, statements, COPY rows, UUIDs, DSNs, or CI credential markers. This closes WP5C only for the tested Linux local adapter, same filesystem, cooperative importers, and privileged complete-reference reader. It does not establish Windows or network-filesystem safety, multi-node cleanup, malicious-writer resistance, crash-recoverable quarantine, a production least-privilege maintenance role, production reliability, M1-01 completion, demand, ROI, repeat use, or payment.

#### WP5D: Browser Server API Cutover

- Task ID: `M1-01`; baseline commit: `9c4586517db002b7bef52fea2fdb1355e3e0d96a`.
- Backend owned paths: `apps/api/main.py`, `apps/api/marketops_import/http.py`, `apps/api/marketops_import/postgres.py`, `apps/api/openapi/project-import.openapi.yaml`, `apps/api/tests/test_project_import_http.py`, `apps/api/tests/test_project_import_postgres_adapter.py`, and `apps/api/tests/postgres/test_project_import_runtime.py`.
- Browser owned paths: `index.html`, `app.js`, `project-import.js`, `styles.css`, and `scripts/check_project_import.mjs`.
- Integrator owned paths: `scripts/check_m1_01_openapi_contract.py`, `scripts/run_m1_01_browser_cutover_gate.py`, `scripts/run_m1_01_browser_flow.mjs`, the browser gate unit test, `.github/workflows/quality.yml`, this plan, `docs/M1_01_PROJECT_IMPORT_SPIKE.md`, `project-status.json`, and `docs/PROJECT_STATUS.md`.
- Forbidden paths: database migrations, WP5A-WP5C implementation and gate files, runtime requirements/SBOM/licenses, real customer files, credentials, local databases, uploaded object bytes, and generated browser profiles or evidence containing secrets.
- Frozen input: one authenticated single-deployment actor resolved only by the server, the current organization/workspace/client scope, one project name, one supported source file, one supported approved-proposal file, a positive proposal version, explicit approval, and one retry-stable idempotency key.
- Frozen API output: `POST /v1/project-imports` retains the existing result and returns a project `Location`; `GET /v1/projects` returns a bounded deterministic newest-first scoped summary list; `GET /v1/projects/{projectId}` returns only the project name/status, source and proposal display metadata, proposal approval metadata, and timestamps needed by the M1-01 UI. Read responses use `Cache-Control: no-store` and never expose storage keys, object bytes, raw idempotency keys, DSNs, or credentials.
- Frozen browser output: after POST the UI renders only a fresh server detail response, places the server project ID in the URL as navigation state, and on reload restores the same summary with GET. Opening the root URL restores the newest visible project. localStorage, IndexedDB, form values, and demo data are never project facts or failure fallbacks.
- Authentication boundary: the same-origin private-deployment slice may accept the existing Bearer credential for programmatic API calls and browser-native HTTP Basic for protected static assets and same-origin fetches. The Basic username is server configured; the deployment token is the password. No credential may enter HTML, JavaScript, a URL, Web Storage, IndexedDB, logs, exports, or Git. TLS termination remains a deployment requirement. This is not multi-user identity, actor membership, SaaS authentication, or a production session system.
- RLS/privacy boundary: list and detail queries include explicit organization/workspace/client predicates in addition to forced RLS. Artifact/version detail reads set transaction-local `app.project_id`. A foreign-scope project ID and an absent project ID return the same `404 PROJECT_NOT_FOUND` response.
- UI states: empty, client validation failure, upload pending, server-detail loading, created, replayed, reload-restored, unauthenticated, project-not-found, retryable network/server failure, non-retryable API failure, malformed response, and cancelled request. A network-uncertain retry reuses the same idempotency key. The editable `clientName` field and the remote unpkg script are removed because neither belongs to the server contract or credential-safe runtime.
- Shared unknown experiment: browser-native Basic credential reuse for protected static assets and same-origin API requests must be exercised in real Chromium against the FastAPI runtime. Success means zero credentials in browser storage/source/URL, one POST followed by server GET rendering, reload with GET only, recovery after local storage pollution/removal, and correct failure states. Failure means falling back to an injected browser token, repeating POST on reload, or using local records as accepted state; in that case the authentication/cutover design must be revised rather than marking the package complete.
- Claim boundary: this package can establish one synthetic single-user private-deployment browser-to-PostgreSQL import and refresh path. It cannot establish production authentication, actor membership, multi-user authorization, production deployment security, cross-browser support, demand, ROI, time savings, repeat use, payment, or M1-01 completion before independent review and the full completion gate.
- Acceptance commands: `python scripts/check_m1_01_openapi_contract.py`; `python -m unittest apps.api.tests.test_project_import_http apps.api.tests.test_project_import_postgres_adapter apps.api.tests.test_m1_01_browser_cutover_gate -v`; `python -m unittest discover -s apps/api/tests/postgres -p 'test_*.py' -v`; `node --check app.js`; `node --check project-import.js`; `node scripts/check_project_import.mjs`; and the Linux CI runtime must run `python scripts/run_m1_01_browser_cutover_gate.py` with real PostgreSQL, FastAPI, and Chromium.
- Reviewer role: a non-implementer security/browser reviewer must inspect credential handling, same-origin asset protection, RLS read scope, indistinguishable 404 behavior, idempotent uncertain retry, local-fact-source removal, external script removal, responsive states, failure coverage, and actual CI evidence before WP5D or M1-01 can close.

## Completion Gate

`M1-01` may be marked `completed` only after WP1-WP5 pass on the same reviewed commit, an independent reviewer reports no blocking isolation/credential/license issue, the full quality suite and GitHub CI pass, and the registry contains concrete evidence with `selfCheck.status: "passed"`. Until then, `project-status.json` and `docs/PROJECT_STATUS.md` remain unchanged.
