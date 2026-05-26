#!/usr/bin/env python3
"""Behavior tests for auto_release_gate.py."""

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

from auto_release_gate import AutoReleaseGate, CommitInfo, classify_bump


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
) -> None:
    (repo / "host.env").write_text(
        "\n".join(
            [
                f"export RELEASE_AUTO_ENABLE={'true' if enabled else 'false'}",
                f"export REVIEW_BASE_BRANCH={review_base}",
                f"export INTEGRATION_BRANCH={integration}",
                "",
            ]
        ),
        encoding="utf-8",
    )


def write_gh_stub(
    bin_dir: Path,
    *,
    run_conclusions: dict[str, str] | None = None,
    labeled_items: dict[str, list[dict[str, int]]] | None = None,
) -> None:
    run_conclusions = run_conclusions or {}
    labeled_items = labeled_items or {}
    gh = bin_dir / "gh"
    gh.write_text(
        f"""#!/usr/bin/env python3
import json
import sys
from datetime import datetime, timezone

now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
args = sys.argv[1:]
run_conclusions = {json.dumps(run_conclusions, ensure_ascii=False)}
labeled_items = {json.dumps(labeled_items, ensure_ascii=False)}
if args[:3] == ["run", "list", "--branch"]:
    print(json.dumps([
        {{"databaseId": 1, "createdAt": now, "conclusion": run_conclusions.get("contract-tests", "success"), "status": "completed", "name": "contract-tests"}},
        {{"databaseId": 2, "createdAt": now, "conclusion": run_conclusions.get("manifest-version-sync", "success"), "status": "completed", "name": "manifest-version-sync"}},
    ]))
    raise SystemExit(0)
if len(args) >= 2 and args[1] == "list":
    label = args[args.index("--label") + 1] if "--label" in args else ""
    print(json.dumps(labeled_items.get(label, [])))
    raise SystemExit(0)
print("[]")
""",
        encoding="utf-8",
    )
    gh.chmod(0o755)


def write_live_state(repo: Path) -> None:
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    state = repo / ".refactor-loop/state"
    write_json(state / "daemon-heartbeats.json", {name: now for name in (
        "concurrency_monitor.py",
        "codex-progress-reporter.sh",
        "comment-monitor.sh",
        "dev_sync_daemon.py",
        "triage-monitor.sh",
        "phase9_router_daemon.py",
    )})
    write_json(state / "phase8-review-state.json", {"max_consecutive_reject_rounds": 0})
    write_json(state / "meta-resolutions.json", {"unresolved_escalate_human": []})
    write_json(state / "recent-pr-merges.json", {"count": 1})
    write_json(state / "release-commits.json", {"commits": [{"sha": "abc123", "subject": "fix: fixture", "body": ""}]})
    write_json(repo / ".refactor-loop/.concurrency-monitor-state.json", {"zero_streak": 0})


def run_gate_cli(repo: Path, bin_dir: Path | None = None, *extra: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "REPO_ROOT": str(repo)}
    if bin_dir is not None:
        env["PATH"] = f"{bin_dir}{os.pathsep}{os.environ['PATH']}"
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT_PATH.with_name("auto_release_gate.py")),
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


