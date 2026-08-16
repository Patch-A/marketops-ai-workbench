# M2-02 Project Capsule, Feedback, and Candidate Knowledge

Status: active work package

Task ID: `M2-02`

Baseline commit: `d22517bf166f6d704454ea99649f745243d7fb3c`

## Job statement

For a project operator closing an approved project plan, help them preserve the
exact plan, latest execution state, measured or observed outcomes, and explicit
retrospective feedback as a versioned Project Capsule, so the system can create
traceable project-scoped knowledge candidates without silently turning raw project
material or model guesses into reusable knowledge.

## Confirmed facts, decisions, and unknowns

Confirmed facts:

- `projects.status` currently permits only `planning`, `active`, and `archived`.
  It does not identify a completed project.
- M1 persists immutable WBS plan versions, exact-version plan approvals,
  deterministic schedule snapshots, WBS tasks, and append-only task execution
  updates.
- M2-01 persists immutable source identities and cited source chunks, scoped to one
  project by default.
- The current runtime has no admitted model-provider or BYOK execution adapter.
  A model response therefore cannot be a dependency or completion claim in this
  slice.
- M2-03 owns approval, revision, rejection, revocation, scope elevation, and
  cross-project reuse of candidate knowledge.

Decisions for M2-02:

- Project completion is an explicit, actor-attributed finalization event. It is not
  inferred from `projects.status`, project archival, or the passage of time.
- Finalization binds one exact approved WBS plan version and its approval/schedule
  snapshot, all tasks in that version, and the latest execution state of every
  task. Every task must be terminal: `completed` or `cancelled`.
- At least one structured outcome observation or one explicit retrospective
  finding is required. Outcome values remain user-provided observations with a
  source reference; the system does not invent KPI values or causal explanations.
- The Project Capsule is an append-only snapshot. Replaying identical canonical
  input is idempotent; changed feedback or corrected source references creates the
  next capsule version and retains prior versions.
- Candidate generation is deterministic and dependency-neutral. It may restate a
  structured outcome observation, an observed completed-task duration, or an
  explicitly classified retrospective finding. It must not infer a rule,
  recommendation, ROI, causality, or general applicability.
- Every generated item starts as `candidate` with `project` scope and cites the
  exact capsule version plus the feedback, task execution update, plan version, or
  artifact version from which it was derived.
- M2-02 exposes no operation that can approve a candidate, change its scope, make
  it active, or retrieve it from another project.

Unknowns:

- Real users have not yet established which outcome fields and retrospective
  prompts are consistently available at project close.
- Actual task dates are calendar dates while planned duration is a deterministic
  scheduling input. M2-02 may preserve both facts, but it must not claim a
  like-for-like variance unless calendar semantics are explicitly reconciled.
- Project Capsules built from the synthetic fixture can validate traceability,
  versioning, and isolation only. They cannot establish usefulness, demand, ROI,
  time savings, repeat use, or willingness to pay.
- Retention and deletion propagation from source artifacts into historical capsule
  evidence remains a separate policy decision. Stale or withdrawn sources must
  fail closed when later resolved.

## Work-package contract

- Task and baseline: `M2-02` from
  `d22517bf166f6d704454ea99649f745243d7fb3c`.
- Primary integrator paths: migration `apps/api/migrations/0008_project_learning.sql`,
  PostgreSQL adapters, HTTP/OpenAPI integration, backup compatibility, runtime
  gates, this plan, registry and generated status, and final commits.
- Harness first-package owned paths:
  `apps/api/marketops_learning/__init__.py`,
  `apps/api/marketops_learning/service.py`, and
  `apps/api/tests/test_m2_02_learning_service.py`.
- Harness forbidden paths: migrations, PostgreSQL adapters, HTTP/OpenAPI files,
  backup code, scripts, CI, registry/status files, Git operations, external files,
  credentials, model calls, dependencies, and every path not explicitly owned.
- Frozen service input: an authenticated server-derived organization/workspace/
  client/actor scope and a repository-assembled completion snapshot containing the
  project ID, exact plan/plan-version/approval/schedule identities and digests,
  immutable source artifact-version references, the frozen task set, every task's
  latest execution update, and bounded structured outcome/retrospective feedback.
  Scope, approval state, task facts, source hashes, candidate status, and candidate
  scope never come from an untrusted client body.
