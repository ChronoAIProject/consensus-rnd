#!/bin/bash
# codex-progress-reporter.sh
# Every 600s, scan .refactor-loop/logs/*.log; for unfinished logs
# (no EXIT=0), extract the tail and edit one progress comment in place on the
# linked issue/PR. Create the comment on first sight and update the same
# comment id afterward. This does not inflate comment count. On successful
# completion (EXIT=0), delete the progress comment and stop tracking that log.
#
# State: .refactor-loop/codex-progress-state.json
#   { "<log-basename>": { "target": "<issue-or-pr>", "kind": "issue|pr", "comment_id": <id>, "last_md5": "<sha>", "finished": "false|true|failed" } }
#
# Start: bash .claude/skills/codex-refactor-loop/scripts/codex-progress-reporter.sh &
# Stop: kill <pid>

set -u  # Avoid -e/pipefail: daemon must survive occasional subshell non-zero exits.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
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
source "$SCRIPT_DIR/repo_slug.sh"
source "$SCRIPT_DIR/daemon_heartbeat.sh"
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
# Prefer grep "#NNN" from the corresponding prompt; fall back to filename pattern.
parse_target() {
  local base=$1
  local n=""
  # review-pr*/fix-pr*/phase9-issue* filenames carry authoritative targets.
  case "$base" in
    review-pr*) n=$(echo "$base" | sed -nE 's/^review-pr([0-9]+).*/\1/p') ;;
    fix-pr*) n=$(echo "$base" | sed -nE 's/^fix-pr([0-9]+).*/\1/p') ;;
    phase9-issue*) n=$(echo "$base" | sed -nE 's/^phase9-issue([0-9]+).*/\1/p') ;;
  esac
  if [ -z "$n" ]; then
    # implement-/verify- cluster logs lack PR numbers; take the last #NNN from prompt body.
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

