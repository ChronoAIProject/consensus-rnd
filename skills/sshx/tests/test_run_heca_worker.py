"""Run the real heca carrier script against a closed-route fake on PATH.

Baseline recorded before the carrier implementation (2026-10-09): raw spawn
acknowledged a running daemon agent without sshx artifacts; even wait without an
explicit timeout exited 0 with timed_out true. See HECA_WORKER_SPEC.md for the
real evidence coordinate. These deterministic tests supplement that live evidence.
R1 models turn duration against the daemon's 30000 ms default and its maximum
explicit wait duration without spending wall-clock minutes in the fake.
"""
from __future__ import annotations

import json
import os
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
RUNNER = ROOT / "skills/sshx/scripts/run-heca-worker.py"
OWNER = RUNNER.with_name("run-codex-worker.sh")
AGENT_ID = "daemon-issued-agent-1"
FAKE = r'''
import json, os, re, sys
from pathlib import Path
args = sys.argv[1:]
with open(os.environ["HECA_TEST_CALLS"], "a") as out:
    out.write(json.dumps(args) + "\n")
def emit(value, rc=0):
    print(json.dumps(value), flush=True)
    sys.exit(rc)
def agent(state):
    return {"id": os.environ.get("HECA_TEST_OBSERVED_ID", "daemon-issued-agent-1"),
            "state": state, "provider": os.environ.get("HECA_TEST_PROVIDER", "codex"),
            "mode": os.environ.get("HECA_TEST_MODE", "full-access"),
            "worktree_id": "owned-worktree", "cwd": os.environ["HECA_TEST_TARGET"]}
if args == ["--json", "daemon", "status"]:
    emit({"server_version": "verified-test"}, int(os.environ.get("HECA_TEST_DAEMON_EXIT", "0")))
if args == ["--json", "providers", "list"]:
    emit([{ "id": os.environ.get("HECA_TEST_PROVIDER", "codex"), "enabled": os.environ.get("HECA_TEST_DISABLED") != "1",
            "status": os.environ.get("HECA_TEST_PROVIDER_STATUS", "installed"),
            "modes": [os.environ.get("HECA_TEST_LISTED_MODE", os.environ.get("HECA_TEST_MODE", "full-access"))] }])
if args == ["--json", "worktree", "list"]:
    emit([] if os.environ.get("HECA_TEST_NO_PLACEMENT") else
         [{"id": "owned-worktree", "path": os.environ["HECA_TEST_TARGET"], "archived_at": None}])
if args[:2] == ["--json", "run"]:
    expected = ["--json", "run", "--provider", os.environ.get("HECA_TEST_PROVIDER", "codex"),
                "--mode", os.environ.get("HECA_TEST_MODE", "full-access")]
    if os.environ.get("HECA_TEST_THINKING"):
        expected += ["--thinking", os.environ["HECA_TEST_THINKING"]]
    expected += ["--worktree-id", "owned-worktree", "--prompt"]
    if args[:-1] != expected:
        sys.exit(97)
    prompt = args[-1]
    result = Path(re.search(r"^Result envelope: (.+)$", prompt, re.M)[1])
    sentinel = Path(re.search(r"^Completion sentinel: (.+)$", prompt, re.M)[1])
    marker = os.environ.get("HECA_TEST_ARTIFACTS", "valid")
    if marker != "missing":
        value = {"conclusion": {"verdict": os.environ.get("HECA_TEST_VERDICT", "approve"),
                                "evidence": "worker-owned"}, "log_ref": "opaque"}
        data = json.dumps(value) if marker != "invalid" else '{"conclusion": "broken"}'
        if marker == "duplicate":
            data = '{"conclusion":{},"log_ref":"a","log_ref":"b"}'
        if marker == "non-json":
            data = '{"conclusion":{"number":NaN},"log_ref":"a"}'
        if marker == "empty-log":
            data = '{"conclusion":{},"log_ref":""}'
        if marker == "extra-key":
            data = '{"conclusion":{},"log_ref":"a","other":{}}'
        tmp = result.with_name(result.name + ".tmp")
        tmp.write_text(data)
        tmp.replace(result)
        if marker == "symlink":
            result.rename(result.with_name("worker-data"))
            result.symlink_to(result.with_name("worker-data"))
        if marker != "no-sentinel":
            tmp = sentinel.with_name(sentinel.name + ".tmp")
            tmp.write_text("worker-finished\n")
            tmp.replace(sentinel)
    if os.environ.get("HECA_TEST_ACK_MISSING"):
        emit({"loose": False, "worktree_id": "owned-worktree"})
    emit({"agent_id": "daemon-issued-agent-1", "loose": os.environ.get("HECA_TEST_LOOSE") == "1",
          "worktree_id": "owned-worktree", "workspace_id": "owned-workspace"})
if args == ["--json", "agent", "status", "daemon-issued-agent-1"]:
    value = agent("running")
    if os.environ.get("HECA_TEST_WRONG_CWD"):
        value["cwd"] = "/wrong/target"
    emit({"agent": value})
if args == ["--json", "agent", "kill", "daemon-issued-agent-1"]:
    print("ok")
    sys.exit(1 if os.environ.get("HECA_TEST_STOP_FAIL") else 0)
if args[:4] == ["--json", "agent", "wait", "--state"] and args[4] in ("idle", "exited"):
    state = args[4]
    tail = args[5:]
    duration = 30000  # Observed nightly daemon default, not an indefinite wait.
    if len(tail) == 3 and tail[0] == "--timeout-ms":
        duration = int(tail[1])
        if not 0 <= duration <= 4294967295:
            sys.exit(2)  # The real CLI rejects values beyond its u32 range.
        tail = tail[2:]
    if tail != ["daemon-issued-agent-1"]:
        sys.exit(97)
    if state == "exited":
        stopped = not os.environ.get("HECA_TEST_STOP_FAIL")
        stop_ms = int(os.environ.get("HECA_TEST_STOP_MS", "0"))
        emit({"agent": agent("exited" if stopped and stop_ms <= duration else "running"),
              "timed_out": not stopped or stop_ms > duration})
    turn_ms = int(os.environ.get("HECA_TEST_TURN_MS", "10000"))
    if turn_ms > duration:
        emit({"agent": agent("running"), "timed_out": True})
    if os.environ.get("HECA_TEST_WAIT_GATE"):
        with open(os.environ["HECA_TEST_WAIT_GATE"], "w") as ready:
            ready.write("waiting\n")
            ready.flush()
        with open(os.environ["HECA_TEST_RELEASE"], "r") as release:
            release.read()
    emit({"agent": agent(os.environ.get("HECA_TEST_STATE", "idle")),
          "timed_out": os.environ.get("HECA_TEST_TIMED_OUT") == "1"},
         int(os.environ.get("HECA_TEST_WAIT_EXIT", "0")))
sys.stderr.write("unapproved fake route: " + repr(args))
sys.exit(97)
'''


