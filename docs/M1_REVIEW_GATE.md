# M1 Review Gate

Status: completed for the bounded proposal-to-schedule engineering gate

Date: 2026-08-15

Task ID: M1-05

## Acceptance decision

M1 passes its bounded engineering gate. Two user-authorized,
repository-external derived proposal sets completed the recorded path from an
approved proposal through cited review decisions, WBS creation, deterministic
scheduling, exact-version approval, execution updates, audit, and export.

The gate's requirement that one anonymized project reach an accepted
executable schedule without critical dependency omissions is satisfied within
the reviewed candidate boundary:

- The latest validation-project-A review snapshot contains 18 approved, 13 modified, and 16
  rejected candidates. Its WBS contains exactly 31 tasks, matching every
  approved or modified candidate and excluding every rejected candidate.
- The latest validation-project-B review snapshot contains 2 approved and 9 modified
  candidates. Its WBS contains exactly 11 tasks.
- Both stored schedule snapshots are `ready`; each immutable approval targets
  the exact ready snapshot for the same plan version.
- A database integrity query found zero predecessor references to a missing
  task in either accepted plan. The deterministic scheduler and focused tests
  reject missing predecessors and cycles rather than producing a ready result.

This is evidence of complete candidate-to-task mapping and structurally valid
declared dependencies. It is not evidence that extraction found every
semantic dependency in the original source material, or that an external
domain expert judged the plans complete.

## Reproducible checks

- Focused M1-03 discovery run: 46 tests collected, 39 executed and passed,
  with 7 capability-gated skips.
- Focused M1-04 discovery run: 20 tests collected, 14 executed and passed,
  with 6 capability-gated skips.
- Real PostgreSQL 18.4 and FastAPI M1-04 run: 17/17 passed, comprising 11
  service/HTTP checks, 5 PostgreSQL execution checks, and 1 real FastAPI ASGI
  check.
- M1-03 Chromium WBS and approval gate: 20/20 checks passed.
- M1-04 Chromium execution and export gate: 10/10 checks passed.
- OpenAPI mutation gate: 45 guards and 90 mutations passed.
- Frontend unit checks: 14/14 passed across schedule and execution clients.
- Python compilation passed for `apps/api`.

The PostgreSQL evidence was rerun against the existing isolated
`marketops-postgres-m1-04-gate` container. The stored acceptance facts were
queried from the isolated `marketops-postgres-m1-03-local` container without
copying proposal text, identifiers, credentials, or database contents into the
repository.

## Review and claim boundary

The M1-02, M1-03, and M1-04 package records retain their non-implementer review
results, including the defects found and closed before final `CLEAN APPROVE`
decisions. The primary integrator reran the current tree's database, HTTP,
browser, contract, frontend, and compilation checks before closing this gate.

M1 therefore establishes a controlled single-user private-runtime engineering
path. It does not establish production reliability, universal schedule
correctness, expert semantic completeness, demand, time savings, ROI, repeat
use, or willingness to pay. Those claims require the live-task and commercial
evidence defined in the acceptance criteria.

## Next validation

M2 may begin with workspace-scoped retrieval and cited knowledge storage, but
project-learning promotion must remain human controlled. In parallel, the
project should preserve the M1 workflow for a de-identified live task and
record retained tasks, edits, reported omissions, elapsed time, repeat use,
and payment behavior separately from engineering test results.
