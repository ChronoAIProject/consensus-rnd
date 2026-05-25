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

GH_REPO_SLUG="${GH_REPO_SLUG:-${GH_OWNER:+$GH_OWNER/}${GH_REPO_NAME:-${GH_REPO:-}}}"
if [ -n "${GH_REPO_SLUG:-}" ] && ! [[ "$GH_REPO_SLUG" == */* ]]; then
  echo "FATAL: GH_REPO_SLUG must be OWNER/REPO; got '$GH_REPO_SLUG'" >&2
  return 2 2>/dev/null || exit 2
fi
INTEGRATION_BRANCH="${INTEGRATION_BRANCH:-${INTEGRATION:-auto-refact-dev}}"
REVIEW_BASE_BRANCH="${REVIEW_BASE_BRANCH:-${REVIEW_BASE:-dev}}"
gh_repo_args=()
if [ -n "${GH_REPO_SLUG:-}" ]; then
  gh_repo_args=(--repo "$GH_REPO_SLUG")
fi

# Fixes "git worktree add already exists" — detect-or-create
# Usage: safe_worktree <iter> <cluster-id> <base-ref>
# Sets WT_PATH and BRANCH env vars on success.
safe_worktree() {
  local iter="$1" cluster="$2" base="$3"
  WT_PATH="${REPO_ROOT}-wt-iter${iter}-${cluster}"
  BRANCH="refactor/iter${iter}-${cluster}"
  if [ -d "$WT_PATH" ]; then
    echo "  ✓ worktree exists: $WT_PATH" >&2
    return 0
  fi
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
    gh issue close "$linked_issue" "${gh_repo_args[@]}" --reason "completed" --comment "✅ Auto-merged via PR #${pr}。⟦AI:AUTO-LOOP⟧" 2>&1 | tail -1
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

# ⟦AI:AUTO-LOOP⟧
