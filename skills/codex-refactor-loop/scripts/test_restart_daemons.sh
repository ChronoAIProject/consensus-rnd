#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
HELPER="$SCRIPT_DIR/restart-daemons.sh"

TMP_ROOT=""

cleanup() {
  if [ -n "$TMP_ROOT" ] && [ -d "$TMP_ROOT" ]; then
    if [ -d "$TMP_ROOT/repo/.refactor-loop/locks" ]; then
      for pid_file in "$TMP_ROOT"/repo/.refactor-loop/locks/*.pid; do
        [ -f "$pid_file" ] || continue
        pid="$(cat "$pid_file" 2>/dev/null || true)"
        case "$pid" in
          ''|*[!0-9]*) ;;
          *) kill "$pid" 2>/dev/null || true ;;
        esac
      done
    fi
    rm -rf "$TMP_ROOT"
  fi
}
trap cleanup EXIT

assert_eq() {
  local expected="$1" actual="$2" message="$3"
  if [ "$expected" != "$actual" ]; then
    echo "FAIL: $message: expected '$expected', got '$actual'" >&2
    exit 1
  fi
}

assert_ge() {
  local actual="$1" minimum="$2" message="$3"
  if [ "$actual" -lt "$minimum" ]; then
    echo "FAIL: $message: expected >= $minimum, got $actual" >&2
    exit 1
  fi
}

new_fixture() {
  TMP_ROOT="$(mktemp -d)"
  REPO="$TMP_ROOT/repo"
  SKILL="$TMP_ROOT/skill"
  mkdir -p "$REPO/.refactor-loop/logs" "$REPO/.refactor-loop/locks" "$REPO/.refactor-loop/heartbeats" "$SKILL/scripts"
  cp "$HELPER" "$SKILL/scripts/restart-daemons.sh"
  chmod +x "$SKILL/scripts/restart-daemons.sh"
  cat > "$REPO/.refactor-loop/host.env" <<EOF
export REPO_ROOT="$REPO"
export GH_REPO_SLUG="example/repo"
export MAINTAINER_WHITELIST="maintainer"
EOF
  for script in concurrency_monitor.py dev_sync_daemon.py; do
    cat > "$SKILL/scripts/$script" <<'EOF'
#!/usr/bin/env python3
import os
import signal
import sys
import time
from pathlib import Path

repo = Path(os.environ["REPO_ROOT"])
name = os.environ.get("RESTART_DAEMON_NAME", Path(sys.argv[0]).stem)
with (repo / ".refactor-loop" / "logs" / f"{name}.starts").open("a", encoding="utf-8") as fh:
    fh.write(f"{os.getpid()}\n")

running = True
def stop(_signum, _frame):
    global running
    running = False
signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)

while running:
    time.sleep(0.1)
EOF
    chmod +x "$SKILL/scripts/$script"
  done
  for script in comment-monitor.sh codex-progress-reporter.sh triage-monitor.sh; do
    cat > "$SKILL/scripts/$script" <<'EOF'
#!/usr/bin/env bash
set -u
echo "$$" >> "$REPO_ROOT/.refactor-loop/logs/${RESTART_DAEMON_NAME}.starts"
trap 'exit 0' TERM INT
while true; do sleep 1; done
EOF
    chmod +x "$SKILL/scripts/$script"
  done
}

run_helper() {
  (
    cd "$REPO"
    unset REPO_ROOT
    RESTART_DAEMONS_HEARTBEAT_FRESH_SECONDS=2 \
    RESTART_DAEMONS_HEARTBEAT_INTERVAL=1 \
    RESTART_DAEMONS_STOP_GRACE_SECONDS=1 \
      bash "$SKILL/scripts/restart-daemons.sh"
  )
}

wait_for_starts() {
  local name="$1" expected="$2" i
  for i in $(seq 1 50); do
    count="$(count_starts "$name")"
    [ "$count" -ge "$expected" ] && return 0
    sleep 0.1
  done
  echo "FAIL: timed out waiting for $name starts >= $expected" >&2
  exit 1
}

count_starts() {
  local name="$1"
  if [ ! -f "$REPO/.refactor-loop/logs/${name}.starts" ]; then
    echo 0
    return
  fi
  awk 'END { print NR + 0 }' "$REPO/.refactor-loop/logs/${name}.starts"
}

test_idempotent_when_daemon_fresh() {
  new_fixture
  run_helper >/dev/null
  wait_for_starts "concurrency_monitor" 1
  run_helper >/dev/null
  assert_eq "1" "$(count_starts concurrency_monitor)" "fresh heartbeat should skip restart"
  cleanup
  TMP_ROOT=""
}

test_restarts_when_heartbeat_stale() {
  new_fixture
  run_helper >/dev/null
  wait_for_starts "comment-monitor" 1
  old_ts=$(( $(date +%s) - 20 ))
  printf '%s\n' "$old_ts" > "$REPO/.refactor-loop/heartbeats/comment-monitor.ts"
  run_helper >/dev/null
  wait_for_starts "comment-monitor" 2
  assert_eq "2" "$(count_starts comment-monitor)" "stale heartbeat should restart"
  cleanup
  TMP_ROOT=""
}

test_restarts_when_pid_dead() {
  new_fixture
  mkdir -p "$REPO/.refactor-loop/locks" "$REPO/.refactor-loop/heartbeats"
  printf '999999\n' > "$REPO/.refactor-loop/locks/dev_sync_daemon.pid"
  printf '%s\n' "$(date +%s)" > "$REPO/.refactor-loop/heartbeats/dev_sync_daemon.ts"
  run_helper >/dev/null
  wait_for_starts "dev_sync_daemon" 1
  pid="$(cat "$REPO/.refactor-loop/locks/dev_sync_daemon.pid")"
  if ! kill -0 "$pid" 2>/dev/null; then
    echo "FAIL: replacement dev_sync_daemon wrapper is not alive" >&2
    exit 1
  fi
  cleanup
  TMP_ROOT=""
}

test_no_double_spawn_under_race() {
  new_fixture
  (
    run_helper >/tmp/restart-daemons-race-1.log 2>&1
  ) &
  p1=$!
  (
    run_helper >/tmp/restart-daemons-race-2.log 2>&1
  ) &
  p2=$!
  wait "$p1"
  wait "$p2"
  wait_for_starts "triage-monitor" 1
  sleep 0.5
  assert_eq "1" "$(count_starts triage-monitor)" "parallel helpers should not double-spawn"
  assert_eq "1" "$(count_starts concurrency_monitor)" "parallel helpers should not double-spawn concurrency monitor"
  cleanup
  TMP_ROOT=""
}

test_idempotent_when_daemon_fresh
test_restarts_when_heartbeat_stale
test_restarts_when_pid_dead
test_no_double_spawn_under_race

echo "PASS: test_restart_daemons.sh"
