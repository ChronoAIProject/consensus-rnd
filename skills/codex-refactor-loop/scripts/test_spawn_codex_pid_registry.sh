#!/usr/bin/env bash
set -euo pipefail

# Sleep allowlist (per tests-reviewer):
# 本测试为 wrapper lifecycle 行为测试,需 fake codex 进程在 wrapper 启动→运行→退出
# 这段时间内 PID file 存在,wrapper 结束后 trap cleanup 删除。sleep 是必要的(无 sync 替代),
# allowlisted per source-regression test_ensure_project_rules_fixed_points.py 的 sleep-allowlist 段。

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
SPAWN_CODEX="$SCRIPT_DIR/spawn-codex.sh"
TEST_TMP=""

cleanup() {
  if [[ -n "$TEST_TMP" ]]; then
    rm -rf "$TEST_TMP"
  fi
}
trap cleanup EXIT

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

setup_case() {
  TEST_TMP="$(mktemp -d)"
  REPO="$TEST_TMP/repo"
  BIN="$TEST_TMP/bin"
  mkdir -p "$REPO/.refactor-loop/prompts" "$REPO/.refactor-loop/logs" "$BIN"
  REAL_REPO="$(cd "$REPO" && pwd -P)"
  git -C "$REPO" init -q
  PROMPT="$REPO/.refactor-loop/prompts/task.md"
  printf 'do work\n' > "$PROMPT"
  PATH="$BIN:$PATH"
  export PATH REPO_ROOT="$REPO"
}

write_fake_codex() {
  local body="$1"
  cat > "$BIN/codex" <<EOF
#!/usr/bin/env bash
set -euo pipefail
$body
EOF
  chmod +x "$BIN/codex"
}

run_wrapper_background() {
  local log="$1"
  "$SPAWN_CODEX" --cd "$REPO" --prompt "$PROMPT" --log "$log" --stall 30 &
  WRAPPER_PID=$!
}

wait_for_file() {
  local file="$1"
  local i
  for i in $(seq 1 100); do
    [[ -f "$file" ]] && return 0
    sleep 0.05
  done
  return 1
}

wait_for_removal() {
  local file="$1"
  local i
  for i in $(seq 1 100); do
    [[ ! -f "$file" ]] && return 0
    sleep 0.05
  done
  return 1
}

test_pid_file_exists_during_codex_execution() {
  setup_case
  write_fake_codex 'echo "$$" > "$REPO_ROOT/fake-codex.pid"; sleep 5'
  local log="$REPO/.refactor-loop/logs/during.log"
  local reg="$REPO/.refactor-loop/spawned/during.pid"

  run_wrapper_background "$log"
  wait_for_file "$reg" || fail "registry file was not created during execution"
  wait_for_file "$REPO/fake-codex.pid" || fail "fake codex pid file was not created"

  local expected_pid actual_pid
  expected_pid="$(cat "$REPO/fake-codex.pid")"
  actual_pid="$(sed -n 's/^pid=//p' "$reg")"
  [[ "$actual_pid" = "$expected_pid" ]] || fail "registry pid=$actual_pid, expected fake codex pid=$expected_pid"
  local real_log
  real_log="$(cd "$(dirname "$log")" && pwd -P)/$(basename "$log")"
  grep -q "^repo_root=$REAL_REPO$" "$reg" || fail "registry missing repo_root"
  grep -q "^log=$real_log$" "$reg" || fail "registry missing absolute log"

  kill "$WRAPPER_PID" 2>/dev/null || true
  wait "$WRAPPER_PID" 2>/dev/null || true
  wait_for_removal "$reg" || fail "registry file remained after cleanup"
  rm -rf "$TEST_TMP"
  TEST_TMP=""
}

test_pid_file_removed_on_normal_exit() {
  setup_case
  write_fake_codex 'exit 0'
  local log="$REPO/.refactor-loop/logs/normal.log"
  local reg="$REPO/.refactor-loop/spawned/normal.pid"

  "$SPAWN_CODEX" --cd "$REPO" --prompt "$PROMPT" --log "$log" --stall 30
  [[ ! -f "$reg" ]] || fail "registry file remained after normal exit"
  grep -q '^EXIT=0$' "$log" || fail "wrapper did not record EXIT=0"
  rm -rf "$TEST_TMP"
  TEST_TMP=""
}

test_pid_file_removed_on_early_failure() {
  setup_case
  write_fake_codex 'exit 1'
  local log="$REPO/.refactor-loop/logs/failure.log"
  local reg="$REPO/.refactor-loop/spawned/failure.pid"

  set +e
  "$SPAWN_CODEX" --cd "$REPO" --prompt "$PROMPT" --log "$log" --stall 30
  local status=$?
  set -e
  [[ "$status" -eq 1 ]] || fail "expected wrapper exit 1, got $status"
  [[ ! -f "$reg" ]] || fail "registry file remained after early failure"
  grep -q '^EXIT=1$' "$log" || fail "wrapper did not record EXIT=1"
  rm -rf "$TEST_TMP"
  TEST_TMP=""
}

test_pid_file_removed_on_sigterm() {
  setup_case
  write_fake_codex 'sleep 30'
  local log="$REPO/.refactor-loop/logs/sigterm.log"
  local reg="$REPO/.refactor-loop/spawned/sigterm.pid"

  run_wrapper_background "$log"
  wait_for_file "$reg" || fail "registry file was not created before SIGTERM"
  kill -TERM "$WRAPPER_PID"
  wait "$WRAPPER_PID" 2>/dev/null || true
  wait_for_removal "$reg" || fail "registry file remained after SIGTERM"
  rm -rf "$TEST_TMP"
  TEST_TMP=""
}

test_pid_file_exists_during_codex_execution
test_pid_file_removed_on_normal_exit
test_pid_file_removed_on_early_failure
test_pid_file_removed_on_sigterm

echo "ok - spawn-codex pid registry behavior"
