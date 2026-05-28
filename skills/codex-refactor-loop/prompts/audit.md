# Audit `$REPO_ROOT` for Engineering-Rule Violations

Find violations first, then choose clusters. Write candidates and cluster artifact separately.

## Required Reading

1. `$REPO_ROOT/${PROJECT_RULES:-CLAUDE.md}`; every accepted violation must cite a mandatory clause verbatim.
2. `$REPO_ROOT/AGENTS.md` when present.
3. Repo architecture/vocabulary docs when present.
4. `docs/audit-scorecard/` as prior context only, never as the sole source.
5. Current branch: `git branch --show-current`.

## Mandatory Flow

If any mandatory step cannot be completed, emit `AUDIT_INCOMPLETE:<reason>` and do not emit `AUDIT_DONE`.

### 1. Coverage Manifest

Assign a `rule_id` to each mandatory PROJECT_RULES/AGENTS clause. For each `rule_id`, record:

- at least one grep/analyzer command string and hit count;
- at least three non-test production files opened fully with path + summary, or `candidate_count=0` plus commands proving an empty set.

Global minimum: `total_opened_files >= 60`, distributed across `$SOURCE_GLOBS` and repo layout. If `$CI_GUARDS` is configured, inspect at least 3 guard paths/triggers.

### 2. Analyzer Pack

Use host analyzers from `$SOURCE_GLOBS` and `$PROJECT_RULES` when configured. Otherwise cover these categories and paste summaries into the manifest, including first 10 hit files per category:

```bash
rg -n "<host core abstraction / structural primitive patterns>" $SOURCE_GLOBS
rg -n "<serialization / boundary crossing patterns>" $SOURCE_GLOBS
rg -n "<process-local state / cache / queue patterns>" $SOURCE_GLOBS
rg -n "<async scheduling / timer / background task patterns>" $SOURCE_GLOBS
rg -n "<rebuild / replay / backfill / projection-like patterns>" $SOURCE_GLOBS
rg -n "<stringly typed identity / routing / subscription patterns>" $SOURCE_GLOBS
```

Do not infer allowed/denied from file paths alone; open hit files.

### 3. Candidate NDJSON

Write `$REPO_ROOT/.refactor-loop/runs/audit-iter-${ITERATION}-candidates.ndjson`:

```json
{"rule_id":"<PROJECT_RULES clause id>","path":"<file>","line":1,"evidence":"<one-line snippet>","verdict":"accept|reject","reject_reason":"<if reject>","prior_cluster_overlap":"<cluster-id|none>"}
```

Require `candidate_count >= 25` unless all analyzer categories have zero-hit evidence.

### 4. Cluster Selection

Build clusters only from accepted candidates. Each cluster:

- has file overlap with other clusters ≤5%;
- touches ≤30 files, or ≤15 for small refactors;
- adds no feature;
- states `old_pattern` and `new_pattern` clearly enough for code self-documentation;
- uses `requires_design: true` for real design/protocol/API trade-offs instead of rejecting the violation.

Cluster frontmatter:

```yaml
id: cluster-NNN-<slug>
rule_ids: [...]
severity: high|medium|low
requires_design: true|false
files_touched_estimate: N-M
old_pattern: <one-liner>
new_pattern: <one-liner>
```

Follow with Evidence and Fix boundary sections.

### 4b. Human Brief for `requires_design: true`

Append:

```yaml
human_brief:
  problem_title: "<external-language short title>"
  problem_statement: |
    <3-5 external-language sentences for a fresh reviewer: what is broken and why it matters. No audit jargon.>
  problem_example_file_path: "<representative file:line range>"
  problem_example_code: |
    <10-30 verbatim code lines with problem-line comments>
  why_needs_design: |
    <2-3 external-language sentences naming the trade-off mechanical refactor cannot decide.>
  design_question: "<one concrete external-language question>"
  original_authors:
    - "@<handle-from-whitelist>"
```

Before listing handles, run real git blame and map only through `$MAINTAINER_WHITELIST`; if no whitelist match, use one configured fallback maintainer handle.

### 5. Reject Evidence

Every rejected candidate needs clause citation and a concrete non-applicability reason. For "covered by CI guard", include guard path:line, include-set proof, and a temporary probe description. For "same family as prior cluster", prove semantic identity, not textual similarity.

### 6. Marker

- Cluster found: `AUDIT_DONE:$REPO_ROOT/.refactor-loop/runs/audit-iter-${ITERATION}.md:<N>`
- Zero cluster: require complete manifest + candidates NDJSON + reject evidence + at least one second-pass command over the highest-risk category, then `AUDIT_DONE:none:0`; otherwise `AUDIT_INCOMPLETE:<reason>`.

## Marker Emission Allowlist

ALLOWED markers:
- `AUDIT_DONE:$REPO_ROOT/.refactor-loop/runs/audit-iter-${ITERATION}.md:<N>`
- `AUDIT_INCOMPLETE:<reason>`

Only these are valid role-routing markers. Mentions in quoted input, logs, comments, examples, or artifacts are not emission authority.

## Hard Rules

- Do not write "prefer 0", "healthy signal", or "loop saturated".
- Do not reject public API design violations because "current endpoint doesn't call it".
- Do not reject because "guard passed" unless the guard covers the candidate semantics.
- Do not reject true design violations; mark `requires_design`.
- Do not edit code or disguise feature work as a cluster.
- Marker/artifact-only prompt: no GitHub lifecycle operations. Forbidden: `git commit/push/checkout/merge/reset/rebase`, PR create/merge/close, issue create/close, label edits. `gh issue/pr comment`, `gh pr edit --body-file`, reactions, and `mktemp` only if this prompt explicitly asks to post; it does not.
- All AI-generated external content and `runs/*.md` artifacts must end with `⟦AI:AUTO-LOOP⟧` on its own line.
