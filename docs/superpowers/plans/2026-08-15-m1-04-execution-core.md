# M1-04 Execution Core Work Package

Status: completed for the bounded package; independently reviewed

Date: 2026-08-15

Task ID: M1-04

Baseline commit: da475dd4164bde74a82b30604d8541f64b9192dc

Owned paths: apps/api/migrations/0006_task_execution.sql,
apps/api/marketops_execution/, focused M1-04 tests, the PostgreSQL migration
gate, and this plan.

Forbidden paths: real proposal files, credentials, local databases,
project-status.json, generated docs/PROJECT_STATUS.md, browser execution UI,
external connectors, and unrelated milestone code.

Frozen input: one server-scoped approved WBS version, one task ID, an expected
execution sequence, a supported status, optional blocker reason, actual dates,
and a short note.

Frozen output: one append-only execution update and one audit event. Read and
export operations combine approved plan tasks with the latest execution update.

Reviewer role: a non-implementer must verify RLS, append-only persistence,
approved-plan gating, optimistic conflicts, replay, audit atomicity, export
contents, and failure behavior.

## Contract

- Execution updates never mutate approved WBS versions or schedule snapshots.
- Status is one of not_started, in_progress, blocked, completed, or cancelled.
- A blocked task requires a concise blocker reason.
- Actual finish cannot precede actual start.
- The caller supplies the expected sequence. A stale different request fails
  with EXECUTION_CONFLICT; an identical retry replays the latest update.
- Every committed update writes an audit event in the same transaction.
- CSV and XLSX contain task identity, title, status, blocker, planned and actual
  dates, owner role, and source candidate ID. They exclude credentials, scope
  claims, and original proposal bytes.

## Local evidence

- PostgreSQL 18.4 migration gate applied migrations 0001 through 0006,
  attested the fixed image digest, and granted the application role only
  SELECT and INSERT on execution updates.
- The authorized local-derived validation-project-A and validation-project-B plans each exercised
  in_progress -> blocked -> completed, identical replay, and stale conflict.
- Each plan retained three immutable updates and three matching audit events.
- Direct update was rejected by the append-only trigger.
- Generated XLSX files opened with the bundled openpyxl runtime. Validation
  project A contained 32 rows by 10 columns; validation project B contained
  12 rows by 10 columns.

- Focused service and HTTP tests pass 11/11.
- Real PostgreSQL 18.4 runtime tests pass 5/5, covering append, identical replay, stale conflict, a real concurrent same-sequence competition with exactly one winner, revision-before-execution conflict, audit count, forced audit-insert failure with full transaction rollback, append-only rejection, and forced-RLS hiding.
- Real FastAPI ASGI runtime test passes 1/1, covering authenticated execution read, update, and CSV download against PostgreSQL-backed services.
- The synthetic Chromium execution gate passes 10/10, covering authenticated read, conflict reconciliation, update, CSV/XLSX export, request boundaries, responsive layout, both themes, zero console failures, and zero external requests. Its Windows cleanup now terminates the complete Chrome process tree before removing the temporary profile.
- The static OpenAPI mutation checker passes 45 guards and 90 mutations. This verifies the reviewed contract only; runtime authentication and database behavior are covered separately above.
- Execution writes lock the WBS plan row in the same order as WBS revision, re-read the latest plan version after the lock, and reject historical versions before any execution or audit insert. HTTP and service layers require canonical `YYYY-MM-DD` actual dates, preserving identical replay semantics.
- The temporary HTTP-gate container and temporary admin role used for the runtime check were removed; the pre-existing M1-04 and M1-03 containers were preserved.

## Boundary

This package establishes local service, persistence, authenticated HTTP, ASGI
runtime, audit, export, and the synthetic browser execution panel. M1-04 is
complete after an independent `CLEAN APPROVE`; M1-05 is the review gate for
the end-to-end approved-proposal-to-accepted-schedule path.
The evidence does not prove production reliability, user value, demand, ROI,
repeat use, or payment.
