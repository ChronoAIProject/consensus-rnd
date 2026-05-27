#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
PEEK="${SCRIPT_DIR}/peek.sh"
TMP_DIR=""

cleanup() {
  if [ -n "${TMP_DIR:-}" ]; then
    rm -rf "$TMP_DIR"
  fi
}
trap cleanup EXIT

setup_repo() {
  TMP_DIR="$(mktemp -d)"
  mkdir -p "$TMP_DIR/.refactor-loop/logs" "$TMP_DIR/fakebin"

  cat > "$TMP_DIR/fakebin/gh" <<'SH'
#!/usr/bin/env bash
for arg in "$@"; do
  if [ "$arg" = "--jq" ]; then
    exit 0
  fi
done
case "$1 $2" in
  "issue list"|"pr list")
    printf '[]\n'
    ;;
  "issue view"|"pr view")
    printf '{"comments":[],"state":"OPEN"}\n'
    ;;
  *)
    ;;
esac
SH
  chmod +x "$TMP_DIR/fakebin/gh"

  cat > "$TMP_DIR/fakebin/git" <<'SH'
#!/usr/bin/env bash
case "$1" in
  fetch)
    exit 0
    ;;
  worktree)
    if [ "$2" = "list" ]; then
      printf 'worktree %s\n' "${REPO_ROOT:-$PWD}"
    fi
    ;;
  *)
    exit 0
    ;;
esac
SH
  chmod +x "$TMP_DIR/fakebin/git"
}

write_log() {
  local name="$1"
  shift
  printf '%s\n' "$@" > "$TMP_DIR/.refactor-loop/logs/${name}.log"
}

run_peek() {
  PATH="$TMP_DIR/fakebin:$PATH" REPO_ROOT="$TMP_DIR" GH_REPO_SLUG="" bash "$PEEK"
}

assert_contains() {
  local haystack="$1"
  local needle="$2"
  local label="$3"
  if [[ "$haystack" != *"$needle"* ]]; then
    echo "FAIL ${label}: expected output to contain ${needle}" >&2
    echo "$haystack" >&2
    exit 1
  fi
}

assert_not_contains() {
  local haystack="$1"
  local needle="$2"
  local label="$3"
  if [[ "$haystack" == *"$needle"* ]]; then
    echo "FAIL ${label}: expected output not to contain ${needle}" >&2
    echo "$haystack" >&2
    exit 1
  fi
}

test_mid_log_exit_literal_without_exit_ok_is_not_finished() {
  setup_repo
  write_log "audit-mid-exit" \
    "SPAWN: audit" \
    'grep pattern source.sh' \
    'EXIT=$?' \
    "AUDIT_DONE:audit-iter-fake.md:9" \
    "ERROR: command failed"

  local out
  out="$(run_peek)"

  assert_not_contains "$out" "audit-mid-exit: AUDIT_DONE:audit-iter-fake.md:9" "${FUNCNAME[0]}"
}

test_tail_exit_one_is_not_finished() {
  setup_repo
  write_log "audit-exit-one" \
    "SPAWN: audit" \
    "AUDIT_DONE:audit-iter-fake.md:9" \
    "EXIT=1"

  local out
  out="$(run_peek)"

  assert_not_contains "$out" "audit-exit-one: AUDIT_DONE:audit-iter-fake.md:9" "${FUNCNAME[0]}"
}

test_exit_zero_with_real_audit_marker_is_finished() {
  setup_repo
  write_log "audit-real" \
    "SPAWN: audit" \
    "AUDIT_DONE:audit-iter-xxx.md:5" \
    "EXIT=0"

  local out
  out="$(run_peek)"

  assert_contains "$out" "audit-real: AUDIT_DONE:audit-iter-xxx.md:5" "${FUNCNAME[0]}"
}

test_exit_zero_with_audit_placeholder_is_filtered() {
  setup_repo
  write_log "audit-placeholder" \
    "SPAWN: audit" \
    "AUDIT_DONE:none:0" \
    "EXIT=0"

  local out
  out="$(run_peek)"

  assert_not_contains "$out" "audit-placeholder: AUDIT_DONE:none:0" "${FUNCNAME[0]}"
}

test_exit_zero_with_meta_short_placeholder_is_filtered() {
  setup_repo
  write_log "meta-placeholder" \
    "SPAWN: judge" \
    "META_JUDGE_DONE:escalate:stalled:<short>" \
    "EXIT=0"

  local out
  out="$(run_peek)"

  assert_not_contains "$out" "meta-placeholder: META_JUDGE_DONE:escalate:stalled:<short>" "${FUNCNAME[0]}"
}

test_exit_zero_with_real_meta_marker_is_finished() {
  setup_repo
  write_log "meta-real" \
    "SPAWN: judge" \
    "META_JUDGE_DONE:consensus:A-real-framing" \
    "EXIT=0"

  local out
  out="$(run_peek)"

  assert_contains "$out" "meta-real: META_JUDGE_DONE:consensus:A-real-framing" "${FUNCNAME[0]}"
}

test_mid_log_exit_literal_without_exit_ok_is_not_finished
test_tail_exit_one_is_not_finished
test_exit_zero_with_real_audit_marker_is_finished
test_exit_zero_with_audit_placeholder_is_filtered
test_exit_zero_with_meta_short_placeholder_is_filtered
test_exit_zero_with_real_meta_marker_is_finished

echo "peek marker extraction tests ok"
