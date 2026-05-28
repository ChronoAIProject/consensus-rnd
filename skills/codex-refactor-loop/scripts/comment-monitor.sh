#!/usr/bin/env bash
# .claude/skills/codex-refactor-loop/scripts/comment-monitor.sh
#
# Standalone comment monitor:
# - Runs gh api itself to find new design issue / PR comments.
# - Adds an 👀 reaction immediately when detecting a team-member comment,
#   without waiting for the controller.
# - Emits `new-team-comment: <issue> <author> <comment-id>` to stdout.
#   The controller wraps this with the Monitor tool, converting stdout lines
#   into task notifications.
# - Runs forever unless externally killed.
#
# Usage (controller wraps it through the Monitor tool):
#   Monitor(persistent: true, command: ".claude/skills/codex-refactor-loop/scripts/comment-monitor.sh")
#
# State file: .refactor-loop/comment-monitor-state.json (JSON map: comment_id -> "seen")

set -u
if [ -z "${REPO_ROOT:-}" ]; then
  if [ "${ALLOW_GIT_ROOT_FALLBACK:-0}" = "1" ]; then
    REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
  fi
fi
if [ -z "${REPO_ROOT:-}" ]; then
  echo "FATAL: REPO_ROOT is unset; source .refactor-loop/host.env or set ALLOW_GIT_ROOT_FALLBACK=1 for interactive use" >&2
  exit 2
fi
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)/repo_slug.sh"
REPO="$(resolve_github_repo_slug 1 1)" || exit $?
if [ -z "${MAINTAINER_WHITELIST:-}" ]; then
  echo "FATAL: MAINTAINER_WHITELIST is unset; comment-monitor fails closed" >&2
  exit 2
fi
STATE_FILE="${STATE_FILE:-$REPO_ROOT/.refactor-loop/comment-monitor-state.json}"
INTERVAL="${INTERVAL:-30}"

mkdir -p "$(dirname "$STATE_FILE")"
[ -f "$STATE_FILE" ] || echo '{}' > "$STATE_FILE"

# Maintainer whitelist is injected by the host project and fails closed when missing.
is_team_member() {
  local author="$1" item
  for item in ${MAINTAINER_WHITELIST//,/ }; do
    [ "$author" = "$item" ] && return 0
  done
  return 1
}

# Skip controller / writer-codex own posts. body first line check.
# Includes:
# - "## 🤖" (codex artifact)
# - "## 📊" (controller status banner per SKILL.md status-banner)
# - "## 📢 cc" (cc original author)
# - "## 📎" (attachment / raw)
# - "## ✅" (consensus reached / merged)
# - "## 🎉" (celebration)
# - "## 🔄" (rebase / round dispatched)
# - "## Phase " (writer-codex titles such as "## Phase 9 r2 converged..." / "## Phase 8 ...")
# - "## Studio " / "## Workflow " / "## iterN" (writer-codex usually titles by cluster topic)
# - "Generated with Claude Code" suffix
# - Any body containing a "POSTED:phase" marker; writer-codex's own marker should
#   not appear in the body, but skip it if it is accidentally passed through.
is_controller_post() {
  # Primary check: sentinel ⟦AI:AUTO-LOOP⟧ anywhere in body means AI post
  # (per SKILL "AI content identifier").
  case "$2" in
    *"⟦AI:AUTO-LOOP⟧"*) return 0 ;;
    *"Generated with Claude Code"*) return 0 ;;
  esac
  # Legacy emoji prefix for transitional old comments without sentinel:
  # any first line starting with ## + emoji + ...
  case "$1" in
    "## 🤖"*|"## 📊"*|"## 📢"*|"## 📎"*|"## ✅"*|"## 🆘"*|"## 🎉"*|"## 🔄"*|"## ⏸️"*|"## 🔍"*|"## 🛠️"*|"## 🚀"*|"## 👀"*|"## 🔧"*|"## ⚙️"*|"## Phase "*|"## Studio "*|"## Workflow "*|"## iter"*) return 0 ;;
    *) return 1 ;;
  esac
}

seen() {
  jq -e --arg id "$1" 'has($id)' "$STATE_FILE" > /dev/null 2>&1
}