# Refactor (iter4/issue17-hygiene): Old pattern: binary in_flight/done handling treated failed codex exits as done and deleted the progress comment. New principle: tri-state in_flight/exit_ok/exit_failed keeps failed comments and state.finished=failed visible to maintainers.
exit_status() {
  # Only inspect the last 5 lines: wrapper appends "EXIT=<code>\nDONE_AT=..."
  # as the last two lines. Do not grep the full log because codex may echo/cat
  # content containing "EXIT=" mid-run, causing false positives.
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

# zombie: no writes for 30 minutes and no EXIT marker; process likely died and
# should not keep posting as in-flight.
is_zombie() {
  local log=$1
  if is_finished "$log"; then return 1; fi
  local mtime now
  mtime=$(stat -f %m "$log" 2>/dev/null || stat -c %Y "$log")
  now=$(date +%s)
  [ $(( now - mtime )) -gt 1800 ]
}

extract_tail() {
  # Take the last 25 lines, skipping EXIT/DONE_AT markers.
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

  # Already terminal and previous state recorded the same terminal state: skip.
  # Refactor (issue-69/orphan-delete-retry): exception — if finished=true but state still
  # carries a real comment_id (cid != 0 / "deleted" / "gone"), the prior delete attempt did
  # not confirm. Fall through so the delete branch retries this tick instead of leaving an
  # orphan progress comment on GitHub forever.
  local prev_finished
  prev_finished=$(state_get "$base" "finished")
  local needs_delete_retry=0
  if [ "$finished" = "true" ] && [ -n "$cid" ] && [ "$cid" != "null" ] && [ "$cid" != "0" ]; then
    needs_delete_retry=1
  fi
  if [ "$finished" = "$prev_finished" ] && [ "$needs_delete_retry" = "0" ] && { [ "$finished" = "true" ] || [ "$finished" = "failed" ]; }; then
    return
  fi

  # State changed from in-flight to finished: delete comment and remove from state.
  # Refactor (issue-69/orphan-delete-retry): Old pattern: marked finished=true regardless of
  # delete result, so any transient DELETE failure (rate-limit / network) orphaned the comment
  # forever (empirically seen as 30 spam comments on issue #69). New principle: only mark finished=true after
  # confirmed delete OR confirmed 404; otherwise leave state untouched so next tick retries.
  if [ "$finished" = "true" ] && [ -n "$cid" ] && [ "$cid" != "null" ] && [ "$cid" != "0" ]; then
    if gh api -X DELETE "repos/$REPO/issues/comments/$cid" >/dev/null 2>&1; then
      log_msg "deleted progress comment for $base (finished, cid=$cid was=$kind #$target)"
      state_set "$base" "$target" "$kind" "0" "deleted" "true"
    else
      # DELETE failed. Distinguish "already gone" (404 = ok, mark finished) from
      # transient failure (still exists, leave state alone so we retry).
      if ! gh api "repos/$REPO/issues/comments/$cid" >/dev/null 2>&1; then
        log_msg "comment $cid for $base already 404; marking finished"
        state_set "$base" "$target" "$kind" "0" "gone" "true"
      else
        log_msg "FAIL delete comment $cid for $base; comment still exists, retry next tick"
        # Intentionally do NOT state_set — leave finished as-is so the next tick
        # re-enters this branch.
      fi
    fi
    return
  fi

  local body cur_md5
  body=$(build_body "$base" "$log" "$finished")
  cur_md5=$(printf '%s' "$body" | hash_body)

  # Body unchanged and no finished-state transition: skip.
  if [ "$cur_md5" = "$prev_md5" ] && [ "$finished" = "$prev_finished" ]; then
    return
  fi

  local body_file
  body_file="/tmp/codex-progress-$$-$(date +%s%N)-$RANDOM.md"
  echo "$body" > "$body_file"

  if [ "$cid" = "null" ] || [ -z "$cid" ]; then
    # First sight: GitHub must reflect the actual state.
    # Short codex runs can spawn and complete between reporter ticks, so first
    # sight may already be finished. Previously that silently skipped posting,
    # leaving no GitHub trace for maintainers.
    # Fix: first-sight finished log posts a short completed banner that remains
    # as a GitHub trace. Controller sweeps later replace/append a new banner
    # when processing markers.
    local url
    # parse_kind fallback: try $kind first, then the other kind to handle issue/pr mismatch.
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
    # Edit existing comment.
    if gh api -X PATCH "repos/$REPO/issues/comments/$cid" -F body=@"$body_file" >/dev/null 2>&1; then
      state_set "$base" "$target" "$kind" "$cid" "$cur_md5" "$finished"
      log_msg "edited progress comment for $base (cid=$cid, finished=$finished)"
    else
      log_msg "FAIL to edit comment $cid for $base; will retry next tick"
    fi
  fi

  rm -f "$body_file"
}

# Test seam: when sourced with TEST_NO_LOOP=1, expose functions but skip the daemon loop.
if [ "${TEST_NO_LOOP:-0}" = "1" ]; then
  return 0 2>/dev/null || exit 0
fi

# Main loop.
while true; do
  # Refactor (iter1/issue-143):
  #   Old pattern: wrapper sidecar refreshed heartbeat even if this scan loop hung.
  #   New principle: shell actor beats after log scan, then lease-sleeps through long idle intervals.
  #   Heartbeat stays same path/epoch; no new daemon or lifecycle authority.
  log_msg "tick"
  for log in "$LOG_DIR"/*.log; do
    [ -f "$log" ] || continue
    base=$(basename "$log" .log)
    # Skip audit logs: audit enters phase 2 only after completion and has no issue.
    # It could optionally post to a dashboard.
    case "$base" in
      audit-iter-*) continue ;;
      remote-ci-*) continue ;;
    esac
    post_or_update "$base" "$log"
  done
  daemon_heartbeat_beat
  daemon_heartbeat_sleep "$INTERVAL"
done
