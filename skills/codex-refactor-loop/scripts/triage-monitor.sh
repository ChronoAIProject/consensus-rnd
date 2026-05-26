#!/usr/bin/env bash
# triage-monitor.sh — daemon 60s 周期扫 auto-loop-triage label 并直接派 triage codex
#
# 设计:
# - 60s 周期 gh issue list --label "auto-loop-triage" --state open
# - 对每个未处理的 issue:
#   - state 存 .refactor-loop/triage-monitor-state.json(claimed/spawned/failed/done)
#   - materialize prompt and spawn triage codex
# - 启动: nohup bash <skill-root>/scripts/triage-monitor.sh >> .refactor-loop/logs/triage-monitor.log 2>&1 & disown
#
# ⟦AI:AUTO-LOOP⟧

set -u

resolve_skill_root() {
  # Refactor (iter3/skill-skill-root-contract): Old pattern: .claude/skills hardcoded lookup. New principle: self-locate from this script path, with optional validated CODEX_REFACTOR_LOOP_SKILL_ROOT override.
  local root
  if [ -n "${CODEX_REFACTOR_LOOP_SKILL_ROOT:-}" ]; then
    root="$CODEX_REFACTOR_LOOP_SKILL_ROOT"
  else
    root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
  fi
  if ! root="$(cd "$root" 2>/dev/null && pwd -P)"; then
    echo "FATAL: invalid codex-refactor-loop skill root: ${CODEX_REFACTOR_LOOP_SKILL_ROOT:-${BASH_SOURCE[0]}}" >&2
    exit 2
  fi
  if [ ! -f "$root/SKILL.md" ] || [ ! -f "$root/prompts/triage-external-issue.md" ] || [ ! -f "$root/scripts/spawn-codex.sh" ]; then
    echo "FATAL: invalid codex-refactor-loop skill root: missing SKILL.md, prompts/triage-external-issue.md, or scripts/spawn-codex.sh under $root" >&2
    exit 2
  fi
  printf '%s\n' "$root"
}

if ! resolved_skill_root="$(resolve_skill_root)"; then
  exit 2
fi
TRIAGE_PROMPT_TEMPLATE="$resolved_skill_root/prompts/triage-external-issue.md"
SPAWN_CODEX="$resolved_skill_root/scripts/spawn-codex.sh"
if [ -n "${CODEX_REFACTOR_LOOP_SKILL_ROOT_PRINT:-}" ]; then
  # Refactor (iter3/skill-skill-root-contract): Old: triage-monitor default self-location was only observable after repo-state setup. New: deterministic print hook exposes the resolved skill root before mutation for contract tests.
  printf '%s\n' "$resolved_skill_root"
  exit 0
fi

if [ -z "${REPO_ROOT:-}" ]; then
  if [ "${ALLOW_GIT_ROOT_FALLBACK:-0}" = "1" ]; then
    REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
  fi
fi
if [ -z "${REPO_ROOT:-}" ]; then
  echo "FATAL: REPO_ROOT is unset; source .refactor-loop/host.env or set ALLOW_GIT_ROOT_FALLBACK=1 for interactive use" >&2
  exit 2