@dataclass(frozen=True)
class Run:
    process: subprocess.CompletedProcess[bytes]
    receipt: dict[str, object] | None
    status: dict[str, object] | None


class HecaRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / ".worktrees" if (ROOT / ".worktrees").is_dir() else ROOT)
        self.directory = Path(self.temp.name)
        self.bin = self.directory / "bin"
        self.bin.mkdir()
        fake = self.bin / "heca"
        fake.write_text(f"#!{sys.executable}\n" + FAKE)
        fake.chmod(0o755)
        self.calls = self.directory / "calls"
        self.target = self.directory / "checkout"
        self.target.mkdir()
        self.env = os.environ | {"PATH": str(self.bin) + ":" + os.environ["PATH"],
                                "SSHX_HOME": str(self.directory / "sshx"),
                                "HECA_TEST_CALLS": str(self.calls), "HECA_TEST_TARGET": str(self.target)}
        self.options = ["--provider", "codex", "--mode", "full-access"]

    def tearDown(self) -> None:
        self.temp.cleanup()

    def calls_read(self) -> list[list[str]]:
        return [json.loads(line) for line in self.calls.read_text().splitlines()] if self.calls.exists() else []

    def collect(self, process: subprocess.CompletedProcess[bytes]) -> Run:
        receipt = json.loads(process.stdout) if process.stdout else None
        status_path = Path(receipt["status_ref"]) if receipt else None
        status = json.loads(status_path.read_text()) if status_path and status_path.is_file() else None
        return Run(process, receipt, status)

    def run_attempt(self, stage: str = "review", thinking: str | None = None, **env: str) -> Run:
        options = self.options + (["--thinking", thinking] if thinking else [])
        process = subprocess.run([sys.executable, str(RUNNER), *options, "--stage", stage,
                                  "--work-target", str(self.target)], input=b"bounded brief",
                                 capture_output=True, env=self.env | env, timeout=30)
        return self.collect(process)

    def test_check_is_read_only_and_binding_required(self) -> None:
        for extra, detected in (({}, True), ({"HECA_TEST_DISABLED": "1"}, False),
                                ({"HECA_TEST_DAEMON_EXIT": "1"}, False),
                                ({"HECA_TEST_PROVIDER_STATUS": "unavailable"}, False),
                                ({"HECA_TEST_LISTED_MODE": "auto"}, False)):
            with self.subTest(extra=extra):
                self.calls.unlink(missing_ok=True)
                p = subprocess.run([sys.executable, str(RUNNER), "--check", *self.options],
                                   capture_output=True, env=self.env | extra, timeout=30)
                self.assertEqual(json.loads(p.stdout)["detected"], detected)
                self.assertEqual(p.returncode, 0 if detected else 1)
                self.assertFalse(Path(self.env["SSHX_HOME"]).exists())
                self.assertTrue(all(c in (["--json", "daemon", "status"], ["--json", "providers", "list"])
                                    for c in self.calls_read()))

    def test_environment_presence_without_heca_is_not_detection(self) -> None:
        (self.bin / "heca").unlink()
        env = self.env | {"PATH": str(self.bin) + ":/usr/bin:/bin", "HECA_AGENT_ID": "parent-only",
                          "HECA_HOME": str(self.directory / "unused")}
        p = subprocess.run([sys.executable, str(RUNNER), "--check", *self.options],
                           capture_output=True, env=env, timeout=30)
        self.assertEqual(p.returncode, 1)
        self.assertFalse(json.loads(p.stdout)["detected"])
        self.assertFalse(Path(self.env["SSHX_HOME"]).exists())
        self.assertEqual(self.calls_read(), [])

    def test_receipt_before_stdin_and_heca_and_exact_projection(self) -> None:
        p = subprocess.Popen([sys.executable, str(RUNNER), *self.options, "--stage", "review",
                              "--work-target", str(self.target)], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=self.env)
        try:
            ready, _, _ = select.select([p.stdout], [], [], 10)
            self.assertTrue(ready)
            receipt = p.stdout.readline()
            value = json.loads(receipt)
            owner = subprocess.run(["bash", str(OWNER), "--project-paths", "--flight-id", value["flight_id"],
                                    "--attempt", str(value["attempt"])], capture_output=True, env=self.env, check=True)
            self.assertEqual(receipt, owner.stdout)
            self.assertEqual(self.calls_read(), [])
            output, error = p.communicate(b"brief", timeout=30)
            self.assertEqual(output, b"")
            self.assertEqual(p.returncode, 0, error)
        finally:
            if p.poll() is None:
                p.terminate()
                p.communicate(timeout=30)

    def test_complete_worker_owned_artifacts_and_saved_identity(self) -> None:
        r = self.run_attempt()
        self.assertEqual(r.process.returncode, 0, r.process.stderr)
        self.assertEqual(r.status["status"], "COMPLETE")
        self.assertNotIn("verdict", r.status)
        self.assertEqual(r.status["agent_id"], AGENT_ID)
        self.assertEqual(json.loads(Path(r.status["agent_identity_ref"]).read_text())["agent_id"], AGENT_ID)
        self.assertEqual(json.loads(Path(r.receipt["result_ref"]).read_text())["conclusion"]["evidence"], "worker-owned")
        self.assertEqual(Path(r.receipt["completion_sentinel_ref"]).read_text(), "worker-finished\n")
        calls = self.calls_read()
        self.assertEqual(sum(c[:2] == ["--json", "run"] for c in calls), 1)
        self.assertFalse(any(c[1:3] == ["daemon", "status"] or c[1:3] == ["providers", "list"] for c in calls))
        self.assertFalse(any("kill" in c for c in calls))

    def test_fake_default_wait_expires_before_a_seat_length_turn(self) -> None:
        options = [str(self.bin / "heca"), "--json", "agent", "wait", "--state", "idle"]
        for duration, timed_out in (([], True), (["--timeout-ms", "4294967295"], False)):
            with self.subTest(duration=duration):
                p = subprocess.run([*options, *duration, AGENT_ID], capture_output=True,
                                   env=self.env | {"HECA_TEST_TURN_MS": "300000"}, check=True)
                observation = json.loads(p.stdout)
                self.assertEqual(observation["timed_out"], timed_out)
                self.assertEqual(observation["agent"]["state"], "running" if timed_out else "idle")

    def test_seat_length_turn_completes_with_one_maximum_duration_wait(self) -> None:
        r = self.run_attempt(HECA_TEST_TURN_MS="300000")
        self.assertEqual(r.process.returncode, 0, r.process.stderr)
        self.assertEqual(r.status["status"], "COMPLETE")
        waits = [c for c in self.calls_read() if c[1:3] == ["agent", "wait"]]
        self.assertEqual(waits, [["--json", "agent", "wait", "--state", "idle",
                                  "--timeout-ms", "4294967295", AGENT_ID]])

    def test_stop_confirmation_uses_one_maximum_duration_wait(self) -> None:
        r = self.run_attempt(HECA_TEST_TIMED_OUT="1", HECA_TEST_STOP_MS="300000")
        self.assertEqual(r.process.returncode, 1)
        self.assertEqual(r.status["reason_code"], "WAIT_NOT_TERMINAL")
        self.assertTrue(r.status["agent_not_running"])
        waits = [c for c in self.calls_read() if c[1:3] == ["agent", "wait"]]
        self.assertEqual([c[4] for c in waits], ["idle", "exited"])
        self.assertTrue(all(c[5:] == ["--timeout-ms", "4294967295", AGENT_ID] for c in waits))

    def test_one_attempt_no_artifact_repair_on_each_failure(self) -> None:
        cases = [({"HECA_TEST_TIMED_OUT": "1"}, "WAIT_NOT_TERMINAL"),
                 ({"HECA_TEST_STATE": "running"}, "WAIT_NOT_TERMINAL"),
                 ({"HECA_TEST_STATE": "exited"}, "WAIT_NOT_TERMINAL"),
                 ({"HECA_TEST_WAIT_EXIT": "1"}, "WAIT_NOT_TERMINAL"),
                 ({"HECA_TEST_ARTIFACTS": "missing"}, "RESULT_MISSING"),
                 ({"HECA_TEST_ARTIFACTS": "invalid"}, "ENVELOPE_INVALID"),
                 ({"HECA_TEST_ARTIFACTS": "duplicate"}, "ENVELOPE_INVALID"),
                 ({"HECA_TEST_ARTIFACTS": "non-json"}, "ENVELOPE_INVALID"),
                 ({"HECA_TEST_ARTIFACTS": "empty-log"}, "ENVELOPE_INVALID"),
                 ({"HECA_TEST_ARTIFACTS": "extra-key"}, "ENVELOPE_INVALID"),
                 ({"HECA_TEST_ARTIFACTS": "symlink"}, "RESULT_MISSING"),
                 ({"HECA_TEST_ARTIFACTS": "no-sentinel"}, "SENTINEL_MISSING"),
                 ({"HECA_TEST_VERDICT": "satisfied"}, "VERDICT_INVALID"),
                 ({"HECA_TEST_LOOSE": "1"}, "PLACEMENT_INVALID"),
                 ({"HECA_TEST_WRONG_CWD": "1"}, "PLACEMENT_INVALID")]
        for env, reason in cases:
            with self.subTest(env=env):
                self.calls.unlink(missing_ok=True)
                r = self.run_attempt(**env)
                self.assertEqual(r.process.returncode, 1)
                self.assertEqual(r.status["reason_code"], reason)
                self.assertTrue(r.status["agent_not_running"])
                calls = self.calls_read()
                self.assertEqual(sum(c[:2] == ["--json", "run"] for c in calls), 1)
                self.assertEqual(calls[-2:], [["--json", "agent", "kill", AGENT_ID],
                                             ["--json", "agent", "wait", "--state", "exited",
                                              "--timeout-ms", "4294967295", AGENT_ID]])
                if env.get("HECA_TEST_ARTIFACTS") == "invalid":
                    self.assertEqual(Path(r.receipt["result_ref"]).read_text(), '{"conclusion": "broken"}')
                if env.get("HECA_TEST_ARTIFACTS") == "missing":
                    self.assertFalse(Path(r.receipt["result_ref"]).exists())
                    self.assertFalse(Path(r.receipt["completion_sentinel_ref"]).exists())

    def test_unknown_identity_or_failed_stop_keeps_flight_protected(self) -> None:
        r = self.run_attempt(HECA_TEST_OBSERVED_ID="different-agent", HECA_TEST_STOP_FAIL="1")
        self.assertEqual(r.process.returncode, 1)
        self.assertIsNone(r.status)
        self.assertIn(b"STOP_UNCONFIRMED", r.process.stderr)
        self.assertTrue(all("different-agent" not in c for c in self.calls_read()))

    def test_unowned_target_is_unavailable_without_spawn(self) -> None:
        r = self.run_attempt(HECA_TEST_NO_PLACEMENT="1")
        self.assertEqual(r.status["reason_code"], "PLACEMENT_UNAVAILABLE")
        self.assertEqual(self.calls_read(), [["--json", "worktree", "list"]])
        self.assertFalse(Path(r.receipt["result_ref"]).exists())

    def test_signal_stops_only_saved_agent_before_terminal_status(self) -> None:
        gate = self.directory / "gate"
        release = self.directory / "release"
        os.mkfifo(gate)
        os.mkfifo(release)
        fd = os.open(gate, os.O_RDONLY | os.O_NONBLOCK)
        p = subprocess.Popen([sys.executable, str(RUNNER), *self.options, "--stage", "review",
                              "--work-target", str(self.target)], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             env=self.env | {"HECA_TEST_WAIT_GATE": str(gate), "HECA_TEST_RELEASE": str(release)})
        try:
            p.stdin.write(b"brief")
            p.stdin.close()
            p.stdin = None
            receipt = json.loads(p.stdout.readline())
            ready, _, _ = select.select([fd], [], [], 10)
            self.assertTrue(ready)
            self.assertIn(b"waiting", os.read(fd, 4096))
            self.assertFalse(Path(receipt["status_ref"]).exists())
            self.assertEqual(json.loads(Path(receipt["log_refs"]["last_message"]).read_text())["agent_id"], AGENT_ID)
            p.send_signal(signal.SIGTERM)
            _, error = p.communicate(timeout=30)
            self.assertEqual(p.returncode, 1, error)
            status = json.loads(Path(receipt["status_ref"]).read_text())
            self.assertEqual(status["reason_code"], "INTERRUPTED")
            self.assertTrue(status["agent_not_running"])
        finally:
            os.close(fd)
            if p.poll() is None:
                p.terminate()
                p.communicate(timeout=30)

    def test_usage_invalid_identity_collision_no_launch(self) -> None:
        for args in (["--check"], ["--provider", "codex", "--mode", "auto", "--check"],
                     [*self.options, "--check", "--timeout", "1"]):
            p = subprocess.run([sys.executable, str(RUNNER), *args], capture_output=True, env=self.env, timeout=30)
            self.assertEqual(p.returncode, 64)
            self.assertEqual(p.stdout, b"")
        self.assertEqual(self.calls_read(), [])
        args = [*self.options, "--stage", "review", "--work-target", str(self.target),
                "--flight-id", "bad", "--attempt", "1"]
        p = subprocess.run([sys.executable, str(RUNNER), *args], capture_output=True, env=self.env, timeout=30)
        self.assertEqual(p.returncode, 64)
        self.assertEqual(p.stdout, b"")
        r = self.run_attempt()
        calls = self.calls_read()
        args[-3] = r.receipt["flight_id"]
        p = subprocess.run([sys.executable, str(RUNNER), *args], input=b"brief", capture_output=True,
                           env=self.env, timeout=30)
        self.assertEqual(p.returncode, 1)
        self.assertEqual(self.calls_read(), calls)
        self.assertIn(b"RUN_DIR_COLLISION", p.stderr)

    def test_explicit_binding_and_thinking_are_forwarded_without_defaults(self) -> None:
        for provider, mode in (("codex", "full-access"), ("claude", "bypassPermissions")):
            with self.subTest(provider=provider):
                self.calls.unlink(missing_ok=True)
                self.options = ["--provider", provider, "--mode", mode]
                r = self.run_attempt(thinking="low", HECA_TEST_THINKING="low",
                                     HECA_TEST_PROVIDER=provider, HECA_TEST_MODE=mode)
                self.assertEqual(r.process.returncode, 0, r.process.stderr)
                self.assertEqual(r.status["provider"], provider)
                self.assertEqual(r.status["mode"], mode)
                self.assertEqual(r.status["thinking"], "low")

    def test_lost_spawn_identity_is_protected_without_guessing_or_retry(self) -> None:
        r = self.run_attempt(HECA_TEST_ACK_MISSING="1")
        self.assertEqual(r.process.returncode, 1)
        self.assertIsNone(r.status)
        calls = self.calls_read()
        self.assertEqual(sum(c[:2] == ["--json", "run"] for c in calls), 1)
        self.assertFalse(any("kill" in c for c in calls))
        self.assertIn(b"SPAWN_UNCONFIRMED", r.process.stderr)
        self.assertIn(b"STOP_UNCONFIRMED", r.process.stderr)

    def test_stage_verdict_sets_and_implementation_without_verdict(self) -> None:
        for stage, verdict in (("thinking", "propose"), ("termination", "satisfied"), ("implementation", "ignored")):
            r = self.run_attempt(stage, HECA_TEST_VERDICT=verdict)
            self.assertEqual(r.process.returncode, 0, r.process.stderr)


if __name__ == "__main__":
    unittest.main()
