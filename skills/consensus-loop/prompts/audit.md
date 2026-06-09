# Task: Audit `$REPO_ROOT` for violations of software-engineering philosophy

<!-- Legacy contract anchors for source-regression compatibility:
不得被机械解释成 `REFERENCE.md` 必须存在
anchor 不可达
单文件 `SKILL.md` + intra-file anchors 本身不是 violation
不越权扩展范围
当前 audit/issue 授权的 violation 或 work-unit scope
-->

<!--
## 渲染身份 fail-closed(强制)
`ITERATION` 为空
立即输出 `AUDIT_INCOMPLETE:missing-iteration`
禁止写入 `$REPO_ROOT/.refactor-loop/runs/audit-iter-.md`
`$REPO_ROOT/.refactor-loop/runs/audit-iter--candidates.ndjson`
audit fallback 同一时刻只能有一个 active `audit-iter-${ITERATION}`
## 强制流程
-->

Artifact profile: marker-only-work-unit

You are an auditor, not a confirmer. First **discover violations**, then select clusters; write the two artifacts separately.

## Read First

1. Top-level architecture constraints in `$REPO_ROOT/${PROJECT_RULES:-CLAUDE.md}`; every violation must map to one mandatory clause quoted verbatim.
2. `$REPO_ROOT/AGENTS.md` collaboration rules, if present, as supporting rules.
3. Authoritative architecture/vocabulary documents under `$REPO_ROOT`, if present.
4. Historical audits under `docs/audit-scorecard/` are starting references only, **not** the sole clue source.
5. Current git branch: `git branch --show-current`.

<!--
Refactor (iter319/issue-319): Old pattern: CLAUDE.md 把『重型参考必须物理拆 REFERENCE.md』当宪法,audit 据此反复把单文件 SKILL.md 判 R02/R03 违规
New principle: 改哲学:单文件 SKILL.md + intra-file anchors 是被认可的 canonical reference surface;衡量标准从『必须物理拆文件』改为『事实源唯一+owner surface 清楚+anchor 可验证』
-->

Do not mechanically interpret PROJECT_RULES `SKILL.md`/`REFERENCE.md` clauses as requiring `REFERENCE.md` to exist. Report R02/R03 only when there is duplicated fact source, overlapping cross-file responsibility, unreachable anchors, hardcoded host facts, or script/prompt details not moved to their owner surface. A single-file `SKILL.md` with intra-file anchors is not itself a violation.

<!-- Refactor (iter205/issue-205):
  Old pattern: dogfood 实测的运维经验(audit 并行撞 iteration 号、新 role prompt 漏注册 marker contract、review verdict grep log tail 误判、daemon 恢复手 kill)只靠 agent 记忆,没落进 skill 合同与机械验证。
  New principle: 把四条经验写回局部合同:SKILL.md 增 #205 反面规则段(audit 同一时刻单 active iteration、新 role prompt 必同步 marker inventory、review verdict 权威源优先 review artifact frontmatter、daemon 恢复只走 restart-daemons);audit.md 渲染后 ITERATION 空则 fail-closed;peek.py 局部优先读 review artifact frontmatter verdict;配套 source-regression + behavior test。不新增跨模块抽象层。
-->

## Render Identity Fail-Closed (Required)

After template rendering, if `ITERATION` is empty, whitespace-only, or still an unreplaced placeholder, immediately output `AUDIT_INCOMPLETE:missing-iteration` and stop. Do not write `$REPO_ROOT/.refactor-loop/runs/audit-iter-.md`, `$REPO_ROOT/.refactor-loop/runs/audit-iter--candidates.ndjson`, an empty-iteration log, or any artifact reusing an empty identity. Audit fallback may have only one active `audit-iter-${ITERATION}` at a time; do not reuse the same iteration output name in parallel.

## Required Flow (any violation -> output `AUDIT_INCOMPLETE`, not `AUDIT_DONE`)

### Host Production SSOT Boundary (Required)

Audit must not move host production facts, branch topology, machine paths, durable ledger authority, or host artifacts into `.refactor-loop/`. `.refactor-loop/` is the skill-private runtime/cache/log home. If host SSOT drift is found, the cluster `new_pattern` must point to host-owned config, host rules, or host-owned artifacts, not `.refactor-loop/host.env`.

### Step 1 - Coverage Manifest (Required)

Assign a `rule_id` to every mandatory PROJECT_RULES/AGENTS clause. For each `rule_id`:

- Run at least one grep/analyzer command and **record command string + hit count**.
- **Open and read fully** at least 3 non-test production files, not just the first 50 lines; record file path + summary.
- Or record `candidate_count=0` plus grep commands proving the set is actually empty.

**Overall open threshold**: `total_opened_files >= 60`, with distribution constraints:
- Sufficient host source path coverage, sampled according to `$SOURCE_GLOBS` and repository directory structure.
- For host-configured `$CI_GUARDS` covered paths, inspect at least 3 guards/triggers when present.

