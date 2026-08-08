# M0 Review Gate

Status: `Conditional technical Go to M1`

Review date: `2026-08-08`

## Decision Scope

This gate decides whether the repository may begin the narrow M1 vertical slice: retain an approved proposal, present cited candidate deliverables for review, and produce an editable deterministic schedule. It does not decide that the product has demand, produces ROI, saves time, is ready for production, or deserves a commercial Go decision.

The product boundary remains an independent, open-source marketing project workbench. It is not a generic agent, a Feishu shell, or an automatic project operator. The M1 core must work without connectors or an agent framework.

## Evidence Reviewed

| Area | Confirmed technical evidence | What it does not establish |
| --- | --- | --- |
| Validation-set contract | `node scripts/check-validation-set.mjs public` verifies a public set containing two source-cited public reconstructions and one executable synthetic B2B event fixture. Private historical material stays ignored and requires explicit permission and de-identification records. | Public reconstructions and synthetic fixtures do not prove user demand, real workflow duration, retention, ROI, or willingness to pay. The optional full validation set has not been supplied. |
| M0-02 document parsing | Four controlled inputs passed for Markdown, CSV, and a basic DOCX. The parser retains source hashes and coordinates; three failure paths fail explicitly. DOCX component audit passed 20 structural categories. | No PDF, OCR, scanned file, XLSX, PPTX, complex DOCX layout, visual page coordinate, or rendering quality claim is supported. DOCX visual rendering was not run because neither LibreOffice nor Word export worked in this environment. |
| M0-03 deterministic schedule | Four scenarios reproduce defined working-day, finish-to-start, explicit-buffer, locked-date, critical-path, and hard-deadline behavior. Three malformed schedules are rejected; conflicts are reported rather than silently moving locked dates. | It does not validate extracted work completeness, real-world calendar practices, resource balancing, alternate dependency types, hour-level planning, probabilistic estimates, or cost optimization. |
| M0-04 retrieval and citations | Sixteen frozen synthetic oracle cases, including three Chinese cases, passed. Scope filtering occurs before scoring; workspace/client decoys are excluded; stale or malformed citations fail closed. | This is not an embedding benchmark or evidence of Chinese semantic retrieval, production authorization, model answer quality, long-document behavior, deletion propagation, or cross-project knowledge usefulness. |
| M0-05 dependencies and licenses | Fifteen frozen candidates passed audit. PostgreSQL `18.4`, pgvector `0.8.6`, and Procrastinate `3.9.0` are adopted with stated boundaries; six candidates are deferred and six rejected. | License and maintenance review cannot prove integration fitness, Docker behavior, parser fidelity, embedding quality, security over time, or commercial value. |

## Technical Decision

**Conditional technical Go to M1.** The M0 checks support building the M1 proposal-to-schedule vertical slice because its supported input boundary and deterministic scheduling semantics are explicit, observable, and have reproducible failure behavior.

This is not a commercial Go. Market and commercial decisions remain blocked on observed live-task evidence: real users must complete current tasks, provide measurable comparison data, repeat use, and demonstrate a payment or deposit behavior. The market-validation playbook defines that work; neither network research nor the M0 fixtures count toward it.

## M1 Constraints and Entry Gates

M1 may use only the following rules. A condition below is an implementation gate, not a promise that it already works.

