#!/usr/bin/env python3
"""Behavior tests for restart-daemons.sh."""

from __future__ import annotations

import os
import select
import shutil
import signal
import subprocess
import tempfile
import time
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
HELPER = SCRIPT_DIR / "restart-daemons.sh"
DAEMON_NAMES = (
    "concurrency_monitor",
    "comment-monitor",
    "codex-progress-reporter",
    "dev_sync_daemon",
    "phase9_router_daemon",
)


PYTHON_DAEMON = """#!/usr/bin/env python3
import os
import signal
import sys
from pathlib import Path
from daemon_heartbeat import DaemonHeartbeatLease

repo = Path(os.environ["REPO_ROOT"])
name = os.environ.get("RESTART_DAEMON_NAME", Path(sys.argv[0]).stem)
lease = DaemonHeartbeatLease(name, repo)
lease.beat()
with (repo / ".refactor-loop" / "logs" / f"{name}.starts").open("a", encoding="utf-8") as fh:
    fh.write(f"{os.getpid()}\\n")
fifo = repo / ".refactor-loop" / "logs" / f"{name}.start-fifo"
if fifo.exists():
    fd = os.open(fifo, os.O_WRONLY)
    try:
        os.write(fd, f"{os.getpid()}\\n".encode("utf-8"))
    finally:
        os.close(fd)

running = True
def stop(_signum, _frame):
    global running
    running = False
signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)

while running:
    signal.pause()
"""


SHELL_DAEMON = """#!/usr/bin/env bash
set -u
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
source "$SCRIPT_DIR/daemon_heartbeat.sh"
daemon_heartbeat_beat
echo "$$" >> "$REPO_ROOT/.refactor-loop/logs/${RESTART_DAEMON_NAME}.starts"
if [ -p "$REPO_ROOT/.refactor-loop/logs/${RESTART_DAEMON_NAME}.start-fifo" ]; then
  printf '%s\n' "$$" > "$REPO_ROOT/.refactor-loop/logs/${RESTART_DAEMON_NAME}.start-fifo"
fi
trap 'exit 0' TERM INT
while true; do
  read _ < "$REPO_ROOT/.refactor-loop/logs/${RESTART_DAEMON_NAME}.hold"
done
"""


PYTHON_HANG_DAEMON = """#!/usr/bin/env python3
import os
import signal
import sys
from pathlib import Path
from daemon_heartbeat import DaemonHeartbeatLease

repo = Path(os.environ["REPO_ROOT"])
name = os.environ.get("RESTART_DAEMON_NAME", Path(sys.argv[0]).stem)
clock = lambda: int(os.environ.get("TEST_HEARTBEAT_EPOCH", "100"))
DaemonHeartbeatLease(name, repo, clock=clock).beat()
with (repo / ".refactor-loop" / "logs" / f"{name}.starts").open("a", encoding="utf-8") as fh:
    fh.write(f"{os.getpid()}\\n")
fifo = repo / ".refactor-loop" / "logs" / f"{name}.start-fifo"
if fifo.exists():
    fd = os.open(fifo, os.O_WRONLY)
    try:
        os.write(fd, f"{os.getpid()}\\n".encode("utf-8"))
    finally:
        os.close(fd)
signal.signal(signal.SIGTERM, lambda _signum, _frame: sys.exit(0))
signal.signal(signal.SIGINT, lambda _signum, _frame: sys.exit(0))
signal.pause()
"""


