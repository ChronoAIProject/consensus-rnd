#!/usr/bin/env bash
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONTROLLER_LIB="${SCRIPT_DIR}/controller_lib.sh"
TEST_TMP=""
TEST_REPO_ROOT=""
FAKE_GH_LOG=""
HELPER_OUTPUT=""

cleanup() {
  if [ -n "${TEST_TMP:-}" ] && [ -d "$TEST_TMP" ]; then
    rm -rf "$TEST_TMP"
  fi
}
trap cleanup EXIT

fail() {
  echo "not ok - $1" >&2
  if [ -n "${HELPER_OUTPUT:-}" ] && [ -f "$HELPER_OUTPUT" ]; then
    echo "--- helper output ---" >&2
    sed -n '1,120p' "$HELPER_OUTPUT" >&2
  fi
  if [ -n "${FAKE_GH_LOG:-}" ] && [ -f "$FAKE_GH_LOG" ]; then
    echo "--- fake gh log ---" >&2
    sed -n '1,120p' "$FAKE_GH_LOG" >&2
  fi
  exit 1
}

setup_tmpdir() {
  cleanup
  TEST_TMP="$(mktemp -d)"
  TEST_REPO_ROOT="${TEST_TMP}/repo"
  FAKE_GH_LOG="${TEST_TMP}/gh.log"
  HELPER_OUTPUT="${TEST_TMP}/helper.out"
  mkdir -p "${TEST_REPO_ROOT}/.refactor-loop/runs/maintainer-directives" "${TEST_TMP}/bin"
  cp "$CONTROLLER_LIB" "${TEST_TMP}/controller_lib.sh"
  cat > "${TEST_TMP}/bin/gh" <<'EOF'
#!/bin/bash
echo "$@" >> "$FAKE_GH_LOG"
EOF
  chmod +x "${TEST_TMP}/bin/gh"
}

run_helper() {
  local item="$1" topic="${2:-}"
  PATH="${TEST_TMP}/bin:${PATH}" \
    REPO_ROOT="$TEST_REPO_ROOT" \
    GH_REPO_SLUG="test-owner/test-repo" \
    GH_OWNER="" \
    GH_REPO_NAME="" \
    GH_REPO="" \
    FAKE_GH_LOG="$FAKE_GH_LOG" \
    bash -c 'source "$1"; apply_human_label_or_skip "$2" "$3"' \
    bash "${TEST_TMP}/controller_lib.sh" "$item" "$topic" >"$HELPER_OUTPUT" 2>&1
}

assert_status() {
  local expected="$1" actual="$2"
  [ "$actual" -eq "$expected" ] || fail "expected exit $expected, got $actual"
}

assert_output_contains() {
  local needle="$1"
  grep -F -- "$needle" "$HELPER_OUTPUT" >/dev/null || fail "expected helper output to contain: $needle"
}

assert_gh_not_called() {
  [ ! -s "$FAKE_GH_LOG" ] || fail "expected fake gh not to be called"
}

assert_gh_called_with_human_label() {
  grep -F -- "pr edit 55 --add-label 👤 human:需-maintainer-决策" "$FAKE_GH_LOG" >/dev/null \
    || grep -F -- "pr edit 55 --repo test-owner/test-repo --add-label 👤 human:需-maintainer-决策" "$FAKE_GH_LOG" >/dev/null \
    || fail "expected fake gh human-label edit call"
}

test_apply_human_label_skips_when_directive_matches_pr() {
  setup_tmpdir
  cat > "${TEST_REPO_ROOT}/.refactor-loop/runs/maintainer-directives/2026-05-26-some-topic.md" <<'EOF'
Maintainer already authorized the Phase 9 equivalent route for PR #55.
EOF

  local status=0
  run_helper 55 "human-label-semantics-guard" || status=$?

  assert_status 1 "$status"
  assert_output_contains "skip-label"
  assert_gh_not_called
}

test_apply_human_label_applies_when_no_directive_covers() {
  setup_tmpdir

  local status=0
  run_helper 55 "human-label-semantics-guard" || status=$?

  assert_status 0 "$status"
  assert_gh_called_with_human_label
}

test_apply_human_label_applies_when_directive_unrelated_topic() {
  setup_tmpdir
  cat > "${TEST_REPO_ROOT}/.refactor-loop/runs/maintainer-directives/2026-05-26-other-topic.md" <<'EOF'
Maintainer directive for a different PR and unrelated topic.
EOF

  local status=0
  run_helper 55 "human-label-semantics-guard" || status=$?

  assert_status 0 "$status"
  assert_gh_called_with_human_label
}

test_apply_human_label_skips_when_topic_in_directive_body() {
  setup_tmpdir
  cat > "${TEST_REPO_ROOT}/.refactor-loop/runs/maintainer-directives/2026-05-26-topic.md" <<'EOF'
The human-label-semantics-guard route is covered by prior maintainer directive.
EOF

  local status=0
  run_helper 55 "human-label-semantics-guard" || status=$?

  assert_status 1 "$status"
  assert_output_contains "skip-label"
  assert_gh_not_called
}

test_apply_human_label_with_corrupt_directive_fails_safe() {
  setup_tmpdir
  touch "${TEST_REPO_ROOT}/.refactor-loop/runs/maintainer-directives/2026-05-26-empty.md"
  cat > "${TEST_REPO_ROOT}/.refactor-loop/runs/maintainer-directives/2026-05-26-corrupt.txt" <<'EOF'
PR #55 human-label-semantics-guard
EOF

  local status=0
  run_helper 55 "human-label-semantics-guard" || status=$?

  assert_status 0 "$status"
  assert_gh_called_with_human_label
}

tests=(
  test_apply_human_label_skips_when_directive_matches_pr
  test_apply_human_label_applies_when_no_directive_covers
  test_apply_human_label_applies_when_directive_unrelated_topic
  test_apply_human_label_skips_when_topic_in_directive_body
  test_apply_human_label_with_corrupt_directive_fails_safe
)

for test_name in "${tests[@]}"; do
  "$test_name"
  echo "ok - $test_name"
done
