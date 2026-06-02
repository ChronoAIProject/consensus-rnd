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
)
from codex_refactor_loop.processes import ProcessSupervisor
from codex_refactor_loop.restart import DAEMON_COMMANDS


class GhAccountingBehaviorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="gh-accounting-test-"))
        self.repo = self.tmp / "repo"
        self.realbin = self.tmp / "realbin"
        self.repo.mkdir()
        self.realbin.mkdir()
        (self.repo / ".refactor-loop" / "state").mkdir(parents=True)
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


class GhAccountingSourceRegressionTests(unittest.TestCase):
    def test_shim_transparent_forwarding_and_fail_open_contract_is_literal(self) -> None:
        source = SHIM.read_text(encoding="utf-8")
        module = (SCRIPT_DIR / "codex_refactor_loop" / "gh_accounting.py").read_text(encoding="utf-8")
        for token in (
            "Transparent gh shim",
            "run_real_gh(argv, argv0=sys.argv[0])",
            "record_gh_call(argv, exit_code)",
            "except Exception:\n        pass",
            "subprocess.call([real, *sys.argv[1:]])",
        ):
            with self.subTest(token=token):
                self.assertIn(token, source)
        for token in (
            "LIFECYCLE_AUTHORITY_BOUNDARY",
            "observability-only: no issue/PR/label lifecycle",
            "no merge/close",
            "no tag/release",
            "DEFAULT_ARTIFACT_RELATIVE = Path(\".refactor-loop\") / \"state\" / \"gh-usage.jsonl\"",
            "CRND_GH_USAGE_PATH",
            "_repo_contained_path",
            "resolved.relative_to(root)",
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
        self.assertIn('"gh-stats": CommandSpec(gh_stats_main, "read local gh usage accounting", ("read-state",))', cli)
        self.assertNotIn('"gh-stats": CommandSpec(gh_stats_main, "read local gh usage accounting", ("read-gh",))', cli)

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


if __name__ == "__main__":
    unittest.main()