- Frozen service output: an immutable capsule root and version, a canonical digest,
  typed evidence references, and deterministic project-scoped candidate knowledge.
  Identical canonical input returns the same identities; changed canonical input
  produces a new capsule version without modifying history.
- Failure contract: invalid identity or bounds, unapproved/mismatched plan,
  non-terminal or missing task state, absent feedback, invalid source reference,
  cross-scope data, digest drift, version conflict, or persistence failure must fail
  without publishing a capsule or candidate.
- Reviewer role: a non-implementer reviewer must inspect scope binding, terminal
  task completeness, deterministic identity/digest behavior, evidence coverage,
  append-only history, failure atomicity, audit redaction, RLS, backup/restore, and
  the M2-03 boundary before M2-02 can be completed.

## User and workflow map

Primary user: the operator responsible for closing one project.

Secondary user: a reviewer who inspects the retained evidence before later
promoting any knowledge. M2-02 does not add team roles.

```text
selected project and exact approved WBS plan version
-> load approval, schedule, task set, and latest execution states
-> reject if any task is missing state or is non-terminal
-> collect bounded outcome observations and retrospective findings
-> validate artifact/task/source references in the same project scope
-> explicitly finalize
-> atomically append Project Capsule version and evidence links
-> deterministically generate project-scoped candidate knowledge
-> show traceability and version history
-> leave approval, scope elevation, reuse, and revocation to M2-03
```

Human-controlled boundaries:

- Choosing when to declare a project finalized.
- Recording actual outcome values, their source references, and retrospective
  findings.
- Classifying a retrospective finding as a success pattern, failure counterexample,
  risk check, process observation, or non-reusable note.
- Correcting feedback through a new capsule version rather than overwriting history.
- Approving or broadening any candidate only in M2-03.

## MVP scope and non-goals

MVP scope:

- Explicit, authenticated finalization against one exact approved plan version.
- Terminal task-set validation using the latest append-only execution update for
  every WBS task.
- Bounded structured outcome observations and retrospective findings with typed
  same-project source references.
- Append-only capsule root/version history with canonical content digests and
  replay-safe identities.
- Deterministic project-scoped candidates for outcome observations, completed-task
  observed durations, and explicitly reusable retrospective findings.
- Evidence links from every candidate to the exact capsule version and its source
  facts.
- Forced-RLS and explicit organization/workspace/client/project predicates, audit
  events, transaction rollback, and logical backup/restore coverage.

Explicit non-goals:

- Model calls, autonomous synthesis, generic chat, embeddings, or prompt design.
- Candidate approval, revision, rejection, revocation, activation, scope elevation,
  or cross-project retrieval; these belong to M2-03.
- Automatic inference of project completion from archival or task dates.
- Automatic KPI collection, market-signal ingestion, impact analysis, or causal
  claims; these remain M3 or later work.
- A reporting/export UI, retrospective document export, or team workflow; these
  remain M4 work.
- Production authentication, production retention/deletion policy, demand, ROI,
  time savings, repeat use, or payment claims.

## States, data, and safety boundaries

Finalization has request states `validating`, `ready`, `persisting`, `completed`,
`replayed`, `conflict`, `invalid`, `unauthorized/not_found`, `failed`, and
`cancelled`. Only a successful atomic transaction publishes the capsule version,
its evidence, its candidates, and a redacted audit event.

Capsule versions are append-only. A capsule root belongs permanently to one
organization/workspace/client/project. The first successful distinct snapshot is
version 1; later distinct snapshots increment monotonically under a transaction
lock. A canonical digest replay returns the existing version and adds no duplicate
candidate or audit event.

Feedback records are immutable facts attributed to the actor and capsule version.
Outcome observations require a bounded metric/observation name, an actual value or
qualitative result, a source type and same-project source identity, and an optional
planned value/unit. Retrospective findings require a bounded finding, explicit
classification, evidence source, and a boolean reusable-candidate choice. Empty
feedback, untyped free-form evidence, external URLs, and unknown source types fail
closed in the first slice.

