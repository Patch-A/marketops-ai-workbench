# M2-01 Workspace-Scoped Retrieval Core

Status: active work-package contract

Task ID: `M2-01`

Baseline commit: `b91c4238366246625cf947c3574feeadb8896427`

## Job statement

For a project operator, help them find verifiable facts in the current project's
retained document versions when reviewing or planning work, so they can inspect
the exact source location and reject stale or unauthorized evidence before using
it.

## Confirmed facts, decisions, and unknowns

Confirmed facts:

- M1 already persists immutable, scope-bound `ArtifactVersion` records and keeps
  source bytes outside PostgreSQL.
- M0-04 passed 16 synthetic English and Chinese retrieval queries with scope
  filtering before scoring, deterministic ordering, and fail-closed citation
  freshness checks.
- M0-05 adopted PostgreSQL 18.4 and pgvector 0.8.6 conditionally, but prohibited
  shared approximate vector indexes whose filtering can occur after scoring.
- Raw project material is project-scoped by default. Cross-project reuse requires
  later human-approved knowledge promotion.

Decisions for the first slice:

- Reuse M1 artifact versions as the only source identity. Do not add a second
  upload or file-fact path.
- Index only an explicitly selected artifact version belonging to the current
  project. Parser and chunker versions are server-owned facts.
- Start with a deterministic lexical plus character n-gram hybrid baseline. The
  response must label this mode `lexical_ngram`; it is not an embedding claim.
- Filter the authorized project/workspace/client candidate set before either
  score is calculated. PostgreSQL forced RLS and explicit predicates both apply.
- Keep the HTTP surface narrow and authenticated. Search text uses a JSON POST
  body so normal access logs do not put customer queries in URLs.
- Treat index withdrawal as a real state transition with an audit event and
  physical chunk removal. It does not delete the immutable M1 source object.

Unknowns:

- M1 artifact versions are append-only and referenced with `ON DELETE RESTRICT`.
  End-to-end original-file deletion needs a separate retention and redaction
  design. Index withdrawal and stale-reference behavior can be validated now,
  but must not be reported as source-file deletion.
- Real Chinese marketing-document recall, synonym behavior, scale, latency, and
  embedding value are not established by the synthetic fixture.
- The current private-deployment authenticator is not a production multi-user
  membership or role system.

## Work-package contract

- Task and baseline: `M2-01` from `b91c4238366246625cf947c3574feeadb8896427`.
- Owned paths: `apps/api/marketops_retrieval/**`,
  `apps/api/migrations/0007_source_retrieval.sql`, focused M2-01 tests under
  `apps/api/tests/**`, the retrieval routes in
  `apps/api/marketops_import/http.py`, runtime assembly in `apps/api/main.py`,
  the retrieval portion of `apps/api/openapi/project-import.openapi.yaml`,
  `scripts/check_m2_01_openapi_contract.py`,
  `scripts/run_m2_01_postgres_gate.py`, and this plan.
- Integrator-only paths: `project-status.json`, generated
  `docs/PROJECT_STATUS.md`, and final commits. The top-level CI workflow remains
  unchanged until a reviewed runtime package needs a dedicated gate.
- Forbidden paths: real customer files, repository-external validation packages,
  browser profiles, local databases, exports, credentials, DSNs, research PDFs,
  automatic knowledge-promotion code, generic chat, external model calls, and
  unrelated milestone files.
- Frozen input: one authenticated server-derived organization/workspace/client/
  actor scope, a canonical project ID, an existing same-project artifact-version
  ID, its persisted SHA-256 and retained object bytes, and supported deterministic
  parser output.
- Frozen indexed output: immutable chunks containing source version ID, ordinal,
  normalized text, content SHA-256, structured location, parser version, chunker
  version, source SHA-256, and full scope columns. Replaying the same version and
  pipeline version returns the same index identity and chunk hashes.
- Frozen search input: canonical project ID plus a closed JSON object containing
  `query` and optional bounded `limit`. Scope IDs, retrieval mode, candidate
  scope, parser version, and actor never come from the client.
- Frozen search output: deterministic ranked results containing chunk ID, safe
  excerpt, separate lexical and n-gram ranks, combined rank, and a citation with
  artifact version ID, source SHA-256, content SHA-256, location, parser version,
  and freshness status. Candidate counts include only authorized chunks.
- Reviewer role: a non-implementer security/retrieval reviewer must inspect
  scope-before-score behavior, forced RLS, deterministic ranking, content and
  source hash validation, withdrawal atomicity, audit redaction, failure paths,
  and actual PostgreSQL evidence before M2-01 can be completed.

## User and workflow map

Primary user: the operator working inside one selected project.

Secondary users: a reviewer who checks citations and a deployment administrator
who controls retention. M2-01 does not introduce team roles.

