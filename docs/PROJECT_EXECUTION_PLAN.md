# Project Execution Plan

Status: `Active implementation, planning and validation`
Canonical progress source: [`project-status.json`](../project-status.json)
Generated progress record: [`PROJECT_STATUS.md`](PROJECT_STATUS.md)
Multi-agent delivery rules: [`MULTI_AGENT_EXECUTION.md`](MULTI_AGENT_EXECUTION.md)
Current dependency decision: [`M0_05_DEPENDENCY_DECISION.md`](M0_05_DEPENDENCY_DECISION.md)

## Objective

For independent owners of activity, brand, and B2B marketing projects, help them turn an approved proposal into an executable, monitored, and reviewable project plan when execution begins, so they spend less time reconciling documents and status while avoiding missed dependencies and untraceable changes.

This is an independent open-source Web workbench. It is not a generic chat Agent, a Feishu wrapper, or an automated replacement for a marketing owner. Agents are constrained internal execution capabilities; Skills are reusable procedures; Feishu, WeCom, email, calendars, and sources are optional connectors.

## What Is Already Decided

Confirmed decisions are in the [product specification](PRODUCT_SPEC.md): single-user first; B2B, brand, exhibition, and client-event projects as the first template; Docker private deployment; BYOK; Feishu as an optional later connector; controlled knowledge reuse as a core capability.

The following remain hypotheses, not completed validation:

- The strongest paid value is the proposal-to-execution workflow and its traceability, rather than AI proposal generation alone.
- Agencies are the primary early validation group; B2B marketing teams are the comparison group.
- Payment willingness, ideal packaging, alert frequency, and the best first paid surface require observed behavior, not interviews alone.

## End-to-End Product Flow

```text
Approved proposal -> cited deliverables and assumptions -> reviewed WBS / schedule
-> execution updates and risks -> market signal impact review -> weekly report
-> retrospective -> candidate knowledge -> human approval -> cited reuse
```

| Stage | System responsibility | Human-controlled decision | Evidence retained |
| --- | --- | --- | --- |
| Import | Preserve the original material and derive structured candidates | Choose scope and approved version | File version and source coordinates |
| Plan | Propose WBS and calculate dependencies deterministically | Edit and approve plan | Constraints, assumptions, plan version |
| Execute | Show task state, blocker, actual duration, and risks | Update real-world status | Audit record and attachments |
| Monitor | Ingest approved sources and propose impact links | Decide whether to change work | Source, time, confidence, impact graph |
| Learn | Produce candidate knowledge from outcomes | Approve scope, revise, reject, or revoke | Capsule, feedback, approval history |

The [data model](DATA_MODEL.md), [architecture](ARCHITECTURE.md), and [knowledge design](KNOWLEDGE_AND_LEARNING.md) are the detailed contracts. Facts with sources, model hypotheses, human decisions, and measured outcomes must stay distinct.

## Scope and Milestone Gates

| Milestone | Primary deliverable | Gate to continue |
| --- | --- | --- |
| Foundation | Product contract, architecture, data model, acceptance criteria, OSS baseline | Documentation is consistent and reviewable. Complete. |
| M0 | Two source-cited AI-era public reconstructions, one executable synthetic fixture, plus parsing, scheduling, retrieval, and license spikes | Technical risks are evidenced and dependencies have no unresolved license blocker; no demand claim is made. |
| M1 | Approved proposal to editable WBS, schedule, task state, export, and audit | One validation project reaches an accepted executable schedule. |
| M2 | Cited, isolated retrieval and human-approved project learning | Project B reuses approved Project A knowledge without leakage. |
| M3 | Source-managed market signals and human-approved impact changes | A real case produces a useful and explainable action at an agreed noise level. |
| M4 | Reporting, optional Feishu connector, team roles, deployment operations | A small team completes a project cycle without continual developer support. |
| M5 | Repeat-use and payment experiment, then packaging decision | Actual reuse and payment/deposit evidence determines expansion. |

Detailed task ownership, dependencies, statuses, acceptance checks, evidence, and self-checks live in [`project-status.json`](../project-status.json). The generated [status record](PROJECT_STATUS.md) is the review-friendly view.

## Workbench Expansion Track

The original M2/M3 dependency chain remains the engineering evidence track. The user-approved workbench expansion is registered separately as `WB0`, so the failed/blocked M2-04 usefulness evaluation does not block delivery of a usable private-deployment console. `WB-04` is the current expansion task in progress.

