"""Runtime catalog for host-language user-facing renderer copy."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping

from .context import HostEnvLocator, HostWorkLanguage, LoopContextError, normalize_host_work_language, parse_host_env


COPY_CATALOG: Mapping[str, Mapping[HostWorkLanguage, Mapping[str, str]]] = {
    "github_body": {
        "en": {
            "what_prefix": "What this is: ",
            "conclusion": "Conclusion: the full authorization/consensus artifact is inline; the GitHub body is self-auditable.",
            "next_step": "Next step: continue from the self-contained information in this body.",
            "details_heading": "### Details",
            "intro": "This content was rendered by read-only `render-github-body` from local artifacts; the authorization/consensus text is fully inline, and local paths are not the sole source.",
            "artifact_summary": "Inline artifact",
            "debug_summary": "<summary>Local debug clues</summary>",
            "debug_intro": "These paths are for local debugging only, not authorization/consensus sources:",
        },
        "zh": {
            "what_prefix": "这是什么:",
            "conclusion": "结论:授权/共识 artifact 全文已内联,GitHub 正文本身可审计。",
            "next_step": "下一步:按正文中的自包含信息继续处理。",
            "details_heading": "### 详细说明",
            "intro": "以下内容由只读 `render-github-body` 从本地 artifact 渲染;授权/共识正文已完整内联,本地路径不作为唯一来源。",
            "artifact_summary": "内联 artifact",
            "debug_summary": "<summary>本机调试线索</summary>",
            "debug_intro": "这些路径仅供本机调试,不是授权/共识来源:",
        },
    },
    "github_body_kind": {
        "en": {
            "pr": "PR description",
            "design-issue": "design issue",
            "consensus": "consensus",
            "authorization": "authorization",
            "escalation": "escalation",
            "triage": "triage",
        },
        "zh": {
            "pr": "PR 描述",
            "design-issue": "design issue",
            "consensus": "共识",
            "authorization": "授权",
            "escalation": "升级",
            "triage": "triage",
        },
    },
    "banner_role_next_steps": {
        "en": {
            "test-add": "1. test-add completes marker `TEST_ADD_DONE:...`  2. controller auto commit + push  3. codecov retests",
            "fix": "1. fix r<N> completes marker `FIX_DONE:...`  2. controller commit + push  3. dispatch reviewer r<N+1>",
            "reviewer": "1. three reviewers complete verdict markers  2. controller computes consensus  3. reject=0 + approve>=1 -> merge; all-comment -> wait explicit approval; reject -> fix",
            "implement": "1. implement completes marker `IMPLEMENT_DONE:<cluster>:<status>`  2. controller commit + push  3. open PR + dispatch reviewer r1",
            "solver": "1. three solvers complete `SOLVER_DONE:...`  2. controller dispatches meta-judge r<N>  3. consensus -> implement / converge -> fresh round",
            "judge": "1. judge completes `META_JUDGE_DONE:...`  2. consensus -> implement / converge -> fresh round / escalate -> reflector or human intervention",
            "reflector": "1. reflector completes `META_RESOLVED:<kind>`  2. controller routes by kind (retry-fix / re-design / re-cluster / drop / escalate-human)",
            "audit": "1. audit completes marker `AUDIT_DONE:...:<N>`  2. controller verifies + opens design issues + dispatches implement",
        },
        "zh": {
            "test-add": "1. test-add 完成 marker `TEST_ADD_DONE:...`  2. controller 自动 commit + push  3. codecov 重测",
            "fix": "1. fix r<N> 完成 marker `FIX_DONE:...`  2. controller commit + push  3. 派 reviewer r<N+1>",
            "reviewer": "1. 三 reviewer 完成 verdict marker  2. controller 计算 consensus  3. reject=0 + approve>=1 -> merge; all-comment -> wait explicit approval; reject -> fix",
            "implement": "1. implement 完成 marker `IMPLEMENT_DONE:<cluster>:<status>`  2. controller commit + push  3. open PR + 派 reviewer r1",
            "solver": "1. 三 solver `SOLVER_DONE:...`  2. controller 派 meta-judge r<N>  3. consensus → implement / converge → fresh round",
            "judge": "1. judge `META_JUDGE_DONE:...`  2. consensus → implement / converge → fresh round / escalate → reflector or 人介入",
            "reflector": "1. reflector `META_RESOLVED:<kind>`  2. controller 按 kind 路由(retry-fix / re-design / re-cluster / drop / escalate-human)",
            "audit": "1. audit 完成 marker `AUDIT_DONE:...:<N>`  2. controller 验证 + 开 design issues + 派 implement",
        },
    },
    "banner": {
        "en": {
            "heading": "## 📊 Status card — {role} dispatched",
            "table_head": "| Dimension | Value |",
            "stage_label": "Stage",
            "stage_value": "**dispatched codex(role=`{role}`)**",
            "context_label": "Context",
            "next_label": "Next automatic step",
            "human_label": "**Human intervention needed**",
            "human_value": "**❌ No** (automatic progression)",
        },
        "zh": {
            "heading": "## 📊 状态卡片 — {role} 派出",
            "table_head": "| 维度 | 值 |",
            "stage_label": "阶段",
            "stage_value": "**派出 codex(role=`{role}`)**",
            "context_label": "上下文",
            "next_label": "下一步自动会做",
            "human_label": "**是否需要人介入**",
            "human_value": "**❌ 否**(自动推进)",
        },
    },
    "comment_monitor_banner": {
        "en": {
            "heading": "## 📊 Status — maintainer comment received (daemon recognized)",
            "table_head": "| Dimension | Value |",
            "trigger_label": "Trigger comment",
            "summary_label": "Comment summary",
            "reaction_label": "Daemon reaction",
            "reaction_value": "👀 eyes reaction added",
            "next_label": "Next step",
            "next_value": "controller next wakeup (<=25 min) reads daemon log -> dispatches fresh codex round (maintainer-reply-resets-the-round) -> updates this card",
            "human_label": "**Human intervention needed**",
            "human_value": "❌ No (automatic response in progress)",
        },
        "zh": {
            "heading": "## 📊 状态 — 已收到 maintainer 评论(daemon 识别)",
            "table_head": "| 维度 | 值 |",
            "trigger_label": "触发评论",
            "summary_label": "评论摘要",
            "reaction_label": "daemon 反应",
            "reaction_value": "👀 eyes react 已加",
            "next_label": "下一步",
            "next_value": "controller 下次 wakeup(≤25 min)读 daemon log → 派 fresh codex round(maintainer-reply-resets-the-round)→ 更新本卡片",
            "human_label": "**是否需要人介入**",
            "human_value": "❌ 否(自动响应中)",
        },
    },
    "peek": {
        "en": {
            "activity": "▍Activity timeline (read-only facts):",
            "maintainer_comments": "▍🚨 maintainer comments (read first - missed read = controller bug):",
            "milestone": "▍Milestone (priority) issues:",
            "open_prs": "▍Open auto-loop PRs:",
            "unpushed": "▍Unpushed worker output:",
            "zero_streak": "▍Monitor zero_streak (last 10 ticks):",
            "stale_labels": "▍Stale labels (CLOSED but still carrying in-flight phase labels):",
            "linkage_mismatch": "▍Issue/PR linkage mismatch:",
            "spawn_drop": "▍Spawn drop (N solvers complete but judge was not dispatched):",
            "drift": "▍Drift (label vs codex mismatch):",
            "stale_worktree": "▍Stale worktree (remote branch missing; cleanup is controller-owned):",
            "stuck": "▍Stuck too long (>6h without maintainer reply; consider 4h reflector re-evaluation):",
            "open_issues": "▍Open auto-loop issues:",
        },
        "zh": {
            "activity": "▍Activity timeline (read-only facts):",
            "maintainer_comments": "▍🚨 maintainer comments (read first — missed read = controller bug):",
            "milestone": "▍Milestone (优先) issues:",
            "open_prs": "▍Open auto-loop PRs:",
            "unpushed": "▍Unpushed worker output:",
            "zero_streak": "▍Monitor zero_streak (last 10 ticks):",
            "stale_labels": "▍Stale labels (CLOSED but still carrying in-flight phase labels):",
            "linkage_mismatch": "▍Issue/PR linkage mismatch:",
            "spawn_drop": "▍Spawn drop (N solvers complete but judge was not dispatched):",
            "drift": "▍Drift (label vs codex mismatch):",
            "stale_worktree": "▍Stale worktree (remote branch missing; cleanup is controller-owned):",
            "stuck": "▍Stuck too long (>6h without maintainer reply; consider 4h reflector re-evaluation):",
            "open_issues": "▍Open auto-loop issues:",
        },
    },
    "release_rollup": {
        "en": {
            "title_prefix": "Release rollup: integration ahead ",
            "heading": "### Release rollup",
            "target_label": "- Target: `",
            "ahead_label": "- Integration branch ahead of review-base: `",
            "range_label": "- Range: `",
            "issues_label": "- Related issues: ",
            "commit_summary_heading": "### Commit summary",
            "commit_summary_unavailable": "- Local commit summary is unavailable; use the GitHub PR diff as the source of truth.",
            "merge_policy_heading": "### Merge policy",
            "auto_merge_policy_line": "- After required checks are green, controller auto squash-merges; when `ROLLUP_AUTO_MERGE=manual`, wait for human review/merge.",
            "singleton_policy_line": "- This PR is the release rollup singleton; when an open rollup already exists, controller only updates its head and body instead of opening another PR.",
        },
        "zh": {
            "title_prefix": "发布 rollup: integration ahead ",
            "heading": "### 发布 rollup",
            "target_label": "- 目标: `",
            "ahead_label": "- 集成分支领先 review-base: `",
            "range_label": "- 范围: `",
            "issues_label": "- 涉及 issue: ",
            "commit_summary_heading": "### commit 摘要",
            "commit_summary_unavailable": "- 本地 commit 摘要不可用;请以 GitHub PR diff 为准。",
            "merge_policy_heading": "### 合并策略",
            "auto_merge_policy_line": "- required checks 全绿后由 controller 自动 squash merge; `ROLLUP_AUTO_MERGE=manual` 时等待人工 review/merge。",
            "singleton_policy_line": "- 该 PR 是 release rollup singleton;已有 open rollup 时 controller 只更新 head 和 body,不开新 PR。",
        },
    },
    "implementation_commit": {
        "en": {"message": "Implement issue #{issue}"},
        "zh": {"message": "实现 issue #{issue}"},
    },
}


def work_language_from_env(env: Mapping[str, str] | None = None) -> HostWorkLanguage:
    return normalize_host_work_language(env=env)


def current_work_language(*, env: Mapping[str, str] | None = None, repo_root: Path | None = None, cwd: Path | None = None) -> HostWorkLanguage:
    source_env = dict(os.environ if env is None else env)
    root = repo_root or Path(source_env.get("REPO_ROOT") or Path.cwd())
    current_dir = cwd or Path.cwd()
    if HostEnvLocator.EXPLICIT_ENV in source_env:
        location = HostEnvLocator.resolve(root, source_env, current_dir)
        if location is not None:
            return normalize_host_work_language(env=parse_host_env(location.path))
    try:
        location = HostEnvLocator.resolve(root, source_env, current_dir)
    except LoopContextError:
        location = None
    if location is not None:
        return normalize_host_work_language(env=parse_host_env(location.path))
    return normalize_host_work_language(env=source_env)


def copy_for(section: str, *, language: HostWorkLanguage | None = None, env: Mapping[str, str] | None = None) -> Mapping[str, str]:
    resolved = language or work_language_from_env(env)
    try:
        return COPY_CATALOG[section][resolved]
    except KeyError as exc:
        raise LoopContextError(f"missing runtime copy catalog section={section!r} language={resolved!r}") from exc


def all_copy_values(section: str, key: str) -> tuple[str, ...]:
    try:
        values = COPY_CATALOG[section]
        return tuple(language_copy[key] for language_copy in values.values())
    except KeyError as exc:
        raise LoopContextError(f"missing runtime copy catalog value section={section!r} key={key!r}") from exc
