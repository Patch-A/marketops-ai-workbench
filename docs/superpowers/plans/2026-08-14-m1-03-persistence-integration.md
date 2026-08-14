# M1-03 PostgreSQL 与服务集成工作包

状态：`implemented and independently reviewed as a bounded preparation package; registry unchanged`

日期：`2026-08-14`

## 1. Job statement

对于已经逐项完成方案提取审核的项目负责人，在把审核结果转成执行计划时，帮助其保存可追溯、可并发控制的 WBS 版本，并保存由确定性引擎计算的排期快照，从而在服务重启、多人误操作或重复请求后仍能解释计划来自哪份方案和哪次人工审核。

## 2. Work package contract

| Field | Frozen value |
|---|---|
| Registry task | `M1-03` preparation package; the registry task remains `pending` while `M1-02` is the only `in_progress` task |
| Baseline commit | `94d2607b3f7a9ea5ba6bc0343d889bd7f1a45ba3` |
| Owned paths | `apps/api/marketops_schedule/`, `apps/api/migrations/0004_wbs_schedule.sql`, focused M1-03 tests, the migration bootstrap/backup compatibility paths in `apps/api/marketops_import/backup.py` and `scripts/run_m1_01_postgres_gate.py` / `scripts/run_m1_01_backup_restore_gate.py`, this plan, and the minimal `apps/api/main.py` runtime wiring |
| Forbidden paths | `project-status.json`, generated `docs/PROJECT_STATUS.md`, top-level CI, frontend files, M1-02 schemas and behavior, validation/customer files |
| Reviewer role | A non-implementer reviews schema isolation, transaction behavior, failure paths, recovery, and claim boundaries before this package is accepted |

Unexpected dirty changes in an owned path stop implementation until their origin is understood. Existing user changes are preserved.

## 3. User and workflow map

```text
scoped project + approved proposal
  -> scoped M1-02 review read at an explicit version
  -> reject an incomplete or mismatched review
  -> create/replay one append-only WBS plan for that review snapshot
  -> revise with expectedPlanVersion
  -> persist a new plan version and task rows atomically
  -> calculate deterministic schedule for an explicit calendar
  -> persist/replay an immutable schedule snapshot
  -> later HTTP/UI and human plan approval
```

The primary user is the project owner/planner. The server owns project scope, proposal identity, review identity, version checks, timestamps, IDs, and audit entries. The human owns candidate decisions, task edits, locked dates, buffers, and later approval.

## 4. MVP scope and non-goals

MVP scope:

- Append-only plan root, plan versions, normalized task rows, and schedule snapshots.
- Forced RLS by workspace, client, project, and actor, matching the M1-02 private-deployment boundary.
- A service that consumes `ReviewService.read_review`; browser JSON cannot create a plan directly.
- Create/replay by immutable review source, optimistic plan revision, deterministic schedule calculation, scoped reads, and audit events.
- Atomic rollback on persistence failures and stable sanitized service failures.

Explicit non-goals:

- No HTTP route, browser editor, plan approval, export, execution status workflow, resource leveling, model call, external connector, or automatic holiday import.
- No claim that the synthetic fixture proves usability, time savings, demand, ROI, or real-project completeness.
- No cross-workspace, cross-client, cross-project, or cross-actor reuse.

## 5. States, data, and safety boundaries

Plan states remain `draft`; schedule snapshots are `ready` or `needs_review`. Invalid inputs fail before a transaction writes formal state. A plan root binds one project, approved proposal artifact/version/hash, review run, and explicit review version. Every plan version stores the full immutable domain payload plus normalized task rows and a SHA-256 digest. Every schedule snapshot stores its calendar input, plan digest, output digest, and full result payload.

The service rejects pending review candidates before plan creation, even though the pure domain helper can build a partial technical draft. This is a production trust-boundary decision: M1-03 persistence consumes a completed human review, not an intermediate browser state.

