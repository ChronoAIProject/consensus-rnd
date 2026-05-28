#!/usr/bin/env bash
# Refactor (iter4/issue49-r3-consensus):
#   Old pattern: one-off /tmp/restart-consensus-rnd-daemons.sh manual script with host-specific paths that disappeared between sessions.
#   New principle: checked-in helper, $REPO_ROOT-relative, idempotent + heartbeat-fresh skip, cron/launchd runnable;
#     controller wakeup checks stale daemon heartbeats and invokes this helper(per #49 r3 META_JUDGE_DONE:consensus:A-cron-only-with-pending-event-alert).
#
# Maintains singleton + heartbeat wrappers for the six long-running daemons.
# This helper has no lifecycle authority and does not alter repository or
# issue/PR state.

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
SKILL_ROOT="$(cd "$SCRIPT_DIR/.." && pwd -P)"
HEARTBEAT_FRESH_SECONDS="${RESTART_DAEMONS_HEARTBEAT_FRESH_SECONDS:-90}"
HEARTBEAT_INTERVAL="${RESTART_DAEMONS_HEARTBEAT_INTERVAL:-30}"
STOP_GRACE_SECONDS="${RESTART_DAEMONS_STOP_GRACE_SECONDS:-5}"

if [ -f ".refactor-loop/host.env" ]; then
  # shellcheck disable=SC1091
  source ".refactor-loop/host.env"
fi

if [ -z "${REPO_ROOT:-}" ]; then
  echo "FATAL: REPO_ROOT is unset; cd to host repo and/or source .refactor-loop/host.env" >&2
  exit 2
fi

cd "$REPO_ROOT" || exit 2
mkdir -p .refactor-loop/locks .refactor-loop/heartbeats .refactor-loop/logs

RESTART_LOCK_DIR="$REPO_ROOT/.refactor-loop/locks/restart-daemons.lock"

log() {
  printf '[%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"
}

pid_alive() {
  local pid="$1"
  case "$pid" in
    ''|*[!0-9]*) return 1 ;;
  esac
  kill -0 "$pid" 2>/dev/null
}

release_restart_lock() {
  if [ -d "$RESTART_LOCK_DIR" ] && [ "$(cat "$RESTART_LOCK_DIR/pid" 2>/dev/null || true)" = "$$" ]; then
    rm -f "$RESTART_LOCK_DIR/pid"
    rmdir "$RESTART_LOCK_DIR" 2>/dev/null || true
  fi
}

acquire_restart_lock() {
  local attempts=0 holder=""
  while ! mkdir "$RESTART_LOCK_DIR" 2>/dev/null; do
    holder="$(cat "$RESTART_LOCK_DIR/pid" 2>/dev/null || true)"
    if ! pid_alive "$holder"; then
      rm -f "$RESTART_LOCK_DIR/pid" 2>/dev/null || true
      rmdir "$RESTART_LOCK_DIR" 2>/dev/null || true
      continue
    fi
    attempts=$((attempts + 1))
    if [ "$attempts" -ge 30 ]; then
      echo "FATAL: restart-daemons lock held too long by pid=$holder" >&2
      exit 3
    fi
    sleep 1
  done
  printf '%s\n' "$$" > "$RESTART_LOCK_DIR/pid"
  trap release_restart_lock EXIT INT TERM
}

heartbeat_is_fresh() {
  local name="$1" hb="$REPO_ROOT/.refactor-loop/heartbeats/${name}.ts"
  local now ts age
  [ -f "$hb" ] || return 1
  ts="$(cat "$hb" 2>/dev/null || true)"
  case "$ts" in
    ''|*[!0-9]*) return 1 ;;
  esac
  now="$(date +%s)"
  age=$((now - ts))
  [ "$age" -ge 0 ] && [ "$age" -lt "$HEARTBEAT_FRESH_SECONDS" ]
}

singleton_check_fresh() {
  local name="$1" pid_file="$REPO_ROOT/.refactor-loop/locks/${name}.pid"
  local pid
  [ -f "$pid_file" ] || return 1
  pid="$(cat "$pid_file" 2>/dev/null || true)"
  pid_alive "$pid" && heartbeat_is_fresh "$name"
}