class AutoReleaseGateBehaviorTests(unittest.TestCase):
    def test_no_opt_in_exits_noop(self) -> None:
        with copy_repo_fixture() as tmp:
            repo = Path(tmp) / "repo"
            env = {**os.environ, "REPO_ROOT": str(repo)}
            result = subprocess.run(
                [sys.executable, str(SCRIPT_PATH.with_name("auto_release_gate.py")), "--min-recent-merges", "0"],
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
            write_gh_stub(bin_dir, run_conclusions={"contract-tests": "failure"})
            env = {
                **os.environ,
                "REPO_ROOT": str(repo),
                "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            }
            result = subprocess.run(
                [sys.executable, str(SCRIPT_PATH.with_name("auto_release_gate.py")), "--min-recent-merges", "0"],
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
                [sys.executable, str(SCRIPT_PATH.with_name("auto_release_gate.py")), "--score-only", "--min-recent-merges", "0"],
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

    def test_fail_closed_when_blocked_or_human_label_present(self) -> None:
        cases = (
            ("⏸️ phase:blocked", "no_open_blocked_pr"),
            ("👤 human:需-maintainer-决策", "no_human_decision_label"),
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
                        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
                    }
                    result = subprocess.run(
                        [sys.executable, str(SCRIPT_PATH.with_name("auto_release_gate.py")), "--min-recent-merges", "0"],
                        cwd=repo,
                        env=env,
                        capture_output=True,
                        text=True,
                        check=False,
                    )

                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn(signal_name, result.stdout)
                    self.assertFalse((repo / ".refactor-loop/state/release-decision.json").exists())
                    score = subprocess.run(
                        [sys.executable, str(SCRIPT_PATH.with_name("auto_release_gate.py")), "--score-only", "--min-recent-merges", "0"],
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
            stale = (NOW - timedelta(seconds=91)).isoformat().replace("+00:00", "Z")
            state = repo / ".refactor-loop/state"
            write_json(state / "daemon-heartbeats.json", {name: stale for name in (
                "concurrency_monitor.py",
                "codex-progress-reporter.sh",
                "comment-monitor.sh",
                "dev_sync_daemon.py",
                "triage-monitor.sh",
                "phase9_router_daemon.py",
            )})
            write_json(state / "phase8-review-state.json", {"max_consecutive_reject_rounds": 0})
            write_json(state / "meta-resolutions.json", {"unresolved_escalate_human": []})
            write_json(repo / ".refactor-loop/.concurrency-monitor-state.json", {"zero_streak": 0})
            gate = AutoReleaseGate(repo, now=lambda: NOW, runner=FakeRunner())
            score = gate.compute_stability(min_recent_merges=0)

            self.assertFalse(score.ready)
            self.assertIn("heartbeat_stale", score.signals["fresh_heartbeats"]["reason"])

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
            self.assertFalse((repo / ".refactor-loop/state/release-decision.json").exists())
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
            self.assertFalse((repo / ".refactor-loop/state/release-decision.json").exists())
            gate = AutoReleaseGate(repo, now=lambda: NOW, runner=FakeRunner())
            score = gate.compute_stability(min_recent_merges=1)
            self.assertFalse(score.ready)
            self.assertFalse(score.signals["p0_alert_streak_ok"]["passed"])
            self.assertEqual(score.signals["p0_alert_streak_ok"]["zero_streak"], 4)

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
            self.assertFalse((repo / ".refactor-loop/state/release-decision.json").exists())
            gate = AutoReleaseGate(repo, now=lambda: NOW, runner=FakeRunner())
            score = gate.compute_stability(min_recent_merges=1)
            self.assertFalse(score.ready)
            self.assertFalse(score.signals["recent_pr_merges_min"]["passed"])
            self.assertEqual(score.signals["recent_pr_merges_min"]["reason"], "missing_recent_pr_merges_artifact")
            self.assertEqual(score.signals["recent_pr_merges_min"]["minimum"], 1)

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
            self.assertFalse((repo / ".refactor-loop/state/release-decision.json").exists())
            gate = AutoReleaseGate(repo, now=lambda: NOW, runner=FakeRunner())
            score = gate.compute_stability(min_recent_merges=1)
            self.assertFalse(score.ready)
            self.assertFalse(score.signals["no_unresolved_human_escalation"]["passed"])
            self.assertEqual(score.signals["no_unresolved_human_escalation"]["count"], 1)

    def test_dry_run_writes_decision_no_bump(self) -> None:
        with copy_repo_fixture() as tmp:
            repo = Path(tmp) / "repo"
            write_opt_in(repo)
            write_green_signals(repo)
            before = (repo / "package.json").read_text(encoding="utf-8")
            env = {**os.environ, "REPO_ROOT": str(repo)}
            result = subprocess.run(
                [sys.executable, str(SCRIPT_PATH.with_name("auto_release_gate.py")), "--min-recent-merges", "0"],
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

    def test_dispatch_writes_candidate_artifact_no_lifecycle_ops(self) -> None:
        with copy_repo_fixture() as tmp:
            repo = Path(tmp) / "repo"
            write_opt_in(repo)
            write_green_signals(repo)
            before = (repo / "package.json").read_text(encoding="utf-8")
            env = {**os.environ, "REPO_ROOT": str(repo)}
            result = subprocess.run(
                [sys.executable, str(SCRIPT_PATH.with_name("auto_release_gate.py")), "--dispatch", "--min-recent-merges", "0"],
                cwd=repo,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            decision_path = repo / ".refactor-loop/state/release-decision.json"
            candidate_path = repo / ".refactor-loop/state/release-candidate.json"
            self.assertTrue(decision_path.exists())
            self.assertTrue(candidate_path.exists())
            candidate = read_json(candidate_path)
            self.assertIsInstance(candidate, dict)
            assert isinstance(candidate, dict)
            self.assertEqual(candidate["schema"], "decision-artifact-only/v1")
            self.assertEqual(candidate["lifecycle_owner"], "controller-or-release.yml")
            self.assertEqual((repo / "package.json").read_text(encoding="utf-8"), before)

    def test_score_only_cli_prints_stability_no_decision_write(self) -> None:
        """--score-only path prints stability summary, does not write decision.json or bump."""
        with copy_repo_fixture() as tmp:
            repo = Path(tmp) / "repo"
            write_green_signals(repo)
            before = (repo / "package.json").read_text(encoding="utf-8")
            env = {**os.environ, "REPO_ROOT": str(repo)}
            result = subprocess.run(
                [sys.executable, str(SCRIPT_PATH.with_name("auto_release_gate.py")), "--score-only"],
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
                "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            }
            result = subprocess.run(
                [sys.executable, str(SCRIPT_PATH.with_name("auto_release_gate.py")), "--min-recent-merges", "0"],
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
