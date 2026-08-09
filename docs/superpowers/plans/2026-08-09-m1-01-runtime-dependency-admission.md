# M1-01 Runtime Dependency Admission Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Freeze a reproducible, license-reviewed Python HTTP, PostgreSQL, and migration runtime decision before adding any third-party package or executable API code.

**Architecture:** Official release, package metadata, license text, maintenance, Python 3.12 support, transitive dependencies, and replacement boundaries are recorded in a machine-checked audit. The decision may adopt, defer, or reject candidates, but this package does not install them; runtime code starts only after independent review.

**Tech Stack:** Node.js standard library checker, JSON evidence register, Markdown decision record, GitHub and PyPI official metadata.

---

## Frozen Work Package Contract

- Task ID: `M1-01`, Task 3 Step 5.
- Baseline commit: `9d5111990ff99eb8f7a97cb98398ef2a18a73781`.
- Branch: `codex/m1-01-server-import`.
- Frozen input: FastAPI HTTP runtime, PostgreSQL driver, multipart/server requirements, and migration tooling candidates suitable for Python 3.12.
- Frozen output: fixed candidate versions, direct and transitive license evidence, maintenance and compatibility evidence, one selected boundary per capability, fallbacks, and explicit unknowns.
- Forbidden output: an installed dependency, generated lock file, running HTTP endpoint, production-readiness claim, or a completed `M1-01` status.
- Integrator-only paths: `project-status.json`, `docs/PROJECT_STATUS.md`, `.github/workflows/quality.yml`, and final commits.
- Completion boundary: this decision only unlocks runtime implementation. PostgreSQL 18.4 execution, RLS, authentication, concurrency, recovery, and backup remain separate acceptance gates.

### Task 1: Evidence Schema and Failing Checker

**Files:**
- Create: `scripts/check_m1_01_runtime_dependencies.mjs`
- Create later in Task 2: `validation/results/m1-01-runtime-dependency-admission.json`

- [x] **Step 1: Add a checker that initially fails closed**

Require a fixed version, official release and license URLs, verified-at date, Python support, direct/transitive package snapshot, fact/inference/unknown rationale, replacement boundary, fallback, and one decision from `adopted`, `deferred`, or `rejected`. Reject `latest`, open version ranges, missing license evidence, popularity-based rationale, stale report rows, and adopted candidates with unresolved blockers.

- [x] **Step 2: Verify RED**

Run: `node scripts/check_m1_01_runtime_dependencies.mjs`

Expected: exit 1 because `validation/results/m1-01-runtime-dependency-admission.json` does not exist.

### Task 2: Official-Source Candidate Research

**Files:**
- Create: `validation/results/m1-01-runtime-dependency-admission.json`

- [x] **Step 1: Record HTTP runtime evidence**

Capture fixed FastAPI, Starlette, Pydantic, Uvicorn, and multipart package releases; read license files and distribution metadata; record Python 3.12 support and the boundary between the HTTP adapter and `ProjectImportService`.

- [x] **Step 2: Record PostgreSQL driver evidence**

Compare Psycopg 3 installation modes and at least one maintained alternative. Record libpq/binary-bundle implications, pooling, async support, Python 3.12 support, licenses, and the `ImportRepository` replacement boundary.

- [x] **Step 3: Record migration evidence**

Compare Alembic with a raw-SQL-oriented alternative and the no-framework option. Record SQLAlchemy or external-binary costs, PostgreSQL advisory locking/version tracking, downgrade policy, licenses, and the immutable migration-runner boundary.

- [x] **Step 4: Verify GREEN and weakening mutations**

Run: `node scripts/check_m1_01_runtime_dependencies.mjs`

Expected: pass with every required candidate and internally generated weakening mutation rejected.

### Task 3: Decision Record and Repository Integration

**Files:**
- Create: `docs/M1_01_RUNTIME_DEPENDENCY_DECISION.md`
- Modify: `docs/OPEN_SOURCE_REVIEW.md`
- Modify: `docs/superpowers/plans/2026-08-09-m1-01-server-import.md`
- Modify: `.github/workflows/quality.yml`
- Do not modify: `THIRD_PARTY_NOTICES.md` until a dependency is actually added to a manifest or distributed artifact.

- [x] **Step 1: Write the decision report**

Separate confirmed facts, reasonable inferences, and unknowns. State fixed versions, licenses, transitive snapshot, why each capability is adopted/deferred/rejected, replacement boundaries, rollback choices, and what this decision does not prove. Convert shared runtime unknowns into minimum experiments with one primary variable, success and failure signals, and retained evidence.

- [x] **Step 2: Update the candidate review and implementation plan**

Add the reviewed runtime candidates to `docs/OPEN_SOURCE_REVIEW.md` and mark only Task 3 Step 5 of the server-import plan complete. Keep Task 4 and `M1-01` open.

- [x] **Step 3: Add the checker to CI**

Run the new checker in `.github/workflows/quality.yml` without installing runtime packages.

- [x] **Step 4: Run acceptance checks**

Run:

```powershell
node scripts/check_m1_01_runtime_dependencies.mjs
node scripts/check_dependency_decision.mjs
node scripts/check-docs.mjs
node scripts/progress.mjs check
git diff --check
```

Expected: every command exits 0 while `M1-01` remains `in_progress`.

Hardening recheck: the checker freezes canonical boundary/fallback text, distribution conditions, optional/excluded extras, strict calendar dates, generated-at freshness, canonical alternatives, and the report claim boundary. Report checks evaluate positive installed/running/validated/production-ready claims for reviewed candidates and the selected runtime stack by sentence and contrast clause in English and Chinese, while allowing explicit direct and outer-scope negative statements. Its internal mutation suite also exercises body-scope injection, synchronous asyncpg bridges, advisory-lock/checksum/same-transaction omissions, semicolon SQL splitting, `transitiveSnapshot` null/object failure paths, alternative object/drift cases, claim synonyms, and mixed negative/positive clauses. Allowed negative statements are separate baselines, not weakening mutations. The mutation count is computed from the executed cases.

### Task 4: Independent Review and Stage Commit

- [x] **Step 1: Request independent specification review**

The reviewer must verify official-source traceability, license claims, Python 3.12 support, candidate completeness, and explicit unknowns. Any untraceable adopted claim blocks the package.

- [x] **Step 2: Request independent code-quality review**

The reviewer must weaken every checker guard, verify report/audit consistency, and confirm no dependency was installed or added to `THIRD_PARTY_NOTICES.md` prematurely.

- [x] **Step 3: Run the complete repository quality suite**

Use the full M1-01 command set from the server-import plan plus the new runtime dependency checker. Commit and push only after both reviews and all checks pass.

## Self-Review

- Spec coverage: versions, licenses, transitive dependencies, maintenance, Python 3.12, alternatives, replacement boundaries, fallbacks, and unknowns are mapped to Tasks 1-3.
- Claim boundary: dependency admission does not claim runtime behavior or production readiness.
- Four-quadrant boundary: shared known inputs and user-only unknowns are explicit; agent-known risks and alternatives are surfaced; shared runtime unknowns have falsifiable minimum experiments.
- Supply-chain boundary: no package, binary, model, customer file, token, or credential enters the repository in this work package.
- Status boundary: `M1-01` remains `in_progress` until real PostgreSQL and runtime acceptance pass.