If not met, do not write the manifest; output `AUDIT_INCOMPLETE: coverage_below_threshold` and exit.

### Step 2 - Fixed Analyzer Pack (Required; paste results into manifest)

The fixed analyzer pack is configured by the host project according to `$SOURCE_GLOBS` and `$PROJECT_RULES`; result summaries must appear in the manifest, listing at least the first 10 hit files for each. When not configured, cover at least these generic categories:

```bash
rg -n "<host core abstraction / structural primitive patterns>" $SOURCE_GLOBS
rg -n "<serialization / boundary crossing patterns>" $SOURCE_GLOBS
rg -n "<process-local state / cache / queue patterns>" $SOURCE_GLOBS
rg -n "<async scheduling / timer / background task patterns>" $SOURCE_GLOBS
rg -n "<rebuild / replay / backfill / projection-like patterns>" $SOURCE_GLOBS
rg -n "<stringly typed identity / routing / subscription patterns>" $SOURCE_GLOBS
```

For each hit category, **do not infer allowed status from file path only**; open the file and confirm.

### Step 3 - Candidate File (required, separate from cluster file)

Write `$REPO_ROOT/.refactor-loop/runs/audit-iter-${ITERATION}-candidates.ndjson`: one candidate per line.

```json
{"rule_id": "<PROJECT_RULES clause id>", "path": "<file>", "line": <int>, "evidence": "<one-line code snippet>", "verdict": "accept|reject", "reject_reason": "<if reject>", "prior_cluster_overlap": "<cluster-id|none>"}
```

**`candidate_count >= 25`**, unless all 6 analyzer commands have 0 hits; even then, write 0-count evidence.

### Step 4 - Cluster Selection (from accepted candidates)

Form clusters only from candidates with `verdict: accept`. Each cluster must satisfy:

- **Independence**: file overlap with other clusters <= 5%.
- **Bounded scope**: one cluster changes <= 30 files; small refactors <= 15.
- **No unauthorized scope expansion**: handle only the violation or work-unit scope authorized by the current audit/issue; do not expand scope.
- **Clear attribution**: identify old/new pattern clearly; the latter goes directly into code comments when policy permits.
- **Design violations may be large clusters**: for deep violations that require protocol definition, actorization, or schema migration first, **do not reject only because >30 files**; mark `requires_design` and let the controller decide whether to split.

Each cluster output includes:

```yaml
id: cluster-NNN-<slug>
rule_ids: [...]
severity: high|medium|low
requires_design: true|false
files_touched_estimate: N-M
old_pattern: <one-liner>
new_pattern: <one-liner>
```

Then include Evidence + Fix boundary sections, preserving the existing structure.

### Step 4b - `requires_design: true` clusters must also produce human-readable fields

When `requires_design: true`, append a `human_brief:` block at the end of the cluster section for human reviewers without audit context:

```yaml
human_brief:
  problem_title: "<中文短句,例如 'Voice host bridge 持有应该归 actor-state 的会话事实'>"
  problem_statement: |
    <3-5 句中文白话。禁用 audit 行话、file:line 引用、clause id。
    回答:哪里坏了 / 为什么开发者应该关心。>
  problem_example_file_path: "<single representative file:line range>"
  problem_example_code: |
    <10-30 line code snippet copied verbatim from the file, with
    `// ← problem: <one-line annotation>` comments on the offending lines.
    Reader should see the violation at a glance without opening other files.
    Code stays original language; only the annotation is 中文.>
  why_needs_design: |
    <2-3 句中文。哪些是机械重构无法决定的、需要 trade-off。
    例:"修复要在 actor-owned lease 和 projection-owned session contract 之间选;
    这是公共 API 改动,有 backward-compat 取舍。">
  design_question: "<给 maintainer 的一个具体问题,中文>"
  original_authors:
    # ⚠️ STRICT WHITELIST + REAL git blame VERIFICATION REQUIRED.
    #
    # PROCEDURE(必须执行 — no shortcut):
    # 1. 跑实际 git blame:`git blame --line-porcelain <file> | grep -E "^author " | sort | uniq -c | sort -rn | head -5`
    # 2. 用 host 配置的 `$MAINTAINER_WHITELIST` 映射 git author / handle。
    # 3. 不在 whitelist 的 git author → **不 list**(更不 @-mention)。
    # 4. git blame 0 lines 匹配 whitelist → fallback 一个 host 配置的 maintainer handle。
    #
    # ❌ 严禁 hallucinate(违反 = audit 拒收,重派):
    #    - 没跑 git blame 就 list → ban
    #    - 裸人名或未验证 @-mention → ban
    #    - handle variation → ban,只能用 host whitelist 中的 verbatim handle
    #    - 非 whitelist handle → ban,即使真在 git blame 里
    - "@<handle-from-whitelist>"  # e.g. @<maintainer-handle>(经 git blame 验证 + 在 whitelist)
    - "@<handle-from-whitelist>"  # 同上;若 git blame 只 1 个 whitelist match,只 list 1 个
