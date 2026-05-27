#!/usr/bin/env bash
# controller_lib.sh — bash library for codex-refactor-loop controller actions
#
# Controller boilerplate bugs often come from manually repeating post/spawn/merge glue.
# (post_banner + spawn + commit + push + close issue + cleanup labels)。
# 这个库统一抽象,消除 bash 变量传值 bug、重复 sed、worktree race。
#
# Usage(在 controller 里):
#   source .claude/skills/codex-refactor-loop/scripts/controller_lib.sh
#   merge_pr <pr> <issue>   # PR <pr> merge + close <issue> + cleanup labels
#   safe_worktree iterN cluster-026 origin/auto-refact-dev
#   open_pr_with_label "iterN cluster-NNN: ..." pr-body.md auto-refact-dev
#
# ⟦AI:AUTO-LOOP⟧

set -u

if [ -z "${REPO_ROOT:-}" ]; then
  if [ "${ALLOW_GIT_ROOT_FALLBACK:-0}" = "1" ]; then
    REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
  fi
fi
if [ -z "${REPO_ROOT:-}" ]; then
  echo "FATAL: REPO_ROOT is unset; source .refactor-loop/host.env or set ALLOW_GIT_ROOT_FALLBACK=1 for interactive use" >&2
  return 2 2>/dev/null || exit 2
fi

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)/repo_slug.sh"
set_gh_repo_args 0 0 || return 2 2>/dev/null || exit 2
INTEGRATION_BRANCH="${INTEGRATION_BRANCH:-${INTEGRATION:-auto-refact-dev}}"
REVIEW_BASE_BRANCH="${REVIEW_BASE_BRANCH:-${REVIEW_BASE:-dev}}"

# Fixes "git worktree add already exists" — detect-or-create
# Usage: safe_worktree <iter> <cluster-id> <base-ref>
# Sets WT_PATH and BRANCH env vars on success.
# Refactor (iter4/skill-worktree-inside-repo): Old pattern: sibling `<repo>-wt-<name>/`. New principle: inside `<repo>/.worktrees/<name>/` + gitignored.
safe_worktree() {
  local iter="$1" cluster="$2" base="$3"
  WT_PATH="${REPO_ROOT}/.worktrees/iter${iter}-${cluster}"
  BRANCH="refactor/iter${iter}-${cluster}"
  if [ -d "$WT_PATH" ]; then
    echo "  ✓ worktree exists: $WT_PATH" >&2
    return 0
  fi
  mkdir -p "${REPO_ROOT}/.worktrees"
  if git -C "$REPO_ROOT" show-ref --quiet "refs/heads/$BRANCH"; then
    git -C "$REPO_ROOT" worktree add "$WT_PATH" "$BRANCH" 2>&1 | tail -2 >&2
  else
    git -C "$REPO_ROOT" worktree add -b "$BRANCH" "$WT_PATH" "$base" 2>&1 | tail -2 >&2
  fi
  export WT_PATH BRANCH
}

