# Repository Instructions

## Reasoning Standards

1. Do not agree with a proposal merely to be agreeable. Check for false premises, logical jumps, missing information, and unverified claims before answering or implementing.
2. Clearly distinguish confirmed facts, reasonable inferences, opinions, and unverified information. Verify factual claims, dates, numbers, sources, and examples where practical; state when verification is unavailable.
3. When disagreeing, explain the reason, counterexamples, risks, or a more defensible alternative.
4. Preserve existing user changes. Do not add, expose, or commit customer material, research PDFs, credentials, tokens, local databases, or other sensitive files.

## Progress Protocol

1. Read `project-status.json`, `docs/PROJECT_EXECUTION_PLAN.md`, and the acceptance criteria for the active milestone before substantial work.
2. Use the registry as the single source of task status. In the solo workflow, mark at most one task as `in_progress`.
3. A task may be marked `completed` only after its acceptance check has passed. It must then include `completedAt`, one or more concrete evidence entries, and `selfCheck.status: "passed"`.
4. Run `node scripts/progress.mjs render` after changing the registry, then run `node scripts/progress.mjs check` with the task-specific checks. Do not manually edit `docs/PROJECT_STATUS.md`.
5. Never treat a draft, a model response, a static mockup, or a verbal claim as completion evidence for a product or market-validation task.
6. Mark blocked work as `blocked`; record the specific blocker in its change record or issue. Do not silently skip it.
