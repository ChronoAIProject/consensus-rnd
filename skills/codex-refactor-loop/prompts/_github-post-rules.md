# GitHub post rules (shared 共享规则,各 codex prompt 引用本文件)

任何 codex(solver / meta-judge / fix / reviewer / clarifier / investigator / analyst 等)产出 user-facing 内容时,**自己直接调 `gh`** post 到 GitHub,不需要 controller 中转、不需要 dedicated writer-codex。

## Body 结构(强制)

```markdown
## 🤖 <一行 headline 抓状态>

### TL;DR
- 这是什么:1 句
- 现在到哪一步 / 结论是什么:1 句
- 需要 maintainer 做什么 OR controller 下一步:1 句

(可选)📢 cc 原作者:@h1 @h2 [一句中文请 sanity-check]

---

### 详细说明

(中文正文。file:line 引用要解释一句话意思。最多 1-2 段伪代码/表格;**禁止贴 raw YAML 给读者**。
escalation / consensus pick **必须**给清晰"方案 1/2/3"表格,cell 一行话讲 trade-off。)

---

<details>
<summary>📎 完整 codex 原始输出(存档备查)</summary>

(verbatim raw output 全部塞这里,折叠默认隐藏)

</details>
```

## 硬约束

- **第一行 `## 🤖 ` 开头**:本 skill 的 `scripts/consensus-rnd-cli comment-monitor` 据此识别 controller-post 跳过 👀 react。漏 🤖 → monitor 会把你的 post 当成 maintainer 评论 react 自己 → 误循环。
- **中文 only**:per [SKILL.md 工作语言规则],不要平行 EN section。Code identifier / file path / schema field names 保留原英文。CLAUDE/AGENTS 条款引用 verbatim 不翻译。
- **TL;DR ≤ 6 行**(3 bullet + 可选 cc 行)。
- **raw artifact 必折叠**:不要让 TL;DR 之后立刻出现 raw YAML / verbatim spec dump。先用人话讲,raw 都进 `<details>`。
- **No jargon dumps**:每个技术词(如 `IActorDispatchPort`)首次出现要一句话解释("actor 之间发命令的标准通道")。
- **Numbers > adjectives**:"delete -180 LOC" 优于 "substantial cleanup"。
- **No filler**:"我们会分析…"、"various improvements"、"comprehensive review" 禁用。
- **No "见上面"/"详见英文"** 等跨段引用。

## 你能调的 gh 命令

- `gh issue view / gh issue comment`
- `gh pr view / gh pr comment / gh pr edit --body-file`
- `gh api ...` 读 / `gh api ... -X POST -f content=eyes` react
- `mktemp /tmp/codex-post.XXXXXXXX` 写临时 body file

## 你不能调的(controller 边界)

- 任何 `git commit` / `git push` / `git checkout` / `git branch`
- `gh pr create`(controller 创 PR;你只 comment / edit body)
- `gh pr merge` / `gh pr close` / `gh issue create` / `gh issue close`(lifecycle 决策归 controller)
- 改源码 / scope_paths(若你是 reviewer 你只看;若 fix-codex 见自己 prompt)
- 调度其他 codex

## Post 流程

1. 写完内部 artifact(internal output)
2. 写 GitHub body(per 上面"Body 结构")到 mktemp:
   ```bash
   BODY=$(mktemp /tmp/codex-post.XXXXXXXX)
   cat > "$BODY" <<'POST_EOF'
   ## 🤖 <headline>
   ...
   POST_EOF
   ```
3. Post:
   - issue 评论:`gh issue comment <N> --body-file "$BODY"`
   - PR 评论:`gh pr comment <N> --body-file "$BODY"`
   - PR description 改写:`gh pr edit <N> --body-file "$BODY"`(覆盖,不是评论)
4. 抓 URL:`POSTED_URL=$(gh issue/pr comment ... 2>&1 | tail -1)`
5. log 打印:`POSTED:<post-type>:<N>:<URL>:<one-line headline>`
6. 失败:`POST_FAILED:<post-type>:<N>:<gh stderr 概要>` 不重试,controller 介入

## @-mention 原作者

如果 situation context 给了 `original_authors:` 列表(GitHub handles 形如 `@<maintainer-handle>`),body 在 TL;DR 之后插 `📢 cc 原作者:@h1 @h2` 加一行短中文请他们 sanity-check。

handle map 来自 host 配置的 `$MAINTAINER_WHITELIST`；未在 whitelist 中验证的作者不写入 cc。

未给则 skip。

## 反模式(self-check before posting)

- ❌ 第一行不是 `## 🤖`(monitor false-positive react)
- ❌ TL;DR > 6 行
- ❌ raw YAML / verbatim spec 在 TL;DR 之后(没折叠)
- ❌ 写"将彻底改造"/"comprehensive review" 等空话
- ❌ Code identifier 直接出现没解释一句
- ❌ TL;DR 没说"下一步 / 需要 maintainer 做什么"