# Post-merge cleanup: merge PR + close linked issue + cleanup labels on both
# Usage: merge_pr <pr-number> [linked-issue]
merge_pr() {
  # Refactor (iter3/skill-human-label-taxonomy):
  #   Old: 四个 Human label(含两个 🆘),no-gap/escalation 判定散落
  #   New principle: 恰好两个 active Human label;causes 移到 reason surface(#15 structural 共识)
  local pr="$1" linked_issue="${2:-}"
  if [ -z "$pr" ]; then
    echo "merge_pr: missing pr number" >&2
    return 1
  fi
  # Auto-extract Closes #N if linked_issue not provided
  if [ -z "$linked_issue" ]; then
    linked_issue=$(gh pr view "$pr" "${gh_repo_args[@]}" --json body --jq '.body' 2>/dev/null | grep -oE "Closes #[0-9]+" | head -1 | grep -oE "[0-9]+")
  fi
  gh pr merge "$pr" "${gh_repo_args[@]}" --admin --squash --delete-branch 2>&1 | tail -1
  # Cleanup PR labels: in-flight → merged
  gh pr edit "$pr" "${gh_repo_args[@]}" \
    --remove-label "🚀 phase:pr-open" \
    --remove-label "👀 phase:reviewing" \
    --remove-label "🔧 phase:fixing" \
    --remove-label "⏸️ phase:blocked" \
    --remove-label "auto-loop-stuck" \
    --remove-label "👤 human:需-maintainer-决策" \
    --remove-label "🆘 human:卡死" \
    --remove-label "🆘 human:卡死-需-rework" \
    --add-label "🎉 phase:merged" 2>&1 >/dev/null
  # Close linked issue + cleanup its labels
  if [ -n "$linked_issue" ]; then
    local close_comment
    close_comment=$(printf '✅ Auto-merged via PR #%s。\n\n⟦AI:AUTO-LOOP⟧' "$pr")
    gh issue close "$linked_issue" "${gh_repo_args[@]}" --reason "completed" --comment "$close_comment" 2>&1 | tail -1
    gh issue edit "$linked_issue" "${gh_repo_args[@]}" \
      --remove-label "🔍 phase:design-solving" \
      --remove-label "🛠️ phase:implementing" \
      --remove-label "🤖 human:auto-推进" \
      --remove-label "👤 human:需-maintainer-决策" \
      --remove-label "⏸️ phase:blocked" \
      --remove-label "auto-loop-stuck" \
      --remove-label "🆘 human:卡死" \
      --remove-label "🆘 human:卡死-需-rework" \
      --add-label "🎉 phase:merged" 2>&1 >/dev/null
  fi
  # Remove worktree if exists
  local wt
  wt=$(git -C "$REPO_ROOT" worktree list --porcelain | awk -v b="$(gh pr view "$pr" "${gh_repo_args[@]}" --json headRefName --jq '.headRefName' 2>/dev/null)" '
    $1 == "worktree" { path=$2 }
    $1 == "branch" && $2 == "refs/heads/"b { print path; exit }
  ')
  [ -n "$wt" ] && [ "$wt" != "$REPO_ROOT" ] && git -C "$REPO_ROOT" worktree remove "$wt" --force 2>&1 | tail -1
}

# Open PR + add auto-loop label atomically + capture PR number into PR_NUM
# Usage: open_pr_with_label <title> <body-file> [base] [head]
# Sets PR_NUM env var.
# head 必须显式传(controller 当前分支可能不是 cluster branch),
# 防止 gh fallback 到 current branch 导致 head/base 相同。
open_pr_with_label() {
  local title="$1" body_file="$2" base="${3:-$INTEGRATION_BRANCH}" head="${4:-}"
  if [ -z "$head" ]; then
    echo "open_pr_with_label: head branch required (avoid gh fallback to current branch = base)" >&2
    return 1
  fi
  local pr_url
  # gh pr create 多行 output (warnings + URL),URL 可能不在 last line;grep first occurrence
  pr_url=$(gh pr create "${gh_repo_args[@]}" --base "$base" --head "$head" --title "$title" --body-file "$body_file" 2>&1 | grep -oE "https://github.com/[^/]+/[^/]+/pull/[0-9]+" | head -1)
  PR_NUM=$(echo "$pr_url" | grep -oE "[0-9]+$")
  if [ -z "$PR_NUM" ]; then
    echo "open_pr_with_label: failed to extract PR num from: $pr_url" >&2
    return 1
  fi
  gh pr edit "$PR_NUM" "${gh_repo_args[@]}" --add-label "auto-loop,🚀 phase:pr-open,👀 phase:reviewing,🤖 human:auto-推进" 2>&1 >/dev/null
  echo "$pr_url"
  export PR_NUM
}

_parse_release_rollup_pending_event() {
  local event_json="$1"
  if [ -z "$event_json" ]; then
    echo "open_release_rollup_pr_from_pending_event: missing event json" >&2
    return 2
  fi

  local parsed
  parsed=$(EVENT_JSON="$event_json" python3 - <<'PY'
import json
import os
import sys

try:
    event = json.loads(os.environ["EVENT_JSON"])
except Exception:
    print("invalid json", file=sys.stderr)
    sys.exit(2)

fields = [
    "integration_branch",
    "review_base_branch",
    "integration_sha",
    "review_base_sha",
    "ahead_count",
]
missing = [field for field in fields if event.get(field) in ("", None)]
if missing:
    print("missing facts: " + ",".join(missing), file=sys.stderr)
    sys.exit(2)

try:
    ahead = int(event["ahead_count"])
except Exception:
    print("ahead_count must be an integer", file=sys.stderr)
    sys.exit(2)

if ahead <= 0:
    print("ahead_count must be positive", file=sys.stderr)
    sys.exit(2)

values = [
    event["integration_branch"],
    event["review_base_branch"],
    event["integration_sha"],
    event["review_base_sha"],
    str(ahead),
    event.get("reason", "release-rollup-needed"),
]
print("\t".join(str(value) for value in values))
PY
  ) || {
    local status=$?
    echo "open_release_rollup_pr_from_pending_event: invalid event" >&2
    return "$status"
  }

  IFS=$'\t' read -r RELEASE_ROLLUP_HEAD RELEASE_ROLLUP_BASE RELEASE_ROLLUP_INTEGRATION_SHA_VALUE RELEASE_ROLLUP_REVIEW_BASE_SHA_VALUE RELEASE_ROLLUP_AHEAD_COUNT_VALUE RELEASE_ROLLUP_REASON_VALUE <<< "$parsed"
  if [ -z "$RELEASE_ROLLUP_HEAD" ] || [ -z "$RELEASE_ROLLUP_BASE" ]; then
    echo "open_release_rollup_pr_from_pending_event: head/base required" >&2
    return 2
  fi
  if [ "$RELEASE_ROLLUP_HEAD" = "$RELEASE_ROLLUP_BASE" ]; then
    echo "open_release_rollup_pr_from_pending_event: head and base must differ" >&2
    return 2
  fi
  if [ "$RELEASE_ROLLUP_HEAD" != "$INTEGRATION_BRANCH" ]; then
    echo "open_release_rollup_pr_from_pending_event: head must equal INTEGRATION_BRANCH" >&2
    return 2
  fi
  if [ "$RELEASE_ROLLUP_BASE" != "$REVIEW_BASE_BRANCH" ]; then
    echo "open_release_rollup_pr_from_pending_event: base must equal REVIEW_BASE_BRANCH" >&2
    return 2
  fi
  if [ -z "$RELEASE_ROLLUP_INTEGRATION_SHA_VALUE" ] || [ -z "$RELEASE_ROLLUP_REVIEW_BASE_SHA_VALUE" ] || [ -z "$RELEASE_ROLLUP_AHEAD_COUNT_VALUE" ]; then
    echo "open_release_rollup_pr_from_pending_event: sha and ahead facts required" >&2
    return 2
  fi
}

_release_rollup_pr_exists() {
  local head="$1" base="$2"
  gh pr list "${gh_repo_args[@]}" --state open --head "$head" --base "$base" --limit 1 --json number --jq '.[0].number // ""' 2>/dev/null || true
}

# Refactor (iter5/issue-65-release-rollup-pending-event):
#   Old pattern: release-rollup event parsing, duplicate-open check, and PR creation were packed into one large controller helper.
#   New principle: keep the public lifecycle helper as orchestration; isolate event parsing and existing-rollup lookup behind narrow helpers.
# Open the release rollup PR from a daemon pending event.
# Usage: open_release_rollup_pr_from_pending_event <event-json> <body-file>
# Validates the event is exactly $INTEGRATION_BRANCH -> $REVIEW_BASE_BRANCH
# with non-empty SHA/ahead facts, then delegates lifecycle creation to
# open_pr_with_label. This helper belongs to the controller lifecycle surface;
# dev_sync_daemon.py must only emit the pending event.
open_release_rollup_pr_from_pending_event() {
  local event_json="$1" body_file="$2"
  if [ -z "$body_file" ] || [ ! -f "$body_file" ]; then
    echo "open_release_rollup_pr_from_pending_event: missing body file" >&2
    return 2
  fi

  _parse_release_rollup_pending_event "$event_json" || return "$?"

  local existing
  existing=$(_release_rollup_pr_exists "$RELEASE_ROLLUP_HEAD" "$RELEASE_ROLLUP_BASE")
  if [[ -n "$existing" && "$existing" =~ ^[0-9]+$ ]]; then
    PR_NUM="$existing"
    export PR_NUM
    echo "release-rollup PR already exists: #$existing"
    return 0
  fi

  RELEASE_ROLLUP_REASON="$RELEASE_ROLLUP_REASON_VALUE" \
  RELEASE_ROLLUP_INTEGRATION_SHA="$RELEASE_ROLLUP_INTEGRATION_SHA_VALUE" \
  RELEASE_ROLLUP_REVIEW_BASE_SHA="$RELEASE_ROLLUP_REVIEW_BASE_SHA_VALUE" \
  RELEASE_ROLLUP_AHEAD_COUNT="$RELEASE_ROLLUP_AHEAD_COUNT_VALUE" \
    open_pr_with_label "Release rollup: ${RELEASE_ROLLUP_HEAD} to ${RELEASE_ROLLUP_BASE}" "$body_file" "$RELEASE_ROLLUP_BASE" "$RELEASE_ROLLUP_HEAD"
}

# Refactor (iter4/human-label-semantics-guard): Old pattern: label 当 architect reject workaround. New principle: 严语义 + reflector self-check + controller helper guard + source-regression test.
# Apply the maintainer-decision label only after checking maintainer-directive artifacts.
# Usage: apply_human_label_or_skip <pr-number> <reason-or-topic>
apply_human_label_or_skip() {
  local pr_number="$1" reason="${2:-}"
  if [ -z "$pr_number" ]; then
    echo "apply_human_label_or_skip: missing pr_number" >&2
    return 2
  fi

  local directive_dir="${REPO_ROOT}/.refactor-loop/runs/maintainer-directives"
  if [ -d "$directive_dir" ]; then
    local target escaped_reason
    target="${pr_number#\#}"
    if grep -RIlE "(^|[^0-9])(PR[ -]?)?#?${target}([^0-9]|$)" "$directive_dir"/*.md >/dev/null 2>&1; then
      echo "skip-label: maintainer-directive 已覆盖,见 .refactor-loop/runs/maintainer-directives/"
      return 1
    fi
    if [ -n "$reason" ]; then
      escaped_reason=$(printf '%s\n' "$reason" | sed 's/[][(){}.^$*+?|\\]/\\&/g')
      if grep -RIlE "(^|[^[:alnum:]_-])${escaped_reason}([^[:alnum:]_-]|$)" "$directive_dir"/*.md >/dev/null 2>&1; then
        echo "skip-label: maintainer-directive 已覆盖,见 .refactor-loop/runs/maintainer-directives/"
        return 1
      fi
    fi
  fi

  gh pr edit "$pr_number" "${gh_repo_args[@]}" --add-label "👤 human:需-maintainer-决策" 2>&1 >/dev/null
}

# Substitute {{handlebars}} placeholders in a template using current env vars
# Usage: render_template <template-file> <output-file>
# Reads $WORK_UNIT_ID, $CLUSTER_ID, $ITERATION, $WORKTREE_PATH, $BRANCH, $OLD_PATTERN, $NEW_PRINCIPLE, $SCOPE_PATHS, $VERIFICATION_HINTS, and $VAR in template.
render_template() {
  local in="$1" out="$2"
  perl -pe '
    s/\{\{work_unit_id\}\}/($ENV{WORK_UNIT_ID} || $ENV{CLUSTER_ID})/ge;
    s/\{\{cluster_id\}\}/$ENV{CLUSTER_ID}/g;
    s/\{\{iteration\}\}/$ENV{ITERATION}/g;
    s/\{\{worktree_path\}\}/$ENV{WORKTREE_PATH}/g;
    s/\{\{branch\}\}/$ENV{BRANCH}/g;
    s/\{\{old_pattern\}\}/$ENV{OLD_PATTERN}/g;
    s/\{\{new_principle\}\}/$ENV{NEW_PRINCIPLE}/g;
    s/\{\{scope_paths\}\}/$ENV{SCOPE_PATHS}/g;
    s/\{\{verification_hints\}\}/$ENV{VERIFICATION_HINTS}/g;
  ' < "$in" | envsubst > "$out"
}

# Sweep all closed auto-loop issues/PRs and clean stale in-flight phase labels
# Usage: sweep_stale_labels
sweep_stale_labels() {
  # Refactor (iter3/skill-human-label-taxonomy):
  #   Old: 四个 Human label(含两个 🆘),no-gap/escalation 判定散落
  #   New principle: 恰好两个 active Human label;causes 移到 reason surface(#15 structural 共识)
  # Refactor (iter3/skill-hygiene-scripts):
  #   Old: stale labels were interpolated into a shell-built gh edit string.
  #   New: build a real argv array and append one --remove-label "$label" pair.
  #   This preserves labels with spaces/quotes as single arguments and removes command injection risk.
  local n_fixed=0
  for kind in issue pr; do
    gh "$kind" list "${gh_repo_args[@]}" --label "auto-loop" --state closed --limit 50 --json number,labels 2>/dev/null | \
      python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
except Exception:
    sys.exit(0)
stale = ['🚀 phase:pr-open', '👀 phase:reviewing', '🔧 phase:fixing', '⏸️ phase:blocked', 'auto-loop-stuck', '👤 human:需-maintainer-决策', '🆘 human:卡死', '🆘 human:卡死-需-rework', '🔍 phase:design-solving', '🛠️ phase:implementing', '🤖 human:auto-推进']
for x in d:
    bad = [l['name'] for l in x['labels'] if l['name'] in stale]
    if bad:
        print(x['number'], ','.join(bad))
" | while read num bad; do
        [ -z "$num" ] && continue
        local cmd=(gh "$kind" edit "$num" "${gh_repo_args[@]}")
        old_IFS="$IFS"; IFS=,
        for l in $bad; do
          cmd+=(--remove-label "$l")
        done
        IFS="$old_IFS"
        # Add 🎉 phase:merged if it's MERGED
        if [ "$kind" = "pr" ]; then
          local state
          state=$(gh pr view "$num" "${gh_repo_args[@]}" --json state --jq '.state' 2>/dev/null)
          [ "$state" = "MERGED" ] && cmd+=(--add-label "🎉 phase:merged")
        else
          cmd+=(--add-label "🎉 phase:merged")
        fi
        "${cmd[@]}" 2>&1 >/dev/null && n_fixed=$((n_fixed+1)) && echo "  cleaned $kind #$num: $bad"
      done
  done
  echo "  total fixed: $n_fixed"
}

# Validate prompt file has no unresolved {{var}}
# Usage: validate_prompt <prompt-file>
validate_prompt() {
  local f="$1"
  local n
  n=$(grep -c "{{" "$f" 2>/dev/null | tr -d ' \n' || echo "0")
  [ -z "$n" ] && n=0
  if [ "$n" -gt 0 ] 2>/dev/null; then
    echo "❌ prompt $f 仍有 $n 个未解析 {{var}}:" >&2
    grep -n "{{" "$f" | head -5 >&2
    return 1
  fi
  return 0
}

# Verify trunk builds after merge — call after merge_pr to catch cross-PR API breaks
# Usage: verify_trunk_build
# Catch cross-PR API mismatch after independently green PRs merge into trunk.
verify_trunk_build() {
  cd "$REPO_ROOT" || return 1
  git pull --ff-only origin "$INTEGRATION_BRANCH" 2>&1 | tail -1
  if [ -z "${BUILD_CMD:-}" ]; then
    echo "❌ BUILD_CMD unset; source .refactor-loop/host.env before verify_trunk_build" >&2
    return 2
  fi
  if ! $BUILD_CMD; then
    echo "❌ trunk build broken after merge — 派 hotfix codex(参考 SKILL Phase 4 hotfix 段)" >&2
    return 2
  fi
  # 加 architecture_guards 包括 docs lint,防止 merge 后 docs/architecture regression.
  if [ -n "${CI_GUARDS:-}" ]; then
    if ! $CI_GUARDS > /tmp/_verify_trunk_guards.log 2>&1; then
      echo "❌ trunk architecture/docs guards 挂(merge 后 lint regression)" >&2
      tail -5 /tmp/_verify_trunk_guards.log >&2
      return 3
    fi
    echo "✓ trunk build + guards green"
  else
    echo "✓ trunk build green; guards skipped: CI_GUARDS unset"
  fi
  return 0
}

# Refactor (iter4/skill-safe-push-helper): Old pattern: controller 每次 commit 后直接 push,
# 但 dev_sync_daemon 在独立 worktree 同步 origin/dev -> auto-refact-dev 时会先 push,导致
# main repo HEAD 落后远端;controller 这边 push 撞 non-fast-forward,必须手动 pull --rebase。
# New principle: 强制走 safe_push,内置 fetch + 必要时 rebase --autostash + 再 push;
# 同时暴露 safe_sync_main 在 session 入口 / pre-commit 时主动追远端。
# (2026-05-26 maintainer-directive 等价 Phase 9 共识)

# Usage: safe_push <remote> <branch>
# Behavior: fetch remote/branch; if local diverges or is behind, rebase --autostash;
#   then push. Returns non-zero if rebase has conflicts (caller must resolve).
safe_push() {
  local remote="${1:-origin}"
  local branch="${2:-$(git -C "$REPO_ROOT" rev-parse --abbrev-ref HEAD)}"
  if [ -z "$branch" ] || [ "$branch" = "HEAD" ]; then
    echo "safe_push: cannot determine branch (HEAD detached?); aborting" >&2
    return 2
  fi
  ( cd "$REPO_ROOT" || return 1
    git fetch "$remote" "$branch" 2>&1 | tail -3
    local behind
    behind=$(git rev-list --count "HEAD..$remote/$branch")
    if [ "${behind:-0}" -gt 0 ]; then
      echo "safe_push: local behind $remote/$branch by $behind commit(s); rebasing"
      if ! git pull --rebase --autostash "$remote" "$branch"; then
        echo "❌ safe_push: rebase conflict on $remote/$branch — resolve manually then push" >&2
        return 3
      fi
    fi
    git push "$remote" "$branch"
  )
}

# Usage: safe_sync_main [remote] [branch]
# Idempotent fast-forward of the current working tree to the remote tip. Use at session
# start or before commits to avoid the safe_push rebase path. No-op when already current.
safe_sync_main() {
  local remote="${1:-origin}"
  local branch="${2:-$(git -C "$REPO_ROOT" rev-parse --abbrev-ref HEAD)}"
  if [ -z "$branch" ] || [ "$branch" = "HEAD" ]; then
    echo "safe_sync_main: cannot determine branch; skipping" >&2
    return 0
  fi
  ( cd "$REPO_ROOT" || return 1
    git fetch "$remote" "$branch" 2>&1 | tail -3
    local behind
    behind=$(git rev-list --count "HEAD..$remote/$branch")
    if [ "${behind:-0}" -gt 0 ]; then
      echo "safe_sync_main: local behind $remote/$branch by $behind; pulling --rebase --autostash"
      git pull --rebase --autostash "$remote" "$branch"
    else
      echo "safe_sync_main: already up to date with $remote/$branch"
    fi
  )
}

# ⟦AI:AUTO-LOOP⟧