Rows are append-only. Updates, deletes, and truncation are rejected. Database constraints and deferred integrity triggers verify source identity, contiguous plan versions, task completeness, task/citation identity, schedule-plan digest identity, and actor ownership. Repository errors never include SQL, credentials, proposal text, or task text.

## 6. Ordered implementation checklist

1. Add migration `0004_wbs_schedule.sql` with append-only tables, constraints, integrity triggers, forced RLS, and public privilege revocation.
2. Add schedule service data classes and repository protocols for create/replay, read, revision, calculation, and audit.
3. Add the asyncpg repository using the existing scoped transaction and sanitized error patterns.
4. Wire the service into the existing FastAPI lifespan without exposing HTTP writes.
5. Add unit, adapter, PostgreSQL/RLS/concurrency/rollback/recovery, migration, domain regression, and progress checks.
6. Obtain non-implementer review, then run same-SHA CI before claiming the preparation package accepted.

## 7. Frozen inputs, outputs, and acceptance

Inputs:

- `ScheduleScopeContext`, `projectId`, `reviewRunId`, and explicit `reviewVersion`.
- Revision input: `planId`, `expectedPlanVersion`, and WBS-owned task changes.
- Schedule input: `planId`, `expectedPlanVersion`, `projectStart`, and explicit ISO holiday dates.

Outputs:

- `CreatePlanResult(plan, replayed)` with immutable source identity.
- `PlanReadModel` for a selected or latest plan version.
- A new `PlanReadModel` for each successful revision.
- `CreateScheduleResult(snapshot, replayed)` for each unique deterministic output.

Acceptance commands:

```powershell
python -m unittest apps.api.tests.test_m1_03_schedule_domain
python -m unittest apps.api.tests.test_m1_03_schedule_service
python -m unittest apps.api.tests.test_m1_03_schedule_postgres_adapter
python -m unittest apps.api.tests.postgres.test_m1_03_schedule_runtime
python scripts/check_schedule_spike.py
python -m compileall apps/api
node scripts/progress.mjs check
git diff --check
```

Capability-gated PostgreSQL skips are not runtime evidence. The runtime test must pass against PostgreSQL 18.4 locally or in same-SHA CI before this package is accepted.

## 8. Risks, unknowns, and smallest validation experiment

Confirmed engineering risks are stale plan revisions, partial writes, source-review mismatch, task-row drift, cross-scope reads, and duplicate schedule requests. The migration and service tests must exercise each path.

Unknowns remain whether users understand the fields, whether the calendar model fits a real organization, and whether extracted tasks are complete. The smallest later validation experiment is one authorized or de-identified proposal: complete M1-02 review, create a persisted WBS, compare it with a manual baseline, record retained/edited/rejected tasks and missed dependencies, then revise and recalculate once. That experiment is deferred by user decision and is not replaced by this package.

## 9. Final evidence

- Final candidate commit: `fd5e8d8`.
- Same-SHA GitHub Actions Quality run: `31810250390`; `static-checks` and `m1-01-runtime` passed, including PostgreSQL 18.4, browser cutover, restart, backup/restore, orphan cleanup, and connection-loss rollback.
- Local API discovery: `385 passed, 61 skipped`; capability-gated skips are not treated as runtime evidence.
- Focused service/adapter tests and PostgreSQL runtime coverage verify valid commits, replay, stale revision conflicts, rollback, cross-review-run rejection with the specific integrity message, and row/payload plan-version consistency.
- Independent review found no P0/P1. Its two P2 findings were closed by binding every task to the selected review run/snapshot and requiring `plan_payload.planVersion` to equal the persisted row version in both the database trigger and service read validation.

This accepts only the server persistence preparation package. `M1-03` remains `pending`, and `M1-02` remains the registry's only `in_progress` task. HTTP writes, browser WBS editing, plan approval, export, and authorized real-proposal validation remain unfinished.
