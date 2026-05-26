#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
STATUSLINE="${SCRIPT_DIR}/statusline.sh"
TMP_DIR=""

cleanup() {
  if [ -n "${TMP_DIR:-}" ]; then
    rm -rf "$TMP_DIR"
  fi
}
trap cleanup EXIT

setup_repo() {
  TMP_DIR="$(mktemp -d)"
  mkdir -p "$TMP_DIR/.refactor-loop/state"
}

write_snapshot() {
  cat > "$TMP_DIR/.refactor-loop/state/statusline-snapshot.json"
}

assert_contains() {
  local haystack="$1"
  local needle="$2"
  local label="$3"
  if [[ "$haystack" != *"$needle"* ]]; then
    echo "FAIL ${label}: expected output to contain ${needle}; got: ${haystack}" >&2
    exit 1
  fi
}

test_statusline_runs_under_200ms() {
  setup_repo
  write_snapshot <<'JSON'
{"actual":7,"expected":5,"floor":4,"p0_streak":0,"open_pr_count":5,"open_issue_count":4,"freeze_minutes":0}
JSON
  local start end elapsed output
  start="$(python3 - <<'PY'
import time
print(time.monotonic_ns())
PY
)"
  output="$(REPO_ROOT="$TMP_DIR" bash "$STATUSLINE")"
  end="$(python3 - <<'PY'
import time
print(time.monotonic_ns())
PY
)"
  elapsed=$(( (end - start) / 1000000 ))
  if [ "$elapsed" -ge 200 ]; then
    echo "FAIL test_statusline_runs_under_200ms: elapsed=${elapsed}ms output=${output}" >&2
    exit 1
  fi
}

test_no_snapshot_returns_placeholder() {
  setup_repo
  local output
  output="$(REPO_ROOT="$TMP_DIR" bash "$STATUSLINE")"
  if [ "$output" != "⏸ no-snapshot" ]; then
    echo "FAIL test_no_snapshot_returns_placeholder: ${output}" >&2
    exit 1
  fi
}

test_healthy_state_shows_actual_floor() {
  setup_repo
  write_snapshot <<'JSON'
{"actual":7,"expected":5,"floor":4,"p0_streak":0,"open_pr_count":5,"open_issue_count":4,"freeze_minutes":0}
JSON
  assert_contains "$(REPO_ROOT="$TMP_DIR" bash "$STATUSLINE")" "7/4" "test_healthy_state_shows_actual_floor"
}

test_below_floor_shows_warning_icon() {
  setup_repo
  write_snapshot <<'JSON'
{"actual":2,"expected":5,"floor":4,"p0_streak":0,"open_pr_count":5,"open_issue_count":4,"freeze_minutes":0}
JSON
  assert_contains "$(REPO_ROOT="$TMP_DIR" bash "$STATUSLINE")" "⚠" "test_below_floor_shows_warning_icon"
}

test_freeze_minutes_over_10_shows_stuck() {
  setup_repo
  write_snapshot <<'JSON'
{"actual":7,"expected":5,"floor":4,"p0_streak":0,"open_pr_count":5,"open_issue_count":4,"freeze_minutes":15}
JSON
  assert_contains "$(REPO_ROOT="$TMP_DIR" bash "$STATUSLINE")" "[STUCK 15m]" "test_freeze_minutes_over_10_shows_stuck"
}

test_p0_streak_over_2_shows_highlight() {
  setup_repo
  write_snapshot <<'JSON'
{"actual":7,"expected":5,"floor":4,"p0_streak":3,"open_pr_count":5,"open_issue_count":4,"freeze_minutes":0}
JSON
  assert_contains "$(REPO_ROOT="$TMP_DIR" bash "$STATUSLINE")" "P0×3" "test_p0_streak_over_2_shows_highlight"
}

test_statusline_runs_under_200ms
test_no_snapshot_returns_placeholder
test_healthy_state_shows_actual_floor
test_below_floor_shows_warning_icon
test_freeze_minutes_over_10_shows_stuck
test_p0_streak_over_2_shows_highlight

echo "statusline tests ok"