stop_existing_daemon() {
  local name="$1" pid_file="$REPO_ROOT/.refactor-loop/locks/${name}.pid"
  local pid waited=0
  [ -f "$pid_file" ] || return 0
  pid="$(cat "$pid_file" 2>/dev/null || true)"
  if pid_alive "$pid"; then
    kill "$pid" 2>/dev/null || true
    while pid_alive "$pid" && [ "$waited" -lt "$STOP_GRACE_SECONDS" ]; do
      sleep 1
      waited=$((waited + 1))
    done
    if pid_alive "$pid"; then
      kill -KILL "$pid" 2>/dev/null || true
    fi
  fi
  rm -f "$pid_file"
}

start_daemon() {
  local name="$1" cmd="$2"
  local pid_file="$REPO_ROOT/.refactor-loop/locks/${name}.pid"
  local hb_file="$REPO_ROOT/.refactor-loop/heartbeats/${name}.ts"
  local log_file="$REPO_ROOT/.refactor-loop/logs/${name}.log"

  if singleton_check_fresh "$name"; then
    log "$name skip: alive pid=$(cat "$pid_file") heartbeat=fresh"
    return 0
  fi

  stop_existing_daemon "$name"

  nohup bash -c '
    set -u
    name="$1"
    repo_root="$2"
    hb_interval="$3"
    cmd="$4"
    pid_file="$repo_root/.refactor-loop/locks/${name}.pid"
    hb_file="$repo_root/.refactor-loop/heartbeats/${name}.ts"
    died_file="$repo_root/.refactor-loop/logs/${name}.died"
    child_pid=""
    hb_pid=""

    cleanup() {
      local ec=$?
      if [ -n "$child_pid" ]; then
        kill "$child_pid" 2>/dev/null || true
        wait "$child_pid" 2>/dev/null || true
      fi
      if [ -n "$hb_pid" ]; then
        kill "$hb_pid" 2>/dev/null || true
        wait "$hb_pid" 2>/dev/null || true
      fi
      if [ "$(cat "$pid_file" 2>/dev/null || true)" = "$$" ]; then
        rm -f "$pid_file"
      fi
      printf "daemon %s wrapper exited at %s (exit=%s)\n" "$name" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$ec" >> "$died_file"
    }
    terminate() {
      exit 143
    }
    trap cleanup EXIT
    trap terminate INT TERM

    printf "%s\n" "$$" > "$pid_file"
    (
      while true; do
        hb_tmp="${hb_file}.$$"
        date -u +%s > "$hb_tmp"
        mv "$hb_tmp" "$hb_file"
        sleep "$hb_interval"
      done
    ) &
    hb_pid=$!

    # shellcheck disable=SC1091
    source "$repo_root/.refactor-loop/host.env"
    cd "$repo_root" || exit 2
    export RESTART_DAEMON_NAME="$name"
    bash -c "$cmd" &
    child_pid=$!
    wait "$child_pid"
  ' _ "$name" "$REPO_ROOT" "$HEARTBEAT_INTERVAL" "$cmd" >> "$log_file" 2>&1 &

  local wrapper_pid=$! i
  for i in $(seq 1 50); do
    if [ "$(cat "$pid_file" 2>/dev/null || true)" = "$wrapper_pid" ] && [ -f "$hb_file" ]; then
      break
    fi
    sleep 0.1
  done
  log "$name restarted: wrapper_pid=$wrapper_pid heartbeat=$hb_file"
}

acquire_restart_lock

start_daemon "concurrency_monitor" "python3 '$SKILL_ROOT/scripts/concurrency_monitor.py'"
start_daemon "comment-monitor" "bash '$SKILL_ROOT/scripts/comment-monitor.sh'"
start_daemon "codex-progress-reporter" "INTERVAL=600 bash '$SKILL_ROOT/scripts/codex-progress-reporter.sh'"
start_daemon "dev_sync_daemon" "python3 '$SKILL_ROOT/scripts/dev_sync_daemon.py'"
start_daemon "phase9_router_daemon" "python3 '$SKILL_ROOT/scripts/phase9_router_daemon.py' --daemon --repo-root \"\$REPO_ROOT\""