class RestartDaemonsBehaviorTests(unittest.TestCase):
    # Refactor (iter1/issue-138):
    #   Old pattern: tearDown killed only tracked wrapper pids and missed detached
    #   daemon descendants, leaving persistent test processes.
    #   New principle: test-only cleanup reaps wrapper groups and tmp-root
    #   descendants, then asserts no tmp-root process remains.
    REFACTOR_SELF_DOCUMENTATION = """
Refactor (iter1/issue-138):
  Old pattern: test_restart_daemons.py tearDown 只 kill tracked pid,无法回收 detached daemon 子孙进程 → 测试泄漏常驻进程(本轮发现 256 孤儿)。
  New principle: 按 .refactor-loop/runs/phase9-issue138-r3-judge.md consensus(hybrid test-only cleanup,no production contract change)逐条:tearDown 按 tmp_root/进程组回收 detached daemon + behavior test 断言无残留;硬约束:(1) 不重建 REFERENCE.md(单文件 SKILL.md);(2) refactor self-doc 注释必须自含 Old/New,禁止 'see issue #X' placeholder;(3) 严格按 design decision Implement plan,不超范围。
""".strip()
    def setUp(self) -> None:
        self.tmp_root = Path(tempfile.mkdtemp(prefix="restart-daemons-test-"))
        self.repo = self.tmp_root / "repo"
        self.skill = self.tmp_root / "skill"
        self.fake_bin = self.tmp_root / "fake-bin"
        self.fake_bin.mkdir()
        self.fake_epoch_file = self.tmp_root / "fake-date-epoch"
        self.fake_epoch_file.write_text(f"{int(time.time())}\n", encoding="utf-8")
        self.helper_process_groups: set[int] = set()
        self._write_executable(
            self.fake_bin / "date",
            "#!/usr/bin/env bash\n"
            "if [ \"$1\" = \"+%s\" ]; then\n"
            f"  cat \"{self.fake_epoch_file}\"\n"
            "else\n"
            "  /bin/date \"$@\"\n"
            "fi\n",
        )
        for rel in (
            ".refactor-loop/logs",
            ".refactor-loop/locks",
            ".refactor-loop/heartbeats",
        ):
            (self.repo / rel).mkdir(parents=True, exist_ok=True)
        for name in DAEMON_NAMES:
            os.mkfifo(self.repo / ".refactor-loop" / "logs" / f"{name}.start-fifo")
            os.mkfifo(self.repo / ".refactor-loop" / "logs" / f"{name}.hold")
        (self.skill / "scripts").mkdir(parents=True, exist_ok=True)
        shutil.copy2(HELPER, self.skill / "scripts" / "restart-daemons.sh")
        shutil.copy2(SCRIPT_DIR / "daemon_heartbeat.py", self.skill / "scripts" / "daemon_heartbeat.py")
        shutil.copy2(SCRIPT_DIR / "daemon_heartbeat.sh", self.skill / "scripts" / "daemon_heartbeat.sh")
        (self.skill / "scripts" / "restart-daemons.sh").chmod(0o755)
        (self.skill / "scripts" / "daemon_heartbeat.py").chmod(0o755)
        (self.skill / "scripts" / "daemon_heartbeat.sh").chmod(0o755)
        (self.repo / ".refactor-loop" / "host.env").write_text(
            "\n".join(
                (
                    f'export REPO_ROOT="{self.repo}"',
                    'export GH_REPO_SLUG="example/repo"',
                    'export MAINTAINER_WHITELIST="maintainer"',
                    "",
                )
            ),
            encoding="utf-8",
        )
        for script in ("concurrency_monitor.py", "dev_sync_daemon.py", "phase9_router_daemon.py"):
            self._write_executable(self.skill / "scripts" / script, PYTHON_DAEMON)
        for script in ("comment-monitor.sh", "codex-progress-reporter.sh"):
            self._write_executable(self.skill / "scripts" / script, SHELL_DAEMON)

    def tearDown(self) -> None:
        self._cleanup_daemons()
        shutil.rmtree(self.tmp_root, ignore_errors=True)

    def _write_executable(self, path: Path, text: str) -> None:
        path.write_text(text, encoding="utf-8")
        path.chmod(0o755)

    def _cleanup_daemons(self) -> None:
        lock_dir = self.repo / ".refactor-loop" / "locks"
        if lock_dir.exists():
            for pid_file in lock_dir.glob("*.pid"):
                pid = self._read_pid(pid_file)
                if pid is not None:
                    self._terminate_process(pid)
                try:
                    pid_file.unlink()
                except FileNotFoundError:
                    pass

        for process_group in tuple(self.helper_process_groups):
            self._terminate_process_group(process_group)
        self._sweep_tmp_root_processes()
        self.assertEqual({}, self._tmp_root_processes())

    def _read_pid(self, pid_file: Path) -> int | None:
        try:
            raw = pid_file.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            return None
        if not raw.isdigit():
            return None
        return int(raw)

    def _kill_process(self, pid: int) -> None:
        self._terminate_process(pid)

    def _terminate_process(self, pid: int, timeout: float = 2.0) -> None:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        except PermissionError:
            return
        self._wait_for_pid_exit(pid, timeout)
        if not self._pid_alive(pid):
            return
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        self._wait_for_pid_exit(pid, timeout)

    def _terminate_process_group(self, process_group: int, timeout: float = 2.0) -> None:
        try:
            os.killpg(process_group, signal.SIGTERM)
        except ProcessLookupError:
            self.helper_process_groups.discard(process_group)
            return
        except PermissionError:
            return
        self._wait_for_process_group_exit(process_group, timeout)
        if not self._tmp_root_processes(process_group=process_group):
            self.helper_process_groups.discard(process_group)
            return
        try:
            os.killpg(process_group, signal.SIGKILL)
        except ProcessLookupError:
            pass
        self._wait_for_process_group_exit(process_group, timeout)
        if not self._tmp_root_processes(process_group=process_group):
            self.helper_process_groups.discard(process_group)

    def _sweep_tmp_root_processes(self) -> None:
        for proc in self._tmp_root_processes().values():
            self._terminate_process(proc["pid"])
        for proc in self._tmp_root_processes().values():
            self._terminate_process(proc["pid"], timeout=0.5)

    def _wait_for_pid_exit(self, pid: int, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not self._pid_alive(pid):
                return
            time.sleep(0.02)

    def _wait_for_process_group_exit(self, process_group: int, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not self._tmp_root_processes(process_group=process_group):
                return
            time.sleep(0.02)

    def _tmp_root_processes(self, *, process_group: int | None = None) -> dict[int, dict[str, int | str]]:
        current_pid = os.getpid()
        result = subprocess.run(
            ["ps", "-axo", "pid=,pgid=,command="],
            text=True,
            capture_output=True,
            check=True,
        )
        processes: dict[int, dict[str, int | str]] = {}
        tmp_roots = {str(self.tmp_root), str(self.tmp_root.resolve())}
        for line in result.stdout.splitlines():
            parts = line.strip().split(maxsplit=2)
            if len(parts) != 3:
                continue
            raw_pid, raw_process_group, command = parts
            if not raw_pid.isdigit() or not raw_process_group.isdigit():
                continue
            pid = int(raw_pid)
            proc_group = int(raw_process_group)
            if pid == current_pid or not any(root in command for root in tmp_roots):
                continue
            if process_group is not None and proc_group != process_group:
                continue
            processes[pid] = {
                "pid": pid,
                "process_group": proc_group,
                "command": command,
            }
        return processes

    def _pid_alive(self, pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    def _run_helper(self) -> subprocess.CompletedProcess[str]:
        return self._run_helper_with_env(
            {
                "RESTART_DAEMONS_HEARTBEAT_FRESH_SECONDS": "30",
                "RESTART_DAEMONS_HEARTBEAT_INTERVAL": "1",
                "RESTART_DAEMONS_STOP_GRACE_SECONDS": "1",
            }
        )

    def _run_helper_with_fresh_seconds(self, fresh_seconds: int) -> subprocess.CompletedProcess[str]:
        return self._run_helper_with_env(
            {
                "RESTART_DAEMONS_HEARTBEAT_FRESH_SECONDS": str(fresh_seconds),
                "RESTART_DAEMONS_HEARTBEAT_INTERVAL": "1",
                "RESTART_DAEMONS_STOP_GRACE_SECONDS": "1",
            }
        )

    def _run_helper_at_epoch(self, *, now_epoch: int, fresh_seconds: int) -> subprocess.CompletedProcess[str]:
        self.fake_epoch_file.write_text(f"{now_epoch}\n", encoding="utf-8")
        return self._run_helper_with_env(
            {
                "PATH": f"{self.fake_bin}{os.pathsep}{os.environ.get('PATH', '')}",
                "TEST_HEARTBEAT_EPOCH": "100",
                "RESTART_DAEMONS_HEARTBEAT_FRESH_SECONDS": str(fresh_seconds),
                "RESTART_DAEMONS_HEARTBEAT_INTERVAL": "1",
                "RESTART_DAEMONS_STOP_GRACE_SECONDS": "1",
            }
        )

    def _run_helper_with_env(self, updates: dict[str, str]) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env.pop("REPO_ROOT", None)
        env.update(updates)
        process = subprocess.Popen(
            ["bash", str(self.skill / "scripts" / "restart-daemons.sh")],
            cwd=self.repo,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        self.helper_process_groups.add(process.pid)
        stdout, stderr = process.communicate()
        completed = subprocess.CompletedProcess(process.args, process.returncode, stdout, stderr)
        if process.returncode != 0:
            raise subprocess.CalledProcessError(process.returncode, process.args, stdout, stderr)
        return completed

    def _start_helper_process(self, env: dict[str, str]) -> subprocess.Popen[str]:
        process = subprocess.Popen(
            ["bash", str(self.skill / "scripts" / "restart-daemons.sh")],
            cwd=self.repo,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        self.helper_process_groups.add(process.pid)
        return process

    def _start_count(self, name: str) -> int:
        starts = self.repo / ".refactor-loop" / "logs" / f"{name}.starts"
        if not starts.exists():
            return 0
        return len(starts.read_text(encoding="utf-8").splitlines())

    def _read_start_signal(self, name: str, timeout: float = 5.0) -> int:
        fifo = self.repo / ".refactor-loop" / "logs" / f"{name}.start-fifo"
        fd = os.open(fifo, os.O_RDONLY | os.O_NONBLOCK)
        try:
            readable, _, _ = select.select([fd], [], [], timeout)
            if not readable:
                self.fail(f"timed out waiting for {name} start signal")
            raw = os.read(fd, 64).decode("utf-8").strip()
        finally:
            os.close(fd)
        self.assertTrue(raw.isdigit(), raw)
        return int(raw)

    def _stale_heartbeat(self, name: str) -> None:
        stale_ts = int(time.time()) - 120
        (self.repo / ".refactor-loop" / "heartbeats" / f"{name}.ts").write_text(
            f"{stale_ts}\n",
            encoding="utf-8",
        )

    def test_idempotent_when_daemon_fresh(self) -> None:
        self._run_helper()
        self._read_start_signal("concurrency_monitor")

        self._run_helper()

        self.assertEqual(1, self._start_count("concurrency_monitor"))

    def test_restarts_when_heartbeat_stale(self) -> None:
        self._run_helper()
        self._read_start_signal("comment-monitor")
        self._stale_heartbeat("comment-monitor")

        self._run_helper()

        self._read_start_signal("comment-monitor")
        self.assertEqual(2, self._start_count("comment-monitor"))

    def test_hung_child_stops_renewing_heartbeat_and_is_restarted(self) -> None:
        self._write_executable(self.skill / "scripts" / "phase9_router_daemon.py", PYTHON_HANG_DAEMON)
        self._run_helper_at_epoch(now_epoch=100, fresh_seconds=2)
        old_child_pid = self._read_start_signal("phase9_router_daemon")
        self.assertEqual(1, self._start_count("phase9_router_daemon"))
        self.assertEqual(
            "100",
            (self.repo / ".refactor-loop" / "heartbeats" / "phase9_router_daemon.ts").read_text(encoding="utf-8").strip(),
        )

        self._run_helper_at_epoch(now_epoch=103, fresh_seconds=2)

        new_child_pid = self._read_start_signal("phase9_router_daemon")
        self.assertNotEqual(old_child_pid, new_child_pid)
        self.assertEqual(2, self._start_count("phase9_router_daemon"))
        self.assertFalse(self._pid_alive(old_child_pid))

    def test_restarts_when_heartbeat_missing(self) -> None:
        self._run_helper()
        self._read_start_signal("codex-progress-reporter")
        (self.repo / ".refactor-loop" / "heartbeats" / "codex-progress-reporter.ts").unlink()

        self._run_helper()

        self._read_start_signal("codex-progress-reporter")
        self.assertEqual(2, self._start_count("codex-progress-reporter"))

    def test_restarts_when_heartbeat_malformed(self) -> None:
        self._run_helper()
        self._read_start_signal("phase9_router_daemon")
        (self.repo / ".refactor-loop" / "heartbeats" / "phase9_router_daemon.ts").write_text(
            "not-a-timestamp\n",
            encoding="utf-8",
        )

        self._run_helper()

        self._read_start_signal("phase9_router_daemon")
        self.assertEqual(2, self._start_count("phase9_router_daemon"))

    def test_restarts_when_pid_dead(self) -> None:
        (self.repo / ".refactor-loop" / "locks" / "dev_sync_daemon.pid").write_text(
            "999999\n",
            encoding="utf-8",
        )
        (self.repo / ".refactor-loop" / "heartbeats" / "dev_sync_daemon.ts").write_text(
            f"{int(time.time())}\n",
            encoding="utf-8",
        )

        self._run_helper()

        self._read_start_signal("dev_sync_daemon")
        pid = self._read_pid(self.repo / ".refactor-loop" / "locks" / "dev_sync_daemon.pid")
        self.assertIsNotNone(pid)
        self.assertTrue(self._pid_alive(pid))

    def test_stale_restart_cleans_up_old_wrapper(self) -> None:
        self._run_helper()
        self._read_start_signal("concurrency_monitor")
        old_pid = self._read_pid(self.repo / ".refactor-loop" / "locks" / "concurrency_monitor.pid")
        self.assertIsNotNone(old_pid)
        self._stale_heartbeat("concurrency_monitor")

        self._run_helper()

        self._read_start_signal("concurrency_monitor")
        new_pid = self._read_pid(self.repo / ".refactor-loop" / "locks" / "concurrency_monitor.pid")
        self.assertIsNotNone(new_pid)
        self.assertNotEqual(old_pid, new_pid)
        self.assertFalse(self._pid_alive(old_pid))
        self.assertTrue(self._pid_alive(new_pid))
        self.assertEqual(2, self._start_count("concurrency_monitor"))

    def test_no_double_spawn_under_race(self) -> None:
        env = os.environ.copy()
        env.pop("REPO_ROOT", None)
        env.update(
            {
                "RESTART_DAEMONS_HEARTBEAT_FRESH_SECONDS": "30",
                "RESTART_DAEMONS_HEARTBEAT_INTERVAL": "1",
                "RESTART_DAEMONS_STOP_GRACE_SECONDS": "1",
            }
        )
        command = ["bash", str(self.skill / "scripts" / "restart-daemons.sh")]

        first = self._start_helper_process(env)
        second = self._start_helper_process(env)
        first_stdout, first_stderr = first.communicate(timeout=10)
        second_stdout, second_stderr = second.communicate(timeout=10)
        self.assertEqual(0, first.returncode, first_stdout + first_stderr)
        self.assertEqual(0, second.returncode, second_stdout + second_stderr)

        self._read_start_signal("phase9_router_daemon")
        self.assertEqual(1, self._start_count("phase9_router_daemon"))
        self.assertEqual(1, self._start_count("concurrency_monitor"))

    def test_cleanup_daemons_reaps_tmp_root_wrappers_and_children(self) -> None:
        self._run_helper()
        self._read_start_signal("concurrency_monitor")
        self.assertTrue(self._tmp_root_processes())

        self._cleanup_daemons()

        self.assertEqual({}, self._tmp_root_processes())

    def test_restart_daemons_starts_phase9_router_daemon(self) -> None:
        self._run_helper()
        self._read_start_signal("phase9_router_daemon")

        self._run_helper()
        self.assertEqual(1, self._start_count("phase9_router_daemon"))

        self._stale_heartbeat("phase9_router_daemon")
        self._run_helper()
        self._read_start_signal("phase9_router_daemon")
        self.assertEqual(2, self._start_count("phase9_router_daemon"))

        (self.repo / ".refactor-loop" / "heartbeats" / "phase9_router_daemon.ts").unlink()
        self._run_helper()
        self._read_start_signal("phase9_router_daemon")
        self.assertEqual(3, self._start_count("phase9_router_daemon"))

        (self.repo / ".refactor-loop" / "heartbeats" / "phase9_router_daemon.ts").write_text(
            "not-a-timestamp\n",
            encoding="utf-8",
        )
        self._run_helper()
        self._read_start_signal("phase9_router_daemon")
        self.assertEqual(4, self._start_count("phase9_router_daemon"))

        old_pid = self._read_pid(self.repo / ".refactor-loop" / "locks" / "phase9_router_daemon.pid")
        self.assertIsNotNone(old_pid)
        assert old_pid is not None
        self._kill_process(old_pid)
        self._run_helper()
        self._read_start_signal("phase9_router_daemon")
        self.assertEqual(5, self._start_count("phase9_router_daemon"))

        old_pid = self._read_pid(self.repo / ".refactor-loop" / "locks" / "phase9_router_daemon.pid")
        self.assertIsNotNone(old_pid)
        assert old_pid is not None
        self._stale_heartbeat("phase9_router_daemon")
        self._run_helper()
        self._read_start_signal("phase9_router_daemon")
        new_pid = self._read_pid(self.repo / ".refactor-loop" / "locks" / "phase9_router_daemon.pid")
        self.assertIsNotNone(new_pid)
        self.assertNotEqual(old_pid, new_pid)
        self.assertFalse(self._pid_alive(old_pid))
        self.assertTrue(self._pid_alive(new_pid))
        self.assertEqual(6, self._start_count("phase9_router_daemon"))


if __name__ == "__main__":
    unittest.main()
