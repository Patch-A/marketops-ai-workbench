# M1-03 WBS 与确定性排期准备切片

状态：`Preparation package independently reviewed; M1-02 remains the registry's only in_progress task`

日期：`2026-08-14`

## 1. Job statement

对于已经审核过方案候选的活动、品牌或 B2B 营销项目负责人，在准备执行计划时，帮助其把已接受的交付物和里程碑整理成可编辑 WBS，并在任务、依赖、日期或缓冲变化后稳定重算排期，从而让计划影响可解释、可复核且不会覆盖原始方案事实。

## 2. User and workflow map

```text
approved/modified review candidates
  -> create editable WBS draft
  -> human edits task fields and dependencies
  -> deterministic schedule calculation
  -> ready | needs_review | failed
  -> human reviews conflicts and approves a plan version
  -> M1-04 execution state and export
```

Primary user is the project owner or planner. A reviewer may inspect and approve a plan later; no external client or connector is needed for this slice.

The human remains responsible for accepting task wording, assigning owners, changing dates and buffers, resolving locked-date conflicts, and approving the resulting plan. The scheduler never silently moves a locked task, changes the approved proposal, or turns a constraint/assumption into a task without an explicit candidate kind.

## 3. MVP contract

### Inputs

- Server-owned approved proposal identity: `versionId` and `sha256`.
- One selected immutable M1-02 review snapshot containing project/run identity, selected review version, candidates, complete human decisions, replacement text when modified, and `sourceCitation`.
- The server-only `bind_review_snapshot` adapter combines the scoped M1-02 GET response with the already validated route `projectId/runId`; raw browser or client JSON must not bypass the M1-02 scoped read service.
- Editable task fields: title, duration in workdays, predecessors, owner role, planned dates, hard deadline, approved buffer, lock flag, and execution status.
- Project start date and an explicitly supplied holiday list.

### Outputs

- A versioned WBS draft containing only `approve` or `modify` deliverable/milestone candidates as tasks.
- Constraints and assumptions retained as cited controls, not silently converted into tasks.
- A new deterministic schedule snapshot with topological order, calculated dates, conflicts, deadline misses, source date drift, status, and a digest.
- Stable failure codes for invalid or mismatched review/proposal identity, missing approval evidence or citation, invalid duration/buffer/date/type, missing predecessor, dependency cycle, locked-date mismatch, and empty plans.

### Version and source boundary

- The input review snapshot and candidate citations are immutable facts; the WBS draft and schedule are new derived versions.
- The review run proposal identity and every accepted citation must match the server-approved proposal; every accepted candidate must carry a complete matching human decision for the selected run and snapshot.
- The review run project identity must match the requested project before any candidate is consumed.
- The adapter preserves the existing M1-02 HTTP response contract by enriching a deep copy with route identity; the WBS domain still rejects unbound snapshots.
- The WBS stores the selected `sourceReviewRunId` and `sourceReviewVersion`; it never infers snapshot identity from the maximum candidate decision version.
- Task edits require `expectedPlanVersion`, allow only WBS-owned fields, and return a new plan version; stale edits fail with `plan_version_conflict`.
- `pending` and `reject` candidates never enter the formal WBS.
- A modified candidate uses its replacement text while retaining the original candidate text, review status, version, and citation.
- No cross-project retrieval, browser storage, model decision, HTTP write, migration, or automatic schedule approval is part of this slice.

## 4. States and failure handling

`draft -> calculating -> ready | needs_review | failed`; an explicit human approval creates the next plan version. Locked dependency conflicts and hard-deadline misses return `needs_review` with the locked dates preserved. Invalid input, cycles, missing predecessors, missing citations, and inconsistent locked dates fail closed without a partial schedule.

## 5. Acceptance checks for this preparation package

