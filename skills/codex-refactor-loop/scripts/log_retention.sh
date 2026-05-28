#!/usr/bin/env bash
# Refactor (iter326/issue-122):
#   Old pattern: .refactor-loop/logs/ and runs/ grew without bounds, slowing daemon scans and bloating .refactor-loop/.
#   New principle: daemonless 24h log_retention.sh under restart-daemons; direct rm only, no archive/index/new daemon
#   (Phase 9 r1 consensus:structural)

set -u

RETENTION_TTL_HOURS=24

if [ -f ".refactor-loop/host.env" ]; then
  # shellcheck disable=SC1091
  source ".refactor-loop/host.env"
fi

if [ -z "${REPO_ROOT:-}" ]; then
  echo "FATAL: REPO_ROOT is unset; cd to host repo and/or source .refactor-loop/host.env" >&2
  exit 2
fi

if ! REPO_ROOT_REAL="$(cd "$REPO_ROOT" 2>/dev/null && pwd -P)"; then
  echo "FATAL: REPO_ROOT is not a readable directory: $REPO_ROOT" >&2
  exit 2
fi

LOG_DIR="$REPO_ROOT_REAL/.refactor-loop/logs"
case "$LOG_DIR" in
  "$REPO_ROOT_REAL/.refactor-loop/logs") ;;
  *)
    echo "FATAL: log retention target escaped .refactor-loop/logs: $LOG_DIR" >&2
    exit 2
    ;;
esac

if [ ! -d "$LOG_DIR" ]; then
  echo "log_retention: ttl_hours=$RETENTION_TTL_HOURS deleted=0 kept=0 target=$LOG_DIR missing=true"
  exit 0
fi

file_mtime_epoch() {
  local path="$1"
  # Fix (remote-ci/contract-tests): GNU stat accepts -f for filesystem format, so prefer -c for Linux mtime.
  stat -c %Y "$path" 2>/dev/null || stat -f %m "$path" 2>/dev/null
}

now="$(date +%s)"
cutoff=$((now - RETENTION_TTL_HOURS * 60 * 60))
deleted=0
kept=0

for path in "$LOG_DIR"/*.log; do
  [ -e "$path" ] || continue
  if [ -L "$path" ] || [ ! -f "$path" ]; then
    kept=$((kept + 1))
    continue
  fi
  mtime="$(file_mtime_epoch "$path" || true)"
  case "$mtime" in
    ''|*[!0-9]*)
      kept=$((kept + 1))
      continue
      ;;
  esac
  if [ "$mtime" -lt "$cutoff" ]; then
    rm -f -- "$path"
    deleted=$((deleted + 1))
  else
    kept=$((kept + 1))
  fi
done

echo "log_retention: ttl_hours=$RETENTION_TTL_HOURS deleted=$deleted kept=$kept target=$LOG_DIR"
