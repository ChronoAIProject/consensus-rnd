# GitHub post rules (shared contract used by codex prompts)

Artifact profile: github-ai-post-body

When any codex worker (solver, meta-judge, fix, reviewer, clarifier, investigator, analyst, or similar role) produces user-facing content, **the worker calls `gh` directly** to post to GitHub. Do not route the post through the controller and do not invent a dedicated writer codex.

## Body Structure (Mandatory)

```markdown
## 🤖 <one-line status headline>

### TL;DR
- What this is: one sentence
- Current state or conclusion: one sentence
- What the maintainer should do OR what the controller does next: one sentence

(Optional) 📢 cc original authors: @h1 @h2 [one sentence asking them to sanity-check, following `${HOST_WORK_LANGUAGE}`]

---

### <details heading in `${HOST_WORK_LANGUAGE}`>

(Body text follows `${HOST_WORK_LANGUAGE}`. file:line references need one sentence explaining why they matter. Use at most 1-2 pseudocode/table blocks; **do not paste raw YAML to readers**.
Escalation / consensus picks **must** include a clear option table with one-line trade-offs.)

---

<details>
<summary>📎 Full raw codex output (archival)</summary>

(Put the full verbatim raw output here; keep it collapsed by default.)

</details>
```

## Hard Constraints

- **First line starts with `## 🤖 `**: `scripts/consensus-rnd-cli comment-monitor` uses this prefix to identify controller/worker posts and avoid reacting to them. Missing the prefix can make the monitor treat the worker post as maintainer input.
- **Language**: follow `${HOST_WORK_LANGUAGE}` per the SKILL.md work-language rule; do not add a parallel English or Chinese section. Code identifiers, file paths, schema field names, and CLAUDE/AGENTS verbatim quotes are not translated.
- **TL;DR <= 6 lines**: three bullets plus the optional cc line.
- **Raw artifacts must be collapsed**: do not put raw YAML or a verbatim spec dump immediately after the TL;DR. Explain the result in prose first; put raw material in `<details>`.
- **GitHub bodies must be self-contained**: when a GitHub-facing body references authorization, consensus, solver/judge conclusions, escalation, or design/triage judgment, inline the complete raw artifact. Local `.refactor-loop/runs/*.md` paths may appear only in `<details><summary>Local debug context</summary>` and must never be the sole authority source.
- **No jargon dumps**: explain each technical term on first use, for example `IActorDispatchPort` as "the standard command channel between actors".
- **Numbers > adjectives**: prefer "delete -180 LOC" over "substantial cleanup".
- **No filler**: avoid empty phrases such as "we will analyze", "various improvements", or "comprehensive review".
- **No cross-section deferrals**: do not write "see above", "see the English section", or equivalent references.
- **zsh-safe exit-code variables**: if shell code captures a `gh` exit code, use variable names such as `post_exit_code` or `gh_exit_code`; **do not** use `status`. `status` is a zsh read-only special variable and assignment will fail the worker.
- **Decomposition tracking grammar is helper-owned**: workers may discuss `IssueDecompositionPlan` artifacts, but must not write `<!-- crnd:issue-decomposition-tracking -->`, `IssueDecompositionChild fingerprint:`, or parent tracking blocks in GitHub-facing prose; the checked-in #403 helper is the only writer for that idempotency grammar.

## Allowed `gh` Commands

- `gh issue view` / `gh issue comment`
- `gh pr view` / `gh pr comment` / `gh pr edit --body-file`
- `gh api ...` for reads / `gh api ... -X POST -f content=eyes` for reactions
- `mktemp /tmp/codex-post.XXXXXXXX` to write a temporary body file

## Forbidden Commands (Controller Boundary)

- Any git topology or history mutation: `git commit`, `git push`, `git checkout`, `git branch`, `git merge`, `git reset`, or `git rebase`
- `gh pr create`; the controller creates PRs, while workers only comment or edit bodies when authorized
- `gh pr merge`, `gh pr close`, `gh issue create`, or `gh issue close`; lifecycle decisions belong to the controller
- `gh issue edit --add-label`, `gh issue edit --remove-label`, `gh pr edit --add-label`, or `gh pr edit --remove-label`; label decisions belong to the controller
- Source edits or `scope_paths` changes unless your role prompt explicitly authorizes them
- Dispatching other codex workers

## Post Workflow

1. Finish writing the internal artifact.
2. Write the GitHub body, following the body structure above, to `mktemp`:
   ```bash
   BODY=$(mktemp /tmp/codex-post.XXXXXXXX)
   cat > "$BODY" <<'POST_EOF'
   ## 🤖 <headline>
   ...
   POST_EOF
   ```
3. Post:
   - Issue comment: `gh issue comment <N> --body-file "$BODY"`
   - PR comment: `gh pr comment <N> --body-file "$BODY"`
   - PR description rewrite: `gh pr edit <N> --body-file "$BODY"`; this overwrites the body and is not a comment
4. Capture the URL and preserve the exit code, without using `status` as a variable name:
   ```bash
   POST_OUTPUT=$(gh issue/pr comment ... 2>&1)
   post_exit_code=$?
   POSTED_URL=$(printf '%s\n' "$POST_OUTPUT" | tail -1)
   ```
5. On success, print: `POSTED:<post-type>:<N>:<URL>:<one-line headline>`
6. On failure, print: `POST_FAILED:<post-type>:<N>:<gh stderr summary>` without retrying; the controller intervenes.

## @-Mention Original Authors

If the situation context includes an `original_authors:` list with GitHub handles shaped as `@<maintainer-handle>`, add `📢 cc original authors: @h1 @h2` after the TL;DR plus one short sentence asking for a sanity-check in `${HOST_WORK_LANGUAGE}`.

The handle map comes from the host-configured `$MAINTAINER_WHITELIST`; do not cc authors that are not verified by the whitelist.

If no original authors are provided, skip this section.

## Anti-Patterns (Self-Check Before Posting)

- ❌ First line is not `## 🤖`; this can cause monitor false-positive reactions
- ❌ TL;DR exceeds 6 lines
- ❌ Raw YAML or a verbatim spec appears immediately after the TL;DR instead of being collapsed
- ❌ A local path such as `authority:.refactor-loop/runs/phase9-issueN-rM-judge.md` is used as the sole authority source
- ❌ Empty promises such as "complete overhaul" or "comprehensive review"
- ❌ Code identifiers appear without a one-sentence explanation
- ❌ TL;DR omits the next step or what the maintainer should do
