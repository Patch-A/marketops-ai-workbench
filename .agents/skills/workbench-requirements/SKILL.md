---
name: workbench-requirements
description: Turn ambiguous AI workbench, marketing workflow, and personal productivity requests into scoped requirements, user flows, task plans, and acceptance criteria. Use before designing or coding a new workbench, dashboard, agent workflow, or major feature, especially when the request mixes AI capabilities, knowledge bases, automation, and human review.
---

# Workbench Requirements

Use this skill before UI implementation or broad refactors. Keep the brief short enough to guide implementation, but precise enough to expose unknowns and prevent feature sprawl.

## Workflow

### 1. Inspect Context

- Read the repository tree, existing docs, package manifests, and current UI before proposing structure.
- Preserve user changes and existing conventions.
- If the repository is empty, state that explicitly and create a product brief rather than pretending there is an existing architecture.
- Identify available frontend and workflow skills; reuse them instead of duplicating their rules.

### 2. Define the Job

Write one sentence in this form:

For [primary user], help them [job] when [trigger], so they can [measurable outcome].

Then record:

- Primary user and secondary users.
- Trigger, inputs, decisions, deliverables, and downstream handoff.
- What must remain human-controlled.
- Non-goals for the first release.

Do not treat a model capability, a data source, or a UI element as the user problem.

### 3. Model the Workflow

Describe the smallest end-to-end flow as states:

capture -> clarify -> analyze -> draft -> review -> approve -> deliver -> learn

Replace states that do not apply. For an AI marketing workbench, distinguish:

- Confirmed facts and cited sources.
- Model hypotheses or recommendations.
- Human decisions and edits.
- Outcome data used for later evaluation.

For research, GEO, ROI, or performance claims, specify evidence, timestamp, confidence, and what cannot be inferred.

### 4. Specify the Product Contract

Produce a compact table or checklist covering:

- Inputs and accepted formats.
- Output artifacts and export targets.
- Loading, empty, error, partial, approval, and revision states.
- Data model and memory boundaries.
- Permissions, privacy, deletion, and audit requirements.
- Success metrics and an evaluation set with representative examples.

For personal and work data, use separate workspaces or namespaces. Do not save sensitive memories automatically without a visible review or opt-out path.

### 5. Break Down Implementation

Order tasks by dependency and risk:

1. Data and workflow contract.
2. One thin vertical slice from input to useful output.
3. Human review and failure handling.
4. Persistence, citations, and exports.
5. Additional integrations and automation.
6. Visual polish after the main flow works.

Each task should name its file/module ownership, inputs, output, and acceptance check. Keep one task in progress at a time and avoid adding speculative modules.

## Required Output

When this skill is used, return:

1. A one-sentence job statement.
2. A user/workflow map.
3. MVP scope and explicit non-goals.
4. States, data, and safety boundaries.
5. An ordered implementation checklist.
6. Risks, unknowns, and the smallest validation experiment.

Do not start with visual styling or a generic dashboard. If important information is missing, make a low-risk assumption, label it, and continue.
