# Contributing

This repository publishes portable agent skills. Its human contributor entry point is this file; agent-facing operating rules stay in `CLAUDE.md`, and project purpose stays in `README.md`.

Keep contributions narrow. The durable product is the shared `skills/` tree plus the platform manifests that point to it, not an application runtime.

Stable section anchors for future tooling: `#development-flow`, `#issues`, `#commits`, `#pull-requests`, `#skill-changes`, `#style-and-format`, `#ai-generated-content`, `#policy-boundaries`.

## Development Flow

Start by reading `README.md` for repository intent and `CLAUDE.md` for agent work rules. For the current legacy skill, also read `skills/codex-refactor-loop/SKILL.md` before changing its behavior.

Use small work units. A typical change should:

1. Identify the skill, manifest, or documentation surface being changed.
2. Keep edits inside that surface unless the issue explicitly authorizes more.
3. Add or update regression coverage for stable contracts.
4. Run the relevant local checks before handing off.

Version changes are special. When a version moves, `.version-bump.json` defines every manifest that must be synchronized.

## Issues

Issues should name the user-visible problem, the affected skill or manifest, and the contract that should stay stable afterward. Prefer concrete file paths over broad categories.

Existing design context matters:

- #17 records earlier routing and scope constraints.
- #20 records version and release-surface expectations.
- #26 records the merge-policy source of truth.
- #31 is expected to add future CI checks and may grep this document.
- #32 records related follow-up policy boundaries.

Operational labels and markers are part of the current coordination surface. Use `auto-loop-triage` for intake routing, `refactor-design-needed` when design consensus is required, and `phase9-auto-solve` when the Phase 9 solver path is intended.

## Commits

Use concise conventional subjects that identify the surface changed. Preferred prefixes:

- `feat(skill):` for new skill behavior or new published skill content.
- `fix(skill):` for corrections to existing skill behavior or contracts.
- `refactor(skill):` for behavior-preserving skill restructuring.
- `docs(skill):` for documentation-only updates about skills.
- `chore:` for repository maintenance that is not skill behavior.

Keep commit bodies factual. Mention issue numbers and test commands when they explain the change.

## Pull Requests

Pull requests should describe the changed surface, the reason for the change, and the verification performed. Link the issue or design artifact that authorized the scope.

Do not restate reviewer thresholds, branch controls, release commands, or automation internals in a PR description unless the issue is specifically about those policies. Refer readers to the owning documents or existing automation instead.

For AI-assisted work, preserve visible provenance in the PR body or implementation artifact when the workflow requires it. The stable marker is `⟦AI:AUTO-LOOP⟧`.

## Skill Changes

Every skill lives in `skills/<kebab-name>/SKILL.md`. The required `frontmatter` is minimal: `name` plus `description`. The description should say `Use when` and describe trigger conditions, not repeat the full workflow.

Follow `superpowers:writing-skills` discipline for skill authoring: observe the baseline failure first, then change the skill, then verify the behavior. Keep heavyweight detail in `REFERENCE.md` when the main skill would become hard to scan.

Use companion directories only when they are useful:

- Put executable helpers in `scripts/`.
- Put reusable prompt templates in `prompts/`.
- Put long explanations or tables in `REFERENCE.md`.

For `skills/codex-refactor-loop/SKILL.md`, preserve the current host-agnostic migration boundary. Do not rewrite legacy refactor-loop logic unless the active issue explicitly authorizes that behavior change.

## Style And Format

Write skill instructions as operational guidance. Prefer direct commands, stable terms, and concrete paths. Avoid policy prose that sounds authoritative but has no testable owner.

Keep Markdown simple:

- Use short headings.
- Use lists for checklists and rosters.
- Use fenced code blocks only for commands or literal examples.
- Keep machine-grep literals stable when another issue depends on them.

Do not add a new style guide file for ordinary formatting. This document is the human-facing contributor guide; `CLAUDE.md` remains the agent-facing rule surface.

## AI Generated Content

AI-generated or AI-assisted changes must make their scope and verification legible to reviewers. When the auto loop is the actor, keep the required marker visible: `⟦AI:AUTO-LOOP⟧`.

Do not let generated text invent policy owners, release steps, approval rules, or platform requirements. Generated skill text should be checked against the same source-regression tests as hand-written text.

When prompts or scripts produce durable repository content, prefer stable tokens that future automation can grep instead of broad natural-language claims.

## Policy Boundaries

This file is not the owner for every repository policy. It is an index and contributor workflow guide.

Authoritative owners:

- `CLAUDE.md` owns agent work rules.
- `README.md` owns repository purpose and design philosophy.
- `skills/codex-refactor-loop/SKILL.md` owns the current codex-refactor-loop skill behavior.
- `.version-bump.json` owns the synchronized version-file list.
- Existing automation owns its own executable behavior.

This document must not create parallel policy for reviewer thresholds, release mechanics, CI implementation, platform-specific host stacks, or branch controls. When a future issue needs those rules, update the owning surface and add source-regression coverage there.
