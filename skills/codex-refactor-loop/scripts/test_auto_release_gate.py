#!/usr/bin/env python3
"""Behavior tests for consensus-rnd-cli release-gate."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve()
REPO_ROOT = SCRIPT_PATH.parents[3]
NOW = datetime(2026, 5, 26, 12, 0, tzinfo=timezone.utc)
sys.path.insert(0, str(SCRIPT_PATH.parent))

from codex_refactor_loop import labels as label_catalog
from codex_refactor_loop.restart import restart_managed_daemon_names
from codex_refactor_loop.release.gate import AutoReleaseGate, CommitInfo, classify_bump
from codex_refactor_loop.release.versions import next_release_version


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


class FakeRunner:
    def __init__(self) -> None:
        self.commands: list[list[str]] = []

    def __call__(self, cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        self.commands.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")


def copy_repo_fixture() -> tempfile.TemporaryDirectory[str]:
    tmp = tempfile.TemporaryDirectory()
    repo = Path(tmp.name) / "repo"
    paths = [
        ".version-bump.json",
        "package.json",
        ".claude-plugin/plugin.json",
        ".claude-plugin/marketplace.json",
        ".codex-plugin/plugin.json",
        ".cursor-plugin/plugin.json",
        "gemini-extension.json",
        "skills/codex-refactor-loop/VERSION.json",
    ]
    for relative in paths:
        source = REPO_ROOT / relative
        target = repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-q", "-m", "fix: fixture"],
        cwd=repo,
        check=True,
    )
    return tmp


def write_opt_in(
    repo: Path,
    enabled: bool = True,
    review_base: str = "review-base",
    integration: str = "integration-branch",
    repo_slug: str = "owner/repo",
) -> None:
    env_path = repo / ".config" / "consensus-rnd" / "host.env"
    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.write_text(
        "\n".join(
            [
                f"export RELEASE_AUTO_ENABLE={'true' if enabled else 'false'}",
                f"export GH_REPO_SLUG={repo_slug}",
                f"export REVIEW_BASE_BRANCH={review_base}",
                f"export INTEGRATION_BRANCH={integration}",
                "export HOST_GITHUB_RELEASE_REQUIRED_CHECKS=contract-tests,manifest-version-sync,skill-degradation",
                "",
            ]
        ),
        encoding="utf-8",
    )


def write_explicit_opt_in(
    repo: Path,
    enabled: bool = True,
    review_base: str = "review-base",
    integration: str = "integration-branch",
    repo_slug: str = "owner/repo",
) -> Path:
    env_path = repo / ".config" / "consensus-rnd" / "host.env"
    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.write_text(
        "\n".join(
            [
                f"export RELEASE_AUTO_ENABLE={'true' if enabled else 'false'}",
                f"export GH_REPO_SLUG={repo_slug}",
                f"export REVIEW_BASE_BRANCH={review_base}",
                f"export INTEGRATION_BRANCH={integration}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return env_path


def write_gh_stub(
    bin_dir: Path,
    *,
    check_conclusions: dict[str, str] | None = None,
    check_names: list[str] | None = None,
    check_statuses: dict[str, str] | None = None,
    api_exit_code: int = 0,
    api_stdout: str | None = None,
    labeled_items: dict[str, list[dict[str, int]]] | None = None,
    list_failures: dict[str, str] | None = None,
) -> None:
    check_conclusions = check_conclusions or {}
    check_names = check_names or ["contract-tests", "manifest-version-sync", "skill-degradation"]
    check_statuses = check_statuses or {}
    labeled_items = labeled_items or {}
    list_failures = list_failures or {}
    gh = bin_dir / "gh"
    gh.write_text(
        f"""#!/usr/bin/env python3
import json
import sys
from datetime import datetime, timezone

now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
args = sys.argv[1:]
check_conclusions = {json.dumps(check_conclusions, ensure_ascii=False)}
check_names = {json.dumps(check_names, ensure_ascii=False)}
check_statuses = {json.dumps(check_statuses, ensure_ascii=False)}
api_exit_code = {api_exit_code}
api_stdout = {api_stdout!r}
labeled_items = {json.dumps(labeled_items, ensure_ascii=False)}
list_failures = {json.dumps(list_failures, ensure_ascii=False)}
if args[:2] == ["api", "repos/owner/repo/commits/trunk-review/check-runs"] or args[:2] == ["api", "repos/owner/repo/commits/release-integration/check-runs"] or (len(args) >= 2 and args[0] == "api" and args[1].endswith("/check-runs")):
    if api_exit_code:
        print("simulated gh api failure", file=sys.stderr)
        raise SystemExit(api_exit_code)
    if api_stdout is not None:
        print(api_stdout)
        raise SystemExit(0)
    print(json.dumps({{"check_runs": [
        {{"id": index + 1, "created_at": now, "started_at": now, "completed_at": now, "conclusion": check_conclusions.get(name, "success"), "status": check_statuses.get(name, "completed"), "name": name}}
        for index, name in enumerate(check_names)
    ]}}))
    raise SystemExit(0)
if len(args) >= 2 and args[1] == "list":
    kind = args[0]
    failure = list_failures.get(kind)
    if failure == "exit":
        print(f"simulated gh {{kind}} list failure", file=sys.stderr)
        raise SystemExit(3)
    if failure == "invalid-json":
        print("{{not-json")
        raise SystemExit(0)
    label = args[args.index("--label") + 1] if "--label" in args else ""
    print(json.dumps(labeled_items.get(label, [])))
    raise SystemExit(0)
