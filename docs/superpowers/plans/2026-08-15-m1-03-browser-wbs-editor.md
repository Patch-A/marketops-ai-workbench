# M1-03 Browser WBS Editor Work Package

Status: `in progress; registry unchanged`

Date: `2026-08-15`

## 1. Scope

This package adds the browser-facing WBS editor and deterministic schedule surface on top of the committed M1-03 HTTP contract. It keeps the existing review workbench as the source of the latest human-review snapshot and does not claim that M1-03 is complete.

## 2. Work Package Contract

| Field | Frozen value |
|---|---|
| Registry task | `M1-03` browser preparation package; registry remains `pending` while `M1-02` remains the only `in_progress` task |
| Baseline commit | `d91d55d` |
| Owned paths | `index.html`, `app.js`, `project-import.js`, `schedule-workbench.js`, `review-workbench.js` tests, `styles.css`, static asset allowlists, and this plan |
| Forbidden paths | `project-status.json`, generated `docs/PROJECT_STATUS.md`, customer/validation files, credentials, and external model/API integrations |
| Reviewer role | A non-implementer checks route/request agreement, review-to-WBS handoff, optimistic conflict behavior, browser boundary, responsive layout, and theme/accessibility states |

## 3. Frozen User Flow

1. Switch from `交付审核` to `执行排期` in the existing workbench shell.
2. Create or replay a WBS from the latest fully decided review snapshot.
3. Edit task title, duration, owner, status, and other bounded fields in the task table.
4. Save an append-only revision using the displayed plan version; a conflict requires refresh.
5. Recalculate a deterministic schedule using an explicit project start date and comma-separated ISO holidays.
6. Show the immutable schedule digest, conflicts, deadline misses, and a clear `待人工确认` boundary.

## 4. Acceptance Boundary

- No browser-owned scope, actor, source identity, digest, or server timestamp is submitted.
- No pending or historical review snapshot can create or mutate a current WBS.
- The assistant remains explanatory only; no model API or consequential action is wired here.
- Empty, loading, ready, conflict, error, and reduced-motion states remain visible and stable on desktop and mobile widths.

## 5. Current Evidence

- Node schedule/review unit tests: `9/9` passed.
- Existing M1-02 browser contract tests and self-test: passed.
- Static asset protection test: `27/27` passed after adding `schedule-workbench.js` to the server allowlist.
- M1-03 focused service/HTTP tests: `13/13` passed in the temporary Python runtime.
- Compile, docs, progress, and diff checks: passed.

The package still needs an independent browser review and a rendered desktop/mobile run before it can be treated as accepted. M1-03 remains `pending` until browser editing, approval, export, and authorized real-proposal validation are complete.
