#!/usr/bin/env python3
"""Behavior and source-regression tests for gh usage accounting."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock


SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_ROOT = SCRIPT_DIR.parent
REPO_ROOT = SCRIPT_DIR.parents[2]
CLI = SCRIPT_DIR / "consensus-rnd-cli"
SHIM = SCRIPT_DIR / "ghwrap" / "gh"
sys.path.insert(0, str(SCRIPT_DIR))

from codex_refactor_loop.gh_accounting import (
    DEFAULT_RETENTION_LINES,
    aggregate_records,
    accounting_env,
    classify_pool,
    classify_subcommand,
    default_usage_path,
    load_records,
    run_real_gh,
)
from codex_refactor_loop import spawn
from codex_refactor_loop.cli import COMMANDS, CommandSpec, RuntimeCommandRouter
from codex_refactor_loop.context import LoopContext
from codex_refactor_loop.processes import ProcessSupervisor
from codex_refactor_loop.restart import DAEMON_COMMANDS, DaemonProcessInventory, RestartConfig, RestartDaemons


class StringBuffer:
    def __init__(self) -> None:
        self._raw = bytearray()

    @property
    def buffer(self) -> "StringBuffer":
        return self

    @property
    def text(self) -> str:
        return self._raw.decode("utf-8", errors="replace")

    def write(self, value) -> int:
        raw = value if isinstance(value, bytes) else str(value).encode("utf-8", errors="replace")
        self._raw.extend(raw)
        return len(raw)

    def flush(self) -> None:
        return None


class GhAccountingBehaviorTests(unittest.TestCase):
    def setUp(self) -> None:
        self._old_consensus_rnd_host_env = os.environ.pop("CONSENSUS_RND_HOST_ENV", None)
        self.tmp = Path(tempfile.mkdtemp(prefix="gh-accounting-test-"))
        self.repo = self.tmp / "repo"
        self.realbin = self.tmp / "realbin"
        self.repo.mkdir()
        self.realbin.mkdir()
        (self.repo / ".refactor-loop" / "state").mkdir(parents=True)
        (self.repo / ".config" / "consensus-rnd").mkdir(parents=True)
        (self.repo / ".config" / "consensus-rnd" / "host.env").write_text(
            f'export REPO_ROOT="{self.repo}"\nexport GH_REPO_SLUG="owner/repo"\n',
            encoding="utf-8",
        )
        self.usage = self.repo / ".refactor-loop" / "state" / "gh-usage.jsonl"
        self.fake_gh = self.realbin / "gh"
        self.fake_gh.write_text(
            textwrap.dedent(
                """\
                #!/usr/bin/env bash
                printf 'fake stdout:%s\\n' "$*"
                printf 'fake stderr:%s\\n' "$*" >&2
                if [[ "$1" == "fail" ]]; then exit 23; fi
                exit 0
                """
            ),
            encoding="utf-8",
        )
        self.fake_gh.chmod(0o755)

    def tearDown(self) -> None:
        if self._old_consensus_rnd_host_env is not None:
            os.environ["CONSENSUS_RND_HOST_ENV"] = self._old_consensus_rnd_host_env
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_shim(self, args: list[str], *, source: str = "controller", extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env.update(
            {
                "PATH": f"{SHIM.parent}{os.pathsep}{self.realbin}{os.pathsep}{env.get('PATH', '')}",
                "REPO_ROOT": str(self.repo),
                "CRND_GH_SOURCE": source,
                "CRND_GH_USAGE_PATH": str(self.usage),
            }
        )
        if extra_env:
            env.update(extra_env)
        return subprocess.run(
            [str(SHIM), *args],
            cwd=self.repo,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    def read_records(self) -> list[dict]:
        return [json.loads(line) for line in self.usage.read_text(encoding="utf-8").splitlines()]

    def test_shim_records_source_pool_subcommand_and_preserves_stdio_exit(self) -> None:
        result = self.run_shim(["issue", "view", "455", "--json", "comments"], source="codex:issue455-r1")

        self.assertEqual(0, result.returncode)
        self.assertIn("fake stdout:issue view 455 --json comments", result.stdout)
        self.assertIn("fake stderr:issue view 455 --json comments", result.stderr)
        records = self.read_records()
        self.assertEqual(1, len(records))
        self.assertEqual("codex:issue455-r1", records[0]["source"])
        self.assertEqual("issue view", records[0]["subcommand"])
        self.assertEqual("graphql", records[0]["pool"])
        self.assertEqual(0, records[0]["exit_code"])
        self.assertEqual(1, records[0]["schema"])

    def test_rest_vs_graphql_classification_is_stable(self) -> None:
        cases = {
            ("api", "repos/owner/repo/issues/1"): "rest_core",
            ("api", "graphql", "-f", "query=x"): "graphql",
            ("pr", "list", "--json", "number"): "graphql",
            ("search", "issues", "term"): "graphql",
            ("release", "view"): "rest_core",
        }
        for argv, pool in cases.items():
            with self.subTest(argv=argv):
                self.assertEqual(pool, classify_pool(argv))
        self.assertEqual("api", classify_subcommand(["api", "repos/owner/repo"]))
        self.assertEqual("pr merge", classify_subcommand(["pr", "merge", "1"]))

    def test_nonzero_real_gh_exit_is_preserved_and_recorded(self) -> None:
        result = self.run_shim(["fail", "now"], source="daemon:comment-monitor")

        self.assertEqual(23, result.returncode)
        record = self.read_records()[0]
        self.assertEqual("daemon:comment-monitor", record["source"])
        self.assertEqual(23, record["exit_code"])
        self.assertEqual("unknown", record["pool"])

    def test_shim_sleeps_for_active_secondary_backoff_before_real_gh(self) -> None:
        state = self.repo / ".refactor-loop" / "state" / "secondary-mutation-backoff.json"
        state.write_text(json.dumps({"until_epoch": 110, "mutation": "readThrottle", "reason": "unit"}) + "\n", encoding="utf-8")
        env = os.environ.copy()
        env.update(
            {
                "PATH": f"{SHIM.parent}{os.pathsep}{self.realbin}{os.pathsep}{env.get('PATH', '')}",
                "REPO_ROOT": str(self.repo),
                "CRND_GH_SECONDARY_BACKOFF_JITTER_MAX_SECONDS": "0",
            }
        )
        call_order: list[str] = []

        class FakeProcess:
            returncode = 0

            def communicate(self) -> tuple[bytes, bytes]:
                return (b"real stdout\n", b"real stderr\n")

        def fake_sleep(seconds: float) -> None:
            call_order.append(f"sleep:{seconds}")

        def fake_popen(command: list[str], **_kwargs) -> FakeProcess:
            call_order.append("popen:" + " ".join(command[1:]))
            return FakeProcess()

        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch("codex_refactor_loop.gh_accounting.time.time", return_value=100):
                with mock.patch("codex_refactor_loop.gh_accounting.time.sleep", side_effect=fake_sleep) as sleep:
                    with mock.patch("codex_refactor_loop.gh_accounting.subprocess.Popen", side_effect=fake_popen):
                        with mock.patch("codex_refactor_loop.gh_accounting.sys.stdout", new_callable=StringBuffer) as stdout:
                            with mock.patch("codex_refactor_loop.gh_accounting.sys.stderr", new_callable=StringBuffer) as stderr:
                                exit_code = run_real_gh(["issue", "view", "1"], argv0=str(SHIM))

        self.assertEqual(0, exit_code)
        sleep.assert_called_once_with(10.0)
        self.assertEqual(["sleep:10.0", "popen:issue view 1"], call_order)
        self.assertIn("real stdout", stdout.text)
        self.assertIn("real stderr", stderr.text)
        self.assertIn("ghwrap: secondary-backoff sleep=10.000s until=110", stderr.text)

    def test_shim_records_secondary_rate_limit_from_read_command_output(self) -> None:
        self.fake_gh.write_text(
            "#!/usr/bin/env bash\nprintf 'You have exceeded a secondary rate limit\\n' >&2\nexit 1\n",
            encoding="utf-8",
        )
        self.fake_gh.chmod(0o755)

        result = self.run_shim(
            ["issue", "view", "455"],
            extra_env={"SECONDARY_MUTATION_BACKOFF_SECONDS": "30", "CRND_GH_SECONDARY_BACKOFF_JITTER_MAX_SECONDS": "0"},
        )

        self.assertEqual(1, result.returncode)
        self.assertIn("You have exceeded a secondary rate limit", result.stderr)
        state = json.loads((self.repo / ".refactor-loop/state/secondary-mutation-backoff.json").read_text(encoding="utf-8"))
        self.assertEqual("genericSecondary", state["genericSecondary"]["operation"])
        self.assertEqual(30, state["genericSecondary"]["cooldown_seconds"])

    def test_shim_records_named_secondary_mutation_from_write_command_output(self) -> None:
        self.fake_gh.write_text(
            "#!/usr/bin/env bash\nprintf 'GraphQL: was submitted too quickly (createPullRequest)\\n' >&2\nexit 1\n",
            encoding="utf-8",
        )
        self.fake_gh.chmod(0o755)

        result = self.run_shim(
            ["pr", "create", "--title", "x"],
            extra_env={"SECONDARY_MUTATION_BACKOFF_SECONDS": "30", "CRND_GH_SECONDARY_BACKOFF_JITTER_MAX_SECONDS": "0"},
        )

        self.assertEqual(1, result.returncode)
        self.assertIn("createPullRequest", result.stderr)
        state = json.loads((self.repo / ".refactor-loop/state/secondary-mutation-backoff.json").read_text(encoding="utf-8"))
        self.assertEqual("createPullRequest", state["mutationThrottle"]["mutation"])
        self.assertEqual(30, state["mutationThrottle"]["cooldown_seconds"])

    def test_shim_secondary_backoff_import_failure_fails_open_to_real_gh(self) -> None:
        env = os.environ.copy()
        env.update(
            {
                "PATH": f"{SHIM.parent}{os.pathsep}{self.realbin}{os.pathsep}{env.get('PATH', '')}",
                "REPO_ROOT": str(self.repo),
            }
        )

        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch("codex_refactor_loop.gh_accounting.secondary_state_dir_for_repo", side_effect=RuntimeError("boom")):
                with mock.patch("codex_refactor_loop.gh_accounting.time.sleep", side_effect=AssertionError("must not sleep")):
                    exit_code = run_real_gh(["issue", "view", "1"], argv0=str(SHIM))

        self.assertEqual(0, exit_code)

    def test_accounting_failure_fails_open_after_real_gh(self) -> None:
        bad_parent = self.repo / "not-a-dir"
        bad_parent.write_text("not a directory", encoding="utf-8")
        bad_usage = bad_parent / "gh-usage.jsonl"

        result = self.run_shim(["pr", "list"], extra_env={"CRND_GH_USAGE_PATH": str(bad_usage)})

        self.assertEqual(0, result.returncode)
        self.assertIn("fake stdout:pr list", result.stdout)

    def test_usage_path_override_ignores_repo_escape_and_preserves_real_gh(self) -> None:
        outside = self.tmp / "outside" / "gh-usage.jsonl"

        result = self.run_shim(["pr", "list"], extra_env={"CRND_GH_USAGE_PATH": str(outside)})

        self.assertEqual(0, result.returncode)
        self.assertIn("fake stdout:pr list", result.stdout)
        self.assertFalse(outside.exists())
        records = self.read_records()
        self.assertEqual(1, len(records))
        self.assertEqual("pr list", records[0]["subcommand"])

    def test_repo_contained_usage_path_override_is_allowed(self) -> None:
        contained = self.repo / ".refactor-loop" / "state" / "custom-gh-usage.jsonl"

        result = self.run_shim(["issue", "view", "1"], extra_env={"CRND_GH_USAGE_PATH": str(contained)})

        self.assertEqual(0, result.returncode)
        self.assertTrue(contained.exists())
        self.assertFalse(self.usage.exists())
        self.assertEqual("issue view", json.loads(contained.read_text(encoding="utf-8"))["subcommand"])

    def test_import_failure_fallback_delegates_to_real_gh(self) -> None:
        isolated_scripts = self.tmp / "isolated" / "scripts"
        isolated_shim_dir = isolated_scripts / "ghwrap"
        isolated_shim_dir.mkdir(parents=True)
        isolated_shim = isolated_shim_dir / "gh"
        shutil.copy2(SHIM, isolated_shim)

        env = os.environ.copy()
        env.update(
            {
                "PATH": f"{isolated_shim_dir}{os.pathsep}{self.realbin}{os.pathsep}{env.get('PATH', '')}",
                "REPO_ROOT": str(self.repo),
                "CRND_GH_SOURCE": "controller",
                "CRND_GH_USAGE_PATH": str(self.usage),
            }
        )
        env.pop("PYTHONPATH", None)

        result = subprocess.run(
            [str(isolated_shim), "fail", "now"],
            cwd=self.repo,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(23, result.returncode)
        self.assertIn("fake stdout:fail now", result.stdout)
        self.assertIn("fake stderr:fail now", result.stderr)
        self.assertFalse(self.usage.exists())

    def test_shim_skips_other_ghwrap_dirs_when_resolving_real_gh(self) -> None:
        other_scripts = self.tmp / "other" / "skills" / "consensus-loop" / "scripts"
        other_shim_dir = other_scripts / "ghwrap"
        other_shim_dir.mkdir(parents=True)
        other_shim = other_shim_dir / "gh"
        shutil.copy2(SHIM, other_shim)

        env = os.environ.copy()
        env.update(
            {
                "PATH": f"{SHIM.parent}{os.pathsep}{other_shim_dir}{os.pathsep}{self.realbin}{os.pathsep}{env.get('PATH', '')}",
                "REPO_ROOT": str(self.repo),
                "CRND_GH_SOURCE": "daemon:phase9-router",
                "CRND_GH_USAGE_PATH": str(self.usage),
            }
        )

        result = subprocess.run(
            [str(SHIM), "api", "repos/owner/repo/issues/191"],
            cwd=self.repo,
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )

        self.assertEqual(0, result.returncode)
        self.assertIn("fake stdout:api repos/owner/repo/issues/191", result.stdout)
        self.assertEqual(1, len(self.read_records()))

    def test_retention_bounds_jsonl_artifact(self) -> None:
        for index in range(3):
            result = self.run_shim(["api", f"repos/owner/repo/issues/{index}"], extra_env={"CRND_GH_USAGE_MAX_LINES": "2"})
            self.assertEqual(0, result.returncode)

        records = self.read_records()
        self.assertEqual(2, len(records))
        self.assertTrue(records[0]["subcommand"] == "api")

    def test_retention_override_is_bounded(self) -> None:
        for raw in ("0", "-1", str(DEFAULT_RETENTION_LINES + 1), "not-int"):
            with self.subTest(raw=raw):
                for index in range(3):
                    result = self.run_shim(["api", f"repos/owner/repo/issues/{index}"], extra_env={"CRND_GH_USAGE_MAX_LINES": raw})
                    self.assertEqual(0, result.returncode)
                self.assertEqual(3, len(self.read_records()))
                self.usage.unlink()

    def test_aggregate_reports_per_source_pool_and_subcommand(self) -> None:
        self.run_shim(["issue", "view", "1"], source="controller")
        self.run_shim(["api", "repos/owner/repo/issues/1"], source="daemon:progress-reporter")

        summary = aggregate_records(load_records(self.usage), window_minutes=60)

        self.assertEqual(2, summary["total"]["calls"])
        self.assertEqual(1, summary["total"]["by_source"]["controller"])
        self.assertEqual(1, summary["total"]["by_source"]["daemon:progress-reporter"])
        self.assertEqual(1, summary["total"]["by_pool"]["graphql"])
        self.assertEqual(1, summary["total"]["by_pool"]["rest_core"])

    def test_accounting_env_prepends_shim_without_dropping_path_or_source(self) -> None:
        env = accounting_env({"PATH": "/usr/bin"}, skill_root=SKILL_ROOT, repo_root=self.repo, source="controller")

        self.assertTrue(env["PATH"].split(os.pathsep)[0].endswith("scripts/ghwrap"))
        self.assertIn("/usr/bin", env["PATH"])
        self.assertEqual(str(self.usage), env["CRND_GH_USAGE_PATH"])
        self.assertEqual("controller", env["CRND_GH_SOURCE"])

    def test_accounting_env_rewrites_escaping_usage_path_to_repo_default(self) -> None:
        outside = self.tmp / "outside" / "gh-usage.jsonl"
        env = accounting_env(
            {"PATH": "/usr/bin", "CRND_GH_USAGE_PATH": str(outside)},
            skill_root=SKILL_ROOT,
            repo_root=self.repo,
            source="controller",
        )

        self.assertEqual(str(self.usage), env["CRND_GH_USAGE_PATH"])
        self.assertEqual(
            self.usage.resolve(),
            default_usage_path({"REPO_ROOT": str(self.repo), "CRND_GH_USAGE_PATH": "../escape.jsonl"}).resolve(),
        )

    def test_process_supervisor_passes_env_to_child(self) -> None:
        prompt = self.tmp / "prompt.md"
        log = self.tmp / "codex.log"
        prompt.write_text("prompt\n", encoding="utf-8")

        code = "import os; print(os.environ.get('CRND_GH_SOURCE', 'missing'))"
        exit_code = ProcessSupervisor(poll_interval=0.01).supervise(
            [sys.executable, "-c", code],
            stdin=prompt,
            log=log,
            stall=5,
            env={"PATH": os.environ.get("PATH", ""), "CRND_GH_SOURCE": "codex:task"},
        )

        self.assertEqual(0, exit_code)
        self.assertIn("codex:task", log.read_text(encoding="utf-8"))

    def test_cli_exposes_read_only_gh_stats(self) -> None:
        self.run_shim(["issue", "view", "1"])
        env = os.environ.copy()
        env.update({"REPO_ROOT": str(self.repo), "CRND_GH_USAGE_PATH": str(self.usage)})

        result = subprocess.run(
            [sys.executable, str(CLI), "gh-stats", "--json"],
            cwd=self.repo,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(0, result.returncode, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(1, payload["total"]["by_pool"]["graphql"])

    def test_cli_rejects_undocumented_path_argument(self) -> None:
        outside = self.tmp / "outside" / "gh-usage.jsonl"
        outside.parent.mkdir()
        outside.write_text(
            json.dumps(
                {
                    "schema": 1,
                    "ts": "2026-06-03T00:00:00Z",
                    "source": "outside",
                    "subcommand": "pr list",
                    "pool": "graphql",
                    "exit_code": 0,
                    "count": 99,
                }
            )
            + "\n",
            encoding="utf-8",
        )
        env = os.environ.copy()
        env.update({"REPO_ROOT": str(self.repo), "CRND_GH_USAGE_PATH": str(self.usage)})

        result = subprocess.run(
            [sys.executable, str(CLI), "gh-stats", "--json", "--path", str(outside)],
            cwd=self.repo,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertNotEqual(0, result.returncode)
        self.assertIn("unrecognized arguments: --path", result.stderr)
        self.assertNotIn("outside", result.stdout)

    def test_controller_router_accounts_handler_gh_calls_as_controller(self) -> None:
        def handler(_args: list[str] | None) -> int:
            return subprocess.run(["gh", "issue", "view", "457"], check=False).returncode

        env = {
            "PATH": f"{self.realbin}{os.pathsep}/usr/bin{os.pathsep}/bin",
            "REPO_ROOT": str(self.repo),
            "CRND_GH_USAGE_PATH": str(self.usage),
        }

        with mock.patch.dict(COMMANDS, {"accounting-test": CommandSpec(handler, "test command", ("read-gh",))}):
            with mock.patch.dict(os.environ, env, clear=True):
                exit_code = RuntimeCommandRouter(script_dir=SCRIPT_DIR).run("accounting-test", [])

        self.assertEqual(0, exit_code)
        record = self.read_records()[0]
        self.assertEqual("controller", record["source"])
        self.assertEqual("issue view", record["subcommand"])
        self.assertEqual("graphql", record["pool"])

    def test_restart_daemon_child_receives_daemon_accounting_environment(self) -> None:
        env_path = self.tmp / "daemon-env.json"
        child_code = textwrap.dedent(
            """\
            import json, os, signal
            from pathlib import Path
            Path(os.environ["TEST_DAEMON_ENV_PATH"]).write_text(json.dumps({
                "source": os.environ.get("CRND_GH_SOURCE"),
                "usage": os.environ.get("CRND_GH_USAGE_PATH"),
                "path_head": os.environ.get("PATH", "").split(os.pathsep)[0],
            }, sort_keys=True), encoding="utf-8")
            Path(os.environ["RESTART_DAEMON_HEARTBEAT_FILE"]).write_text("1\\n", encoding="utf-8")
            signal.signal(signal.SIGTERM, lambda _signum, _frame: raise_system_exit())
            def raise_system_exit():
                raise SystemExit(0)
            while True:
                signal.pause()
            """
        )
        command = (sys.executable, "-c", child_code)
        ctx = LoopContext.load(
            repo_root=self.repo,
            skill_root=SKILL_ROOT,
            env={"CONSENSUS_RND_HOST_ENV": ".config/consensus-rnd/host.env"},
        )
        helper = RestartDaemons(ctx, RestartConfig(heartbeat_fresh_seconds=30, heartbeat_interval=1, stop_grace_seconds=1))
        env = {
            "PATH": f"{self.realbin}{os.pathsep}/usr/bin{os.pathsep}/bin",
            "REPO_ROOT": str(self.repo),
            "TEST_DAEMON_ENV_PATH": str(env_path),
            "CRND_GH_USAGE_PATH": str(self.usage),
        }

        try:
            with mock.patch.dict(os.environ, env, clear=True):
                with mock.patch("codex_refactor_loop.restart.DaemonProcessInventory.collect", return_value=DaemonProcessInventory(())):
                    helper.start_daemon("accounting-daemon", command)
            payload = json.loads(env_path.read_text(encoding="utf-8"))
            self.assertEqual("daemon:accounting-daemon", payload["source"])
            self.assertEqual(str(self.usage.resolve()), payload["usage"])
            self.assertEqual(str(SKILL_ROOT / "scripts" / "ghwrap"), payload["path_head"])
        finally:
            for proc in helper._wrappers:
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        proc.kill()

    def test_spawn_codex_uses_log_derived_task_source_and_accounting_environment(self) -> None:
        env_path = self.tmp / "codex-env.json"
        prompt = self.tmp / "prompt.md"
        log = self.tmp / "review-pr457 fix!.log"
        prompt.write_text("prompt\n", encoding="utf-8")
        fake_codex = self.realbin / "codex"
        fake_codex.write_text(
            textwrap.dedent(
                f"""\
                #!{sys.executable}
                import json, os, sys
                from pathlib import Path
                Path(os.environ["TEST_CODEX_ENV_PATH"]).write_text(json.dumps({{
                    "argv": sys.argv[1:],
                    "source": os.environ.get("CRND_GH_SOURCE"),
                    "usage": os.environ.get("CRND_GH_USAGE_PATH"),
                    "path_head": os.environ.get("PATH", "").split(os.pathsep)[0],
                }}, sort_keys=True), encoding="utf-8")
                raise SystemExit(0)
                """
            ),
            encoding="utf-8",
        )
        fake_codex.chmod(0o755)
        env = {
            "PATH": f"{self.realbin}{os.pathsep}/usr/bin{os.pathsep}/bin",
            "REPO_ROOT": str(self.repo),
            "TEST_CODEX_ENV_PATH": str(env_path),
            "CRND_GH_USAGE_PATH": str(self.usage),
        }

        with mock.patch.dict(os.environ, env, clear=True):
            exit_code = spawn.main(
                [
                    "--cd",
                    str(self.repo),
                    "--prompt",
                    str(prompt),
                    "--log",
                    str(log),
                    "--stall",
                    "5",
                ]
            )

        self.assertEqual(0, exit_code)
        payload = json.loads(env_path.read_text(encoding="utf-8"))
        self.assertEqual("codex:review-pr457-fix", payload["source"])
        self.assertEqual(str(self.usage.resolve()), payload["usage"])
        self.assertEqual(str(SKILL_ROOT / "scripts" / "ghwrap"), payload["path_head"])
        self.assertEqual(["exec", "--dangerously-bypass-approvals-and-sandbox", "--skip-git-repo-check", "-C", str(self.repo), "-"], payload["argv"])

    def test_spawn_codex_main_defers_during_secondary_backoff_without_worker_launch(self) -> None:
        prompt = self.tmp / "prompt.md"
        log = self.tmp / "review-pr458-fix.log"
        prompt.write_text("prompt\n", encoding="utf-8")
        state = self.repo / ".refactor-loop" / "state" / "secondary-mutation-backoff.json"
        state.write_text(json.dumps({"until_epoch": 110, "mutation": "readThrottle", "reason": "unit"}) + "\n", encoding="utf-8")
        env = {
            "PATH": f"{self.realbin}{os.pathsep}/usr/bin{os.pathsep}/bin",
            "REPO_ROOT": str(self.repo),
            "CRND_GH_USAGE_PATH": str(self.usage),
        }

        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch("codex_refactor_loop.secondary_mutation_backoff.time.time", return_value=100):
                with mock.patch("codex_refactor_loop.spawn.ProcessSupervisor") as supervisor:
                    with mock.patch("codex_refactor_loop.spawn.sys.stderr", new_callable=StringBuffer) as stderr:
                        exit_code = spawn.main(
                            [
                                "--cd",
                                str(self.repo),
                                "--prompt",
                                str(prompt),
                                "--log",
                                str(log),
                                "--stall",
                                "5",
                            ]
                        )

        self.assertEqual(3, exit_code)
        supervisor.assert_not_called()
        self.assertIn("SPAWN_CODEX_BACKOFF:secondary until=110", stderr.text)
        self.assertFalse(log.exists())


class GhAccountingSourceRegressionTests(unittest.TestCase):
    def test_shim_transparent_forwarding_and_fail_open_contract_is_literal(self) -> None:
        source = SHIM.read_text(encoding="utf-8")
        module = (SCRIPT_DIR / "codex_refactor_loop" / "gh_accounting.py").read_text(encoding="utf-8")
        for token in (
            "Transparent gh shim",
            "run_real_gh(argv, argv0=sys.argv[0])",
            "record_gh_call(argv, exit_code)",
            "except Exception:\n        pass",
            "subprocess.Popen([real, *sys.argv[1:]], stdout=subprocess.PIPE, stderr=subprocess.PIPE)",
        ):
            with self.subTest(token=token):
                self.assertIn(token, source)
        for token in (
            "DEFAULT_ARTIFACT_RELATIVE = Path(\".refactor-loop\") / \"state\" / \"gh-usage.jsonl\"",
            "SECONDARY_STATE_RELATIVE = Path(\".refactor-loop\") / \"state\"",
            "CRND_GH_USAGE_PATH",
            "_repo_contained_path",
            "resolved.relative_to(root)",
            "currently_backing_off",
            "record_generic_secondary_backoff_from_gh_output",
            "CRND_GH_USAGE_MAX_LINES",
            "1 <= value <= DEFAULT_RETENTION_LINES",
            "schema",
            "ts",
            "source",
            "subcommand",
            "pool",
            "exit_code",
            "count",
            "DEFAULT_RETENTION_LINES",
        ):
            with self.subTest(token=token):
                self.assertIn(token, module)
        self.assertNotIn("CRND_GH_REAL", source)
        self.assertNotIn("CRND_GH_REAL", module)

    def test_spawn_and_daemon_paths_inject_path_and_crnd_source(self) -> None:
        spawn = (SCRIPT_DIR / "codex_refactor_loop" / "spawn.py").read_text(encoding="utf-8")
        restart = (SCRIPT_DIR / "codex_refactor_loop" / "restart.py").read_text(encoding="utf-8")
        cli = (SCRIPT_DIR / "codex_refactor_loop" / "cli.py").read_text(encoding="utf-8")

        self.assertIn("source=f\"codex:{task_id}\"", spawn)
        self.assertIn("force_source=True", spawn)
        self.assertIn("env=child_env", spawn)
        self.assertIn("source=f\"daemon:{name}\"", restart)
        self.assertIn("force_source=True", restart)
        self.assertIn("activate_controller_accounting", cli)
        for name, _command in DAEMON_COMMANDS:
            with self.subTest(daemon=name):
                self.assertIn(name, restart)

    def test_gh_stats_command_declares_read_state_only(self) -> None:
        cli = (SCRIPT_DIR / "codex_refactor_loop" / "cli.py").read_text(encoding="utf-8")
        module = (SCRIPT_DIR / "codex_refactor_loop" / "gh_accounting.py").read_text(encoding="utf-8")
        self.assertIn('"gh-stats": CommandSpec(gh_stats_main, "read local gh usage accounting", ("read-state",))', cli)
        self.assertNotIn('"gh-stats": CommandSpec(gh_stats_main, "read local gh usage accounting", ("read-gh",))', cli)
        self.assertIn("load_records(default_usage_path())", module)
        self.assertNotIn('parser.add_argument("--path")', module)

    def test_runtime_surface_bounds_are_documented(self) -> None:
        skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        authorization = (SKILL_ROOT / "authorizations" / "runtime-exceptions.md").read_text(encoding="utf-8")
        for source in (skill, authorization):
            with self.subTest(source="skill" if source is skill else "authorization"):
                self.assertIn("CRND_GH_USAGE_PATH", source)
                self.assertIn("repo-relative or repo-contained", source)
                self.assertIn("CRND_GH_USAGE_MAX_LINES", source)
                self.assertIn("invalid, non-positive, or larger values fall back to the default", source)
                self.assertIn("no accounting artifact outside `$REPO_ROOT`", source)

    def test_closed_label_reconciler_avoids_full_history_closed_managed_hot_path(self) -> None:
        reconciler = (SCRIPT_DIR / "codex_refactor_loop" / "closed_label_reconciler.py").read_text(encoding="utf-8")
        projection = (SCRIPT_DIR / "codex_refactor_loop" / "closed_phase_labels.py").read_text(encoding="utf-8")
        peek = (SCRIPT_DIR / "codex_refactor_loop" / "peek.py").read_text(encoding="utf-8")

        self.assertIn("closed_reconcile_candidate_queries", reconciler)
        self.assertIn("plan_closed_reconcile_candidate", reconciler)
        self.assertIn("plan_closed_reconcile_candidate(kind, item) is None", reconciler)
        self.assertIn("closed_reconcile_candidate_queries", peek)
        self.assertIn("plan_closed_reconcile_candidate", peek)
        self.assertIn("managed_label: str", projection)
        self.assertIn("dirty_label: str | None", projection)
        self.assertIn('args.extend(["--search", f\'label:"{self.dirty_label}"\'])', projection)
        self.assertIn("item_matches_closed_reconcile_query", projection)
        self.assertIn("label_catalog.MANAGED not in projection.canonical", projection)
        self.assertIn("query.gh_args(fields)", reconciler)
        self.assertIn("item_matches_closed_reconcile_query(kind, item, query)", reconciler)
        self.assertIn("(query.managed_label,)", peek)
        self.assertIn("item_matches_closed_reconcile_query(kind, item, query)", peek)
        self.assertIn('search=f\'label:"{query.dirty_label}"\' if query.dirty_label else None', peek)
        self.assertIn("RECENT_CLOSED_MANAGED_WINDOW_LIMIT", projection)
        self.assertIn("NONTERMINAL_PHASE_LABELS", projection)
        self.assertIn("label_catalog.STUCK", projection)
        self.assertNotIn("def _has_human_label_drift", reconciler)
        self.assertNotIn("expected exactly one canonical human label", reconciler)
        self.assertNotIn("query.label", reconciler)
        self.assertNotIn("query.label", peek)
        for forbidden in (
            '"--label", label_catalog.MANAGED, "--state", "closed", "--limit", "100"',
            '"--label", query_label, "--state", state, "--limit", "100", "--json", fields',
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, reconciler)


if __name__ == "__main__":
    unittest.main()
