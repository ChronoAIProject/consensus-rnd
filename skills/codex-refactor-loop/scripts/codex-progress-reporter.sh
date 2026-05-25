#!/bin/bash
# codex-progress-reporter.sh
# 每 600s 扫 .refactor-loop/logs/*.log; 对未结束 (无 EXIT=0) 的 log 提取 tail; edit-in-place
# 一条 progress comment 到关联 issue/PR. 首次创建, 后续 update 同一 comment id.
# 不会膨胀评论数. 成功完成时(检测到 EXIT=0) 自动删除进展评论并停止追该 log.
#
# State: .refactor-loop/codex-progress-state.json
#   { "<log-basename>": { "target": "<issue-or-pr>", "kind": "issue|pr", "comment_id": <id>, "last_md5": "<sha>", "finished": "false|true|failed" } }
#
# 启动: bash .claude/skills/codex-refactor-loop/scripts/codex-progress-reporter.sh &
# 停止: kill <pid>

set -u  # 不用 -e/pipefail — daemon 必须存活,subshell 偶发 non-zero 不应导致整个 daemon 死

if [ -z "${REPO_ROOT:-}" ]; then
  if [ "${ALLOW_GIT_ROOT_FALLBACK:-0}" = "1" ]; then
    REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
  fi
fi
if [ -z "${REPO_ROOT:-}" ]; then
  echo "FATAL: REPO_ROOT is unset; source .refactor-loop/host.env or set ALLOW_GIT_ROOT_FALLBACK=1 for interactive use" >&2
  exit 2
fi
cd "$REPO_ROOT"
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)/repo_slug.sh"
REPO="$(resolve_github_repo_slug 1 1)" || exit $?

INTERVAL="${INTERVAL:-600}"
STATE_DIR=".refactor-loop"
STATE_FILE="$STATE_DIR/codex-progress-state.json"
LOG_DIR="$STATE_DIR/logs"
PROMPTS_DIR="$STATE_DIR/prompts"

mkdir -p "$STATE_DIR"
[ -f "$STATE_FILE" ] || echo "{}" > "$STATE_FILE"

log_msg() { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*" >&2; }

# parse log basename → target number (issue or PR)
# 优先从对应 prompt 文件 grep "#NNN"; 次选从 log 文件名 pattern 提取
parse_target() {
  local base=$1
  local n=""
  # review-pr*/fix-pr*/phase9-issue* 等文件名本身带权威 target → 优先文件名
  case "$base" in
    review-pr*) n=$(echo "$base" | sed -nE 's/^review-pr([0-9]+).*/\1/p') ;;
    fix-pr*) n=$(echo "$base" | sed -nE 's/^fix-pr([0-9]+).*/\1/p') ;;
    phase9-issue*) n=$(echo "$base" | sed -nE 's/^phase9-issue([0-9]+).*/\1/p') ;;
  esac
  if [ -z "$n" ]; then
    # implement-/verify- 等 cluster log 没有 PR 号 → 从 prompt body 取最后 #NNN
    # meta-cluster prompts may mention multiple issue numbers; primary is the last one.
    local prompt="$PROMPTS_DIR/$base.md"
    if [ -f "$prompt" ]; then
      n=$(grep -oE "#[0-9]+" "$prompt" 2>/dev/null | tail -1 | tr -d '#' || true)
    fi
  fi
  echo "$n"
}

# return "pr" or "issue"
parse_kind() {
  local n=$1
  if gh pr view "$n" --repo "$REPO" --json number >/dev/null 2>&1; then
    echo "pr"
  else
    echo "issue"
  fi
}

exit_status() {
  # 只看末 5 行 — wrapper 在结束时 append "EXIT=<code>\nDONE_AT=..." 作为最后两行
  # 不能 grep 全 log,因为 codex 中途可能 echo / cat 含 "EXIT=" 的内容造成误判
  local line code
  line=$(tail -5 "$1" 2>/dev/null | grep -E "^EXIT=[0-9]+$" | tail -1 || true)
  if [ -z "$line" ]; then
    echo "in_flight"
    return
  fi
  code="${line#EXIT=}"
  if [ "$code" = "0" ]; then
    echo "exit_ok"
  else
    echo "exit_failed"
  fi
}

