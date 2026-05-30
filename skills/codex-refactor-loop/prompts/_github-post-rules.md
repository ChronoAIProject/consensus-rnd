# GitHub post rules

Artifact profile: github-ai-post-body

Any direct-post prompt writes user-facing GitHub content itself with `gh`; controller is not a writer relay.

## Body structure

```markdown
## 🤖 <headline>

### TL;DR
- 这是什么:1 句
- 现在到哪一步 / 结论是什么:1 句
- 需要 maintainer 做什么 OR controller 下一步:1 句

(optional)📢 cc 原作者:@h1 @h2 [一句中文请 sanity-check]

---

### 详细说明

中文正文。file:line 引用解释一句。最多 1-2 段伪代码/表格;raw YAML/spec 不直接贴在正文。

---

<details>
<summary>📎 完整 codex 原始输出(存档备查)</summary>

verbatim raw output

</details>
```

## Hard rules

- First line must start with `## 🤖 `; `comment-monitor` uses it to avoid reacting to controller posts.
- 中文 only; code identifiers, paths, schema fields, errors, and clause quotes may remain original.
- TL;DR ≤ 6 lines.
- Raw artifact must be folded under `<details>`; GitHub body must be self-contained and cannot rely on a local `.refactor-loop/runs/*.md` path as sole authority.
- Explain first use of jargon; numbers > adjectives; no filler like "comprehensive review".
- Final standalone line must be `⟦AI:AUTO-LOOP⟧`.

## Allowed gh commands

- `gh issue view / gh issue comment`
- `gh pr view / gh pr comment / gh pr edit --body-file`
- read `gh api ...` / react `gh api ... -X POST -f content=eyes`
- `mktemp /tmp/codex-post.XXXXXXXX`

## Disallowed lifecycle commands

- `git commit`, `git push`, `git checkout`, `git branch`
- `gh pr create`, `gh pr merge`, `gh pr close`
- `gh issue create`, `gh issue close`
- source edits or scheduling other codexes unless the role prompt explicitly authorizes them.

## Post flow

1. Write internal artifact.
2. Write body to `mktemp`.
3. Post with `gh issue comment`, `gh pr comment`, or `gh pr edit --body-file`.
4. Capture URL.
5. Print `POSTED:<post-type>:<N>:<URL>:<one-line headline>` or `POST_FAILED:<post-type>:<N>:<gh stderr summary>`.

## Mentions

Only include `original_authors:` handles verified through `$MAINTAINER_WHITELIST`.

## Self-check

Reject before posting if first line is not `## 🤖`, TL;DR exceeds 6 lines, raw artifact is not folded, local path is sole authority, jargon is unexplained, or next action is missing.
