# Repository Instructions

## Reasoning Standards

1. Do not agree with a proposal merely to be agreeable. Check for false premises, logical jumps, missing information, and unverified claims before answering or implementing.
2. Clearly distinguish confirmed facts, reasonable inferences, opinions, and unverified information. Verify factual claims, dates, numbers, sources, and examples where practical; state when verification is unavailable.
3. When disagreeing, explain the reason, counterexamples, risks, or a more defensible alternative.
4. Preserve existing user changes. Do not add, expose, or commit customer material, research PDFs, credentials, tokens, local databases, or other sensitive files.

## Progress Protocol

1. Read `project-status.json`, `docs/PROJECT_EXECUTION_PLAN.md`, and the acceptance criteria for the active milestone before substantial work.
2. Use the registry as the single source of task status. Mark at most one milestone task as `in_progress`; parallel agents work on packages inside that task.
3. A task may be marked `completed` only after its acceptance check has passed. It must then include `completedAt`, one or more concrete evidence entries, and `selfCheck.status: "passed"`.
4. Run `node scripts/progress.mjs render` after changing the registry, then run `node scripts/progress.mjs check` with the task-specific checks. Do not manually edit `docs/PROJECT_STATUS.md`.
5. Never treat a draft, a model response, a static mockup, or a verbal claim as completion evidence for a product or market-validation task.
6. Mark blocked work as `blocked`; record the specific blocker in its change record or issue. Do not silently skip it.

## Multi-Agent Delivery Rules

These rules apply to every agent working in this repository.

## Source of truth

- Read `project-status.json` before starting. Only one milestone task may be `in_progress`.
- The primary integrator alone edits `project-status.json`, `docs/PROJECT_STATUS.md`, the top-level CI workflow, and the final commit.
- A sub-agent may complete only its assigned work package. It must not mark the milestone task complete.

## Work package contract

Before implementation, record the task ID, baseline commit, owned paths, forbidden paths, frozen inputs and outputs, acceptance commands, and reviewer role.

- Do not edit outside owned paths.
- Do not revert or overwrite another agent's changes.
- Stop and report an unexpected dirty file that overlaps the assignment.
- Do not add real customer files, credentials, copied third-party assets, or unreviewed dependencies.

## Evidence and review

- Separate confirmed facts, inference, decisions, and unknowns.
- Public cases and synthetic fixtures can validate engineering behavior, not demand, ROI, time savings, repeat use, or willingness to pay.
- An implementer cannot be the final reviewer of its own work.
- Completion requires reproducible checks, failure-path coverage, explicit limitations, an independent review, and primary-integrator approval.
- Any cross-workspace or cross-client retrieval, credential exposure, unsupported license, stale result, or untraceable high-impact claim blocks completion.

## Handoff

Return changed files, commands and results, confirmed behavior, limitations, unresolved risks, and the recommended review evidence. Do not claim completion beyond the assigned work package.