```text
WB-01 model profiles / task matching
-> WB-02 Brief / research / proposal draft
-> WB-03 keyword / GEO snapshot
-> WB-04 content / image assets / 7-30 day schedule
```

This track does not change M2-04 status and does not turn synthetic engineering evidence into ROI, production-readiness, or market-validation evidence. WB-01 through WB-03 are complete; WB-04 is the current implementation task. Its content Brief, asset, image-task, calendar, and read-only Obsidian note-index records now use authenticated server persistence with an explicitly labelled offline-draft fallback. Real model execution, note-body citation/write-back, external publishing, and project aggregation remain outside this slice.

## P0 Build Sequence

1. Prepare the validation projects and settle the risky technical choices in M0.
2. Build one thin vertical slice: approved proposal import through editable WBS and deterministic schedule.
3. Add manual execution updates, audit trail, error recovery, and exports.
4. Add citations, workspace-scoped retrieval, and human-approved knowledge promotion.
5. Add market signals only after project state and impact relations are reliable.
6. Add connector and multi-user capabilities after the standalone core works.

P0 excludes generic companion chat, automatic external messages, personal WeChat access, full SEO/media/CRM replacement, automatic ROI claims, universal web monitoring, and automatic cross-project memory. See [PRODUCT_SPEC.md](PRODUCT_SPEC.md) for the full non-goal list.

## Validation Design

M0 technical testing uses public reconstructions and synthetic data because a solo builder may not have complete historical archives. The first market test remains a current real task: participants run a de-identified live Brief or project step through a prototype and compare it with their current documents, spreadsheets, chat, and generic AI workflow.

| Question | Evidence | Go / no-go signal |
| --- | --- | --- |
| Does plan generation solve a recurring, material workflow cost? | Timed task, retained or edited WBS, observed omissions | Participants retain most usable tasks and ask to reuse it in live work. |
| Does traceability beat a generic Agent + Skill setup? | Comparison with current tool stack, source-to-task inspection | Users value updates, impact visibility, audit, and reuse enough to continue rather than returning to their stack. |
| Are alerts useful rather than distracting? | Source logs, action rate, false-positive review | Alerts create explainable human actions at the agreed threshold. |
| Is there willingness to pay? | Repeat use plus payment/deposit experiment | Actual payment or deposit, not verbal intent or page traffic. |

The detailed interview, prototype, and evidence process remains in [market-validation-playbook.md](market-validation-playbook.md). Network examples and desk research can supply technical scenarios; they cannot establish time savings, repeat use, willingness to pay, or causality.

## Safety and Data Boundaries

- Workspace and client isolation are mandatory.
- Do not automatically elevate raw files or chat into reusable knowledge.
- Keep original source, parser output, model proposal, human edit, approval, and outcome separately versioned.
- Do not make automatic schedule, proposal, budget, campaign, or external communication changes.
- Private deployment and BYOK should keep credentials out of browser code, logs, and Git.
- Reject or quarantine sources whose access terms, provenance, or personal-data status are unclear.

## Progress Controls

`project-status.json` is the canonical register. Every completed task must include a completion date, evidence, and a passed self-check. `scripts/progress.mjs` validates these conditions and generates `PROJECT_STATUS.md`; CI fails if either the registry is invalid or the generated status page is stale.

This mechanism provides accountability, not an autonomous truth detector. A script can verify that evidence exists and the process was followed; it cannot independently establish that a user need is real or that a model output is correct. Those claims require the milestone acceptance evidence above.

## Four-Quadrant Work-Package Gate

Every new work package and every retrospective review uses the following gate before implementation or a completion claim. This is an evidence discipline, not a requirement to ask the user questions on every task.

| Quadrant | Required record | Execution rule |
| --- | --- | --- |
| Shared known | Goal, relevant project background, acceptance command or observed outcome, owned and forbidden paths, and the claim boundary | Proceed without repeating questions when these inputs are sufficient. |
| User known / agent unknown | Material context, preference, data, or operational constraint that only the user can supply | Ask no more than three questions only when the missing answer would materially change the result; otherwise state the assumption and produce an exploratory version. |
| User unknown / agent known | Counterexamples, engineering or market risks, evidence limits, and viable alternatives with trade-offs | Surface these before locking the approach. Do not continue from a false premise merely because it appeared in the request. |
| Shared unknown | A falsifiable hypothesis, one primary changed variable, success and failure signals, and the next data to retain | Run or schedule the smallest relevant experiment. A document, synthetic fixture, public case, or model output cannot stand in for live behavior when the claim concerns demand, ROI, repeat use, or payment. |