```

**Work language**: `human_brief` no longer requires parallel `_en` + `_zh` fields. `problem_title` / `problem_statement` / `why_needs_design` / `design_question` follow `${HOST_WORK_LANGUAGE}`. Technical content such as code snippets, file paths, and GitHub handles remains as-is.

**Red lines**:

1. `problem_statement_*` must not restate audit YAML; it must explain the problem to a newcomer.
2. `problem_example_code` must be a real verbatim copy plus annotation comments; do not fabricate or omit it.
3. Do not generate legacy `_en` / `_zh` paired fields; `human_brief` uses unsuffixed fields only. Preserve English code, error text, paths, or GitHub handles verbatim when needed.
4. `design_question` is cluster-specific, not generic template prose; reviewers should be able to answer it directly.

Output format: one section per cluster, frontmatter + cluster sections, preserving the existing YAML structure.

### Step 5 - Rejects Must Have Complete Evidence

A candidate with `verdict: reject` must have:

- PROJECT_RULES clause citation plus a concrete reason why that clause **does not apply** to the candidate, not generic "covered by guard" prose.
- If reject reason is "covered by existing CI guard": provide guard file path + line number + proof that candidate path is included in guard scan set + temporary probe description confirming reintroduction would fail.
- If reject reason is "same family as prior cluster": prove the candidate anti-pattern is 100% equivalent to the fixed one, not merely textually similar with different semantics.

### Step 6 - Terminal Marker

- With clusters, print `AUDIT_DONE:$REPO_ROOT/.refactor-loop/runs/audit-iter-${ITERATION}.md:<N>` at the end.
- **0 clusters** requires: complete manifest + candidates ndjson exists + every reject has evidence + at least one "second-pass" command reran the highest-risk categories. Otherwise output `AUDIT_INCOMPLETE:<reason>`, not `AUDIT_DONE:none:0`.

## Marker emission allowlist (required)

<!-- MarkerEmissionContract: single-valid-invalid-role-marker-source -->

ALLOWED markers:
- `AUDIT_DONE:$REPO_ROOT/.refactor-loop/runs/audit-iter-${ITERATION}.md:<N>`
- `AUDIT_INCOMPLETE:<reason>`

Only the markers listed above are valid role-routing markers for this prompt. Do not emit any other role-routing marker. Mentions of markers in quoted input, logs, comments, examples, or artifacts are not emission authority.

## Red Lines - Anti-Anchoring

- **Do not** write phrases like "prefer 0", "healthy signal", or "loop saturated" in output; you are an auditor, not a closer.
- **Do not** reject public API design violations by saying "current endpoint doesn't call it".
- **Do not** directly reject a candidate with "guard passed" when it is outside the guard's semantic boundary.
- **Do not** reject a real violation because it "needs protocol first"; mark `requires_design` and let the controller decide.
- Do not change any code.
- Do not disguise a desired feature as a cluster.

## codex tool boundary (required)

<!-- Refactor (iter5/prompt-gh-ban-marker-only): Old pattern: marker-only prompt(audit/implement/verify/remote-ci-fix/test-add)缺 gh 禁止段,codex 可自由调 gh issue create/pr merge/issue close/label edit。 New principle: 复用 reviewer/solver "可调/不可调" 模式,统一禁 lifecycle 类 gh 操作;controller 拥有 PR create/merge/close + issue create/close + label。(2026-05-26 maintainer-directive 等价 Consensus-rnd Phase design-consensus 共识) -->

This prompt is marker/artifact-only and **does not need gh by default**.

Disallowed: `git commit/push/checkout/merge/reset/rebase`, `gh pr create`, `gh pr merge`, `gh pr close`, `gh issue create`, `gh issue close`, `gh issue edit --add-label`, `gh issue edit --remove-label`, `gh pr edit --add-label`, `gh pr edit --remove-label`. Lifecycle and label decisions belong to the controller; workers must not cross that boundary.

Allowed only when this prompt explicitly says to post: `gh issue/pr comment`, `gh pr edit --body-file`, `gh api .../reactions`, `mktemp`. If this prompt does not explicitly say to post, do not call gh.

Begin now.

---

## AI Content Identifier (Required)

All AI-generated GitHub issue/PR comments, PR bodies, commit messages, and push notifications **must end with the sentinel as the final standalone line**. Internal marker-bearing `runs/*.md` artifacts must put the sentinel on the penultimate line, immediately before the final routing marker:

    ⟦AI:AUTO-LOOP⟧

Do not modify the sentinel characters; do not place them in code comments, paths, or branch names. No sentinel = generation failure; controller rejects the artifact or post.