```text
existing retained artifact version
-> validate project ownership and persisted source hash
-> parse and deterministically chunk
-> persist versioned index and citations
-> submit bounded project search
-> apply RLS and explicit scope filter
-> calculate lexical and n-gram ranks on authorized candidates only
-> validate source/chunk freshness
-> return cited results or fail closed
-> operator inspects evidence before using it
```

Human-controlled boundaries:

- Selecting which retained artifact version to index.
- Deciding whether a cited result supports a project decision.
- Withdrawing an index.
- Promoting any derived knowledge beyond the current project in later tasks.

## MVP scope and non-goals

MVP scope:

- Persistent source-index metadata and deterministic `SourceChunk` records.
- Markdown, TXT, and basic DOCX inputs accepted by the current M1 runtime parser;
  unsupported or partial formats continue to fail explicitly. CSV import exists,
  but CSV runtime parsing is not implemented and must not be advertised yet.
- Authenticated index, search, status/read, and index-withdrawal operations.
- Project/workspace/client isolation in service logic, SQL predicates, forced
  RLS, and cross-scope runtime tests.
- Deterministic lexical plus character n-gram ranking with Chinese single- and
  adjacent-character tokens carried forward from M0-04.
- Citation freshness checks, replay behavior, audit events, and withdrawal tests.

Explicit non-goals:

- Generic chat, answer generation, autonomous recommendations, or model calls.
- Client- or workspace-wide raw-document search.
- Automatic cross-project memory or knowledge promotion.
- Embeddings, approximate indexes, model downloads, rerankers, or background
  queue adoption in the first slice.
- PDF/PPTX/OCR support, broad parser replacement, or semantic-completeness claims.
- Production authentication, production deletion policy, demand, ROI, time
  savings, repeat use, or payment claims.

## States, data, and safety boundaries

Index states: `indexing -> ready | failed`, and `ready -> withdrawn`. A failed or
withdrawn index is never searchable. Replay of an identical ready index is safe;
a pipeline-version change creates a new immutable index rather than mixing chunks.

Search states: `loading`, `empty`, `ready`, `stale`, `unauthorized/not_found`,
`invalid`, `failed`, and `cancelled`. Cross-scope and absent project/version IDs
use indistinguishable not-found behavior.

Data boundaries:

- PostgreSQL is the fact source; any future vector data remains derived.
- Original source bytes are not copied into queue arguments, audit data, errors,
  URLs, or exports.
- Excerpts are bounded and returned only after authorization and freshness checks.
- Facts, model hypotheses, human decisions, and outcomes remain separate. This
  slice stores source facts and retrieval evidence only.
- Source, parser, chunker, and scoring versions participate in deterministic
  result identity. Mixed pipeline versions fail closed.

Deletion and audit boundaries:

- Index withdrawal deletes chunk text and derived tokens atomically, marks the
  index withdrawn, and appends a redacted audit event.
- Existing citations to a withdrawn index resolve as stale and cannot be reused
  as current evidence.
- The immutable original artifact version and object are retained in this slice.
  A later source-retention decision must define legal hold, audit preservation,
  derived-reference invalidation, backup propagation, and irreversible deletion.

## Ordered implementation checklist

1. Freeze domain types, validation errors, parser/chunker/scoring versions, and
   deterministic ranking tests in `marketops_retrieval/service.py`.
2. Add migration `0007` for source indexes and chunks with composite scope foreign
   keys, forced RLS, append-only identities, bounded fields, unique replay keys,
   and controlled withdrawal/deletion behavior.
3. Implement the asyncpg repository so scope is transaction-local, candidate
   authorization is materialized before scoring, writes are atomic, and driver
   errors are sanitized.
4. Add unit and real PostgreSQL tests for idempotent indexing, ordering, Chinese
   tokens, workspace/client/project decoys, content/hash drift, mixed pipeline
   versions, withdrawal, stale citations, cancellation, and rollback.
5. Add authenticated closed-body index/search/withdraw routes and a static OpenAPI
   mutation checker. Keep server scope and pipeline facts out of request bodies.
6. Rerun M0-04 as regression evidence, then run M1 focused regression, compilation,
   docs, progress, OpenAPI, PostgreSQL, and diff checks.
7. Obtain non-implementer review. Only after fixes and reproducible evidence may
   the primary integrator add completion evidence and mark M2-01 complete.
8. Evaluate a separate pgvector/embedding slice only after provider/model/version/
   license, deletion, rebuild, exact authorized-candidate scoring, and lexical
   fallback contracts are frozen.

## First-slice evidence

The dependency-neutral domain core is implemented in
`apps/api/marketops_retrieval/service.py`. Eight focused tests pass for stable
index/chunk identities and digests, bounded long-block splitting, deterministic
English and Chinese retrieval, scope contamination rejected before scoring,
source-hash freshness, content-drift citation invalidation, withdrawal removing
derived chunk text, and sanitized invalid-input failures. The frozen M0-04 gate
also still passes all 16 oracle cases plus scope-order, freshness, and CLI checks.

