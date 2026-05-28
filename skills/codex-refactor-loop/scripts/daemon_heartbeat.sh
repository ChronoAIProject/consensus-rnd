#!/usr/bin/env bash
# Refactor (iter1/issue-143):
#   Old pattern: restart-daemons.sh wrapper sidecar wrote heartbeat while actor loop could hang.
#   New principle: shell daemon actor owns heartbeat beat + lease sleep; no spawned heartbeat sidecar or lifecycle authority.
#   Keeps .refactor-loop/heartbeats/<daemon>.ts integer epoch, 90s stale consumers, statusline, and auto-release compatibility.
#   Refactor helper, no behavior change outside heartbeat ownership.

daemon_heartbeat_file() {
  local name="${RESTART_DAEMON_NAME:-$(basename "$0")}"
  if [ -n "${RESTART_DAEMON_HEARTBEAT_FILE:-}" ]; then
    printf '%s\n' "$RESTART_DAEMON_HEARTBEAT_FILE"
    return 0
  fi
  if [ -z "${REPO_ROOT:-}" ]; then
    echo "FATAL: REPO_ROOT is unset for daemon heartbeat" >&2
    return 2
  fi
  printf '%s/.refactor-loop/heartbeats/%s.ts\n' "$REPO_ROOT" "$name"
}

daemon_heartbeat_beat() {
  local hb_file hb_dir hb_tmp
  hb_file="$(daemon_heartbeat_file)" || return $?
  hb_dir="$(dirname "$hb_file")"
  mkdir -p "$hb_dir"
  hb_tmp="$hb_dir/.heartbeat.$$.tmp"
  date -u +%s > "$hb_tmp"
  mv "$hb_tmp" "$hb_file"
}

daemon_heartbeat_sleep() {
  local remaining="$1"
  local interval="${RESTART_DAEMON_HEARTBEAT_INTERVAL:-30}"
  local chunk
  while [ "$remaining" -gt 0 ]; do
    if [ "$remaining" -lt "$interval" ]; then
      chunk="$remaining"
    else
      chunk="$interval"
    fi
    sleep "$chunk"
    daemon_heartbeat_beat || return $?
    remaining=$((remaining - chunk))
  done
}
