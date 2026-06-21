#!/usr/bin/env python3
"""Behavior tests for async publish verification jobs and receipts."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Mapping, Sequence
from unittest import mock

import sys

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from codex_refactor_loop import publish_verification


class PublishVerificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="publish-verification-test-"))
        self.worktree = self.tmp / ".worktrees" / "iter77-issue-77"
        self.worktree.mkdir(parents=True)
        self.host_env = self.tmp / ".config" / "consensus-rnd" / "host.env"
        self.host_env.parent.mkdir(parents=True)
        self.host_env.write_text(
            f'export REPO_ROOT="{self.tmp}"\n'
            'export GH_REPO_SLUG="owner/repo"\n'
            'export BUILD_CMD="make build"\n'
            'export TEST_CMD="make test"\n',
            encoding="utf-8",
        )
        self.env = {
            "BUILD_CMD": "make build",
            "TEST_CMD": "make test",
            "CONSENSUS_RND_HOST_ENV": ".config/consensus-rnd/host.env",
        }
        self.identity = {
            "repo_root": self.tmp,
            "worktree": self.worktree,
            "issue": "77",
            "action": "publish_implementation_output",
            "head_ref": "refactor/iter77-issue-77",
            "candidate_sha": "a" * 40,
            "env": self.env,
        }

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_parent_writes_immutable_request_pins_private_ref_and_starts_hidden_child(self) -> None:
        git_calls: list[list[str]] = []
        popen_calls: list[list[str]] = []

        def fake_git(args: Sequence[str]) -> subprocess.CompletedProcess[str]:
            git_calls.append([str(arg) for arg in args])
            if args[0] == "update-ref":
                return subprocess.CompletedProcess(args, 0, "", "")
            if args[:2] == ["rev-parse", "--verify"]:
                return subprocess.CompletedProcess(args, 0, "a" * 40 + "\n", "")
            raise AssertionError(f"unexpected git call: {args}")

        def fake_popen(command: Sequence[str], **_kwargs: object) -> mock.Mock:
            popen_calls.append([str(arg) for arg in command])
            return mock.Mock(pid=1234)

        with mock.patch("codex_refactor_loop.publish_verification.run_fixed_host_command", side_effect=AssertionError("parent must not run host commands")):
            with mock.patch("codex_refactor_loop.publish_verification.subprocess.Popen", side_effect=fake_popen):
                result = publish_verification.prepare_or_schedule(**self.identity, git_runner=fake_git)

        self.assertEqual("queued", result.status)
        self.assertEqual("started", result.reason)
        self.assertTrue(result.job_dir.is_dir())
        request = json.loads((result.job_dir / "request.json").read_text(encoding="utf-8"))
        self.assertEqual("PublishVerificationRequest", request["schema"])
        self.assertEqual(result.job_key, request["job_key"])
        self.assertEqual("a" * 40, request["candidate_sha"])
        self.assertEqual("a" * 40, request["verified_sha"])
        self.assertEqual("refactor/iter77-issue-77", request["head_ref"])
        self.assertEqual(".worktrees/iter77-issue-77", request["worktree"])
        self.assertEqual("refs/consensus/publish/" + result.job_key, request["private_ref"])
        self.assertEqual(
            {
                "BUILD_CMD": publish_verification.string_digest("make build"),
                "TEST_CMD": publish_verification.string_digest("make test"),
            },
            request["checkpoint_hashes"],
        )
        self.assertEqual([["update-ref", request["private_ref"], "a" * 40]], git_calls)
        self.assertEqual(1, len(popen_calls))
        self.assertEqual("publish-verification-worker", popen_calls[0][-2])
        self.assertEqual(str(result.job_dir), popen_calls[0][-1])

        with mock.patch("codex_refactor_loop.publish_verification.subprocess.Popen", side_effect=AssertionError("immutable request must not relaunch while slot is held")):
            second = publish_verification.prepare_or_schedule(**self.identity, git_runner=fake_git)
        self.assertEqual("queued", second.status)
        self.assertEqual("slot-busy", second.reason)
        self.assertEqual(request, json.loads((result.job_dir / "request.json").read_text(encoding="utf-8")))

    def test_one_shot_child_runs_build_test_and_writes_verified_receipt(self) -> None:
        result = self._prepared_job_without_child()
        commands: list[str] = []

        def fake_run(command: str, *, cwd: Path, env: Mapping[str, str], log: Path) -> int:
            commands.append(command)
            self.assertEqual(self.worktree.resolve(), cwd.resolve())
            self.assertEqual(str(self.tmp.resolve()), env["REPO_ROOT"])
            log.write_text(f"COMMAND={command}\nEXIT=0\n", encoding="utf-8")
            return 0

        with mock.patch("codex_refactor_loop.publish_verification.run_fixed_host_command", side_effect=fake_run):
            exit_code = publish_verification.run_one_publish_ratchet(
                result.job_dir,
                git_runner=self._worktree_git(head_oid="a" * 40, private_ref_oid="a" * 40),
            )

        self.assertEqual(0, exit_code)
        self.assertEqual(["make build", "make test"], commands)
        receipt = json.loads((result.job_dir / "result.json").read_text(encoding="utf-8"))
        self.assertEqual("VERIFIED", receipt["status"])
        self.assertEqual(result.job_key, receipt["job_key"])
        self.assertEqual("a" * 40, receipt["verified_sha"])
        self.assertEqual("a" * 40, receipt["tested_sha"])
        self.assertEqual("a" * 40, receipt["post_tested_sha"])
        self.assertEqual("a" * 40, receipt["private_ref_oid"])
        self.assertEqual(["BUILD_CMD", "TEST_CMD"], [item["name"] for item in receipt["commands"]])
        self.assertTrue(all(item["exit"] == 0 and item["exit_marker"] is True for item in receipt["commands"]))
        validated = publish_verification.validate_verified_receipt(result.job_dir, env=self.env, git_runner=self._private_ref_git("a" * 40))
        self.assertTrue(validated.ok)
        self.assertEqual("verified", validated.reason)

    def test_worker_cli_entrypoint_runs_exact_job_dir(self) -> None:
        job_dir = self.tmp / ".refactor-loop/state/publish-verification/jobs/job123"
        job_dir.mkdir(parents=True)

        with mock.patch("codex_refactor_loop.publish_verification.run_one_publish_ratchet", return_value=0) as ratchet:
            exit_code = publish_verification.main([str(job_dir)])

        self.assertEqual(0, exit_code)
        ratchet.assert_called_once_with(job_dir)

    def test_one_shot_child_rejects_moved_worktree_head_before_commands(self) -> None:
        result = self._prepared_job_without_child()

        with mock.patch("codex_refactor_loop.publish_verification.run_fixed_host_command", side_effect=AssertionError("mismatched subject must not run commands")):
            exit_code = publish_verification.run_one_publish_ratchet(
                result.job_dir,
                git_runner=self._worktree_git(head_oid="b" * 40, private_ref_oid="a" * 40),
            )

        self.assertEqual(3, exit_code)
        receipt = json.loads((result.job_dir / "result.json").read_text(encoding="utf-8"))
        self.assertEqual("FAILED", receipt["status"])
        self.assertEqual("worktree-head-mismatch", receipt["reason"])
        self.assertEqual("b" * 40, receipt["tested_sha"])
        self.assertEqual([], receipt["commands"])
        validated = publish_verification.validate_verified_receipt(
            result.job_dir,
            env=self.env,
            git_runner=self._worktree_git(head_oid="b" * 40, private_ref_oid="a" * 40),
        )
        self.assertFalse(validated.ok)
        self.assertEqual("worktree-head-mismatch", validated.reason)

    def test_missing_build_command_fails_closed_before_job_creation(self) -> None:
        env = dict(self.env)
        env.pop("BUILD_CMD")
        identity = dict(self.identity)
        identity["env"] = env

        with mock.patch("codex_refactor_loop.publish_verification.subprocess.Popen", side_effect=AssertionError("missing command must not launch child")):
            result = publish_verification.prepare_or_schedule(
                **identity,
                git_runner=self._unexpected_git("missing command must not pin private ref"),
            )

        self.assertEqual("failed", result.status)
        self.assertEqual("missing-BUILD_CMD", result.reason)
        self.assertFalse(result.job_dir.exists())

    def test_missing_test_command_fails_closed_before_job_creation(self) -> None:
        env = dict(self.env)
        env.pop("TEST_CMD")
        identity = dict(self.identity)
        identity["env"] = env

        with mock.patch("codex_refactor_loop.publish_verification.subprocess.Popen", side_effect=AssertionError("missing command must not launch child")):
            result = publish_verification.prepare_or_schedule(
                **identity,
                git_runner=self._unexpected_git("missing command must not pin private ref"),
            )

        self.assertEqual("failed", result.status)
        self.assertEqual("missing-TEST_CMD", result.reason)
        self.assertFalse(result.job_dir.exists())

    def test_one_shot_child_records_nonzero_command_exit_as_failed_receipt(self) -> None:
        result = self._prepared_job_without_child()
        commands: list[str] = []

        def fake_run(command: str, *, cwd: Path, env: Mapping[str, str], log: Path) -> int:
            commands.append(command)
            log.write_text(f"COMMAND={command}\nEXIT=7\n", encoding="utf-8")
            return 7

        with mock.patch("codex_refactor_loop.publish_verification.run_fixed_host_command", side_effect=fake_run):
            exit_code = publish_verification.run_one_publish_ratchet(
                result.job_dir,
                git_runner=self._worktree_git(head_oid="a" * 40, private_ref_oid="a" * 40),
            )

        self.assertEqual(3, exit_code)
        self.assertEqual(["make build"], commands)
        receipt = json.loads((result.job_dir / "result.json").read_text(encoding="utf-8"))
        self.assertEqual("FAILED", receipt["status"])
        self.assertEqual("BUILD_CMD-failed:7", receipt["reason"])
        self.assertEqual(
            [{"name": "BUILD_CMD", "exit": 7, "exit_marker": False}],
            [{"name": item["name"], "exit": item["exit"], "exit_marker": item["exit_marker"]} for item in receipt["commands"]],
        )

    def test_one_shot_child_requires_exit_zero_marker(self) -> None:
        result = self._prepared_job_without_child()
        commands: list[str] = []

        def fake_run(command: str, *, cwd: Path, env: Mapping[str, str], log: Path) -> int:
            commands.append(command)
            log.write_text(f"COMMAND={command}\nno terminal marker\n", encoding="utf-8")
            return 0

        with mock.patch("codex_refactor_loop.publish_verification.run_fixed_host_command", side_effect=fake_run):
            exit_code = publish_verification.run_one_publish_ratchet(
                result.job_dir,
                git_runner=self._worktree_git(head_oid="a" * 40, private_ref_oid="a" * 40),
            )

        self.assertEqual(3, exit_code)
        self.assertEqual(["make build"], commands)
        receipt = json.loads((result.job_dir / "result.json").read_text(encoding="utf-8"))
        self.assertEqual("FAILED", receipt["status"])
        self.assertEqual("BUILD_CMD-failed:0", receipt["reason"])
        self.assertEqual(False, receipt["commands"][0]["exit_marker"])

    def test_one_shot_child_rejects_dirty_worktree_after_commands(self) -> None:
        result = self._prepared_job_without_child()

        def fake_run(command: str, *, cwd: Path, env: Mapping[str, str], log: Path) -> int:
            log.write_text(f"COMMAND={command}\nEXIT=0\n", encoding="utf-8")
            return 0

        with mock.patch("codex_refactor_loop.publish_verification.run_fixed_host_command", side_effect=fake_run):
            exit_code = publish_verification.run_one_publish_ratchet(
                result.job_dir,
                git_runner=self._worktree_git_with_statuses(
                    head_oids=["a" * 40, "a" * 40],
                    private_ref_oid="a" * 40,
                    statuses=["", " M implementation.txt\n"],
                ),
            )

        self.assertEqual(3, exit_code)
        receipt = json.loads((result.job_dir / "result.json").read_text(encoding="utf-8"))
        self.assertEqual("FAILED", receipt["status"])
        self.assertEqual("worktree-dirty", receipt["reason"])
        self.assertEqual("a" * 40, receipt["tested_sha"])
        self.assertEqual("a" * 40, receipt["post_tested_sha"])
        self.assertEqual(["BUILD_CMD", "TEST_CMD"], [item["name"] for item in receipt["commands"]])

    def test_one_shot_child_rejects_moved_worktree_head_after_commands(self) -> None:
        result = self._prepared_job_without_child()

        def fake_run(command: str, *, cwd: Path, env: Mapping[str, str], log: Path) -> int:
            log.write_text(f"COMMAND={command}\nEXIT=0\n", encoding="utf-8")
            return 0

        with mock.patch("codex_refactor_loop.publish_verification.run_fixed_host_command", side_effect=fake_run):
            exit_code = publish_verification.run_one_publish_ratchet(
                result.job_dir,
                git_runner=self._worktree_git_with_statuses(
                    head_oids=["a" * 40, "b" * 40],
                    private_ref_oid="a" * 40,
                    statuses=["", ""],
                ),
            )

        self.assertEqual(3, exit_code)
        receipt = json.loads((result.job_dir / "result.json").read_text(encoding="utf-8"))
        self.assertEqual("FAILED", receipt["status"])
        self.assertEqual("worktree-head-mismatch", receipt["reason"])
        self.assertEqual("a" * 40, receipt["tested_sha"])
        self.assertEqual("b" * 40, receipt["post_tested_sha"])
        self.assertEqual(["BUILD_CMD", "TEST_CMD"], [item["name"] for item in receipt["commands"]])

    def test_verified_receipt_rejects_private_ref_mismatch_and_superseded_job(self) -> None:
        result = self._write_verified_receipt()

        mismatch = publish_verification.validate_verified_receipt(
            result.job_dir,
            env=self.env,
            git_runner=self._private_ref_git("b" * 40),
        )
        self.assertFalse(mismatch.ok)
        self.assertEqual("private-ref-mismatch", mismatch.reason)

        (result.job_dir / "superseded.json").write_text(json.dumps({"by": "new-job"}) + "\n", encoding="utf-8")
        superseded = publish_verification.validate_verified_receipt(
            result.job_dir,
            env=self.env,
            git_runner=self._private_ref_git("a" * 40),
        )
        self.assertFalse(superseded.ok)
        self.assertEqual("superseded", superseded.reason)

    def test_running_child_receipt_stays_pending_without_retry_consumption(self) -> None:
        result = self._prepared_job_without_child()
        request = json.loads((result.job_dir / "request.json").read_text(encoding="utf-8"))
        running = {
            "schema": "PublishVerificationResult",
            "status": "RUNNING",
            "reason": "running",
            "job_key": result.job_key,
            "verified_sha": "a" * 40,
            "tested_sha": "a" * 40,
            "post_tested_sha": "a" * 40,
            "gate_id": request["gate_id"],
            "command_digest": request["command_digest"],
            "checkpoint_hashes": request["checkpoint_hashes"],
            "private_ref": request["private_ref"],
            "commands": [],
        }
        (result.job_dir / "result.json").write_text(json.dumps(running, indent=2, sort_keys=True) + "\n", encoding="utf-8")

        queued = publish_verification.prepare_or_schedule(
            **self.identity,
            git_runner=self._private_ref_git("a" * 40),
            start_child=False,
        )

        self.assertEqual("queued", queued.status)
        self.assertEqual("running", queued.reason)
        self.assertFalse((result.job_dir / "retry.json").exists())

    def test_new_candidate_supersedes_old_unpublished_job(self) -> None:
        old = self._write_verified_receipt()
        newer = dict(self.identity)
        newer["candidate_sha"] = "b" * 40

        with mock.patch("codex_refactor_loop.publish_verification.subprocess.Popen", return_value=mock.Mock(pid=99)):
            new_result = publish_verification.prepare_or_schedule(**newer, git_runner=self._private_ref_git("b" * 40))

        self.assertNotEqual(old.job_key, new_result.job_key)
        superseded = json.loads((old.job_dir / "superseded.json").read_text(encoding="utf-8"))
        self.assertEqual(new_result.job_key, superseded["superseded_by"])
        self.assertEqual("b" * 40, superseded["candidate_sha"])

    def test_new_candidate_does_not_supersede_published_job(self) -> None:
        published = self._write_verified_receipt()
        publish_verification.mark_published(published.job_dir, pr_number=414, remote_oid="a" * 40)
        newer = dict(self.identity)
        newer["candidate_sha"] = "b" * 40

        with mock.patch("codex_refactor_loop.publish_verification.subprocess.Popen", return_value=mock.Mock(pid=99)):
            new_result = publish_verification.prepare_or_schedule(**newer, git_runner=self._private_ref_git("b" * 40))

        self.assertNotEqual(published.job_key, new_result.job_key)
        self.assertFalse((published.job_dir / "superseded.json").exists())
        self.assertTrue((published.job_dir / "published.json").is_file())

    def test_mark_published_records_pr_remote_oid_and_clears_retry_state(self) -> None:
        result = self._prepared_job_without_child()
        publish_verification.record_job_retry(result.job_dir, "push-failed:7", now=1_000_000.0)

        publish_verification.mark_published(result.job_dir, pr_number=414, remote_oid="a" * 40)

        payload = json.loads((result.job_dir / "published.json").read_text(encoding="utf-8"))
        self.assertEqual("PublishVerificationPublished", payload["schema"])
        self.assertEqual(414, payload["pr_number"])
        self.assertEqual("a" * 40, payload["remote_oid"])
        self.assertFalse((result.job_dir / "retry.json").exists())

    def test_failed_receipts_use_per_job_retry_schedule_then_quarantine(self) -> None:
        result = self._prepared_job_without_child()
        retry_path = result.job_dir / "retry.json"
        root_quarantine_marker = self.tmp / "QUARANTINED"

        statuses: list[tuple[str, str]] = []
        now = 1_000_000.0
        self.assertFalse(root_quarantine_marker.exists())
        for index, wait_seconds in enumerate((1800, 7200, 28800, None), start=1):
            self._write_failed_result(result.job_dir, f"failure-{index}")
            status = publish_verification.record_failed_receipt_retry(result.job_dir, now=now)
            statuses.append((status.state, status.reason))
            payload = json.loads(retry_path.read_text(encoding="utf-8"))
            self.assertEqual(index, payload["failure_count"])
            if wait_seconds is not None:
                self.assertEqual(now + wait_seconds, payload["next_retry_after_epoch"])
                now += wait_seconds + 1

        self.assertEqual(
            [
                ("RETRY_WAIT", "failed:failure-1"),
                ("RETRY_WAIT", "failed:failure-2"),
                ("RETRY_WAIT", "failed:failure-3"),
                ("QUARANTINED", "failed:failure-4"),
            ],
            statuses,
        )
        self.assertFalse(root_quarantine_marker.exists())

    def test_failed_receipt_wait_does_not_compound_and_elapsed_wait_relaunches_child(self) -> None:
        result = self._prepared_job_without_child()
        self._write_failed_result(result.job_dir, "failure-1")
        publish_verification.record_failed_receipt_retry(result.job_dir, now=1_000_000.0)
        popen_calls: list[list[str]] = []

        with mock.patch("codex_refactor_loop.publish_verification.time.time", return_value=1_000_100.0):
            with mock.patch("codex_refactor_loop.publish_verification.subprocess.Popen", side_effect=AssertionError("retry wait must not relaunch")):
                waiting = publish_verification.prepare_or_schedule(
                    **self.identity,
                    git_runner=self._private_ref_git("a" * 40),
                    start_child=True,
                )

        self.assertEqual("waiting", waiting.status)
        self.assertEqual("retry-wait", waiting.reason)
        self.assertEqual(1, json.loads((result.job_dir / "retry.json").read_text(encoding="utf-8"))["failure_count"])

        def fake_popen(command: Sequence[str], **_kwargs: object) -> mock.Mock:
            popen_calls.append([str(arg) for arg in command])
            return mock.Mock(pid=4321)

        with mock.patch("codex_refactor_loop.publish_verification.time.time", return_value=1_001_801.0):
            with mock.patch("codex_refactor_loop.publish_verification.subprocess.Popen", side_effect=fake_popen):
                relaunched = publish_verification.prepare_or_schedule(
                    **self.identity,
                    git_runner=self._private_ref_git("a" * 40),
                    start_child=True,
                )

        self.assertEqual("queued", relaunched.status)
        self.assertEqual("started", relaunched.reason)
        self.assertEqual(1, len(popen_calls))
        self.assertFalse((result.job_dir / "result.json").exists())
        archived = sorted(result.job_dir.glob("result-retry-*.json"))
        self.assertEqual(1, len(archived))

    def _prepared_job_without_child(self) -> publish_verification.PublishVerificationJobResult:
        with mock.patch("codex_refactor_loop.publish_verification.subprocess.Popen", side_effect=AssertionError("child launch disabled in test")):
            return publish_verification.prepare_or_schedule(
                **self.identity,
                git_runner=self._private_ref_git("a" * 40),
                start_child=False,
            )

    def _write_verified_receipt(self) -> publish_verification.PublishVerificationJobResult:
        result = self._prepared_job_without_child()
        request = json.loads((result.job_dir / "request.json").read_text(encoding="utf-8"))
        payload = {
            "schema": "PublishVerificationResult",
            "status": "VERIFIED",
            "reason": "verified",
            "job_key": result.job_key,
            "verified_sha": "a" * 40,
            "tested_sha": "a" * 40,
            "post_tested_sha": "a" * 40,
            "gate_id": request["gate_id"],
            "command_digest": request["command_digest"],
            "checkpoint_hashes": request["checkpoint_hashes"],
            "private_ref": request["private_ref"],
            "private_ref_oid": "a" * 40,
            "commands": [
                {
                    "name": "BUILD_CMD",
                    "command_sha256": publish_verification.string_digest("make build"),
                    "exit": 0,
                    "exit_marker": True,
                    "log": ".refactor-loop/state/publish-verification/jobs/x/BUILD_CMD.log",
                },
                {
                    "name": "TEST_CMD",
                    "command_sha256": publish_verification.string_digest("make test"),
                    "exit": 0,
                    "exit_marker": True,
                    "log": ".refactor-loop/state/publish-verification/jobs/x/TEST_CMD.log",
                },
            ],
        }
        (result.job_dir / "result.json").write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return result

    def _write_failed_result(self, job_dir: Path, reason: str) -> None:
        payload = {
            "schema": "PublishVerificationResult",
            "status": "FAILED",
            "reason": reason,
            "completed_at": reason,
        }
        (job_dir / "result.json").write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def _private_ref_git(self, oid: str):
        def fake_git(args: Sequence[str]) -> subprocess.CompletedProcess[str]:
            if args[0] == "update-ref":
                return subprocess.CompletedProcess(args, 0, "", "")
            if args[:2] == ["rev-parse", "--verify"]:
                return subprocess.CompletedProcess(args, 0, oid + "\n", "")
            raise AssertionError(f"unexpected git call: {args}")

        return fake_git

    def _unexpected_git(self, message: str):
        def fake_git(args: Sequence[str]) -> subprocess.CompletedProcess[str]:
            raise AssertionError(f"{message}: {args}")

        return fake_git

    def _worktree_git(self, *, head_oid: str, private_ref_oid: str, clean: bool = True):
        def fake_git(args: Sequence[str]) -> subprocess.CompletedProcess[str]:
            if args == ["rev-parse", "HEAD"]:
                return subprocess.CompletedProcess(args, 0, head_oid + "\n", "")
            if args == ["status", "--porcelain"]:
                return subprocess.CompletedProcess(args, 0, "" if clean else " M implementation.txt\n", "")
            if args[:2] == ["rev-parse", "--verify"]:
                return subprocess.CompletedProcess(args, 0, private_ref_oid + "\n", "")
            if args[0] == "update-ref":
                return subprocess.CompletedProcess(args, 0, "", "")
            raise AssertionError(f"unexpected git call: {args}")

        return fake_git

    def _worktree_git_with_statuses(self, *, head_oids: list[str], private_ref_oid: str, statuses: list[str]):
        heads = list(head_oids)
        worktree_statuses = list(statuses)

        def fake_git(args: Sequence[str]) -> subprocess.CompletedProcess[str]:
            if args == ["rev-parse", "HEAD"]:
                if not heads:
                    raise AssertionError("unexpected extra HEAD probe")
                return subprocess.CompletedProcess(args, 0, heads.pop(0) + "\n", "")
            if args == ["status", "--porcelain"]:
                if not worktree_statuses:
                    raise AssertionError("unexpected extra status probe")
                return subprocess.CompletedProcess(args, 0, worktree_statuses.pop(0), "")
            if args[:2] == ["rev-parse", "--verify"]:
                return subprocess.CompletedProcess(args, 0, private_ref_oid + "\n", "")
            if args[0] == "update-ref":
                return subprocess.CompletedProcess(args, 0, "", "")
            raise AssertionError(f"unexpected git call: {args}")

        return fake_git


if __name__ == "__main__":
    unittest.main()
