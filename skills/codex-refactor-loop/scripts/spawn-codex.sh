#!/usr/bin/env bash
# spawn-codex.sh — standardized `codex exec` wrapper for the codex-refactor-loop skill.
#
# Pattern (intended use): invoke via Bash with run_in_background: true so the harness
# tracks completion and wakes the controller on exit.
#
# Usage:
#   spawn-codex.sh --cd <dir> --prompt <prompt-file> --log <log-file> --stall <seconds>
#                  [--model <model>] [--add-dir <dir>] [--prompt-text "..."]
#
# Required flags: --cd, --prompt OR --prompt-text, --log, --stall.
#
# File-based contract (mandatory):
#   - Prompt is read from --prompt <file>, OR --prompt-text "..." writes a /tmp temp file.
#   - Output is written to --log <file>. Caller chooses path (typically .refactor-loop/logs/).
#   - At start, this wrapper prints `SPAWN: prompt=<path> log=<path> cd=<dir> stall=<s>` to stderr
#     so callers / `tail` see exact paths immediately. At end, prints `DONE: log=<path> exit=<N>`.
#   - Debug recipe: `cat <prompt-path>` to see what codex got; `cat <log-path>` to see what it did.
#
# Forbidden:
#   - Passing prompt content via argv (would hit shell length limit + obscure debug).
#   - Sending codex output to /dev/null or stdout-only (loses debug trail).
#   - Treating --stall or the legacy --timeout alias as a wall-clock ceiling.

set -euo pipefail

CD=""
PROMPT=""
PROMPT_TEXT=""
LOG=""
STALL=""
MODEL=""
ADD_DIRS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --cd)          CD="$2"; shift 2;;
    --prompt)      PROMPT="$2"; shift 2;;
    --prompt-text) PROMPT_TEXT="$2"; shift 2;;
    --log)         LOG="$2"; shift 2;;
    --timeout)     STALL="$2"; shift 2;;
    --stall)       STALL="$2"; shift 2;;
    --model)       MODEL="$2"; shift 2;;
    --add-dir)     ADD_DIRS+=("$2"); shift 2;;
    *)             echo "unknown flag: $1" >&2; exit 2;;
  esac
done

# If --prompt-text is given, materialize a temp prompt file so codex still reads from a file.
# Caller can find the file via the SPAWN: stderr banner if they want to debug.
if [[ -n "$PROMPT_TEXT" && -z "$PROMPT" ]]; then
  # macOS mktemp only substitutes trailing Xs (BSD); Linux GNU substitutes anywhere.
  # Use trailing-X pattern for cross-platform; codex doesn't care about file extension.
  PROMPT=$(mktemp /tmp/codex-prompt.XXXXXXXX)
  printf '%s\n' "$PROMPT_TEXT" > "$PROMPT"
elif [[ -n "$PROMPT_TEXT" && -n "$PROMPT" ]]; then
  echo "pass either --prompt OR --prompt-text, not both" >&2
  exit 2
fi

for var in CD PROMPT LOG STALL; do
  if [[ -z "${!var}" ]]; then
    flag=$(printf '%s' "$var" | tr '[:upper:]' '[:lower:]')
    echo "missing required flag --${flag} (use --prompt <file> or --prompt-text \"...\" for prompt; --timeout is a legacy alias for --stall)" >&2
    exit 2
  fi
done

if [[ ! -f "$PROMPT" ]]; then
  echo "prompt file not found: $PROMPT" >&2
  exit 2
fi

if ! [[ "$STALL" =~ ^[0-9]+$ ]] || (( STALL <= 0 )); then
  echo "codex stall window must be a positive integer number of seconds: $STALL" >&2
  exit 2
fi

mkdir -p "$(dirname "$LOG")"
# Refactor (iter3/skill-hygiene-scripts):
#   Old: the wrapper truncated --log before checking reuse or in-flight state.
#   New principle: unfinished existing logs are refused before truncation.
#   This preserves SPAWN/EXIT marker evidence for controller recovery.
if [[ -e "$LOG" ]]; then
  if ! tail -5 "$LOG" 2>/dev/null | grep -q '^EXIT='; then
    echo "refusing to reuse unfinished log without EXIT=: $LOG" >&2
    exit 3
  fi
fi
: > "$LOG"

if [[ -z "${REPO_ROOT:-}" ]]; then
  REPO_ROOT="$(cd "$CD" && git rev-parse --show-toplevel 2>/dev/null || true)"
fi
if [[ -z "${REPO_ROOT:-}" ]]; then
  echo "REPO_ROOT is unset and could not be derived from --cd: $CD" >&2
  exit 2
fi

# Debug banner — caller / `tail` sees exact paths immediately.
# Both prompt + log are real files on disk; debug by `cat <prompt-path>` and `cat <log-path>`.
BANNER="SPAWN: prompt=$PROMPT log=$LOG cd=$CD stall=${STALL}s${MODEL:+ model=$MODEL}"
echo "$BANNER" >&2
echo "$BANNER" >> "$LOG"

