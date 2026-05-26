#!/bin/bash
# test_codex_progress_reporter_orphan.sh
# Behavior tests for the orphan-delete-retry contract in codex-progress-reporter.sh.
# Verifies:
#   1. delete success on first attempt → state finished=true, comment_id=0.
#   2. delete fails AND comment still exists → state left alone (so next tick retries).
#   3. delete fails AND comment 404 → state finished=true, comment_id=0 (gone).
#   4. orphan from prior run (state finished=true + real cid) re-attempts delete next tick.
#
# Test seam: sources codex-progress-reporter.sh with TEST_NO_LOOP=1 inside an isolated
# tmp tree with stubbed gh + repo_slug.sh on PATH so no real network calls happen.

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
SOURCE_DAEMON="$SCRIPT_DIR/codex-progress-reporter.sh"

if [ ! -f "$SOURCE_DAEMON" ]; then
  echo "FATAL: cannot find $SOURCE_DAEMON" >&2
  exit 2
fi

setup_iso() {
  TMP=$(mktemp -d -t codex-progress-test.XXXXXX)
  mkdir -p "$TMP/repo/.refactor-loop/logs" "$TMP/repo/.refactor-loop/prompts" "$TMP/scripts" "$TMP/bin"
  cp "$SOURCE_DAEMON" "$TMP/scripts/codex-progress-reporter.sh"
  # Stub repo_slug.sh next to the daemon copy so $(dirname $0)/repo_slug.sh finds it.
  cat > "$TMP/scripts/repo_slug.sh" <<'SLUG'
resolve_github_repo_slug() { echo "fake/repo"; return 0; }
set_gh_repo_args() { gh_repo_args=(--repo "fake/repo"); return 0; }
SLUG
  # Stub gh: behavior controlled via GH_FAKE_* env vars set per-test.
  # Args we care about:
  #   gh api -X DELETE repos/.../comments/<cid>
  #   gh api -X PATCH  repos/.../comments/<cid>  (edit existing)
  #   gh api repos/.../comments/<cid>            (GET existence check)
  cat > "$TMP/bin/gh" <<'GH'
#!/bin/bash
if [ "$1" = "api" ] && [ "$2" = "-X" ]; then
  case "$3" in
    DELETE) exit "${GH_FAKE_DELETE_EXIT:-0}" ;;
    PATCH)  exit "${GH_FAKE_PATCH_EXIT:-0}"  ;;
  esac
elif [ "$1" = "api" ]; then
  # GET existence check
  exit "${GH_FAKE_GET_EXIT:-0}"
fi
exit 0
GH
  chmod +x "$TMP/bin/gh"
  export PATH="$TMP/bin:$PATH"
  export REPO_ROOT="$TMP/repo"
  export TEST_NO_LOOP=1
  STATE_FILE="$REPO_ROOT/.refactor-loop/codex-progress-state.json"
  LOG_FILE="$REPO_ROOT/.refactor-loop/logs/foo-test.log"
}

teardown_iso() {
  rm -rf "$TMP"
  unset GH_FAKE_DELETE_EXIT GH_FAKE_GET_EXIT GH_FAKE_PATCH_EXIT TEST_NO_LOOP
}

# Seed: codex log with EXIT=0; state pre-populated with an in-flight comment id.
seed_exit0_with_cid() {
  local cid="${1:-12345}" finished="${2:-false}"
  cat > "$LOG_FILE" <<EOF
some output line
SOLVER_DONE:fake:propose:nothing
EXIT=0
DONE_AT=2026-01-01T00:00:00Z
EOF
  printf '{"foo-test":{"target":"42","kind":"issue","comment_id":%s,"last_md5":"abc","finished":"%s"}}\n' "$cid" "$finished" > "$STATE_FILE"
}

# Run post_or_update in an isolated subshell so each test gets a clean source.
run_post_or_update() {
  ( cd "$REPO_ROOT" && source "$TMP/scripts/codex-progress-reporter.sh" && post_or_update "foo-test" "$LOG_FILE" )
}

assert_field() {
  local field="$1" expected="$2"
  local actual
  actual=$(jq -r ".[\"foo-test\"][\"$field\"]" "$STATE_FILE")
  if [ "$actual" != "$expected" ]; then
    echo "  FAIL: state[$field] expected=<$expected> actual=<$actual>" >&2
    return 1
  fi
  return 0
}

PASS=0
FAIL=0
record() {
  if [ "$1" = "pass" ]; then PASS=$((PASS+1)); echo "PASS $2"; else FAIL=$((FAIL+1)); echo "FAIL $2"; fi
}

# Test 1: delete success on first attempt — state should mark finished=true + cid=0.
test_delete_success_first_attempt() {
  setup_iso
  seed_exit0_with_cid 12345 false
  GH_FAKE_DELETE_EXIT=0 run_post_or_update >/dev/null 2>&1
  if assert_field "finished" "true" && assert_field "comment_id" "0"; then
    record pass test_delete_success_first_attempt
  else
    record fail test_delete_success_first_attempt
  fi
  teardown_iso
}

# Test 2: delete fails AND comment still exists (GET ok) — state must be left alone.
# This is the orphan-prevention contract: we must NOT mark finished=true if the
# comment is still on GitHub, because then we would never retry and the comment
# would be orphaned forever.
test_delete_fail_keeps_state_for_retry() {
  setup_iso
  seed_exit0_with_cid 12345 false
  GH_FAKE_DELETE_EXIT=1 GH_FAKE_GET_EXIT=0 run_post_or_update >/dev/null 2>&1
  # State should be unchanged: finished still "false", comment_id still 12345.
  if assert_field "finished" "false" && assert_field "comment_id" "12345"; then
    record pass test_delete_fail_keeps_state_for_retry
  else
    record fail test_delete_fail_keeps_state_for_retry
  fi
  teardown_iso
}

# Test 3: delete fails BUT GET also fails (comment is 404) — mark finished + gone.
test_delete_fail_but_404_marks_gone() {
  setup_iso
  seed_exit0_with_cid 12345 false
  GH_FAKE_DELETE_EXIT=1 GH_FAKE_GET_EXIT=1 run_post_or_update >/dev/null 2>&1
  if assert_field "finished" "true" && assert_field "comment_id" "0"; then
    record pass test_delete_fail_but_404_marks_gone
  else
    record fail test_delete_fail_but_404_marks_gone
  fi
  teardown_iso
}

# Test 4: orphan from prior buggy run (state already finished=true + real cid).
# The new skip-condition must NOT short-circuit; it must fall through to delete-retry.
# This is the regression test for the bug that produced 30 orphan comments on issue #69.
test_orphan_state_retried_on_next_tick() {
  setup_iso
  # Pre-state: finished=true + real cid (orphan condition).
  seed_exit0_with_cid 12345 true
  GH_FAKE_DELETE_EXIT=0 run_post_or_update >/dev/null 2>&1
  # After: cid should be zeroed (delete confirmed this tick).
  if assert_field "finished" "true" && assert_field "comment_id" "0"; then
    record pass test_orphan_state_retried_on_next_tick
  else
    record fail test_orphan_state_retried_on_next_tick
  fi
  teardown_iso
}

test_delete_success_first_attempt
test_delete_fail_keeps_state_for_retry
test_delete_fail_but_404_marks_gone
test_orphan_state_retried_on_next_tick

echo "---"
echo "Total: $((PASS+FAIL))  Pass: $PASS  Fail: $FAIL"
[ "$FAIL" -eq 0 ] && exit 0 || exit 1
