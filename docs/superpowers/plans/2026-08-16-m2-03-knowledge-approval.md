# M2-03 Knowledge Approval and Reuse Evaluation

Status: completed 2026-08-16

Task ID: `M2-03`

Baseline commit: `a8672235ae07f93611df90b79790a713ceac2d36`, with the verified
uncommitted M2-02 worktree as the frozen prerequisite input.

## Job statement

For a project operator reviewing a project-scoped knowledge candidate, provide an
explicit, actor-attributed, append-only decision path to approve, revise, reject,
revoke, and scope that candidate. A later project may only cite the approved
knowledge version when the effective scope permits it; it must never retrieve raw
source-project materials or obtain a candidate merely because it is similar.

## Confirmed facts, decisions, and unknowns

Confirmed facts:

- M2-02 persists immutable `knowledge_items`, their version-one content, and
  typed evidence. Each item is fixed to `status = candidate`, `scope = project`,
  and its source project.
- Current M2-02 reads are project scoped in both the PostgreSQL queries and
  forced-RLS policies. It has no approval or cross-project read route.
- `audit_events` and all M2-02 learning rows are append-only. Existing migrations
  use transaction-local scope helpers, composite tenant foreign keys, forced RLS,
  and a least-privilege `marketops_app` role.
- The M2-03 acceptance is functional and narrow: a user approves, scopes,
  rejects, or revokes knowledge, and project B can cite approved knowledge from
  project A.

Decisions for this package:

- Candidate facts and M2-02 evidence remain immutable. M2-03 adds an append-only
  promotion/decision ledger; it does not update `knowledge_items` or pretend that
  the candidate's version-one content changed.
- A decision is actor attributed and concurrency checked. Approval/elevation is
  explicit, never inferred from candidate type, source project, or a model result.
- Approval initially creates only a `project`-scoped effective version. A separate
  explicit elevation can broaden it to `client`; project B may then use it only
  when it belongs to the same server-derived client. `workspace` elevation is
  deferred: the current single-actor runtime has no role/consent model proving
  that one customer may receive another customer's knowledge.
- A revised approval stores a new, bounded approved rendering and hash in a new
  immutable promotion version, with the candidate and its evidence retained as
  provenance. Rejection and revocation do not erase history.
- Project B creates an explicit, immutable citation snapshot that binds the
  approved promotion version, source project A, target project B, and a
  user-supplied bounded reason. It is historical provenance, not an independent
  authorization grant: effective citation reads re-check the source promotion
  head, so a revoked or out-of-scope promotion cannot be listed or cited.
- No automatic elevation, automatic cross-project reuse, raw artifact/chunk
  retrieval, model decision, or production usefulness claim is in scope.

Unknowns:

- There is no established role model beyond the deployment actor. This slice can
  prove actor attribution and tenant isolation, not organizational authorization
  policy such as approver delegation.
- Synthetic data can prove that approved knowledge is selectable and cited, not
  that it improves planning quality, demand, ROI, or repeat use. M2-04 owns the
  usefulness evaluation gate.

## Work-package contract

- Task and baseline: `M2-03` from the baseline above, after the M2-02 prerequisite
  is present in the same worktree.
- Primary integrator paths: this plan; migration `0009`; `marketops_learning`;
  learning HTTP/OpenAPI integration; M2-03 tests and gates; backup compatibility;
  `project-status.json` and generated status page.
- Forbidden paths: customer data, credentials, real source artifacts, connector
  code, model-provider adapters, top-level CI unless a verified gate requires it,
  and unrelated M0/M1 behavior. Do not rewrite M2-02 immutable candidate facts.
- Frozen input: authenticated server-derived organization/workspace/client/actor
  scope, URL project identities, existing immutable candidate/evidence records,
  and a closed decision body containing only action, expected decision version,
  explicitly selected effective scope, bounded revised content when applicable,
  and bounded reason. Client bodies never provide source tenant IDs, source
  project IDs, candidate status, evidence hashes, or target-scope facts.
- Frozen output: immutable decision/promotion history, an effective approved view
  only where scope permits, an append-only target-project citation, and redacted
  audit events. Replaying an identical decision request is safe; stale expected
  versions fail without partial persistence.