- Same plan, project start, and holiday inputs produce byte-stable schedule output and digest.
- Candidate import excludes pending/rejected items and preserves citation objects.
- Review run, selected snapshot, decision chain, and citations are bound to the same approved proposal before any WBS task is created.
- Candidate modification requires replacement text and never mutates the source candidate.
- Editing duration, predecessors, buffers, or lock fields affects only the new schedule result.
- Missing predecessors and cycles are rejected deterministically.
- Locked dates are not moved; dependency conflicts and deadline misses are surfaced as `needs_review`.
- `node scripts/progress.mjs check`, focused Python tests, `compileall`, and `git diff --check` pass.

## 6. Explicit non-goals and unknowns

This package does not establish that real users understand WBS fields, that dates match every organization calendar, or that planning is faster. It does not support resource capacity, start-to-start or finish-to-finish links, partial completion dependencies, time zones, automatic holiday imports, probabilistic estimates, or production persistence. Those decisions require later evidence and must not be inferred from the M0 synthetic fixture.

The smallest later validation experiment is one authorized or de-identified proposal: compare a manual WBS baseline with the reviewed draft, record retained/edited/rejected tasks, missed dependencies, and time to reach an approved schedule.

## 7. Implementer evidence

- Candidate implementation is limited to `apps/api/marketops_schedule/` and `apps/api/tests/test_m1_03_schedule_domain.py`; it does not add HTTP, persistence, migration, browser UI, or external dependencies.
- Focused M1-03 domain tests pass 13/13. They cover the actual M1-02 HTTP response shape plus scoped server adapter, approved-only handoff, explicit review-snapshot identity, complete matching human decisions, project/proposal/citation binding, modified text with preserved source text, malformed plan/date/type failures, deterministic output, input immutability, version conflicts, immutable review-owned fields, digest changes, missing plan identity, missing predecessors, dependency cycles, locked conflicts, and deadline misses.
- The existing M0-03 scheduling check still passes all 4 scenarios, 3 rejected-input paths, and 2 reported issue types.
- Full local API discovery passes 364 tests and skips 55 capability-gated tests. The skipped tests require PostgreSQL, FastAPI/runtime dependencies, Linux `flock`, or Windows symbolic-link privileges; local skips are not runtime acceptance evidence.
- Frontend review tests pass 6/6; Python `compileall`, JavaScript syntax, `node scripts/progress.mjs check`, and `git diff --check` pass.
- The repository Quality workflow already runs `python -m unittest discover -s apps/api/tests -p 'test_*.py' -v`, so the new domain tests are included without changing the top-level CI workflow.

Independent review found trust-boundary, review-version, date/type, project-scope, and M1-02 handoff incompatibilities. The follow-up implementation adds a server-only route-binding adapter, binds the selected review run/snapshot and full human decisions to the project and approved proposal, records the explicit snapshot version, and adds stable fail-closed validation for malformed plans, citations, dates, and editable field types. M1-03 remains `pending`, and M1-02 remains the only registry task marked `in_progress` until the revised package passes same-SHA CI and final non-implementer re-review.

The final reviewed implementation is commit `94d534b`. GitHub Actions Quality run `31803184303` passed both `static-checks` and `m1-01-runtime`, and the final non-implementer review returned APPROVE with no P0/P1/P2 finding. This closes only the domain preparation package. M1-03 remains `pending` because HTTP writes, PostgreSQL persistence, the browser WBS editor, and real-proposal validation are not implemented.

## 8. Follow-up service integration package

The follow-up package in [`2026-08-14-m1-03-persistence-integration.md`](superpowers/plans/2026-08-14-m1-03-persistence-integration.md) adds the server-side persistence preparation without changing the registry status. It freezes an append-only plan root, plan versions, normalized tasks, deterministic schedule snapshots, forced RLS, source-review/proposal integrity triggers, optimistic version checks, replay of identical sources and schedule digests, rollback coverage, and backup compatibility for the new migration. The package deliberately stops before HTTP write routes, browser editing, plan approval, export, and real-proposal validation; M1-03 remains `pending` until those acceptance boundaries are separately evidenced.