fi
GH_REPO_SLUG="${GH_REPO_SLUG:-${GH_OWNER:+$GH_OWNER/}${GH_REPO_NAME:-${GH_REPO:-}}}"
if [ -n "${GH_REPO_SLUG:-}" ] && ! [[ "$GH_REPO_SLUG" == */* ]]; then
  echo "FATAL: GH_REPO_SLUG must be OWNER/REPO; got '$GH_REPO_SLUG'" >&2
  exit 2
fi
gh_repo_args=()
[ -n "${GH_REPO_SLUG:-}" ] && gh_repo_args=(--repo "$GH_REPO_SLUG")
INTERVAL="${INTERVAL:-60}"
TRIAGE_MAX_RETRIES="${TRIAGE_MAX_RETRIES:-3}"
TRIAGE_RETRY_BACKOFF_SECONDS="${TRIAGE_RETRY_BACKOFF_SECONDS:-300}"
STATE_FILE="$REPO_ROOT/.refactor-loop/triage-monitor-state.json"
PENDING_LOG="$REPO_ROOT/.refactor-loop/.controller-pending-events.log"
HEARTBEAT_FILE="$REPO_ROOT/.refactor-loop/heartbeats/triage-monitor.sh.ts"

mkdir -p "$REPO_ROOT/.refactor-loop/prompts" "$REPO_ROOT/.refactor-loop/logs" "$(dirname "$HEARTBEAT_FILE")"
[ -f "$STATE_FILE" ] || echo "{}" > "$STATE_FILE"

log() {
  # Refactor (iter3/skill-hygiene-scripts):
  #   Old: triage dispatch was marked seen before any durable spawn proof existed.
  #   New: state transitions are logged around claimed/spawned/failed/done records.
  #   This makes retry/backoff behavior diagnosable from the daemon log.
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*"
}

write_heartbeat() {
  # Refactor (iter4/spawn-codex-pid-registry):
  #   Old pattern: controller health checked daemon names via process-table grep.
  #   New principle: daemon writes repo-local heartbeat timestamp; controller uses heartbeat-mtime <90s.
  date +%s > "$HEARTBEAT_FILE"
}

state_status() {
  # Refactor (iter3/skill-hygiene-scripts):
  #   Old: state value was a timestamp string, equivalent to a permanent seen bit.
  #   New: object records carry status/retries/next_attempt/log, with strings retried.
  #   Legacy timestamp entries are treated as failed so old pre-spawn claims can recover.
  local issue="$1"
  jq -r --arg n "$issue" '
    if (.[$n] // null) == null then "new"
    elif (.[$n] | type) == "object" then (.[$n].status // "new")
    else "failed"
    end
  ' "$STATE_FILE" 2>/dev/null
}

state_retries() {
  # Refactor (iter3/skill-hygiene-scripts):
  #   Old: seen state had no retry budget, so dispatch failures were unrecoverable.
  #   New: each issue records deterministic retry count consumed by failed attempts.
  #   Missing or legacy entries start at zero retries.
  local issue="$1"
  jq -r --arg n "$issue" 'if (.[$n] | type) == "object" then (.[$n].retries // 0) else 0 end' "$STATE_FILE" 2>/dev/null
}

state_next_attempt() {
  # Refactor (iter3/skill-hygiene-scripts):
  #   Old: failed prompt/spawn paths had no backoff state.
  #   New: next_attempt prevents tight respawn loops while preserving eventual retry.
  #   A missing next_attempt is immediately eligible.
  local issue="$1"
  jq -r --arg n "$issue" 'if (.[$n] | type) == "object" then (.[$n].next_attempt // 0) else 0 end' "$STATE_FILE" 2>/dev/null
}

state_log_file() {
  # Refactor (iter3/skill-hygiene-scripts):
  #   Old: state could not connect an issue claim to durable log evidence.
  #   New: state records the chosen log path and validates SPAWN/EXIT markers there.
  #   This lets claimed/spawned recovery be based on files, not memory.
  local issue="$1"
  jq -r --arg n "$issue" 'if (.[$n] | type) == "object" then (.[$n].log // "") else "" end' "$STATE_FILE" 2>/dev/null
}

set_state() {
  # Refactor (iter3/skill-hygiene-scripts):
  #   Old: jq wrote a raw timestamp string before prompt materialization/spawn.
  #   New: jq atomically writes status/retries/next_attempt/log/update timestamp.
  #   Terminal dispatch status is set only after log marker proof exists.
  local issue="$1" status="$2" retries="$3" next_attempt="$4" log_file="$5"
  local ts tmp
  ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  tmp=$(mktemp)
  jq --arg n "$issue" \
     --arg status "$status" \
     --argjson retries "$retries" \
     --argjson next_attempt "$next_attempt" \
     --arg log "$log_file" \
     --arg ts "$ts" \
     '. + {($n): {status: $status, retries: $retries, next_attempt: $next_attempt, log: $log, updated_at: $ts}}' \
     "$STATE_FILE" > "$tmp" && mv "$tmp" "$STATE_FILE"
}

log_has_spawn_or_exit_marker() {
  # Refactor (iter3/skill-hygiene-scripts):
  #   Old: spawn was assumed successful immediately after backgrounding a process.
  #   New: spawned/done requires durable evidence in the selected log file.
  #   SPAWN proves wrapper startup; EXIT proves terminal completion.
  local log_file="$1"
  [ -f "$log_file" ] && grep -Eq '^(SPAWN:|EXIT=)' "$log_file"
}

log_has_exit_marker() {
  # Refactor (iter3/skill-hygiene-scripts):
  #   Old: triage-monitor never distinguished running from completed dispatches.
  #   New: EXIT in the wrapper log advances spawned work to done.
  #   Tail-only matching avoids accidental prompt/log content earlier in the file.
  local log_file="$1"
  [ -f "$log_file" ] && tail -5 "$log_file" 2>/dev/null | grep -q '^EXIT='
}

mark_failed_retry() {
  # Refactor (iter3/skill-hygiene-scripts):
  #   Old: prompt or spawn failure was hidden behind a permanent seen bit.
  #   New: failed attempts increment a capped retry counter and schedule backoff.
  #   The issue remains recoverable until TRIAGE_MAX_RETRIES is exhausted.
  local issue="$1" retries="$2" log_file="$3" reason="$4"
  local now next
  now=$(date +%s)
  retries=$((retries + 1))
  next=$((now + TRIAGE_RETRY_BACKOFF_SECONDS))
  set_state "$issue" "failed" "$retries" "$next" "$log_file"
  log "failed: triage issue #$issue attempt ${retries}/${TRIAGE_MAX_RETRIES}: $reason"
}

log "triage-monitor started: interval=${INTERVAL}s"

while true; do
  write_heartbeat
  # Query open issues with auto-loop-triage label
  issues=$(gh issue list "${gh_repo_args[@]}" --label "auto-loop-triage" --state open --json number,author --jq '.[] | "\(.number) \(.author.login)"' 2>/dev/null)
  if [ -z "$issues" ]; then
    [ "${TRIAGE_MONITOR_ONCE:-0}" = "1" ] && exit 0
    sleep "$INTERVAL"
    continue
  fi

  while read -r issue author; do
    [ -z "$issue" ] && continue
    status=$(state_status "$issue")
    retries=$(state_retries "$issue")
    next_attempt=$(state_next_attempt "$issue")
    log_file=$(state_log_file "$issue")
    [ -z "$log_file" ] && log_file="$REPO_ROOT/.refactor-loop/logs/triage-issue-${issue}-attempt-$((retries + 1)).log"

    if [ "$status" = "done" ]; then
      continue
    fi
    if [ "$status" = "spawned" ]; then
      if log_has_exit_marker "$log_file"; then
        set_state "$issue" "done" "$retries" 0 "$log_file"
        log "done: triage codex for issue #$issue"
      fi
      continue
    fi
    if [ "$status" = "claimed" ] && log_has_spawn_or_exit_marker "$log_file"; then
      if log_has_exit_marker "$log_file"; then
        set_state "$issue" "done" "$retries" 0 "$log_file"
        log "done: triage codex for issue #$issue"
      else
        set_state "$issue" "spawned" "$retries" 0 "$log_file"
        log "spawned: triage codex for issue #$issue (author=$author)"
      fi
      continue
    fi

    now=$(date +%s)
    if [ "$status" = "claimed" ] && [ "$next_attempt" -le "$now" ] 2>/dev/null; then
      mark_failed_retry "$issue" "$retries" "$log_file" "missing-spawn-marker"
      continue
    fi
    if [ "$retries" -ge "$TRIAGE_MAX_RETRIES" ]; then
      log "retry-cap: triage issue #$issue remains $status after $retries attempts"
      continue
    fi
    if [ "$next_attempt" -gt "$now" ] 2>/dev/null; then
      continue
    fi

    prompt_file="$REPO_ROOT/.refactor-loop/prompts/triage-issue-${issue}.md"
    log_file="$REPO_ROOT/.refactor-loop/logs/triage-issue-${issue}-attempt-$((retries + 1)).log"
    set_state "$issue" "claimed" "$retries" "$((now + TRIAGE_RETRY_BACKOFF_SECONDS))" "$log_file"
    if ! ISSUE_NUMBER="$issue" COMMENT_AUTHOR="$author" \
      perl -pe 's/\Q${ISSUE_NUMBER}\E/$ENV{ISSUE_NUMBER}/g; s/Author: maintainer/Author: $ENV{COMMENT_AUTHOR}/g' \
        "$TRIAGE_PROMPT_TEMPLATE" \
        > "$prompt_file" 2>/dev/null; then
      if ! cp "$TRIAGE_PROMPT_TEMPLATE" "$prompt_file" 2>/dev/null; then
        mark_failed_retry "$issue" "$retries" "$log_file" "prompt-materialization"
        continue
      fi
    fi

    ISSUE_NUMBER="$issue" nohup bash "$SPAWN_CODEX" \
      --cd "$REPO_ROOT" \
      --prompt "$prompt_file" \
      --log "$log_file" \
      --stall 5400 >> "$REPO_ROOT/.refactor-loop/logs/triage-monitor.log" 2>&1 &
    spawn_pid=$!
    if [ "${TRIAGE_MONITOR_TEST_WAIT_SPAWN:-0}" = "1" ]; then
      wait "$spawn_pid" 2>/dev/null || true
    fi
    disown "$spawn_pid" 2>/dev/null || true
    if log_has_spawn_or_exit_marker "$log_file"; then
      set_state "$issue" "spawned" "$retries" 0 "$log_file"
      log "spawned: triage codex for issue #$issue (author=$author)"
    else
      log "claimed: triage issue #$issue waiting for spawn marker (pid=$spawn_pid)"
    fi
  done <<< "$issues"

  [ "${TRIAGE_MONITOR_ONCE:-0}" = "1" ] && exit 0
  sleep "$INTERVAL"
done
