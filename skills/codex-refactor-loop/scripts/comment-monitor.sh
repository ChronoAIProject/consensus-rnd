#!/usr/bin/env bash
# .claude/skills/codex-refactor-loop/scripts/comment-monitor.sh
#
# 独立运行的 comment monitor:
# - 自己跑 gh api 查 design issue / PR 新评论
# - 检测到 team-member 新评论 → 自己加 👀 react(不等 controller)
# - 同时 emit `new-team-comment: <issue> <author> <comment-id>` 到 stdout
#   (controller 通过 Monitor tool 包它,把 stdout 行变成 task-notification)
# - 跑 forever,除非外部 kill
#
# 用法(controller 通过 Monitor tool 包):
#   Monitor(persistent: true, command: ".claude/skills/codex-refactor-loop/scripts/comment-monitor.sh")
#
# state 文件:.refactor-loop/comment-monitor-state.json (JSON map: comment_id → "seen")

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
REPO="${GH_REPO_SLUG:-${GH_OWNER:+$GH_OWNER/}${GH_REPO_NAME:-${GH_REPO:-}}}"
if [ -n "${REPO:-}" ] && ! [[ "$REPO" == */* ]]; then
  echo "FATAL: GH_REPO_SLUG must be OWNER/REPO; got '$REPO'" >&2
  exit 2
fi
if [ -n "${REPO:-}" ]; then
  :
else
  REPO="$(gh repo view --json nameWithOwner -q .nameWithOwner 2>/dev/null || true)"
fi
if [ -z "${REPO:-}" ]; then
  echo "FATAL: GH_REPO_SLUG is unset and gh repo view failed" >&2
  exit 2
fi
if [ -z "${MAINTAINER_WHITELIST:-}" ]; then
  echo "FATAL: MAINTAINER_WHITELIST is unset; comment-monitor fails closed" >&2
  exit 2
fi
STATE_FILE="${STATE_FILE:-$REPO_ROOT/.refactor-loop/comment-monitor-state.json}"
HEARTBEAT_FILE="$REPO_ROOT/.refactor-loop/heartbeats/comment-monitor.sh.ts"
INTERVAL="${INTERVAL:-30}"

mkdir -p "$(dirname "$STATE_FILE")"
mkdir -p "$(dirname "$HEARTBEAT_FILE")"
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
# 包括:
# - "## 🤖" (codex artifact)
# - "## 📊" (controller status banner per SKILL.md status-banner)
# - "## 📢 cc" (cc 原作者)
# - "## 📎" (attachment / raw)
# - "## ✅" (consensus reached / merged)
# - "## 🎉" (celebration)
# - "## 🔄" (rebase / round dispatched)
# - "## Phase " (writer-codex 标题如 "## Phase 9 r2 已收敛..." / "## Phase 8 ...")
# - "## Studio " / "## Workflow " / "## iterN" (writer-codex 通常用 cluster 主题做标题)
# - "Generated with Claude Code" 后缀
# - 任何 body 内含 "POSTED:phase" 标记的(writer-codex 自身 marker 不会出现在 body,但若误传则 skip)
is_controller_post() {
  # 主判定:sentinel ⟦AI:AUTO-LOOP⟧ 在 body 任意位置 → AI post(per SKILL "AI 内容标识符")
  case "$2" in
    *"⟦AI:AUTO-LOOP⟧"*) return 0 ;;
    *"Generated with Claude Code"*) return 0 ;;
  esac
  # Legacy emoji-prefix(过渡期老评论无 sentinel)— 任何 ## + emoji + ... 第一行
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

write_heartbeat() {
  # Refactor (iter4/spawn-codex-pid-registry):
  #   Old pattern: controller health checked daemon names via process-table grep.
  #   New principle: daemon writes repo-local heartbeat timestamp; controller uses heartbeat-mtime <90s.
  date +%s > "$HEARTBEAT_FILE"
}

while true; do
  write_heartbeat
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

      # Team member new comment → 立刻 eyes react
      react_out=$(gh api "repos/$REPO/issues/comments/$id/reactions" -X POST -f content=eyes 2>&1)
      react_ok=$?
      if [ $react_ok -eq 0 ]; then
        echo "new-team-comment: $n $author $id eyes-reacted-at=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
        # 通知 controller 缩 wakeup:append 到 pending events file
        # controller per-wakeup 第一步读此文件,有新 entry → 下次 wakeup 缩到 600s
        echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) new-team-comment $n $author $id" \
          >> "$REPO_ROOT/.refactor-loop/.controller-pending-events.log"
        # 立刻 post status 卡片让 maintainer 看到 daemon 已识别 + controller 在路上
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
