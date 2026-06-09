# GitHub post rules (shared rules for codex prompts)

<!-- Legacy contract anchors for source-regression compatibility:
## Body 结构(强制)
第一行 `## 🤖 ` 开头
raw artifact 必折叠
zsh-safe 退出码变量
禁止**用 `status`
可调:
不可调:
gh pr create/merge/close
gh issue create/close
-->

<!--
## 你能调的 gh 命令
- gh issue/pr comment
- gh issue view
- gh issue comment
- gh pr view
- gh pr comment
- gh pr edit --body-file
- gh api .../reactions
- mktemp
## 你不能调的(controller 边界)
- git commit/push/checkout
- git merge/reset/rebase
- git merge
- git reset
- git rebase
- gh pr create/merge/close
- gh pr close
- gh issue create/close
- gh issue edit --add-label
- gh issue edit --remove-label
- gh pr edit --add-label
- gh pr edit --remove-label
-->

Artifact profile: github-ai-post-body

When any codex role (solver, meta-judge, fix, reviewer, clarifier, investigator, analyst, etc.) produces user-facing content, **call `gh` directly yourself** to post it to GitHub. The controller does not relay it, and there is no dedicated writer-codex.

## Body Structure (Required)

```markdown
## 🤖 <一行 headline 抓状态>

### TL;DR
- 这是什么:1 句
- 现在到哪一步 / 结论是什么:1 句
- 需要 maintainer 做什么 OR controller 下一步:1 句

(可选)📢 cc 原作者:@h1 @h2 [一句中文请 sanity-check]

---

### <details heading in `${HOST_WORK_LANGUAGE}`>

(Body text follows `${HOST_WORK_LANGUAGE}`. file:line references need one sentence explaining why they matter. Use at most 1-2 pseudocode/table blocks; **do not paste raw YAML to readers**.
Escalation / consensus picks **must** include a clear option table with one-line trade-offs.)

---

<details>
<summary>📎 完整 codex 原始输出(存档备查)</summary>

(verbatim raw output 全部塞这里,折叠默认隐藏)

</details>
```

## Hard Constraints

- **First line starts with `## 🤖 `**: this skill's `scripts/consensus-rnd-cli comment-monitor` uses it to identify controller posts and skip eyes reactions. Missing it can make the monitor react to its own post as if it were a maintainer comment.
- **Language**: follow `${HOST_WORK_LANGUAGE}` per the SKILL.md work-language rules; do not add a parallel English section. Code identifiers, file paths, schema field names, and CLAUDE/AGENTS verbatim quotes are not translated.
- **TL;DR <= 6 lines** (3 bullets plus optional cc line).
- **Raw artifacts must be folded**: do not put raw YAML or verbatim spec dumps immediately after the TL;DR. Explain first in human-readable prose, then put raw text in `<details>`.
- **GitHub bodies must be self-contained**: any GitHub-facing body that cites authorization, consensus, solver/judge conclusions, escalation, or design/triage judgment must inline the full raw artifact. Local `.refactor-loop/runs/*.md` paths may appear only under `<details><summary>Local debug clues</summary>` and are never the only authority source.
- **No jargon dumps**: explain each technical term on first use, e.g. `IActorDispatchPort` is the standard channel for sending commands between actors.
- **Numbers > adjectives**: prefer "delete -180 LOC" over "substantial cleanup".
- **No filler**: avoid phrases like "we will analyze", "various improvements", or "comprehensive review".
- **No cross-section shortcuts** such as "see above" or "see the English section".
- **zsh-safe exit-code variables**: if shell code stores a `gh` exit code, use safe names such as `post_exit_code` or `gh_exit_code`; **do not** use `status`, which is a zsh read-only special variable.

## Allowed gh Commands

- `gh issue view / gh issue comment`
- `gh pr view / gh pr comment / gh pr edit --body-file`
- `gh api ...` reads / `gh api ... -X POST -f content=eyes` reactions
- `mktemp /tmp/codex-post.XXXXXXXX` to write a temporary body file

## Disallowed Commands (Controller Boundary)

- Any git topology or history mutation: `git commit` / `git push` / `git checkout` / `git branch` / `git merge` / `git reset` / `git rebase`
- `gh pr create` (the controller creates PRs; you only comment or edit bodies)
- `gh pr merge` / `gh pr close` / `gh issue create` / `gh issue close` (lifecycle decisions belong to the controller)
- `gh issue edit --add-label` / `gh issue edit --remove-label` / `gh pr edit --add-label` / `gh pr edit --remove-label` (label decisions belong to the controller)
- Editing source or `scope_paths` when your role is reviewer; fix-codex follows its own prompt
- Dispatching other codex workers

## Post Procedure

1. Finish writing the internal artifact.
2. Write the GitHub body, following the body structure above, to a mktemp file:
   ```bash
   BODY=$(mktemp /tmp/codex-post.XXXXXXXX)
   cat > "$BODY" <<'POST_EOF'
   ## 🤖 <headline>
   ...
   POST_EOF
   ```
3. Post:
   - issue comment: `gh issue comment <N> --body-file "$BODY"`
   - PR comment: `gh pr comment <N> --body-file "$BODY"`
   - PR description rewrite: `gh pr edit <N> --body-file "$BODY"` (overwrite, not comment)
4. Capture the URL and preserve the exit code; do not name the variable `status`:
   ```bash
   POST_OUTPUT=$(gh issue/pr comment ... 2>&1)
   post_exit_code=$?
   POSTED_URL=$(printf '%s\n' "$POST_OUTPUT" | tail -1)
   ```
5. On success, print: `POSTED:<post-type>:<N>:<URL>:<one-line headline>`
6. On failure, print: `POST_FAILED:<post-type>:<N>:<gh stderr summary>`; do not retry, the controller will intervene.

## @-Mention Original Authors

If the situation context provides an `original_authors:` list with GitHub handles such as `@<maintainer-handle>`, insert `📢 cc original authors: @h1 @h2` after the TL;DR with one short request for sanity-checking in `${HOST_WORK_LANGUAGE}`.

The handle map comes from host-configured `$MAINTAINER_WHITELIST`; do not include authors that are not verified in the whitelist.

Skip this when no original authors are provided.

## Anti-Patterns (Self-Check Before Posting)

- Bad: first line is not `## 🤖` (monitor false-positive reaction)
- Bad: TL;DR > 6 lines
- Bad: raw YAML or verbatim spec immediately after TL;DR without folding
- Bad: using a local path such as `authorization:.refactor-loop/runs/phase9-issueN-rM-judge.md` as the only source
- Bad: vague promises such as "will completely transform" or "comprehensive review"
- Bad: code identifiers appear without one-sentence explanation
- Bad: TL;DR does not state the next step or what the maintainer needs to do