Each work-package handoff must distinguish confirmed facts, reasonable inferences, decisions, and unknowns. An unknown may stay open when it is outside the current acceptance boundary, but it must not be silently converted into a fact or completion claim.

### Retrospective Recheck of Existing Progress

| Completed or active scope | Confirmed by current evidence | Not established by that evidence | Required next validation |
| --- | --- | --- | --- |
| Foundation, completed | Product boundary, architecture, data contracts, safety rules, acceptance criteria, and open-source policy are documented and mechanically consistent. | Product demand, usability, implementation feasibility, time savings, or willingness to pay. | Validate each later milestone against executable behavior and live-user evidence rather than reopening settled documentation by default. |
| M0, completed | Public reconstructions, synthetic fixtures, deterministic spikes, dependency review, and an independent technical review passed their recorded engineering checks. | Real customer demand, causal ROI, repeat use, payment, production reliability, or permission to reuse customer data. | Preserve M0 as technical risk evidence; use a de-identified live task and observed behavior for market claims. |
| M1-01, completed | The asynchronous HTTP import path, hashed Linux runtime, PostgreSQL 18.4 migration, role/grant attestation, forced-RLS isolation, concurrent idempotency, normal same-container restart recovery, uncommitted connection-loss rollback, isolated application-level logical backup/restore, dry-run-first orphan cleanup, and real Chromium server-source recovery passed their bounded checks. Commit `cd3ff09` and GitHub Actions run `31397741577` passed final non-implementer review with no P0/P1/P2 findings. | Power-loss durability, durable host volumes, WAL/PITR, production recovery or RPO/RTO, Windows/network-filesystem or multi-node cleanup, crash-recoverable quarantine, cross-browser behavior, production authentication/reliability, demand, ROI, time savings, repeat use, or payment. | Preserve these engineering boundaries and begin M1-02 deliverable extraction with source citations and explicit human review. |
| M1-02 through M1-05, completed | Authorized derived proposal inputs completed cited review, WBS generation, deterministic scheduling, exact-version plan approval, execution state, audit, and CSV/XLSX export. The latest accepted plans contain 31 and 11 tasks; both schedules are `ready`, approvals target their exact snapshots, and the predecessor-integrity query found zero missing references. | Semantic completeness of the original source proposals, universal calendar correctness, production reliability, demand, ROI, time savings, repeat use, or payment. | Start M2 only after preserving the human-approval and workspace-isolation boundaries; separately run a de-identified live task for value evidence. |

### M1 review gate and M2 kickoff

M1-05 is complete for the bounded engineering gate recorded in [M1 Review Gate](M1_REVIEW_GATE.md). The end-to-end path uses authorized derived inputs, preserves citations and human decisions, excludes rejected candidates from WBS, rejects structurally invalid dependencies, and requires exact-version approval before execution. It must not be read as proof of semantic completeness, production readiness, demand, ROI, repeat use, or payment.

M2 is the next pending milestone. Its first implementation slice must preserve workspace/client/project isolation and human approval for any knowledge promotion; it must not expand into automatic cross-project memory, generic chat, or external monitoring.

This recheck does not change completed task statuses: no recorded acceptance evidence was disproved. It narrows what those statuses are allowed to mean and makes the remaining unknowns testable.

## Current Risks

| Risk | Current response | Decision trigger |
| --- | --- | --- |
| Scope drifts into a general AI assistant | Keep the approved-proposal-to-project-state loop as the sole P0 spine | Any feature that cannot strengthen the loop is deferred. |
| Generic models erode value | Validate persistent project state, deterministic scheduling, traceability, and controlled reuse | Users return to Agent + Skill workflows after prototype use. |
| Customer data is incomplete or cannot be used safely | Use cited public reconstructions and synthetic fixtures for engineering; reserve real data for consented live pilots | A live pilot lacks approved data handling. |
| Retrieval introduces leakage or confident errors | Require citations, isolation tests, and human knowledge approval | Any cross-customer result or uncited high-impact output. |
| Alerts create noise | Begin with user-selected sources and manual impact review | Action rate stays below the agreed threshold. |