This was the first dependency-neutral checkpoint. Later evidence below adds
PostgreSQL, source-object loading, HTTP, and OpenAPI behavior; it still does not
establish original-file deletion, independent review, real-document recall, or
M2-01 completion. The registry must remain `in_progress`.

## PostgreSQL persistence evidence

Migration `0007_source_retrieval.sql`, the asyncpg repository, and a reproducible
PostgreSQL gate are implemented. The focused domain, persistence, indexing, and
HTTP suite passes 21/21 checks. A dedicated PostgreSQL 18.4 container applied and
recorded migrations 0001 through 0007, then the replay-safe runtime gate passed
5/5 checks:

- two concurrent identical index writes serialized on the deterministic index ID
  and produced one fact plus one replay;
- near-identical sources in a second workspace/client/project remained visible
  only under their own RLS scope;
- the application role had no table-wide index update, index delete, chunk update,
  or truncate privilege; withdrawal used only three column updates and controlled
  chunk delete;
- withdrawal removed all derived chunk text, appended one redacted audit event,
  and replayed without adding another event;
- a forced withdrawal-audit failure rolled back the status change, withdrawal
  fields, chunk deletion, and audit insert together;
- the server loaded an explicitly selected same-project `ArtifactVersion`,
  verified retained object size and SHA-256, parsed Markdown, persisted the index,
  and replayed it without accepting storage or pipeline facts from the caller;
- authenticated ASGI requests exercised index, search, status, and withdrawal
  against the actual PostgreSQL application role, including an empty result after
  withdrawal;
- container logging used `terse/panic`; 59 captured lines had zero matches for
  DSNs, passwords, synthetic source text, index markers, or UUIDs.

The M0-04 gate still passes 16 oracle cases plus scope-order, freshness, and CLI
checks. Existing backup and restore contract tests pass 33/33, but the current backup
manifest is schema v5 and does not include either the 0006 execution table or the
0007 retrieval tables. Those tests are regression evidence only, not M2 recovery
evidence. Backup schema expansion and real restore must pass before M2-01 can make
a recovery claim.

The M2 OpenAPI checker passes 11 guards and 11 mutations; the existing combined
contract checker passes 45 guards and 104 mutations. Full API discovery passes
450 tests with 36 capability-gated skips, while the five M2 PostgreSQL tests run
separately against the dedicated container. Search rechecks source hashes and
chunk-content hashes before scoring, and returns bounded cited excerpts through a
JSON POST body rather than a query URL.

CSV remains an import-only format: the runtime retrieval parser supports Markdown,
TXT, and basic DOCX, and rejects CSV explicitly instead of overstating support.
Backup and restore expansion through 0007, original-file deletion, independent
review, and real-document recall remain unfinished. The task stays `in_progress`.

## Acceptance commands

```powershell
python -m unittest apps.api.tests.test_m2_01_retrieval_service `
  apps.api.tests.test_m2_01_retrieval_postgres_adapter `
  apps.api.tests.test_m2_01_retrieval_indexing `
  apps.api.tests.test_m2_01_retrieval_http -v
python -m unittest apps.api.tests.postgres.test_m2_01_retrieval_runtime -v
python scripts/check_m2_01_openapi_contract.py
python scripts/check_hybrid_retrieval.py --check
python -m compileall -q apps/api
node scripts/check-docs.mjs
node scripts/progress.mjs check
git diff --check
```

## Risks and smallest validation experiment

Main risks are cross-scope leakage, stale citations that still look valid, chunk
deletion leaving derived text behind, ranking drift, and overstating an n-gram
proxy as semantic retrieval. A later embedding dependency adds model-license,
version-mixing, rebuild, cost, latency, and offline-deployment risks.

Smallest falsifiable experiment:

- Seed one relevant source and near-duplicate decoys across two projects, two
  clients, and two workspaces in PostgreSQL.
- Change only the authenticated scope while keeping the query constant.
- Success: unauthorized chunks never enter the scored-candidate count; authorized
  ordering and result hash repeat exactly; source/content hash drift fails closed;
  withdrawal removes all derived chunk text and makes old citations stale.
- Failure: any cross-scope result or candidate count, fresh response from stale or
  withdrawn data, ranking drift, partial deletion, or unsanitized source text in
  an error/audit event blocks the package.
- Retain only synthetic IDs, result hashes, counts, timings, and redacted failure
  codes as evidence. Do not retain source text or credentials.

This experiment can validate bounded engineering behavior only. It cannot prove
real-document recall, semantic completeness, user value, production reliability,
demand, ROI, repeat use, or payment.