print("[]")
""",
        encoding="utf-8",
    )
    gh.chmod(0o755)


def write_live_state(repo: Path) -> None:
    now = int(datetime.now(timezone.utc).timestamp())
    state = repo / ".refactor-loop/state"
    heartbeat_dir = repo / ".refactor-loop/heartbeats"
    heartbeat_dir.mkdir(parents=True, exist_ok=True)
    for name in restart_managed_daemon_names():
        (heartbeat_dir / f"{name}.ts").write_text(f"{now}\n", encoding="utf-8")
    write_json(state / "phase8-review-state.json", {"max_consecutive_reject_rounds": 0})
    write_json(state / "meta-resolutions.json", {"unresolved_escalate_human": []})
    write_json(state / "recent-pr-merges.json", {"count": 1})
    write_json(state / "release-commits.json", {"commits": [{"sha": "abc123", "subject": "fix: fixture", "body": ""}]})
    write_json(repo / ".refactor-loop/.concurrency-monitor-state.json", {"zero_streak": 0})


def write_heartbeat_files(repo: Path, values: dict[str, int | str]) -> None:
    heartbeat_dir = repo / ".refactor-loop/heartbeats"
    heartbeat_dir.mkdir(parents=True, exist_ok=True)
    for name, value in values.items():
        (heartbeat_dir / f"{name}.ts").write_text(f"{value}\n", encoding="utf-8")


def run_gate_cli(repo: Path, bin_dir: Path | None = None, *extra: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "REPO_ROOT": str(repo), "CONSENSUS_RND_HOST_ENV": ".config/consensus-rnd/host.env"}
    env.pop("REVIEW_BASE_BRANCH", None)
    env.pop("INTEGRATION_BRANCH", None)
    if bin_dir is not None:
        env["PATH"] = f"{bin_dir}{os.pathsep}{os.environ['PATH']}"
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT_PATH.with_name("consensus-rnd-cli")),
            "release-gate",
            "--min-recent-merges",
            "1",
            *extra,
        ],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def assert_no_release_artifacts(test: unittest.TestCase, repo: Path) -> None:
    test.assertFalse((repo / ".refactor-loop/state/release-decision.json").exists())
    test.assertFalse((repo / ".refactor-loop/state/release-candidate.json").exists())


def score_only_summary(test: unittest.TestCase, repo: Path, bin_dir: Path | None = None) -> dict[str, object]:
    result = run_gate_cli(repo, bin_dir, "--score-only")
    test.assertEqual(result.returncode, 0, result.stderr)
    summary = json.loads(result.stdout[result.stdout.index("{"):])
    test.assertIsInstance(summary, dict)
    assert isinstance(summary, dict)
    return summary


def assert_signal_blocked(
    test: unittest.TestCase,
    repo: Path,
    bin_dir: Path | None,
    signal_name: str,
    expected_reason: str,
) -> None:
    summary = score_only_summary(test, repo, bin_dir)
    stability = summary["stability"]
    test.assertIsInstance(stability, dict)
    assert isinstance(stability, dict)
    test.assertFalse(stability["ready"])
    signals = stability["signals"]
    test.assertIsInstance(signals, dict)
    assert isinstance(signals, dict)
    test.assertIn(signal_name, signals)
    signal = signals[signal_name]
    test.assertIsInstance(signal, dict)
    assert isinstance(signal, dict)
    test.assertFalse(signal["passed"])
    test.assertIn(signal_name, str(signal.get("reason", "")))
    test.assertIn(expected_reason, json.dumps(signal, ensure_ascii=False))


def write_green_signals(repo: Path) -> None:
    write_json(
        repo / ".refactor-loop/state/auto-release-signals.json",
        {"signals": {name: True for name in (
            "required_checks_recent_green",
            "no_open_blocked_pr",
            "no_human_decision_label",
            "no_phase8_reject_churn",
            "p0_alert_streak_ok",
            "recent_pr_merges_min",
            "fresh_heartbeats",
            "no_unresolved_human_escalation",
        )}},
    )
    state = repo / ".refactor-loop/state"
    write_json(state / "release-commits.json", {"commits": [{"sha": "abc123", "subject": "fix: fixture", "body": ""}]})


def classify_expected_patch(version: str) -> str:
    return next_release_version(version, "patch")


def set_mapped_version(repo: Path, version: str) -> None:
    # refactor helper, no behavior change
    mapping = read_json(repo / ".version-bump.json")
    assert isinstance(mapping, dict)
    for item in mapping["files"]:
        path = repo / item["path"]
        data = read_json(path)
        current = data
        parts = item["field"].split(".")
        for part in parts[:-1]:
            current = current[int(part)] if isinstance(current, list) else current[part]
        last = parts[-1]
        if isinstance(current, list):
            current[int(last)] = version
        else:
            current[last] = version
        write_json(path, data)


class AutoReleaseGateBehaviorTests(unittest.TestCase):
    def test_no_opt_in_exits_noop(self) -> None:
        with copy_repo_fixture() as tmp:
            repo = Path(tmp) / "repo"
            env = {**os.environ, "REPO_ROOT": str(repo)}
            result = subprocess.run(
                [sys.executable, str(SCRIPT_PATH.with_name("consensus-rnd-cli")), "release-gate", "--min-recent-merges", "0"],
                cwd=repo,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("RELEASE_AUTO_ENABLE is not true", result.stdout)
            self.assertFalse((repo / ".refactor-loop/state/release-decision.json").exists())

    def test_stability_signals_all_green_yields_ready(self) -> None:
        with copy_repo_fixture() as tmp:
            repo = Path(tmp) / "repo"
            write_green_signals(repo)
            gate = AutoReleaseGate(repo, now=lambda: NOW, runner=FakeRunner())
            score = gate.compute_stability()

            self.assertTrue(score.ready)
            self.assertEqual(score.score, 100)

    def test_unstable_signal_blocks_release(self) -> None:
        with copy_repo_fixture() as tmp:
            repo = Path(tmp) / "repo"
            write_green_signals(repo)
            data = read_json(repo / ".refactor-loop/state/auto-release-signals.json")
            assert isinstance(data, dict)
            data["signals"]["fresh_heartbeats"] = False
            write_json(repo / ".refactor-loop/state/auto-release-signals.json", data)
            gate = AutoReleaseGate(repo, now=lambda: NOW, runner=FakeRunner())
            score = gate.compute_stability()

            self.assertFalse(score.ready)
            self.assertLess(score.score, 100)

    def test_commit_prefix_breaking_change_yields_major(self) -> None:
        commits = [CommitInfo("a", "feat!: replace contract", "")]
        self.assertEqual(classify_bump(commits), "major")

    def test_commit_prefix_feat_yields_minor(self) -> None:
        commits = [CommitInfo("a", "fix: repair", ""), CommitInfo("b", "feat: add gate", "")]
        self.assertEqual(classify_bump(commits), "minor")

    def test_commit_prefix_fix_yields_patch(self) -> None:
        commits = [CommitInfo("a", "fix: repair", "")]
        self.assertEqual(classify_bump(commits), "patch")

    def test_min_interval_blocks_too_frequent_release(self) -> None:
        with copy_repo_fixture() as tmp:
            repo = Path(tmp) / "repo"
            write_green_signals(repo)
            write_json(
                repo / ".refactor-loop/state/release-history.json",
                {"latest_release_at": (NOW - timedelta(minutes=30)).isoformat().replace("+00:00", "Z")},
            )
            gate = AutoReleaseGate(repo, now=lambda: NOW, runner=FakeRunner())
            decision = gate.decide_release(gate.compute_stability(), min_interval_hours=2)

            self.assertFalse(decision["ready"])
            self.assertIn("min_interval", decision["blocked_reasons"])
            self.assertEqual(decision["from_version"], decision["to_version"])

    def test_fail_closed_when_required_check_red(self) -> None:
        with copy_repo_fixture() as tmp:
            repo = Path(tmp) / "repo"
            write_opt_in(repo, review_base="trunk-review", integration="release-integration")
            write_live_state(repo)
            bin_dir = repo / "bin"
            bin_dir.mkdir()
            write_gh_stub(bin_dir, check_conclusions={"contract-tests": "failure"})
            env = {
                **os.environ,
                "REPO_ROOT": str(repo),
                "CONSENSUS_RND_HOST_ENV": ".config/consensus-rnd/host.env",
                "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            }
            result = subprocess.run(
                [sys.executable, str(SCRIPT_PATH.with_name("consensus-rnd-cli")), "release-gate", "--min-recent-merges", "0"],
                cwd=repo,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("required_checks_recent_green", result.stdout)
            self.assertFalse((repo / ".refactor-loop/state/release-decision.json").exists())
            score = subprocess.run(
                [sys.executable, str(SCRIPT_PATH.with_name("consensus-rnd-cli")), "release-gate", "--score-only", "--min-recent-merges", "0"],
                cwd=repo,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(score.returncode, 0, score.stderr)
            summary = json.loads(score.stdout[score.stdout.index("{"):])
            self.assertFalse(summary["stability"]["ready"])
            self.assertIn("ci_red", summary["stability"]["signals"]["required_checks_recent_green"]["reason"])

    def test_fail_closed_when_branch_env_unset(self) -> None:
        cases = (
            ("unset", "export RELEASE_AUTO_ENABLE=true\nexport GH_REPO_SLUG=owner/repo\n", "REVIEW_BASE_BRANCH"),
            (
                "empty",
                "\n".join([
                    "export RELEASE_AUTO_ENABLE=true",
                    "export GH_REPO_SLUG=",
                    "export REVIEW_BASE_BRANCH=",
                    "export INTEGRATION_BRANCH=",
                    "",
                ]),
                "empty GH_REPO_SLUG, REVIEW_BASE_BRANCH, or INTEGRATION_BRANCH",
            ),
        )
        for name, host_env, expected_reason in cases:
            with self.subTest(name=name):
                with copy_repo_fixture() as tmp:
                    repo = Path(tmp) / "repo"
                    env_path = repo / ".config" / "consensus-rnd" / "host.env"
                    env_path.parent.mkdir(parents=True, exist_ok=True)
                    env_path.write_text(host_env, encoding="utf-8")
                    write_live_state(repo)
                    bin_dir = repo / "bin"
                    bin_dir.mkdir()
                    write_gh_stub(bin_dir)

                    result = run_gate_cli(repo, bin_dir)

                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn("required_checks_recent_green", result.stdout)
                    assert_no_release_artifacts(self, repo)
                    assert_signal_blocked(self, repo, bin_dir, "required_checks_recent_green", expected_reason)

    def test_fail_closed_when_check_runs_api_nonzero(self) -> None:
        with copy_repo_fixture() as tmp:
            repo = Path(tmp) / "repo"
            write_opt_in(repo, review_base="trunk-review", integration="release-integration")
            write_live_state(repo)
            bin_dir = repo / "bin"
            bin_dir.mkdir()
            write_gh_stub(bin_dir, api_exit_code=2)

            result = run_gate_cli(repo, bin_dir)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("required_checks_recent_green", result.stdout)
            assert_no_release_artifacts(self, repo)
            assert_signal_blocked(self, repo, bin_dir, "required_checks_recent_green", "api_failure")

    def test_fail_closed_when_invalid_check_runs_json(self) -> None:
        with copy_repo_fixture() as tmp:
            repo = Path(tmp) / "repo"
            write_opt_in(repo, review_base="trunk-review", integration="release-integration")
            write_live_state(repo)
            bin_dir = repo / "bin"
            bin_dir.mkdir()
            write_gh_stub(bin_dir, api_stdout="{not-json")

            result = run_gate_cli(repo, bin_dir)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("required_checks_recent_green", result.stdout)
            assert_no_release_artifacts(self, repo)
            assert_signal_blocked(self, repo, bin_dir, "required_checks_recent_green", "invalid_json")

    def test_fail_closed_when_required_check_runs_missing(self) -> None:
        with copy_repo_fixture() as tmp:
            repo = Path(tmp) / "repo"
            write_opt_in(repo, review_base="trunk-review", integration="release-integration")
            write_live_state(repo)
            bin_dir = repo / "bin"
            bin_dir.mkdir()
            write_gh_stub(bin_dir, check_names=["consensus-rnd-ci"])

            result = run_gate_cli(repo, bin_dir)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("required_checks_recent_green", result.stdout)
            assert_no_release_artifacts(self, repo)
            summary = score_only_summary(self, repo, bin_dir)
            stability = summary["stability"]
            self.assertIsInstance(stability, dict)
            assert isinstance(stability, dict)
            self.assertFalse(stability["ready"])
            signal = stability["signals"]["required_checks_recent_green"]
            self.assertFalse(signal["passed"])
            self.assertIn("required_checks_recent_green", signal["reason"])
            self.assertIn("missing_required_checks_recent_green", signal["reason"])
            branches = signal["branches"]
            self.assertFalse(branches["trunk-review"]["contract-tests"])
            self.assertFalse(branches["trunk-review"]["manifest-version-sync"])
            self.assertFalse(branches["trunk-review"]["skill-degradation"])
            self.assertFalse(branches["release-integration"]["contract-tests"])
            self.assertFalse(branches["release-integration"]["manifest-version-sync"])
            self.assertFalse(branches["release-integration"]["skill-degradation"])

    def test_fail_closed_when_gh_issue_pr_list_fails(self) -> None:
        cases = (
            ({"issue": "exit"}, "no_human_decision_label", "gh issue list failed"),
            ({"pr": "invalid-json"}, "no_open_blocked_pr", "invalid gh JSON for pr"),
        )
        for list_failures, signal_name, expected_reason in cases:
            with self.subTest(list_failures=list_failures):
                with copy_repo_fixture() as tmp:
                    repo = Path(tmp) / "repo"
                    write_opt_in(repo, review_base="trunk-review", integration="release-integration")
                    write_live_state(repo)
                    bin_dir = repo / "bin"
                    bin_dir.mkdir()
                    write_gh_stub(bin_dir, list_failures=list_failures)

                    result = run_gate_cli(repo, bin_dir)

                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn(signal_name, result.stdout)
                    assert_no_release_artifacts(self, repo)
                    assert_signal_blocked(self, repo, bin_dir, signal_name, expected_reason)

    def test_fail_closed_when_blocked_or_human_label_present(self) -> None:
        cases = (
            (label_catalog.PHASE_BLOCKED, "no_open_blocked_pr"),
            (label_catalog.HUMAN_MAINTAINER_DECISION, "no_human_decision_label"),
        )
        for label, signal_name in cases:
            with self.subTest(label=label):
                with copy_repo_fixture() as tmp:
                    repo = Path(tmp) / "repo"
                    write_opt_in(repo, review_base="trunk-review", integration="release-integration")
                    write_live_state(repo)
                    bin_dir = repo / "bin"
                    bin_dir.mkdir()
                    write_gh_stub(bin_dir, labeled_items={label: [{"number": 58}]})
                    env = {
                        **os.environ,
                        "REPO_ROOT": str(repo),
                        "CONSENSUS_RND_HOST_ENV": ".config/consensus-rnd/host.env",
                        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
                    }
                    result = subprocess.run(
                        [sys.executable, str(SCRIPT_PATH.with_name("consensus-rnd-cli")), "release-gate", "--min-recent-merges", "0"],
                        cwd=repo,
                        env=env,
                        capture_output=True,
                        text=True,
                        check=False,
                    )

                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn(signal_name, result.stdout)
                    assert_no_release_artifacts(self, repo)
                    score = subprocess.run(
                        [sys.executable, str(SCRIPT_PATH.with_name("consensus-rnd-cli")), "release-gate", "--score-only", "--min-recent-merges", "0"],
                        cwd=repo,
                        env=env,
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    self.assertEqual(score.returncode, 0, score.stderr)
                    summary = json.loads(score.stdout[score.stdout.index("{"):])
                    self.assertFalse(summary["stability"]["ready"])
                    signal = summary["stability"]["signals"][signal_name]
                    self.assertIn("label_present", json.dumps(signal, ensure_ascii=False))
                    self.assertIn(label, json.dumps(signal, ensure_ascii=False))

    def test_fail_closed_when_daemon_heartbeat_stale(self) -> None:
        with copy_repo_fixture() as tmp:
            repo = Path(tmp) / "repo"
            stale = int((NOW - timedelta(seconds=91)).timestamp())
            write_heartbeat_files(repo, {name: stale for name in restart_managed_daemon_names()})
            state = repo / ".refactor-loop/state"
            write_json(state / "phase8-review-state.json", {"max_consecutive_reject_rounds": 0})
            write_json(state / "meta-resolutions.json", {"unresolved_escalate_human": []})
            write_json(repo / ".refactor-loop/.concurrency-monitor-state.json", {"zero_streak": 0})
            gate = AutoReleaseGate(repo, now=lambda: NOW, runner=FakeRunner())
            score = gate.compute_stability(min_recent_merges=0)

            self.assertFalse(score.ready)
            self.assertIn("heartbeat_stale", score.signals["fresh_heartbeats"]["reason"])

    def test_fresh_heartbeats_reads_real_heartbeat_files_without_legacy_state_artifact(self) -> None:
        with copy_repo_fixture() as tmp:
            repo = Path(tmp) / "repo"
            fresh = int(NOW.timestamp())
            write_heartbeat_files(repo, {**{name: fresh for name in restart_managed_daemon_names()}, "extra-observer": fresh})
            gate = AutoReleaseGate(repo, now=lambda: NOW, runner=FakeRunner())

            signal = gate.fresh_heartbeats()

            self.assertTrue(signal["passed"])
            self.assertEqual(signal["source"], "heartbeats/*.ts")
            self.assertEqual(sum(1 for value in signal["heartbeats"].values() if value), len(restart_managed_daemon_names()) + 1)
            self.assertTrue(signal["heartbeats"]["concurrency_monitor"])
            self.assertTrue(signal["heartbeats"]["closed_label_reconciler"])
            self.assertTrue(signal["heartbeats"]["wakeup_runner_daemon"])

    def test_fail_closed_when_any_restart_managed_heartbeat_is_missing(self) -> None:
        with copy_repo_fixture() as tmp:
            repo = Path(tmp) / "repo"
            fresh = int(NOW.timestamp())
            write_heartbeat_files(repo, {name: fresh for name in restart_managed_daemon_names() if name != "closed_label_reconciler"})
            gate = AutoReleaseGate(repo, now=lambda: NOW, runner=FakeRunner())

            signal = gate.fresh_heartbeats()

            self.assertFalse(signal["passed"])
            self.assertIn("heartbeat_stale", signal["reason"])
            self.assertFalse(signal["heartbeats"]["closed_label_reconciler"])

    def test_fail_closed_when_real_heartbeat_file_is_malformed(self) -> None:
        with copy_repo_fixture() as tmp:
            repo = Path(tmp) / "repo"
            fresh = int(NOW.timestamp())
            values: dict[str, int | str] = {name: fresh for name in restart_managed_daemon_names()}
            values["closed_label_reconciler"] = "not-an-epoch"
            write_heartbeat_files(repo, values)
            gate = AutoReleaseGate(repo, now=lambda: NOW, runner=FakeRunner())

            signal = gate.fresh_heartbeats()

            self.assertFalse(signal["passed"])
            self.assertFalse(signal["heartbeats"]["closed_label_reconciler"])

    def test_fail_closed_when_phase8_reject_churn_reaches_limit(self) -> None:
        with copy_repo_fixture() as tmp:
            repo = Path(tmp) / "repo"
            write_opt_in(repo, review_base="trunk-review", integration="release-integration")
            write_live_state(repo)
            write_json(repo / ".refactor-loop/state/phase8-review-state.json", {"max_consecutive_reject_rounds": 3})
            bin_dir = repo / "bin"
            bin_dir.mkdir()
            write_gh_stub(bin_dir)

            result = run_gate_cli(repo, bin_dir)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("no_phase8_reject_churn", result.stdout)
            assert_no_release_artifacts(self, repo)
            gate = AutoReleaseGate(repo, now=lambda: NOW, runner=FakeRunner())
            score = gate.compute_stability(min_recent_merges=1)
            self.assertFalse(score.ready)
            self.assertFalse(score.signals["no_phase8_reject_churn"]["passed"])
            self.assertEqual(score.signals["no_phase8_reject_churn"]["max_consecutive_reject_rounds"], 3)

    def test_fail_closed_when_p0_alert_streak_overflows(self) -> None:
        with copy_repo_fixture() as tmp:
            repo = Path(tmp) / "repo"
            write_opt_in(repo, review_base="trunk-review", integration="release-integration")
            write_live_state(repo)
            write_json(repo / ".refactor-loop/.concurrency-monitor-state.json", {"zero_streak": 4})
            bin_dir = repo / "bin"
            bin_dir.mkdir()
            write_gh_stub(bin_dir)

            result = run_gate_cli(repo, bin_dir)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("p0_alert_streak_ok", result.stdout)
            assert_no_release_artifacts(self, repo)
            gate = AutoReleaseGate(repo, now=lambda: NOW, runner=FakeRunner())
            score = gate.compute_stability(min_recent_merges=1)
            self.assertFalse(score.ready)
            self.assertFalse(score.signals["p0_alert_streak_ok"]["passed"])
            self.assertEqual(score.signals["p0_alert_streak_ok"]["zero_streak"], 4)

    def test_fail_closed_when_p0_alert_streak_overflow(self) -> None:
        with copy_repo_fixture() as tmp:
            repo = Path(tmp) / "repo"
            write_opt_in(repo, review_base="trunk-review", integration="release-integration")
            write_live_state(repo)
            write_json(repo / ".refactor-loop/.concurrency-monitor-state.json", {"zero_streak": 4})
            bin_dir = repo / "bin"
            bin_dir.mkdir()
            write_gh_stub(bin_dir)

            result = run_gate_cli(repo, bin_dir)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("p0_alert_streak_ok", result.stdout)
            assert_no_release_artifacts(self, repo)
            summary = score_only_summary(self, repo, bin_dir)
            signal = summary["stability"]["signals"]["p0_alert_streak_ok"]
            self.assertFalse(signal["passed"])
            self.assertEqual(signal["zero_streak"], 4)
            self.assertIn("p0_alert_streak_ok", summary["stability"]["signals"])

    def test_fail_closed_when_p0_alert_overflow_blocks_release(self) -> None:
        with copy_repo_fixture() as tmp:
            repo = Path(tmp) / "repo"
            write_opt_in(repo, review_base="trunk-review", integration="release-integration")
            write_live_state(repo)
            alert_log = repo / ".refactor-loop/.concurrency-alert.log"
            alert_log.parent.mkdir(parents=True, exist_ok=True)
            now = datetime.now(timezone.utc).replace(microsecond=0)
            alert_lines = [
                f"[{iso}] P0 no-gap-violation: fixture {index}"
                for index, iso in enumerate(
                    (now - timedelta(minutes=offset)).isoformat().replace("+00:00", "Z")
                    for offset in (1, 2, 3, 4)
                )
            ]
            alert_log.write_text("\n".join(alert_lines) + "\n", encoding="utf-8")
            bin_dir = repo / "bin"
            bin_dir.mkdir()
            write_gh_stub(bin_dir)

            result = run_gate_cli(repo, bin_dir, "--dispatch")

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("p0_alert_streak_ok", result.stdout)
            assert_no_release_artifacts(self, repo)
            summary = score_only_summary(self, repo, bin_dir)
            signal = summary["stability"]["signals"]["p0_alert_streak_ok"]
            self.assertFalse(signal["passed"])
            self.assertEqual(signal["zero_streak"], 0)
            self.assertEqual(signal["recent_p0_alerts"], 4)

    def test_fail_closed_when_recent_pr_merges_below_minimum(self) -> None:
        with copy_repo_fixture() as tmp:
            repo = Path(tmp) / "repo"
            write_opt_in(repo, review_base="trunk-review", integration="release-integration")
            write_live_state(repo)
            (repo / ".refactor-loop/state/recent-pr-merges.json").unlink()
            bin_dir = repo / "bin"
            bin_dir.mkdir()
            write_gh_stub(bin_dir)

            result = run_gate_cli(repo, bin_dir)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("recent_pr_merges_min", result.stdout)
            assert_no_release_artifacts(self, repo)
            gate = AutoReleaseGate(repo, now=lambda: NOW, runner=FakeRunner())
            score = gate.compute_stability(min_recent_merges=1)
            self.assertFalse(score.ready)
            self.assertFalse(score.signals["recent_pr_merges_min"]["passed"])
            self.assertIn("recent_pr_merges_min", score.signals["recent_pr_merges_min"]["reason"])
            self.assertIn("missing_recent_pr_merges_artifact", score.signals["recent_pr_merges_min"]["reason"])
            self.assertEqual(score.signals["recent_pr_merges_min"]["minimum"], 1)

    def test_recent_pr_merges_accepts_projection_shape(self) -> None:
        with copy_repo_fixture() as tmp:
            repo = Path(tmp) / "repo"
            write_json(
                repo / ".refactor-loop/state/recent-pr-merges.json",
                {
                    "count": 1,
                    "window_hours": 2,
                    "updated_at": "2026-05-29T01:03:04Z",
                    "merges": [
                        {
                            "pr": 55,
                            "sha": "abc123",
                            "merged_at": "2026-05-29T01:02:03Z",
                            "base_ref": "auto-refact-dev",
                            "head_ref": "refactor/issue145",
                        }
                    ],
                },
            )
            gate = AutoReleaseGate(repo, now=lambda: NOW, runner=FakeRunner())

            signal = gate.recent_pr_merges_min(NOW - timedelta(hours=2), 1)

            self.assertTrue(signal["passed"])
            self.assertEqual(signal["count"], 1)
            self.assertEqual(signal["minimum"], 1)
            self.assertEqual(signal["window_hours"], 2)
            self.assertEqual(signal["updated_at"], "2026-05-29T01:03:04Z")

    def test_fail_closed_when_unresolved_human_escalation_exists(self) -> None:
        with copy_repo_fixture() as tmp:
            repo = Path(tmp) / "repo"
            write_opt_in(repo, review_base="trunk-review", integration="release-integration")
            write_live_state(repo)
            write_json(repo / ".refactor-loop/state/meta-resolutions.json", {"unresolved_escalate_human": ["issue-61"]})
            bin_dir = repo / "bin"
            bin_dir.mkdir()
            write_gh_stub(bin_dir)

            result = run_gate_cli(repo, bin_dir)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("no_unresolved_human_escalation", result.stdout)
            assert_no_release_artifacts(self, repo)
            gate = AutoReleaseGate(repo, now=lambda: NOW, runner=FakeRunner())
            score = gate.compute_stability(min_recent_merges=1)
            self.assertFalse(score.ready)
            self.assertFalse(score.signals["no_unresolved_human_escalation"]["passed"])
            self.assertEqual(score.signals["no_unresolved_human_escalation"]["count"], 1)

    def test_fail_closed_when_mapped_manifest_versions_diverge(self) -> None:
        with copy_repo_fixture() as tmp:
            repo = Path(tmp) / "repo"
            write_opt_in(repo, review_base="trunk-review", integration="release-integration")
            write_live_state(repo)
            package_json = read_json(repo / "package.json")
            self.assertIsInstance(package_json, dict)
            assert isinstance(package_json, dict)
            package_json["version"] = "9.9.9"
            write_json(repo / "package.json", package_json)
            bin_dir = repo / "bin"
            bin_dir.mkdir()
            write_gh_stub(bin_dir)

            result = run_gate_cli(repo, bin_dir)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("mapped manifest versions are not synchronized", result.stderr)
            assert_no_release_artifacts(self, repo)

    def test_dry_run_writes_decision_no_bump(self) -> None:
        with copy_repo_fixture() as tmp:
            repo = Path(tmp) / "repo"
            write_opt_in(repo)
            write_green_signals(repo)
            before = (repo / "package.json").read_text(encoding="utf-8")
            env = {**os.environ, "REPO_ROOT": str(repo), "CONSENSUS_RND_HOST_ENV": ".config/consensus-rnd/host.env"}
            result = subprocess.run(
                [sys.executable, str(SCRIPT_PATH.with_name("consensus-rnd-cli")), "release-gate", "--min-recent-merges", "0"],
                cwd=repo,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((repo / ".refactor-loop/state/release-decision.json").exists())
            self.assertFalse((repo / ".refactor-loop/state/release-candidate.json").exists())
            self.assertEqual((repo / "package.json").read_text(encoding="utf-8"), before)

    def test_explicit_host_env_opt_in_writes_decision_without_legacy_file(self) -> None:
        with copy_repo_fixture() as tmp:
            repo = Path(tmp) / "repo"
            write_explicit_opt_in(repo)
            self.assertFalse((repo / ".refactor-loop/host.env").exists())
            write_green_signals(repo)
            env = {
                **os.environ,
                "REPO_ROOT": str(repo),
                "CONSENSUS_RND_HOST_ENV": ".config/consensus-rnd/host.env",
            }

            result = subprocess.run(
                [sys.executable, str(SCRIPT_PATH.with_name("consensus-rnd-cli")), "release-gate", "--min-recent-merges", "0"],
                cwd=repo,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn("RELEASE_AUTO_ENABLE is not true", result.stdout)
            self.assertTrue((repo / ".refactor-loop/state/release-decision.json").exists())
            self.assertFalse((repo / ".refactor-loop/state/release-candidate.json").exists())

    def test_fail_closed_when_no_commits_since_last_release(self) -> None:
        cases = ("absent", "empty")
        for case in cases:
            with self.subTest(case=case):
                with copy_repo_fixture() as tmp:
                    repo = Path(tmp) / "repo"
                    write_green_signals(repo)
                    if case == "absent":
                        (repo / ".refactor-loop/state/release-commits.json").unlink()
                    else:
                        write_json(repo / ".refactor-loop/state/release-commits.json", {"commits": []})
                    gate = AutoReleaseGate(repo, now=lambda: NOW, runner=FakeRunner())
                    stability = gate.compute_stability(min_recent_merges=0)
                    decision = gate.decide_release(stability, min_interval_hours=2)

                    self.assertTrue(stability.ready)
                    self.assertFalse(decision["ready"])
                    self.assertIsNone(decision["bump_type"])
                    self.assertEqual(decision["to_version"], decision["from_version"])
                    self.assertEqual(decision["commits"], [])
                    self.assertIn("no_commits_since_last_release", decision["blocked_reasons"])

    def test_dispatch_happy_path_asserts_ready_and_version_transition(self) -> None:
        with copy_repo_fixture() as tmp:
            repo = Path(tmp) / "repo"
            write_opt_in(repo)
            write_green_signals(repo)
            before = (repo / "package.json").read_text(encoding="utf-8")
            env = {**os.environ, "REPO_ROOT": str(repo), "CONSENSUS_RND_HOST_ENV": ".config/consensus-rnd/host.env"}
            result = subprocess.run(
                [sys.executable, str(SCRIPT_PATH.with_name("consensus-rnd-cli")), "release-gate", "--dispatch", "--min-recent-merges", "0"],
                cwd=repo,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("controller-owned release publisher owns", result.stdout)
            decision_path = repo / ".refactor-loop/state/release-decision.json"
            candidate_path = repo / ".refactor-loop/state/release-candidate.json"
            self.assertTrue(decision_path.exists())
            self.assertTrue(candidate_path.exists())
            decision = read_json(decision_path)
            self.assertIsInstance(decision, dict)
            assert isinstance(decision, dict)
            self.assertTrue(decision["ready"])
            self.assertEqual(decision["bump_type"], "patch")
            # Version-agnostic: from_version must equal the current package.json
            # version (decision dispatch is artifact-only and does not bump it),
            # so this assertion survives every release bump without edits.
            current_version = read_json(repo / "package.json")["version"]
            self.assertEqual(decision["from_version"], current_version)
            self.assertEqual(decision["to_version"], classify_expected_patch(current_version))
            self.assertNotEqual(decision["to_version"], decision["from_version"])
            candidate = read_json(candidate_path)
            self.assertIsInstance(candidate, dict)
            assert isinstance(candidate, dict)
            self.assertEqual(candidate["schema"], "decision-artifact-only/v2")
            self.assertEqual(candidate["lifecycle_owner"], "controller")
            self.assertEqual(candidate["publish_preflight"], "controller-release-publish-preflight")
            self.assertIn("expires_at", candidate)
            self.assertIn("target_ref", candidate)
            self.assertIn("required_signals", candidate)
            self.assertIn("decision_digest", candidate)
            self.assertTrue(candidate["ready"])
            self.assertEqual(candidate["bump_type"], "patch")
            self.assertEqual(candidate["from_version"], decision["from_version"])
            self.assertEqual(candidate["to_version"], decision["to_version"])
            self.assertEqual((repo / "package.json").read_text(encoding="utf-8"), before)

    def test_dispatch_from_beta_base_writes_beta_next_candidate(self) -> None:
        cases = (
            ("fix: fixture", "", "patch"),
            ("feat: fixture", "", "minor"),
            ("feat!: fixture", "", "major"),
        )
        for subject, body, expected_bump in cases:
            with self.subTest(expected_bump=expected_bump), copy_repo_fixture() as tmp:
                repo = Path(tmp) / "repo"
                write_opt_in(repo)
                write_green_signals(repo)
                set_mapped_version(repo, "1.0.0-beta.3")
                write_json(
                    repo / ".refactor-loop/state/release-commits.json",
                    {"commits": [{"sha": "abc123", "subject": subject, "body": body}]},
                )
                env = {**os.environ, "REPO_ROOT": str(repo), "CONSENSUS_RND_HOST_ENV": ".config/consensus-rnd/host.env"}

                result = subprocess.run(
                    [sys.executable, str(SCRIPT_PATH.with_name("consensus-rnd-cli")), "release-gate", "--dispatch", "--min-recent-merges", "0"],
                    cwd=repo,
                    env=env,
                    capture_output=True,
                    text=True,
                    check=False,
                )

                self.assertEqual(result.returncode, 0, result.stderr)
                decision = read_json(repo / ".refactor-loop/state/release-decision.json")
                candidate = read_json(repo / ".refactor-loop/state/release-candidate.json")
                self.assertIsInstance(decision, dict)
                self.assertIsInstance(candidate, dict)
                assert isinstance(decision, dict)
                assert isinstance(candidate, dict)
                self.assertEqual(decision["from_version"], "1.0.0-beta.3")
                self.assertEqual(decision["to_version"], "1.0.0-beta.4")
                self.assertEqual(decision["bump_type"], expected_bump)
                self.assertEqual(candidate["to_version"], "1.0.0-beta.4")
                self.assertNotIn(decision["to_version"], {"1.0.1", "1.0.0"})

    def test_score_only_cli_prints_stability_no_decision_write(self) -> None:
        """--score-only path prints stability summary, does not write decision.json or bump."""
        with copy_repo_fixture() as tmp:
            repo = Path(tmp) / "repo"
            write_green_signals(repo)
            before = (repo / "package.json").read_text(encoding="utf-8")
            env = {**os.environ, "REPO_ROOT": str(repo)}
            result = subprocess.run(
                [sys.executable, str(SCRIPT_PATH.with_name("consensus-rnd-cli")), "release-gate", "--score-only"],
                cwd=repo,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            summary = json.loads(result.stdout)
            self.assertIn("stability", summary)
            self.assertIn("ready", summary["stability"])
            self.assertIn("score", summary["stability"])
            self.assertFalse((repo / ".refactor-loop/state/release-decision.json").exists())
            self.assertEqual((repo / "package.json").read_text(encoding="utf-8"), before)

    def test_signal_failure_reason_includes_signal_name(self) -> None:
        with copy_repo_fixture() as tmp:
            repo = Path(tmp) / "repo"
            write_green_signals(repo)
            data = read_json(repo / ".refactor-loop/state/auto-release-signals.json")
            assert isinstance(data, dict)
            data["signals"]["p0_alert_streak_ok"] = {"passed": False, "reason": "p0_alert_streak_ok"}
            write_json(repo / ".refactor-loop/state/auto-release-signals.json", data)
            gate = AutoReleaseGate(repo, now=lambda: NOW, runner=FakeRunner())
            decision = gate.decide_release(gate.compute_stability(), min_interval_hours=2)

            self.assertFalse(decision["ready"])
            self.assertLess(decision["stability_score"], 100)
            self.assertIn("p0_alert_streak_ok", decision["signals"])
            self.assertEqual(decision["signals"]["p0_alert_streak_ok"]["reason"], "p0_alert_streak_ok")

    def test_live_stability_collector_path_without_fixtures(self) -> None:
        """Without signal monkey-patching, exercise the live collector path end-to-end."""
        with copy_repo_fixture() as tmp:
            repo = Path(tmp) / "repo"
            write_opt_in(repo, review_base="trunk-review", integration="release-integration")
            write_live_state(repo)
            write_json(
                repo / ".refactor-loop/state/release-commits.json",
                {"commits": [{"sha": "def456", "subject": "feat: live collector", "body": ""}]},
            )
            bin_dir = repo / "bin"
            bin_dir.mkdir()
            write_gh_stub(bin_dir)
            env = {
                **os.environ,
                "REPO_ROOT": str(repo),
                "CONSENSUS_RND_HOST_ENV": ".config/consensus-rnd/host.env",
                "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            }
            result = subprocess.run(
                [sys.executable, str(SCRIPT_PATH.with_name("consensus-rnd-cli")), "release-gate", "--min-recent-merges", "0"],
                cwd=repo,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("check branches: trunk-review, release-integration", result.stdout)
            decision = read_json(repo / ".refactor-loop/state/release-decision.json")
            self.assertIsInstance(decision, dict)
            assert isinstance(decision, dict)
            for key in ("from_version", "to_version", "stability_score", "signals", "ready", "blocked_reasons"):
                self.assertIn(key, decision)
            self.assertEqual(set(decision["signals"]), {
                "required_checks_recent_green",
                "no_open_blocked_pr",
                "no_human_decision_label",
                "no_phase8_reject_churn",
                "p0_alert_streak_ok",
                "recent_pr_merges_min",
                "fresh_heartbeats",
                "no_unresolved_human_escalation",
            })
            for signal in decision["signals"].values():
                self.assertTrue(signal["passed"])
            ci_signal = decision["signals"]["required_checks_recent_green"]
            self.assertEqual(set(ci_signal["branches"]), {"trunk-review", "release-integration"})
            self.assertTrue(ci_signal["passed"])


if __name__ == "__main__":
    unittest.main()