# Refactor (iter4/issue52-r1):
#   Old pattern: monitor 用 ps grep | grep REPO_ROOT 计 in-flight codex(false-positive 跨 host project)
#   New principle: spawn-codex.sh 自维护 PID registry,monitor / router 读 .refactor-loop/spawned/*.pid 校验,本仓库脚本闭环
SPAWNED_DIR="${REPO_ROOT}/.refactor-loop/spawned"
mkdir -p "$SPAWNED_DIR"
TASK_ID="$(basename "$LOG" .log)"
REG_FILE="$SPAWNED_DIR/${TASK_ID}.pid"
ABS_LOG="$(cd "$(dirname "$LOG")" && pwd -P)/$(basename "$LOG")"
ABS_REPO_ROOT="$(cd "$REPO_ROOT" && pwd -P)"

cleanup_spawned() {
  rm -f "$REG_FILE"
}
trap cleanup_spawned EXIT INT TERM

write_spawned_registry() {
  local child_pid="$1"
  {
    echo "pid=$child_pid"
    echo "repo_root=$ABS_REPO_ROOT"
    echo "log=$ABS_LOG"
    echo "cmdline=spawn-codex.sh --cd $CD --prompt $PROMPT --log $LOG --stall $STALL"
    echo "started=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  } > "$REG_FILE"
}

# Standard args:
# - --dangerously-bypass-approvals-and-sandbox: required for unattended mode (caller-supplied authorization).
# - --skip-git-repo-check: worktrees count as repos, but defensive.
# - -C: working directory (the worktree for implement/verify; trunk for audit).
ARGS=(
  exec
  --dangerously-bypass-approvals-and-sandbox
  --skip-git-repo-check
  -C "$CD"
)

for d in "${ADD_DIRS[@]+"${ADD_DIRS[@]}"}"; do
  ARGS+=(--add-dir "$d")
done

if [[ -n "$MODEL" ]]; then
  ARGS+=(-m "$MODEL")
fi

# Read prompt from stdin via the `-` placeholder so very long prompts don't hit argv limits.
ARGS+=(-)

log_size() {
  stat -f %z "$LOG" 2>/dev/null || stat -c %s "$LOG" 2>/dev/null || echo 0
}

kill_process_group() {
  local pid="$1"
  kill -KILL "-$pid" 2>/dev/null || kill -KILL "$pid" 2>/dev/null || true
}

# Supervise liveness, not total runtime. Codex may run indefinitely while it keeps
# producing output; only a silent stall window kills the whole process group.
set +e
if command -v setsid >/dev/null 2>&1; then
  setsid codex "${ARGS[@]}" < "$PROMPT" >> "$LOG" 2>&1 &
elif command -v perl >/dev/null 2>&1; then
  perl -MPOSIX=setsid -e 'setsid() or die "setsid failed: $!"; exec @ARGV or die "exec failed: $!"' \
    codex "${ARGS[@]}" < "$PROMPT" >> "$LOG" 2>&1 &
else
  echo "setsid unavailable: install setsid(1) or perl with POSIX::setsid" >&2
  exit 127
fi
CHILD=$!
write_spawned_registry "$CHILD"
LAST_SIZE=$(log_size)
LAST_OUTPUT_AT=$(date +%s)
STALLED=0
CLEANUP_CHILD=1

cleanup_child() {
  if (( CLEANUP_CHILD == 1 )) && kill -0 "$CHILD" 2>/dev/null; then
    kill_process_group "$CHILD"
  fi
  cleanup_spawned
}
terminate_child() {
  local status="$1"
  cleanup_child
  exit "$status"
}
trap cleanup_child EXIT
trap 'terminate_child 130' INT
trap 'terminate_child 143' TERM
trap 'terminate_child 129' HUP

while kill -0 "$CHILD" 2>/dev/null; do
  sleep 1
  NOW_SIZE=$(log_size)
  if [[ "$NOW_SIZE" != "$LAST_SIZE" ]]; then
    LAST_SIZE="$NOW_SIZE"
    LAST_OUTPUT_AT=$(date +%s)
    continue
  fi

  NOW=$(date +%s)
  if (( NOW - LAST_OUTPUT_AT >= STALL )); then
    {
      echo "STALL_KILL_AFTER=${STALL}s"
      echo "STALL_KILL_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    } >> "$LOG"
    kill_process_group "$CHILD"
    STALLED=1
    break
  fi
done

wait "$CHILD" 2>/dev/null
EXIT=$?
CLEANUP_CHILD=0
cleanup_spawned
trap - INT TERM HUP EXIT
set -e

if (( STALLED == 1 )) && (( EXIT == 0 )); then
  EXIT=137
fi

{
  echo "EXIT=$EXIT"
  echo "DONE_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
} >> "$LOG"

echo "DONE: log=$LOG exit=$EXIT prompt=$PROMPT" >&2

exit "$EXIT"