mark_seen() {
  local id="$1" tmp
  tmp=$(mktemp)
  jq --arg id "$id" '. + {($id): "seen"}' "$STATE_FILE" > "$tmp" && mv "$tmp" "$STATE_FILE"
}

while true; do
  # Auto-discover targets: open issues with refactor-design-needed label + open PRs with auto-loop label
  targets=$(
    {
      gh issue list --repo "$REPO" --state open --label "refactor-design-needed" --json number -q '.[].number' 2>/dev/null
      gh pr list --repo "$REPO" --state open --label "auto-loop" --json number -q '.[].number' 2>/dev/null
    } | sort -u
  )

  for n in $targets; do
    # Try issue then pr
    comments=$(gh api "repos/$REPO/issues/$n/comments" --jq '.[] | {id, author: .user.login, body, created_at}' 2>/dev/null)
    [ -z "$comments" ] && continue

    while IFS= read -r raw; do
      id=$(jq -r '.id' <<<"$raw")
      author=$(jq -r '.author' <<<"$raw")
      body=$(jq -r '.body' <<<"$raw")
      created=$(jq -r '.created_at' <<<"$raw")
      [ -z "$id" ] && continue

      if seen "$id"; then continue; fi

      first_line=$(echo "$body" | head -1)
      if is_controller_post "$first_line" "$body"; then
        mark_seen "$id"
        continue
      fi

      if ! is_team_member "$author"; then
        # Not a team member; mark seen so we don't keep checking,
        # but log a one-line event for controller to decide (e.g. PushNotification)
        mark_seen "$id"
        echo "new-outsider-comment: $n $author $id (skipped reply per security gate)"
        continue
      fi

      # Team member new comment: react with eyes immediately.
      react_out=$(gh api "repos/$REPO/issues/comments/$id/reactions" -X POST -f content=eyes 2>&1)
      react_ok=$?
      if [ $react_ok -eq 0 ]; then
        echo "new-team-comment: $n $author $id eyes-reacted-at=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
        # Notify controller to shorten wakeup by appending to the pending events file.
        # Controller reads this file first on each wakeup; a new entry shortens
        # the next wakeup to 600s.
        echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) new-team-comment $n $author $id" \
          >> "$REPO_ROOT/.refactor-loop/.controller-pending-events.log"
        # Immediately post a status card so maintainers can see the daemon
        # recognized the comment and the controller is on the way.
        body_excerpt=$(echo "$body" | head -1 | head -c 80)
        tmp_banner=$(mktemp)
        cat > "$tmp_banner" <<EOF
## 📊 状态 — 已收到 maintainer 评论(daemon 识别)

| 维度 | 值 |
|---|---|
| 触发评论 | id=$id author=$author |
| 评论摘要 | $body_excerpt |
| daemon 反应 | 👀 eyes react 已加 |
| 下一步 | controller 下次 wakeup(≤25 min)读 daemon log → 派 fresh codex round(maintainer-reply-resets-the-round)→ 更新本卡片 |
| **是否需要人介入** | ❌ 否(自动响应中) |

🤖 comment-monitor.sh daemon

⟦AI:AUTO-LOOP⟧
EOF
        post_out=$(gh issue comment "$n" --repo "$REPO" --body-file "$tmp_banner" 2>&1)
        if [ $? -eq 0 ]; then
          echo "daemon-banner-posted: $n $id $(echo "$post_out" | grep -oE 'https://[^ ]+' | head -1)"
        else
          post_out=$(gh pr comment "$n" --repo "$REPO" --body-file "$tmp_banner" 2>&1)
          [ $? -eq 0 ] && echo "daemon-banner-posted: $n $id $(echo "$post_out" | grep -oE 'https://[^ ]+' | head -1)" \
            || echo "daemon-banner-FAILED: $n $id $(echo "$post_out" | head -1)"
        fi
        rm -f "$tmp_banner"
      else
        echo "new-team-comment: $n $author $id eyes-react-FAILED: $(echo "$react_out" | head -1)"
      fi
      mark_seen "$id"
    done < <(echo "$comments" | jq -c '.')
  done

  sleep "$INTERVAL"
done
