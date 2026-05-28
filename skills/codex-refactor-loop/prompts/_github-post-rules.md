# GitHub Post Rules

Use for any GitHub-facing comment or PR body produced by solver, meta-judge, fix, reviewer, triage, analyst, or similar prompt.

## Body

```markdown
## 🤖 <one-line headline>

### TL;DR
- What this is: <one sentence>
- Current state / conclusion: <one sentence>
- Needed maintainer action OR controller next step: <one sentence>

<optional>📢 cc original authors: @h1 @h2 <short external-language sanity-check request>

---

### Details

<External-language body. Explain file:line references. Use at most 1-2 compact tables or pseudocode blocks. Do not paste raw YAML here.>

---

<details>
<summary>📎 Raw codex output</summary>

<verbatim raw output>

</details>

⟦AI:AUTO-LOOP⟧
```

## Rules

- First line must start with `## 🤖 ` so `scripts/comment-monitor.sh` skips controller/codex posts.
- GitHub-facing prose uses the required external language only. Keep code identifiers, file paths, proto fields, errors, and PROJECT_RULES/AGENTS quotes verbatim.
- TL;DR is ≤6 lines including optional cc.
- Raw artifact/spec/YAML goes only inside `<details>`.
- First mention of a technical identifier gets a one-sentence explanation.
- Numbers > adjectives; no filler such as "comprehensive review" or "various improvements".
- If `original_authors:` is supplied, mention only verified whitelist handles from `$MAINTAINER_WHITELIST`.
- Every GitHub-facing body ends with the sentinel on its own line: `⟦AI:AUTO-LOOP⟧`.

## Allowed Commands

Allowed: `gh issue view`, `gh issue comment`, `gh pr view`, `gh pr comment`, `gh pr edit --body-file`, read/POST reactions via `gh api`, `mktemp`.

Forbidden lifecycle commands: `git commit`, `git push`, `git checkout`, `git branch`, `gh pr create`, `gh pr merge`, `gh pr close`, `gh issue create`, `gh issue close`, label edits, dispatching other codexes.

## Post Flow

1. Write internal artifact.
2. Write GitHub body to `mktemp /tmp/codex-post.XXXXXXXX`.
3. Post with `gh issue comment <N> --body-file "$BODY"`, `gh pr comment <N> --body-file "$BODY"`, or `gh pr edit <N> --body-file "$BODY"`.
4. Print `POSTED:<post-type>:<N>:<URL>:<one-line headline>` or `POST_FAILED:<post-type>:<N>:<gh stderr summary>`.
