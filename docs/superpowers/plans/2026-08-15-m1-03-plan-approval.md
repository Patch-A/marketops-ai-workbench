# M1-03 Plan Approval Work Package

Status: `reviewed package accepted as part of completed M1-03`

Date: `2026-08-15`

Task ID: `M1-03`

Baseline commit: `da475dd4164bde74a82b30604d8541f64b9192dc`

Owned paths: `apps/api/migrations/0005_wbs_plan_approval.sql`, `apps/api/marketops_schedule/`, focused M1-03 service/PostgreSQL/HTTP tests, `apps/api/openapi/project-import.openapi.yaml`, `project-import.js`, `schedule-workbench.js`, `styles.css`, browser gate scripts and this plan.

Forbidden paths: real customer material, credentials, tokens, research PDFs, local databases, unrelated milestone files, `project-status.json`, generated `docs/PROJECT_STATUS.md`, and the top-level CI workflow until the complete acceptance package is independently reviewed.

Frozen input: authenticated server scope, project and plan route identity, positive `expectedPlanVersion`, one schedule snapshot UUID, and a trimmed human approval reason.

Frozen output: one immutable approval identity bound to the exact plan version and ready schedule snapshot, server-derived digests and timestamp, plus a replay marker. No scope, actor, credential, export, or source-content field is returned.

Reviewer role: a non-implementer must verify scope binding, immutable persistence, ready-only gating, replay/conflict behavior, audit creation, strict HTTP fields, and browser responsive/accessibility states.

## 1. Job statement

For a project owner or planner, approve one exact WBS version after its deterministic schedule is `ready`, so execution can start from a traceable plan without mutating the reviewed proposal or hiding unresolved schedule risk.

## 2. Frozen contract

- Approval targets one immutable `planVersion` and one immutable `scheduleSnapshotId`.
- The server verifies project, workspace, client, actor, plan, plan version, schedule snapshot, plan digest, and `ready` status. The browser submits only the plan version, snapshot ID, and a non-empty human reason.
- A `needs_review` snapshot cannot be approved. The user must edit the WBS or calendar inputs and calculate a new `ready` snapshot.
- Approval is append-only and idempotent for the same plan version and snapshot. A repeated request returns the existing approval rather than creating a second decision.
- A later WBS revision does not mutate or revoke the historical approval. The latest plan version is simply unapproved until it receives its own approval.
- Every approval writes an audit event. The response exposes the approval ID, target versions, reason, actor-independent public timestamp, and replay flag; it never exposes scope claims or credentials.

## 3. User flow

1. Load the latest WBS and schedule snapshot.
2. Show `ready` as approvable; show `needs_review` with conflicts and deadline misses as blocked.
3. The user opens an inline approval section, confirms the exact WBS version and schedule digest prefix, and enters a reason.
4. The client submits the narrow approval body and reconciles uncertain responses with a server GET.
5. The approved version remains visible as immutable history. A new revision returns the current plan to an unapproved draft state.

## 4. Data and safety boundaries

- No browser storage, model decision, automatic approval, external connector, or cross-project retrieval.
- Approval is not evidence of real user value, schedule correctness for every calendar, ROI, production readiness, or payment willingness.
- Scope and actor identity remain server-derived. Client-supplied digest, organization, workspace, client, actor, or timestamps are rejected.
- A failed approval leaves the current plan and schedule snapshot unchanged and returns a stable public error code.

## 5. Implementation order and ownership

1. Migration and repository contract: approval table, unique target, scope/RLS, append-only trigger, audit event. Owned paths: `apps/api/migrations/`, `apps/api/marketops_schedule/postgres.py`, focused PostgreSQL tests.
2. Service and HTTP contract: create/read approval, replay, exact request fields, `ready` gate, conflict and failure mapping, OpenAPI mutations. Owned paths: `apps/api/marketops_schedule/service.py`, `apps/api/marketops_import/http.py`, OpenAPI, focused API tests.
3. Browser state: approval section, blocked/ready/loading/conflict/error states, narrow request body, uncertain-response reconciliation, history display. Owned paths: `project-import.js`, `schedule-workbench.js`, `styles.css`, browser gate and frontend tests.
4. Independent review: a non-implementer verifies identity binding, ready gating, replay, audit visibility, no client-owned facts, and responsive/accessibility behavior.

## 6. Acceptance checks

- A `ready` snapshot can be approved exactly once and replayed safely.
- A `needs_review` snapshot is rejected without a database write.
- Wrong project, plan, version, snapshot, digest, scope, or stale target fails closed.
- A revision after approval leaves the old approval read-only and the new version unapproved.
- Browser requests contain only the frozen fields and never include scope, actor, digest, or timestamps.
- Focused service/HTTP/PostgreSQL tests, Chromium approval gate, OpenAPI mutation checks, docs/progress checks, and independent review all pass.

The registry remains unchanged until the full M1-03 acceptance evidence, including authorized or de-identified real-proposal validation, is complete.

## 7. Review evidence

- Full Python regression: `404` tests passed; `27` capability-gated tests were skipped because local PostgreSQL test URLs were unavailable.
- Frontend regression: `15/15` Node tests passed, including exact-target approval reconciliation and current-version chronology.
- Actual Chromium gate: `20/20` checks passed at 375 px and 1440 px, including ready/blocked approval, uncertain-response reconciliation, different-snapshot conflict handling, approval-read failure during version switching, request boundaries, console safety, and zero external requests.
- Static contracts: OpenAPI passed `43` guards and `83` mutations; PostgreSQL migration/runner contracts, 74 Markdown files, the progress registry, JavaScript syntax, and `git diff --check` passed.
- Independent non-implementer review first found one P1 and two P2 browser-state defects. Exact snapshot matching, atomic version switching, and chronological version labels were fixed and covered by tests; the re-review returned `CLEAN APPROVE`.

## 8. Remaining evidence boundary

- Local static and fake-adapter evidence does not replace PostgreSQL 18.4 runtime testing. The capability-gated approval runtime test remains unexecuted without approved database URLs.
- No authorized or de-identified real proposal was available. The package establishes synthetic engineering behavior only; it does not establish schedule completeness, production reliability, demand, ROI, repeat use, or payment willingness.
- Therefore this preparation package is reviewed, while registry task `M1-03` correctly remains `pending` and `M1-02` remains the only `in_progress` task.