is_finished() {
  [ "$(exit_status "$1")" = "exit_ok" ]
}

# zombie: 30 min 未写,但无 EXIT marker → 进程很可能死了,不该再当 in-flight 持续 post
is_zombie() {
  local log=$1
  if is_finished "$log"; then return 1; fi
  local mtime now
  mtime=$(stat -f %m "$log" 2>/dev/null || stat -c %Y "$log")
  now=$(date +%s)
  [ $(( now - mtime )) -gt 1800 ]
}

extract_tail() {
  # 取 last 25 行, 跳过 EXIT/DONE_AT marker (不影响)
  tail -30 "$1" | head -25
}

elapsed_sec() {
  local log=$1 mtime now
  mtime=$(stat -f %B "$log" 2>/dev/null || stat -c %Y "$log")
  now=$(date +%s)
  echo $(( now - mtime ))
}

build_body() {
  local base=$1 log=$2 finished=$3
  local elapsed_s
  elapsed_s=$(elapsed_sec "$log")
  local elapsed_min=$(( elapsed_s / 60 ))
  local tail_block
  tail_block=$(extract_tail "$log")
  local status_line delete_note
  if [ "$finished" = "failed" ]; then
    status_line="❌ 失败; 已跑 ${elapsed_min} min"
    delete_note="codex 已非零退出;保留此 comment 直到 controller 处理失败。"
  else
    status_line="⏳ 进行中; 已跑 ${elapsed_min} min"
    delete_note="自动更新每 10 分钟;edit-in-place 不堆评论;codex EXIT=0 后此 comment 自动删除。"
  fi
  cat <<EOF
## 📊 codex 进展 $base (${status_line})

\`\`\`
$tail_block
\`\`\`

> ${delete_note}
🤖 controller progress reporter

⟦AI:AUTO-LOOP⟧
EOF
}

hash_body() {
  if command -v md5 >/dev/null 2>&1; then
    md5
  elif command -v md5sum >/dev/null 2>&1; then
    md5sum | awk '{print $1}'
  else
    python3 -c 'import hashlib, sys; print(hashlib.md5(sys.stdin.buffer.read()).hexdigest())'
  fi
}

state_get() {
  local key=$1 field=$2
  jq -r --arg k "$key" --arg f "$field" '.[$k][$f] // empty' "$STATE_FILE"
}

state_set() {
  local key=$1 target=$2 kind=$3 cid=$4 md5=$5 finished=$6
  local tmp
  tmp=$(mktemp)
  jq --arg k "$key" --arg t "$target" --arg kd "$kind" --argjson cid "$cid" --arg m "$md5" --arg fin "$finished" \
    '.[$k] = {target: $t, kind: $kd, comment_id: $cid, last_md5: $m, finished: $fin}' \
    "$STATE_FILE" > "$tmp" && mv "$tmp" "$STATE_FILE"
}

post_or_update() {
  local base=$1 log=$2
  local target kind cid prev_md5 finished
  target=$(state_get "$base" "target")
  if [ -z "$target" ]; then
    target=$(parse_target "$base")
    if [ -z "$target" ]; then
      log_msg "skip $base: no target"
      return
    fi
    kind=$(parse_kind "$target")
    cid="null"
    prev_md5=""
  else
    kind=$(state_get "$base" "kind")
    cid=$(state_get "$base" "comment_id")
    prev_md5=$(state_get "$base" "last_md5")
  fi
  [ -z "$cid" ] && cid="null"

  case "$(exit_status "$log")" in
    exit_ok) finished="true" ;;
    exit_failed) finished="failed" ;;
    *) finished="false" ;;
  esac
  if [ "$finished" = "false" ] && is_zombie "$log"; then
    log_msg "skip zombie log $base (no EXIT, mtime > 30 min)"
    return
  fi

  # 已 terminal 且上次已记录同一 terminal state → skip
  local prev_finished
  prev_finished=$(state_get "$base" "finished")
  if [ "$finished" = "$prev_finished" ] && { [ "$finished" = "true" ] || [ "$finished" = "failed" ]; }; then
    return
  fi

  # 状态从 in-flight → finished: 删 comment + 从 state 移除
  if [ "$finished" = "true" ] && [ -n "$cid" ] && [ "$cid" != "null" ] && [ "$cid" != "0" ]; then
    if gh api -X DELETE "repos/$REPO/issues/comments/$cid" >/dev/null 2>&1; then
      log_msg "deleted progress comment for $base (finished, cid=$cid was=$kind #$target)"
    else
      log_msg "FAIL to delete comment $cid for $base (already gone?); marking finished anyway"
    fi
    # mark finished in state so we don't re-create next tick
    state_set "$base" "$target" "$kind" "0" "deleted" "true"
    return
  fi

  local body cur_md5
  body=$(build_body "$base" "$log" "$finished")
  cur_md5=$(printf '%s' "$body" | hash_body)

  # 内容没变化且没 finish 切换 → skip
  if [ "$cur_md5" = "$prev_md5" ] && [ "$finished" = "$prev_finished" ]; then
    return
  fi

  local body_file
  body_file="/tmp/codex-progress-$$-$(date +%s%N)-$RANDOM.md"
  echo "$body" > "$body_file"

  if [ "$cid" = "null" ] || [ -z "$cid" ]; then
    # 首次见到:GitHub 必须反映实际状态。
    # 短 codex 在 reporter tick 间 spawn+complete,首次见到时已 finished — 之前 silent skip
    # 导致 GitHub 上看不到任何 codex 痕迹(maintainer 不知道有事发生)。
    # 修法:首见 finished log → post 一张简短"✅ 已完成"banner(不删,留下 GitHub 痕迹)。
    # Controller 后续 sweep 处理 marker 时再覆盖 / append 新 banner。
    local url
    # parse_kind fallback:先 try $kind,fail 再 try 另一个(防 issue 被当 pr)
    if [ "$kind" = "pr" ]; then
      url=$(gh pr comment "$target" --repo "$REPO" --body-file "$body_file" 2>/dev/null | tail -1)
      [ -z "$url" ] && {
        url=$(gh issue comment "$target" --repo "$REPO" --body-file "$body_file" 2>/dev/null | tail -1)
        [ -n "$url" ] && kind="issue"
      }
    else
      url=$(gh issue comment "$target" --repo "$REPO" --body-file "$body_file" 2>/dev/null | tail -1)
      [ -z "$url" ] && {
        url=$(gh pr comment "$target" --repo "$REPO" --body-file "$body_file" 2>/dev/null | tail -1)
        [ -n "$url" ] && kind="pr"
      }
    fi
    local new_cid
    new_cid=$(echo "$url" | grep -oE 'issuecomment-[0-9]+' | sed 's/issuecomment-//')
    if [ -n "$new_cid" ]; then
      state_set "$base" "$target" "$kind" "$new_cid" "$cur_md5" "$finished"
      log_msg "created progress comment for $base → $kind #$target (cid=$new_cid)"
    else
      log_msg "FAIL to create comment for $base → $kind #$target"
    fi
  else
    # edit 现有 comment
    if gh api -X PATCH "repos/$REPO/issues/comments/$cid" -F body=@"$body_file" >/dev/null 2>&1; then
      state_set "$base" "$target" "$kind" "$cid" "$cur_md5" "$finished"
      log_msg "edited progress comment for $base (cid=$cid, finished=$finished)"
    else
      log_msg "FAIL to edit comment $cid for $base; will retry next tick"
    fi
  fi

  rm -f "$body_file"
}

# 主 loop
while true; do
  log_msg "tick"
  for log in "$LOG_DIR"/*.log; do
    [ -f "$log" ] || continue
    base=$(basename "$log" .log)
    # 跳过 audit log(audit 完成后才能进 phase 2,不挂 issue);可以选 post 到 dashboard
    case "$base" in
      audit-iter-*) continue ;;
      remote-ci-*) continue ;;
    esac
    post_or_update "$base" "$log"
  done
  sleep "$INTERVAL"
done