- Failure contract: bad or cross-scope identities, direct candidate mutation,
  invalid state transition, scope broadening without explicit action, missing or
  malformed revision/reason, stale version, rejected/revoked/not-approved
  knowledge, a target project outside the effective scope, duplicate citation,
  persistence failure, or cancellation must fail closed and publish no partial
  decision/citation/audit record.
- Reviewer role: a non-implementer must inspect the state machine, append-only
  history, promotion/citation provenance, RLS and app-role bypass attempts,
  revocation behavior, OpenAPI body closure, backup/restore, and the M2-04
  usefulness-boundary handoff.

## Minimal state and workflow

```text
candidate
  -> approve (project scope only) -> approved version is source-project only
  -> reject -> rejected terminal state
approved
  -> revise -> next approved version at the same scope
  -> explicit client elevation -> next approved version eligible to list in B
  -> revoke -> revoked terminal state, no longer listable/citable

target project B
  -> list only effective approved versions permitted by B's server-derived scope
  -> user explicitly cites one version with a bounded reason
  -> append citation + redacted audit event
```

An approval decision must bind the exact source `knowledge_id` and candidate
content/evidence digest. It must not make the source project capsule, outcomes,
retrospectives, artifact text, or source chunks readable from project B.

## Ordered implementation checklist

1. Add focused domain types and unit tests for the state machine, content/reason
   bounds, canonical decision identities, expected-version conflict, explicit
   scope rules, and citation eligibility.
2. Add migration `0009` for immutable promotion roots, promotion versions, and
   target-project citation snapshots. Keep `knowledge_items` and
   `knowledge_item_versions` unchanged. Use composite tenant foreign keys, source
   and target project constraints, forced RLS, append-only triggers, indexes, and
   explicit `marketops_app` grants. Add deferred integrity checks where a simple
   foreign key cannot prove the effective decision state.
3. Implement scoped PostgreSQL reads/writes that lock the decision root, derive
   eligibility from the latest immutable decision, atomically append decisions or
   citations with redacted audit events, and normalize persistence failures.
4. Add closed authenticated HTTP routes and OpenAPI schemas for source-project
   decision/history reads and target-project eligible-list/citation operations.
   Keep cross-project discovery server derived; do not accept source scope facts
   in the request body.
5. Add static OpenAPI mutation checks plus unit, HTTP, adapter, and real PostgreSQL
   tests for cross-client/workspace decoys, rejected and revoked candidates,
   project-only approval, same-client elevation, deferred workspace elevation,
   stale decision version, direct
   app-role writes, audit rollback, and target citation replay.
6. Extend the backup manifest and clean-container restore gate through migration
   `0009`, including non-empty decision and citation rows and legacy v8 handling.
7. Run focused and full regressions, PostgreSQL/RLS and recovery gates, docs,
   progress, OpenAPI, compile, secret, and diff checks. Obtain an independent
   reviewer before recording completion evidence.

## Acceptance commands

The implementation must at minimum pass the M2-03 unit, HTTP, adapter, OpenAPI,
real PostgreSQL, and backup/restore gates added by this package, followed by:

```powershell
python -m unittest discover -s apps/api/tests -p test_*.py
node scripts/progress.mjs check
node scripts/check-docs.mjs
git diff --check
```

The real PostgreSQL evidence must include the paired synthetic experiment: an
otherwise identical project-B query returns no historical knowledge before an
eligible explicit approval, then returns a cited approved rendering only after
approval and never after revocation. This demonstrates authorization behavior,
not usefulness.

## Completion Evidence

- A fresh PostgreSQL 18.4 M2-03 gate passed approval, explicit client
  elevation, citation, revoke/revise fail-closed reads, audit rollback, replay,
  concurrent revoke/cite, and direct actor/client/workspace/target-actor
  write-rejection tests.
- A fresh PostgreSQL 18.4 v9 backup/restore gate restored non-empty promotion
  and citation rows by row-set hash, retained forced RLS and least privilege,
  and confirmed fresh approval/citation reads. The backup validator also has a
  focused v8 manifest compatibility regression test.
- A DeepSeek Harness read-only independent review and incremental recheck found
  no P0/P1. Remaining observations are non-blocking: effective-citation reads
  are N+1 and one promotion-table unique constraint is redundant.