Candidate status is always `candidate`; scope is always `project`; project identity
is non-null. Candidate evidence uses a closed source-type set and retains the
source identity plus a canonical evidence hash or bound snapshot identity. The
candidate stores no approval actor, elevated scope, adoption count, or active flag.

Data boundaries:

- PostgreSQL remains the fact source; capsule summaries are derived snapshots.
- Raw artifact bytes, full retrieved chunks, secrets, source text, and DSNs are not
  copied into queue arguments, audit events, errors, or candidate metadata.
- Facts, human feedback, deterministic derivations, and future model hypotheses
  remain separate business classifications.
- Cross-scope and absent IDs use indistinguishable not-found behavior.
- Source withdrawal, deletion, or digest drift makes the relevant evidence stale;
  it must not silently remain current.

## Ordered implementation checklist

1. Freeze dependency-neutral domain types, bounds, canonical serialization,
   terminal-task rules, deterministic capsule/candidate identities, evidence
   semantics, replay behavior, and focused unit tests.
2. Add migration `0008` for capsule roots/versions, immutable feedback, candidate
   knowledge, and evidence links with composite scope foreign keys, forced RLS,
   append-only triggers, unique replay keys, bounded fields, and least privilege.
3. Implement the asyncpg repository so it loads approval/task/source facts under
   transaction-local scope, locks version allocation, atomically persists the
   snapshot/candidates/audit event, and sanitizes driver failures.
4. Add unit and real PostgreSQL tests for terminal and cancelled task sets, missing
   updates, approval/digest drift, identical replay, changed feedback creating a
   new version, cross-scope decoys, candidate evidence completeness, audit
   redaction, cancellation, and rollback.
5. Add authenticated closed-body finalization and read-only capsule/version/
   candidate routes plus static OpenAPI mutation checks. Client input may contain
   human feedback and expected-version concurrency fields, but not scope, plan,
   execution, source-hash, candidate-status, or candidate-scope facts.
6. Upgrade the logical backup manifest through migration 0008, retain prior bundle
   read compatibility, and restore non-empty capsule/feedback/candidate/evidence
   rows into an isolated PostgreSQL 18.4 database.
7. Run focused tests, PostgreSQL/RLS and recovery gates, M1 execution and M2-01
   retrieval regressions, compile, docs, progress, OpenAPI, secret/diff checks, and
   inspect the resulting evidence boundaries.
8. Obtain independent non-implementer review and close all P0/P1/P2 findings. Only
   then may the primary integrator record completion evidence and mark M2-02
   completed.

## Acceptance commands

The first domain package must pass:

```powershell
python -m unittest apps.api.tests.test_m2_02_learning_service -v
python -m compileall -q apps/api/marketops_learning
git diff --check
```

The integrated package will add focused PostgreSQL, HTTP/OpenAPI, recovery, and
regression commands before completion. `node scripts/progress.mjs check` and
`node scripts/check-docs.mjs` remain mandatory throughout.

## Risks, unknowns, and smallest experiment

Main risks are treating archival as completion, capturing only favorable outcomes,
creating a candidate without enough evidence, comparing incompatible planned and
actual duration semantics, leaking raw project material through summaries, allowing
project candidates to become cross-project facts, and losing append-only history
through an update path.

Smallest falsifiable experiment:

- Seed one approved synthetic plan with completed and cancelled tasks, exact latest
  execution updates, one sourced outcome observation, and one sourced retrospective
  finding. Add near-identical decoys in another project and workspace.
- Finalize the authorized project twice with identical input, then once with a
  corrected feedback value.
- Success: the first call atomically creates capsule version 1 and only project-
  scoped candidates; the identical replay creates nothing; the correction creates
  version 2 while version 1 remains readable; every candidate resolves to exact
  same-project evidence; decoys never appear.
- Failure: a non-terminal/missing task is accepted, duplicate replay rows appear,
  history is overwritten, an uncited candidate is created, any scope field can be
  client-forged, a candidate becomes approved/elevated, or sensitive content
  appears in audit/error output.
- Retain only synthetic identifiers, canonical digests, counts, states, and
  redacted failure codes as evidence.

This experiment validates bounded engineering behavior only. It does not prove
that the generated candidates are useful, that real projects provide complete
outcome data, or that the product improves time, ROI, repeat use, or payment.
