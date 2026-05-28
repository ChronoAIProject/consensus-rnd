#!/usr/bin/env python3
"""Behavior and source-regression tests for log_retention.sh."""

from __future__ import annotations

import os
import signal
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
HELPER = SCRIPT_DIR / "log_retention.sh"
RESTART_HELPER = SCRIPT_DIR / "restart-daemons.sh"


# Refactor (iter326/issue-122):
#   Old pattern: .refactor-loop/logs/ and runs/ grew without bounds, slowing daemon scans and bloating .refactor-loop/.
#   New principle: daemonless 24h log_retention.sh under restart-daemons; direct rm only, no archive/index/new daemon
#   (Phase 9 r1 consensus:structural)
class LogRetentionBehaviorTests(unittest.TestCase):

    def setUp(self) -> None:
        self.tmp_root = Path(tempfile.mkdtemp(prefix="log-retention-test-"))
        self.repo = self.tmp_root / "repo"
        self.logs = self.repo / ".refactor-loop" / "logs"
        self.logs.mkdir(parents=True)
        (self.repo / ".refactor-loop" / "host.env").write_text(
            f'export REPO_ROOT="{self.repo}"\n',
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp_root, ignore_errors=True)

    def _write_file(self, rel: str, text: str, age_hours: float) -> Path:
        path = self.repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        ts = time.time() - age_hours * 60 * 60
        os.utime(path, (ts, ts))
        return path

    def _prepare_restart_skill(self, retention_script: str | None) -> Path:
        skill = self.tmp_root / "skill"
        (skill / "scripts").mkdir(parents=True)
        shutil.copy2(RESTART_HELPER, skill / "scripts" / "restart-daemons.sh")
        shutil.copy2(SCRIPT_DIR / "daemon_heartbeat.py", skill / "scripts" / "daemon_heartbeat.py")
        shutil.copy2(SCRIPT_DIR / "daemon_heartbeat.sh", skill / "scripts" / "daemon_heartbeat.sh")
        (skill / "scripts" / "restart-daemons.sh").chmod(0o755)
        (skill / "scripts" / "daemon_heartbeat.py").chmod(0o755)
        (skill / "scripts" / "daemon_heartbeat.sh").chmod(0o755)
        if retention_script is not None:
            (skill / "scripts" / "log_retention.sh").write_text(retention_script, encoding="utf-8")
            (skill / "scripts" / "log_retention.sh").chmod(0o755)
        for rel in (
            ".refactor-loop/locks",
            ".refactor-loop/heartbeats",
        ):
            (self.repo / rel).mkdir(parents=True, exist_ok=True)
        daemon = (
            "#!/usr/bin/env bash\n"
            "SCRIPT_DIR=\"$(cd \"$(dirname \"${BASH_SOURCE[0]}\")\" && pwd -P)\"\n"
            "source \"$SCRIPT_DIR/daemon_heartbeat.sh\"\n"
            "daemon_heartbeat_beat\n"
            "printf '%s\\n' \"$RESTART_DAEMON_NAME\" >> \"$REPO_ROOT/.refactor-loop/logs/order.log\"\n"
            "trap 'exit 0' TERM INT\n"
            "while true; do sleep 60; done\n"
        )
        for script in ("comment-monitor.sh", "codex-progress-reporter.sh"):
            (skill / "scripts" / script).write_text(daemon, encoding="utf-8")
            (skill / "scripts" / script).chmod(0o755)
        py_daemon = (
            "#!/usr/bin/env python3\n"
            "import os\n"
            "import signal\n"
            "from pathlib import Path\n"
            "from daemon_heartbeat import DaemonHeartbeatLease\n"
            "DaemonHeartbeatLease(os.environ['RESTART_DAEMON_NAME'], os.environ['REPO_ROOT']).beat()\n"
            "Path(os.environ['REPO_ROOT'], '.refactor-loop/logs/order.log').open('a').write(os.environ['RESTART_DAEMON_NAME'] + '\\n')\n"
            "signal.pause()\n"
        )
        for script in ("concurrency_monitor.py", "dev_sync_daemon.py", "phase9_router_daemon.py"):
            (skill / "scripts" / script).write_text(py_daemon, encoding="utf-8")
            (skill / "scripts" / script).chmod(0o755)
        return skill

    def _run_restart_helper(self, skill: Path) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                ["bash", str(skill / "scripts" / "restart-daemons.sh")],
                cwd=self.repo,
                env={**os.environ, "RESTART_DAEMONS_HEARTBEAT_INTERVAL": "1"},
                text=True,
                capture_output=True,
                timeout=10,
            )
        finally:
            for pid_file in (self.repo / ".refactor-loop" / "locks").glob("*.pid"):
                raw = pid_file.read_text(encoding="utf-8").strip()
                if raw.isdigit():
                    try:
                        os.kill(int(raw), signal.SIGTERM)
                    except ProcessLookupError:
                        pass

    def _run_helper(self, *, cwd: Path | None = None, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        run_env = os.environ.copy()
        run_env.pop("REPO_ROOT", None)
        if env:
            run_env.update(env)
        return subprocess.run(
            ["bash", str(HELPER)],
            cwd=cwd or self.repo,
            env=run_env,
            text=True,
            capture_output=True,
        )

    def test_deletes_only_log_files_older_than_24h(self) -> None:
        old_log = self._write_file(".refactor-loop/logs/old.log", "done\nEXIT=0\n", 25)
        young_log = self._write_file(".refactor-loop/logs/young.log", "done\nEXIT=0\n", 1)
        old_non_log = self._write_file(".refactor-loop/logs/old.txt", "keep\n", 25)
        run_artifact = self._write_file(".refactor-loop/runs/old.log", "keep\n", 25)

        result = self._run_helper()

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertFalse(old_log.exists())
        self.assertTrue(young_log.exists())
        self.assertTrue(old_non_log.exists())
        self.assertTrue(run_artifact.exists())
        self.assertIn("deleted=1", result.stdout)

    def test_recent_unfinished_log_is_kept(self) -> None:
        active_log = self._write_file(".refactor-loop/logs/active.log", "still running\n", 1)

        result = self._run_helper()

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertTrue(active_log.exists())
        self.assertIn("deleted=0", result.stdout)

    def test_idempotent_after_old_logs_removed(self) -> None:
        self._write_file(".refactor-loop/logs/old.log", "done\nEXIT=0\n", 25)

        first = self._run_helper()
        second = self._run_helper()

        self.assertEqual(0, first.returncode, first.stderr)
        self.assertEqual(0, second.returncode, second.stderr)
        self.assertIn("deleted=1", first.stdout)
        self.assertIn("deleted=0", second.stdout)

    def test_refuses_without_repo_root_or_host_env(self) -> None:
        isolated = self.tmp_root / "isolated"
        isolated.mkdir()

        result = self._run_helper(cwd=isolated)

        self.assertNotEqual(0, result.returncode)
        self.assertIn("REPO_ROOT is unset", result.stderr)

    def test_refuses_bad_repo_root(self) -> None:
        isolated = self.tmp_root / "isolated"
        isolated.mkdir()

        result = self._run_helper(
            cwd=isolated,
            env={"REPO_ROOT": str(self.tmp_root / "missing-repo")},
        )

        self.assertEqual(2, result.returncode)
        self.assertIn("REPO_ROOT is not a readable directory", result.stderr)

    def test_missing_log_directory_is_noop(self) -> None:
        shutil.rmtree(self.logs)

        result = self._run_helper()

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("deleted=0", result.stdout)
        self.assertIn("kept=0", result.stdout)
        self.assertIn("missing=true", result.stdout)

    def test_keeps_symlink_and_non_regular_log_paths(self) -> None:
        old_target = self._write_file(".refactor-loop/logs/target.log", "target\n", 25)
        symlink_log = self.logs / "linked.log"
        symlink_log.symlink_to(old_target)
        fifo_log = self.logs / "pipe.log"
        os.mkfifo(fifo_log)

        result = self._run_helper()

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertTrue(symlink_log.is_symlink())
        self.assertTrue(fifo_log.exists())
        self.assertFalse(old_target.exists())
        self.assertIn("kept=2", result.stdout)

    def test_keeps_log_when_mtime_is_unparseable(self) -> None:
        old_log = self._write_file(".refactor-loop/logs/old.log", "done\nEXIT=0\n", 25)
        fake_bin = self.tmp_root / "fake-bin"
        fake_bin.mkdir()
        fake_stat = fake_bin / "stat"
        fake_stat.write_text("#!/usr/bin/env bash\nprintf 'not-a-number\\n'\n", encoding="utf-8")
        fake_stat.chmod(0o755)

        result = self._run_helper(env={"PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}"})

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertTrue(old_log.exists())
        self.assertIn("deleted=0", result.stdout)
        self.assertIn("kept=1", result.stdout)

    def test_restart_daemons_runs_retention_before_daemon_start(self) -> None:
        skill = self._prepare_restart_skill(
            "#!/usr/bin/env bash\n"
            "printf 'retention\\n' >> \"$REPO_ROOT/.refactor-loop/logs/order.log\"\n",
        )
        result = self._run_restart_helper(skill)

        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        order = (self.logs / "order.log").read_text(encoding="utf-8").splitlines()
        self.assertGreaterEqual(len(order), 2, order)
        self.assertEqual("retention", order[0])

    def test_restart_daemons_continues_when_retention_helper_is_missing(self) -> None:
        skill = self._prepare_restart_skill(None)

        result = self._run_restart_helper(skill)

        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        order = (self.logs / "order.log").read_text(encoding="utf-8").splitlines()
        self.assertIn("concurrency_monitor", order)
        self.assertIn("log_retention skip: helper missing", result.stdout)

    def test_restart_daemons_continues_when_retention_helper_fails(self) -> None:
        skill = self._prepare_restart_skill(
            "#!/usr/bin/env python3\n"
            "raise SystemExit(42)\n"
        )

        result = self._run_restart_helper(skill)

        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        order = (self.logs / "order.log").read_text(encoding="utf-8").splitlines()
        self.assertIn("concurrency_monitor", order)
        self.assertIn("log_retention warning: helper failed; continuing daemon restart", result.stdout)


class LogRetentionSourceRegressionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.helper = HELPER.read_text(encoding="utf-8")
        self.restart = RESTART_HELPER.read_text(encoding="utf-8")

    def test_helper_contract_is_narrow_direct_delete_only(self) -> None:
        required = (
            "RETENTION_TTL_HOURS=24",
            ".refactor-loop/logs",
            "*.log",
            "rm -f -- \"$path\"",
            "# Fix (remote-ci/contract-tests): GNU stat accepts -f for filesystem format, so prefer -c for Linux mtime.",
            "stat -c %Y \"$path\"",
            "stat -f %m \"$path\"",
            "Refactor (iter326/issue-122)",
            "direct rm only",
            "no archive/index/new daemon",
        )
        for needle in required:
            with self.subTest(needle=needle):
                self.assertIn(needle, self.helper)
        forbidden = (
            ".refactor-loop/archive",
            "last_processed",
            "while true",
            "nohup",
            "spawn-codex",
            "gh ",
            "git ",
            "commit",
            "push",
            "merge",
            "label",
        )
        for token in forbidden:
            with self.subTest(token=token):
                self.assertNotIn(token, self.helper)
        self.assertNotIn('rm -f -- "$REPO_ROOT', self.helper)
        self.assertNotIn(".refactor-loop/runs/*.log", self.helper)
        self.assertNotIn(".refactor-loop/prompts/*.log", self.helper)
        self.assertLess(
            self.helper.index("stat -c %Y \"$path\""),
            self.helper.index("stat -f %m \"$path\""),
        )

    def test_restart_helper_hooks_retention_before_daemon_start(self) -> None:
        self.assertIn("run_log_retention", self.restart)
        self.assertIn('bash "$helper"', self.restart)
        self.assertLess(
            self.restart.index("run_log_retention"),
            self.restart.index('start_daemon "concurrency_monitor"'),
        )
        self.assertIn("log_retention warning: helper failed; continuing daemon restart", self.restart)


if __name__ == "__main__":
    unittest.main()
