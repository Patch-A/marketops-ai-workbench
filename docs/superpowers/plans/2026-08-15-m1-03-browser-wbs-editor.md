# M1-03 Browser WBS Editor Work Package

Status: `reviewed package accepted as part of completed M1-03`

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

- Node schedule/review unit tests: `15/15` passed, including `9/9` focused schedule tests.
- Existing M1-02 browser contract tests and self-test: passed.
- Static asset protection test: `27/27` passed after adding `schedule-workbench.js` to the server allowlist.
- M1-03 focused service/HTTP tests: `13/13` passed in the temporary Python runtime.
- Compile, docs, progress, and diff checks: passed.

Additional browser/API synthesis evidence:

- `node scripts/run_m1_03_browser_wbs_gate.mjs --browser "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" --profile ".tmp\\m1-03-browser-p2-profile"` -> 20/20 checks passed: WBS creation and replay, task edit request, optimistic conflict refresh, historical read-only state, schedule calculation and replay, approval states and reconciliation, different-snapshot conflict rejection, approval-read failure preservation, chronological version labels, request boundaries, responsive layout, no console failures, and no external HTTP requests.
- The browser gate exposed and closed two cross-layer defects: historical plan selection now sets the workbench read-only state, and the schedule client validates browser/server calendar dates as `YYYY-MM-DD`.
- The browser editor now covers the full frozen task-field set: title, duration, predecessors, owner role, planned start/finish, hard deadline, approved buffer, lock flag, and execution status. Advanced schedule controls remain inline and collapse per task without changing the approved black/white/purple design direction.
- Focused frontend tests now pass `9/9` in `tests/schedule-workbench.test.mjs`; the browser gate contract test passes `3/3`.

An independent non-implementer review found and then verified closure of exact-snapshot reconciliation, atomic version-switch failure handling, and current/history label defects, returning `CLEAN APPROVE`. This accepts the local browser preparation package only. Real PostgreSQL 18.4 runtime evidence and authorized real-proposal validation remain unavailable, so M1-03 stays `pending` while M1-02 is the registry's only `in_progress` task. Export belongs to M1-04 and is not a blocker for this package.
