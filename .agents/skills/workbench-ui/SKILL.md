---
name: workbench-ui
description: Design and implement coherent, implementation-ready AI workbench interfaces from a requirements brief. Use when building or refining dashboards, operator consoles, personal AI workspaces, campaign workbenches, knowledge-base surfaces, or other tool-heavy frontend experiences that need clear hierarchy, states, responsive behavior, and restrained visual quality.
---

# Workbench UI

Use this skill after requirements have been clarified. It turns the workflow into a usable tool surface; it does not invent product scope or replace the existing impeccable and ui-ux-pro-max skills.

## Workflow

### 1. Understand the Product Surface

- Read the requirements brief and inspect the repository, package manager, routes, components, tokens, and existing styles.
- Map the primary loop, the most frequent action, and the important handoff.
- Identify whether the page is a workbench, dashboard, editor, review surface, or settings view.
- Preserve existing user changes and established framework patterns.

### 2. Plan Before Coding

Write a compact UI plan containing:

- Information architecture and navigation.
- Primary action, secondary actions, and approval points.
- Main canvas, context/knowledge area, and assistant or activity area where the workflow needs them.
- Data states: empty, loading, streaming, partial, success, error, rejected, and revision.
- Responsive behavior and stable dimensions for tables, toolbars, cards, counters, and inputs.

For an AI marketing workbench, separate source evidence, AI suggestions, human edits, and approved deliverables visually. Cite sources and show when information was collected.

### 3. Select and Apply Design Guidance

- Use ui-ux-pro-max for product type, style, typography, color, icon, and UX references when available.
- Use impeccable for critique, refinement, responsive checks, and visual hardening when available.
- Use finesse-ui for a deliberate visual direction, anti-generic audits, motion decisions, or detailed AI-console states; keep its larger reference set opt-in rather than loading it for every screen.
- Do not copy a generic AI aesthetic. Prefer a quiet, work-focused interface with clear hierarchy, restrained color, dense but readable information, and purposeful whitespace.
- Use familiar icons from the enabled library for tools and clear text for commands. Add tooltips for unfamiliar icons.
- Avoid decorative gradients, blobs, nested cards, oversized hero type, and controls that look clickable but do nothing.

### 4. Implement the Thin Slice

- Build one complete user path from real input to a useful output before adding secondary modules.
- Use real representative content, not placeholder lorem ipsum, to test wrapping and hierarchy.
- Keep the primary action visible and make review, approve, revise, export, and undo states explicit.
- Do not introduce a new abstraction or UI library unless it removes real duplication or matches the codebase.

### 5. Verify and Refine

- Run the repository's existing checks and start the local development server when the app needs one.
- Verify the main flow at desktop and mobile widths with fresh DOM state and screenshots.
- Check text fit, keyboard focus, contrast, disabled/loading/error states, and overlap.
- Fix layout and interaction defects before adding visual polish.

## AI Workbench Defaults

Use these as starting heuristics, not a rigid template:

- Navigation: keep workspaces, projects, knowledge, experiments, and settings discoverable.
- Main canvas: prioritize the current artifact or decision, not a conversation transcript.
- Context: expose relevant sources, brand rules, assumptions, and memory with clear edit/delete controls.
- Assistant: make suggestions inspectable and actionable; never hide the difference between generated and approved content.
- Activity: show progress, tool calls, citations, revisions, and failures without overwhelming the user.

## Required Handoff

Before implementation, provide the UI plan and name any assumptions. After implementation, report the verified viewport states, remaining risks, and any dependency on unavailable assets or APIs.