1. Accept only verified Markdown, CSV, and basic DOCX inputs. Preserve source bytes, version hash, parsed coordinates, and explicit unsupported or partial states. PDF, OCR, scanned material, PPTX, XLSX, and complex DOCX layout must be rejected or marked `needs_review`; they must never be silently treated as fully parsed.
2. Proposed deliverables, milestones, assumptions, and constraints remain candidates until a user approves them. Each retained item must keep a source citation or be marked as user-entered or unknown.
3. Schedule dates must be calculated by the deterministic engine, not written directly by an LLM. M1 supports only finish-to-start dependencies, explicit workday buffers, an explicitly configured calendar, locked-date conflict reporting, and hard-deadline reporting.
4. M1 must not present resource balancing, Start-to-Start or Finish-to-Finish dependencies, automatic Chinese holiday updates, hour-level planning, cost optimization, or probabilistic risk simulation as supported.
5. The canonical project state, permissions, audit, and business job state remain application-owned. A queue adapter may be introduced only after PostgreSQL migration, crash recovery, idempotency, retry cap, cancellation, orphan-lock, and sensitive-log checks pass.
6. PostgreSQL is the M1 data boundary. pgvector is not required for M1 retrieval behavior. Any later vector search must authorize the candidate set before distance ranking, retain a lexical-only fallback, and satisfy the M2 model-license and real-language evaluation gates.
7. Connectors, external monitoring, and automatic external actions are out of M1. Feishu stays an optional later connector, not a prerequisite or substitute for the independent core.
8. Do not promote raw project material to reusable knowledge in M1. Cross-project learning remains a later M2 workflow requiring explicit human approval, scope, citations, and isolation tests.

## Required M1 Evidence

Before M1 can close, record reproducible evidence for one permitted validation project:

| Requirement | Required evidence |
| --- | --- |
| Import and retention | Original approved-proposal version is preserved; a failed import is observable and retryable. |
| Reviewable extraction | Candidate deliverables, milestones, assumptions, and constraints have citations and user accept/edit/reject records. |
| Editable schedule | WBS, dependencies, dates, and buffers can be edited; recalculation is deterministic and locked conflicts remain visible. |
| Execution and recovery | Status, blocker, actual dates, export, audit, partial failure, retry, cancellation, and recovery behavior are covered by automated checks. |
| Isolation and secrets | Workspace/client authorization tests, no raw customer material in Git, and no credentials in browser bundles, logs, or exports. |
| Operational baseline | Pinned PostgreSQL deployment, migration, backup/restore, and queue-adapter integration evidence before relying on background jobs. |
| Human validation | A de-identified live-task pilot follows the market-validation playbook. Its result is reported separately and cannot be inferred from engineering fixtures. |

## Reproducible Checks Reviewed

Run from the repository root:

```powershell
node --check app.js
node scripts/check-docs.mjs
node scripts/check-validation-set.mjs public
python scripts/check_document_parser.py --check
python scripts/audit_docx_components.py validation/fixtures/document-parser-spike-001/ai-event-brief.docx --check validation/results/m0-02-docx-components.json
python scripts/check_schedule_spike.py --check
python scripts/check_hybrid_retrieval.py --check
node scripts/check_dependency_decision.mjs
node scripts/progress.mjs check
```

The `quality.yml` workflow runs this same M0 technical suite. It checks repository consistency and fixture behavior; it does not validate commercial claims.

## Open Risks and Blockers

No current M0 technical blocker prevents the narrow M1 slice. The following are explicitly deferred and must not be re-labeled as resolved:

- Visual DOCX render QA requires a working Word or LibreOffice renderer.
- Real Chinese document parsing, OCR, semantic retrieval, embedding choice, and vector-index operations require separate authorized evaluation before M2 use.
- Resource-aware scheduling requires measured real project needs and a separate solver evaluation.
- PostgreSQL/Procrastinate integration, migration, backup/restore, crashes, and operational recovery are M1 implementation risks, not validated M0 outcomes.
- Product demand, quality relative to current tools, time saved, repeat use, alert usefulness, packaging, and payment willingness remain unverified commercial hypotheses.

## Independent Review Requirement

The report author may not approve this gate alone. A reviewer who did not implement the M0 spike must inspect the current tree, rerun the commands above, check that the decision text does not convert technical fixtures into market evidence, and record either `approved` or a blocking finding below before `M0-06` is marked complete.

| Reviewer | Review method | Status | Finding / approval |
| --- | --- | --- | --- |
| Independent reviewer (`m0_exit_review`) | Fresh-tree evidence and limitation review; all M0 commands rerun with exit code 0 | Approved | Conditional technical Go to M1. No P0/P1 technical finding. Commercial Go remains unverified. |
