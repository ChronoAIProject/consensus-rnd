#!/usr/bin/env bash
# triage-monitor.sh — daemon 60s 周期扫 auto-loop-triage label 并直接派 triage codex
#
# 设计:
# - 60s 周期 gh issue list --label "auto-loop-triage" --state open
# - 对每个未处理的 issue:
#   - state 存 .refactor-loop/triage-monitor-state.json(seen issue id)
#   - materialize prompt and spawn triage codex
# - 启动: nohup bash .claude/skills/codex-refactor-loop/scripts/triage-monitor.sh >> .refactor-loop/logs/triage-monitor.log 2>&1 & disown
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
STATE_FILE="$REPO_ROOT/.refactor-loop/triage-monitor-state.json"
PENDING_LOG="$REPO_ROOT/.refactor-loop/.controller-pending-events.log"

mkdir -p "$REPO_ROOT/.refactor-loop/prompts" "$REPO_ROOT/.refactor-loop/logs"
[ -f "$STATE_FILE" ] || echo "{}" > "$STATE_FILE"

log() {
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*"
}

log "triage-monitor started: interval=${INTERVAL}s"

while true; do
  # Query open issues with auto-loop-triage label
  issues=$(gh issue list "${gh_repo_args[@]}" --label "auto-loop-triage" --state open --json number,author --jq '.[] | "\(.number) \(.author.login)"' 2>/dev/null)
  if [ -z "$issues" ]; then
    sleep "$INTERVAL"
    continue
  fi

  while read -r issue author; do
    [ -z "$issue" ] && continue
    seen=$(jq -r --arg n "$issue" '.[$n] // "no"' "$STATE_FILE" 2>/dev/null)
    if [ "$seen" = "no" ]; then
      ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)
      # mark seen FIRST(防 spawn 失败后无限重派)
      tmp=$(mktemp)
      jq --arg n "$issue" --arg ts "$ts" '. + {($n): $ts}' "$STATE_FILE" > "$tmp" && mv "$tmp" "$STATE_FILE"
      # Materialize triage codex prompt(每 issue 一份)
      prompt_file="$REPO_ROOT/.refactor-loop/prompts/triage-issue-${issue}.md"
      ISSUE_NUMBER="$issue" COMMENT_AUTHOR="$author" \
        perl -pe 's/\Q${ISSUE_NUMBER}\E/$ENV{ISSUE_NUMBER}/g; s/Author: maintainer/Author: $ENV{COMMENT_AUTHOR}/g' \
          "$REPO_ROOT/.claude/skills/codex-refactor-loop/prompts/triage-external-issue.md" \
          > "$prompt_file" 2>/dev/null || cp "$REPO_ROOT/.claude/skills/codex-refactor-loop/prompts/triage-external-issue.md" "$prompt_file"
      # Spawn triage codex(nohup disown — daemon 自己派,不需 harness 跟踪;codex 自己 update GitHub)
      log_file="$REPO_ROOT/.refactor-loop/logs/triage-issue-${issue}.log"
      ISSUE_NUMBER="$issue" nohup bash "$REPO_ROOT/.claude/skills/codex-refactor-loop/scripts/spawn-codex.sh" \
        --cd "$REPO_ROOT" \
        --prompt "$prompt_file" \
        --log "$log_file" \
        --stall 5400 >> "$REPO_ROOT/.refactor-loop/logs/triage-monitor.log" 2>&1 &
      disown
      log "spawned: triage codex for issue #$issue (author=$author)"
    fi
  done <<< "$issues"

  sleep "$INTERVAL"
done
