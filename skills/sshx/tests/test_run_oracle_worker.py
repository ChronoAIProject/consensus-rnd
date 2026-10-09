"""Behavior tests for ``skills/sshx/scripts/run-oracle-worker.py``.

Every test runs the real script with a fake ``nyxid`` first on ``PATH``. The fake records
each argv and its working directory and rejects every route except the two broker routes
the runner may call. The broker stream fixture is a real response captured on 2026-10-07
with only ``chatgpt_url`` replaced.
"""

import json
import os
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from dataclasses import dataclass
from pathlib import Path

from test_codex_worker_tools import FAKE_CODEX


ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "skills" / "sshx" / "scripts"
ORACLE_RUNNER = SCRIPTS / "run-oracle-worker.py"
CODEX_RUNNER = SCRIPTS / "run-codex-worker.sh"
CLEANUP = SCRIPTS / "clean-codex-worker-runs.sh"
PRUNE = SCRIPTS / "prune-inactive-codex-worker-runs.sh"
BROKER_STREAM = Path(__file__).with_name("fixtures") / "oracle_broker_stream.sse"
CAPTURED_ANSWER = (
    '{"conclusion":{"verdict":"approve","reason":"In ordinary integer arithmetic, adding 2 and 2 '
    'gives 4."},"log_ref":"inline"}'
)
CAPTURED_TASK = "1ad0c7c5-9622-4e3e-8fc5-26752e4c501c"
POOL_ROUTE = ["proxy", "request", "oracle", "api/v1/oracle/pools", "--output", "json"]
CHAT_ROUTE = [
    "proxy", "request", "oracle", "api/v1/oracle/openai/v1/chat/completions",
    "--method", "POST", "--data", "@request.json",
]
DEFAULT_POOLS = {"pools": [{"slug": "pool-x", "is_active": True, "online_workers": 1}]}
COMPLETION_ONLY_KEYS = {"payload_ref", "completion_sentinel_ref", "model"}
WATCHDOG_SECONDS = 60
DAY_SECONDS = 24 * 3600

FAKE_NYXID = r'''
import json
import os
import sys

POOL_ROUTE = ["proxy", "request", "oracle", "api/v1/oracle/pools", "--output", "json"]
CHAT_ROUTE = ["proxy", "request", "oracle", "api/v1/oracle/openai/v1/chat/completions",
              "--method", "POST", "--data", "@request.json"]
argv = sys.argv[1:]
with open(os.environ["FAKE_NYXID_LOG"], "a", encoding="utf-8") as log:
    log.write(json.dumps({"argv": argv, "cwd": os.getcwd()}) + "\n")
if argv == POOL_ROUTE:
    with open(os.environ["FAKE_POOLS"], "rb") as pools:
        sys.stdout.buffer.write(pools.read())
    sys.exit(int(os.environ.get("FAKE_POOLS_EXIT", "0")))
if argv == CHAT_ROUTE:
    with open("request.json", encoding="utf-8") as request:
        json.load(request)
    gate = os.environ.get("FAKE_CHAT_GATE")
    if gate:
        with open(gate + ".ready", "w") as ready:
            ready.write("ready\n")
        with open(gate + ".release") as release:
            release.readline()
    with open(os.environ["FAKE_STREAM"], "rb") as stream:
        sys.stdout.buffer.write(stream.read())
    sys.stderr.write(os.environ.get("FAKE_CHAT_STDERR", ""))
    sys.exit(int(os.environ.get("FAKE_CHAT_EXIT", "0")))
sys.stderr.write("fake nyxid: route not allowed: " + " ".join(argv) + "\n")
sys.exit(97)
'''


def data_line(payload: object) -> str:
    return "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"


def content_chunk(text: str) -> str:
    return data_line({"choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}]})


def final_chunk(finish_reason: str | None = "stop", oracle: dict[str, object] | None = None) -> str:
    fields: dict[str, object] = {"choices": [{"index": 0, "delta": {}, "finish_reason": finish_reason}]}
    fields["oracle"] = {"task_id": "task-1", "pool": "pool-x", "observed_model_switcher": "gpt_6_pro"} if oracle is None else oracle
    return data_line(fields)


DONE = "data: [DONE]\n\n"
COMPLETE_STREAM = content_chunk("ok") + final_chunk() + DONE
# Each case violates exactly one completion conjunct of COMPLETE_STREAM with nyxid exiting 0.
SINGLE_VIOLATIONS = {
    "nyxid exit": (COMPLETE_STREAM, 1, "CARRIER_EXIT_NONZERO"),
    "data line parses": (content_chunk("ok") + "data: {not json\n\n" + final_chunk() + DONE, 0, "STREAM_MALFORMED"),
    "no error chunk": (content_chunk("ok") + data_line({"error": {"code": "usage_limit_reached"}}) + final_chunk() + DONE, 0, "BROKER_ERROR"),
    "done line": (content_chunk("ok") + final_chunk(), 0, "STREAM_NOT_TERMINAL"),
    "finish reason stop": (content_chunk("ok") + final_chunk("length") + DONE, 0, "STREAM_NOT_TERMINAL"),
    "broker task id": (content_chunk("ok") + final_chunk(oracle={"task_id": "", "pool": "pool-x"}) + DONE, 0, "TASK_ID_MISSING"),
}
QUOTED_DONE_STREAM = content_chunk("a reply that quotes\ndata: [DONE]\nand data: [DONE] inline") + final_chunk(None)
# Anchor in the terminal decision -> replacement, and the single-violation case that kills it.
DECISION_MUTANTS = {
    "nyxid exit": ("if carrier_exit != 0:", "if False:"),
    "data line parses": ("if not observation.lines_parse:", "if False:"),
    "no error chunk": ("if observation.error_chunk:", "if False:"),
    "done line": ("if not observation.done_line:", "if False:"),
    "finish reason stop": ('if observation.finish_reason != "stop":', "if False:"),
    "broker task id": ("if task_id is None:", "if False:"),
}


@dataclass(frozen=True)
class OracleRun:
    process: subprocess.CompletedProcess[bytes]
    receipt: dict[str, object] | None
    status: dict[str, object] | None

    @property
    def run_dir(self) -> Path:
        assert self.receipt is not None
        return Path(str(self.receipt["run_dir"]))

    @property
    def stderr(self) -> str:
        return self.process.stderr.decode("utf-8", "replace")


class OracleRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_context = tempfile.TemporaryDirectory()
        self.temp_dir = Path(self.temp_context.name)
        self.bin_dir = self.temp_dir / "bin"
        self.bin_dir.mkdir()
        fake = self.bin_dir / "nyxid"
        fake.write_text(f"#!{sys.executable}\n{FAKE_NYXID}", encoding="utf-8")
        fake.chmod(0o755)
        codex = self.bin_dir / "codex"
        codex.write_text(FAKE_CODEX)
        codex.chmod(0o755)
        real_jq = shutil.which("jq")
        assert real_jq is not None, "jq is required by the layout owner's projection"
        (self.bin_dir / "jq").symlink_to(real_jq)
        self.sshx_home = self.temp_dir / "sshx"
        self.nyxid_log = self.temp_dir / "nyxid.log"
        self.stream = self.temp_dir / "stream.sse"
        self.pools = self.temp_dir / "pools.json"
        self.pools.write_text(json.dumps(DEFAULT_POOLS))
        self.counter = 0

    def tearDown(self) -> None:
        self.temp_context.cleanup()

    def environment(self, **extra: str) -> dict[str, str]:
        env = os.environ.copy()
        env.update({
            "PATH": f"{self.bin_dir}:/bin:/usr/bin",
            "SSHX_HOME": str(self.sshx_home),
            "FAKE_NYXID_LOG": str(self.nyxid_log),
            "FAKE_POOLS": str(self.pools),
            "FAKE_STREAM": str(self.stream),
            "FAKE_LAUNCH_LOG": str(self.temp_dir / "codex-launch.log"),
            "CARRIER_SYNC_DIR": str(self.temp_dir),
        })
        env.update(extra)
        return env

    def next_flight_id(self) -> str:
        self.counter += 1
        return f"{int(time.time()):08x}{self.counter:016x}"

    def nyxid_calls(self) -> list[dict[str, object]]:
        if not self.nyxid_log.exists():
            return []
        return [json.loads(line) for line in self.nyxid_log.read_text(encoding="utf-8").splitlines()]

    def run_oracle(
        self,
        stream: str | bytes = COMPLETE_STREAM,
        *,
        brief: bytes = b"Return a compact JSON envelope.\n",
        arguments: tuple[str, ...] = (),
        runner: Path = ORACLE_RUNNER,
        chat_exit: int = 0,
        **extra: str,
    ) -> OracleRun:
        self.stream.write_bytes(stream.encode("utf-8") if isinstance(stream, str) else stream)
        process = subprocess.run(
            [sys.executable, str(runner), *arguments],
            input=brief,
            capture_output=True,
            env=self.environment(FAKE_CHAT_EXIT=str(chat_exit), **extra),
            timeout=WATCHDOG_SECONDS,
            check=False,
        )
        receipt = json.loads(process.stdout) if process.stdout else None
        status = None
        if receipt is not None and Path(str(receipt["status_ref"])).is_file():
            status = json.loads(Path(str(receipt["status_ref"])).read_text(encoding="utf-8"))
        return OracleRun(process, receipt, status)

    def project_paths(self, flight_id: str, attempt: int) -> bytes:
        return subprocess.run(
            ["bash", str(CODEX_RUNNER), "--project-paths", "--flight-id", flight_id, "--attempt", str(attempt)],
            capture_output=True, check=True, env=self.environment(),
        ).stdout

    def assert_not_complete(self, run: OracleRun, reason: str) -> dict[str, object]:
        self.assertEqual(run.process.returncode, 1, run.stderr)
        assert run.status is not None, run.stderr
        self.assertEqual((run.status["status"], run.status["reason_code"]), ("NOT_COMPLETE", reason))
        self.assertEqual(COMPLETION_ONLY_KEYS & set(run.status), set())
        assert run.receipt is not None
        self.assertFalse(Path(str(run.receipt["log_refs"]["last_message"])).exists())  # type: ignore[index]
        return run.status

    def assert_complete(self, run: OracleRun) -> dict[str, object]:
        self.assertEqual(run.process.returncode, 0, run.stderr)
        assert run.status is not None, run.stderr
        self.assertEqual((run.status["status"], run.status["reason_code"]), ("COMPLETE", "COMPLETE"))
        return run.status

    def test_captured_broker_stream_completes_with_payload_evidence_and_model(self) -> None:
        raw = BROKER_STREAM.read_bytes()
        run = self.run_oracle(raw)
        status = self.assert_complete(run)
        assert run.receipt is not None
        log_refs = run.receipt["log_refs"]
        assert isinstance(log_refs, dict)
        self.assertEqual(Path(str(status["payload_ref"])).read_bytes(), CAPTURED_ANSWER.encode("utf-8"))
        self.assertEqual(status["payload_ref"], log_refs["last_message"])
        self.assertEqual(Path(str(status["raw_response_ref"])).read_bytes(), raw)
        self.assertNotEqual(status["payload_ref"], status["raw_response_ref"])
        self.assertEqual(status["completion_sentinel_ref"], f"broker-task:{CAPTURED_TASK}")
        self.assertEqual(
            status["model"],
            {"model_label": "chatgpt-6-pro", "observed_model_switcher": "gpt_6_pro", "observed_model_effort": "pro"},
        )
        self.assertEqual(status["carrier_exit"], 0)
        self.assertEqual(status["pool"], "pool-x")
        self.assertEqual((status["flight_id"], status["attempt"], status["run_dir"]), (run.receipt["flight_id"], 1, str(run.run_dir)))
        self.assertNotIn("verdict", status)
        for unused in ("result_ref", "completion_sentinel_ref", "carrier_exit_ref"):
            self.assertFalse(Path(str(run.receipt[unused])).exists(), unused)
        run_dir = os.path.realpath(run.run_dir)
        self.assertEqual(self.nyxid_calls(), [{"argv": POOL_ROUTE, "cwd": run_dir}, {"argv": CHAT_ROUTE, "cwd": run_dir}])

    def test_model_identity_is_copied_verbatim_and_null_when_absent(self) -> None:
        oracle = {"task_id": "t", "model_label": {"nested": ["kept", 1]}, "observed_model_effort": None}
        status = self.assert_complete(self.run_oracle(content_chunk("ok") + final_chunk(oracle=oracle) + DONE))
        self.assertEqual(
            status["model"],
            {"model_label": {"nested": ["kept", 1]}, "observed_model_switcher": None, "observed_model_effort": None},
        )
        self.assertEqual(status["completion_sentinel_ref"], "broker-task:t")

    def test_non_ascii_quote_heavy_brief_reaches_one_fresh_streaming_request_verbatim(self) -> None:
        brief = 'Say "hi" and \'bye\'\n\tthen 中文, a back\\slash, a "quoted \\"nest\\"", and \u2028 too\n'.encode("utf-8")
        for locale in ("C", "C.UTF-8"):
            with self.subTest(locale=locale):
                run = self.run_oracle(brief=brief, LC_ALL=locale)
                status = self.assert_complete(run)
                self.assertEqual(Path(str(status["brief_ref"])).read_bytes(), brief)
                request = json.loads(Path(str(status["request_ref"])).read_text(encoding="utf-8"))
                self.assertEqual(
                    request,
                    {"model": "oracle/pool-x", "stream": True, "messages": [{"role": "user", "content": brief.decode("utf-8")}]},
                )
                self.assertNotIn("metadata", request)

    def test_payload_is_byte_exact_across_chunks_crlf_and_line_separators(self) -> None:
        pieces = ['{"a":', ' "中 文\u2028x"', "}\n  "]
        stream = content_chunk(pieces[0]) + ": keep-alive\n\n" + content_chunk(pieces[1]) + content_chunk(pieces[2]) + final_chunk() + DONE
        for label, variant in (("LF", stream), ("CRLF", stream.replace("\n", "\r\n"))):
            with self.subTest(line_ending=label):
                status = self.assert_complete(self.run_oracle(variant))
                self.assertEqual(Path(str(status["payload_ref"])).read_bytes(), "".join(pieces).encode("utf-8"))
        crlf_fixture = BROKER_STREAM.read_bytes().replace(b"\n", b"\r\n")
        status = self.assert_complete(self.run_oracle(crlf_fixture))
        self.assertEqual(Path(str(status["payload_ref"])).read_bytes(), CAPTURED_ANSWER.encode("utf-8"))

    def test_every_completion_conjunct_is_necessary(self) -> None:
        self.assert_complete(self.run_oracle(COMPLETE_STREAM))
        for conjunct, (stream, chat_exit, reason) in SINGLE_VIOLATIONS.items():
            with self.subTest(conjunct=conjunct):
                status = self.assert_not_complete(self.run_oracle(stream, chat_exit=chat_exit), reason)
                self.assertEqual(status["carrier_exit"], chat_exit)
                self.assertTrue(Path(str(status["raw_response_ref"])).is_file())

    def test_missing_task_id_and_non_final_task_id_do_not_complete(self) -> None:
        for label, stream in {
            "absent oracle": content_chunk("ok") + final_chunk(oracle={}) + DONE,
            "null task id": content_chunk("ok") + final_chunk(oracle={"task_id": None}) + DONE,
            "earlier chunk only": data_line({"choices": [], "oracle": {"task_id": "early"}}) + content_chunk("ok") + final_chunk(oracle={}) + DONE,
        }.items():
            with self.subTest(label):
                self.assert_not_complete(self.run_oracle(stream), "TASK_ID_MISSING")

    def test_truncated_or_unfinished_streams_do_not_complete(self) -> None:
        for label, (stream, reason) in {
            "empty": ("", "STREAM_NOT_TERMINAL"),
            "keep-alive only": (": keep-alive\n\n", "STREAM_NOT_TERMINAL"),
            "cut inside the done line": (COMPLETE_STREAM[: COMPLETE_STREAM.index("[DONE]") + 3], "STREAM_MALFORMED"),
            "no finish reason": (content_chunk("ok") + final_chunk(None) + DONE, "STREAM_NOT_TERMINAL"),
        }.items():
            with self.subTest(label):
                self.assert_not_complete(self.run_oracle(stream), reason)

    def test_quoted_done_inside_reply_content_does_not_complete(self) -> None:
        self.assert_not_complete(self.run_oracle(QUOTED_DONE_STREAM), "STREAM_NOT_TERMINAL")

    def test_broker_error_chunk_and_http_error_do_not_complete(self) -> None:
        error_stream = content_chunk("partial") + data_line({"error": {"code": "model_unavailable"}}) + DONE
        self.assert_not_complete(self.run_oracle(error_stream), "BROKER_ERROR")
        status = self.assert_not_complete(
            self.run_oracle("", chat_exit=1, FAKE_CHAT_STDERR='{"error":"HTTP 429"}\n'), "CARRIER_EXIT_NONZERO"
        )
        self.assertEqual(Path(str(status["stderr_ref"])).read_text(), '{"error":"HTTP 429"}\n')

    def test_unavailable_broker_or_pool_never_dispatches(self) -> None:
        listings = {
            "no active pool": {"pools": [{"slug": "busy", "is_active": False, "online_workers": 9}]},
            "no online worker": {"pools": [{"slug": "idle", "is_active": True, "online_workers": 0}]},
            "no pools key": {"items": []},
            "not json": None,
        }
        for label, listing in listings.items():
            with self.subTest(label):
                self.pools.write_text("not json" if listing is None else json.dumps(listing))
                self.nyxid_log.unlink(missing_ok=True)
                status = self.assert_not_complete(self.run_oracle(), "BROKER_UNAVAILABLE")
                self.assertEqual([call["argv"] for call in self.nyxid_calls()], [POOL_ROUTE])
                self.assertEqual((status["pool"], status["carrier_exit"], status["raw_response_ref"], status["request_ref"]), (None, None, None, None))
        self.pools.write_text(json.dumps(DEFAULT_POOLS))
        self.nyxid_log.unlink(missing_ok=True)
        self.assert_not_complete(self.run_oracle(FAKE_POOLS_EXIT="1"), "BROKER_UNAVAILABLE")
        self.assertEqual([call["argv"] for call in self.nyxid_calls()], [POOL_ROUTE])
        (self.bin_dir / "nyxid").unlink()
        self.assert_not_complete(self.run_oracle(), "BROKER_UNAVAILABLE")

    def test_pool_choice_is_the_active_pool_with_most_online_workers(self) -> None:
        self.pools.write_text(json.dumps({"pools": [
            {"slug": "inactive-large", "is_active": False, "online_workers": 50},
            "not a pool",
            {"slug": "small", "is_active": True, "online_workers": 2},
            {"slug": "large", "is_active": True, "online_workers": 5},
            {"slug": "tie", "is_active": True, "online_workers": 5},
            {"slug": "boolean-workers", "is_active": True, "online_workers": True},
        ]}))
        status = self.assert_complete(self.run_oracle())
        self.assertEqual(status["pool"], "large")
        self.assertEqual(json.loads(Path(str(status["request_ref"])).read_text())["model"], "oracle/large")

    def test_invalid_utf8_brief_is_saved_and_never_dispatched(self) -> None:
        status = self.assert_not_complete(self.run_oracle(brief=b"caf\xe9\n"), "BRIEF_INVALID")
        self.assertEqual(Path(str(status["brief_ref"])).read_bytes(), b"caf\xe9\n")
        self.assertEqual(self.nyxid_calls(), [])

    def test_receipt_equals_layout_owner_projection_for_fresh_and_bound_identities(self) -> None:
        fresh = self.run_oracle()
        assert fresh.receipt is not None
        flight_id = str(fresh.receipt["flight_id"])
        self.assertRegex(flight_id, r"^[0-9a-f]{24}$")
        self.assertLessEqual(abs(int(flight_id[:8], 16) - time.time()), 60)
        self.assertEqual(fresh.process.stdout, self.project_paths(flight_id, 1))
        bound = self.run_oracle(arguments=("--flight-id", flight_id, "--attempt", "2"))
        self.assert_complete(bound)
        self.assertEqual(bound.process.stdout, self.project_paths(flight_id, 2))
        self.assertEqual(bound.process.stdout.count(b"\n"), 1)
        self.assertEqual(bound.run_dir.parent, fresh.run_dir.parent)
        self.assertNotEqual(bound.run_dir, fresh.run_dir)

    def test_receipt_precedes_stdin_read_and_broker_calls(self) -> None:
        self.stream.write_text(COMPLETE_STREAM)
        with self.launch() as process:
            assert process.stdout is not None
            readable, _, _ = select.select([process.stdout], [], [], WATCHDOG_SECONDS)
            self.assertTrue(readable, "receipt must precede stdin EOF")
            receipt = json.loads(process.stdout.readline())
            self.assertIsNone(process.poll())
            self.assertEqual(self.nyxid_calls(), [])
            self.assertFalse(Path(str(receipt["brief_ref"])).exists())
            tail, stderr = process.communicate(b"brief\n", timeout=WATCHDOG_SECONDS)
        self.assertEqual((tail, process.returncode), (b"", 0), stderr)

    def test_closed_receipt_pipe_creates_nothing_and_calls_nothing(self) -> None:
        reader, writer = os.pipe()
        os.close(reader)
        try:
            process = subprocess.run(
                [sys.executable, str(ORACLE_RUNNER)], stdin=subprocess.DEVNULL, stdout=writer,
                stderr=subprocess.PIPE, env=self.environment(), timeout=WATCHDOG_SECONDS, check=False,
            )
        finally:
            os.close(writer)
        self.assertEqual(process.returncode, 1, process.stderr)
        self.assertIn(b"INTERNAL_ERROR", process.stderr)
        self.assertFalse(self.sshx_home.exists())
        self.assertEqual(self.nyxid_calls(), [])

    def test_existing_attempt_directory_is_refused_before_any_broker_call(self) -> None:
        flight_id = self.next_flight_id()
        occupied = self.sshx_home / flight_id / "attempt-2"
        occupied.mkdir(parents=True)
        (occupied / "keep.txt").write_text("untouched")
        run = self.run_oracle(arguments=("--flight-id", flight_id, "--attempt", "2"))
        self.assertEqual(run.process.returncode, 1, run.stderr)
        self.assertIn("RUN_DIR_COLLISION", run.stderr)
        self.assertEqual(run.process.stdout, self.project_paths(flight_id, 2))
        self.assertEqual(sorted(path.name for path in occupied.iterdir()), ["keep.txt"])
        self.assertEqual(self.nyxid_calls(), [])
        first = self.run_oracle(arguments=("--flight-id", flight_id, "--attempt", "1"))
        self.assert_complete(first)
        assert first.receipt is not None
        status_ref = Path(str(first.receipt["status_ref"]))
        before = status_ref.read_bytes()
        calls = len(self.nyxid_calls())
        again = self.run_oracle(arguments=("--flight-id", flight_id, "--attempt", "1"))
        self.assertEqual(again.process.returncode, 1, again.stderr)
        self.assertIn("RUN_DIR_COLLISION", again.stderr)
        self.assertEqual(len(self.nyxid_calls()), calls)
        self.assertEqual(status_ref.read_bytes(), before)

    def test_usage_errors_exit_64_with_no_receipt_directory_or_call(self) -> None:
        valid = self.next_flight_id()
        for arguments in [
            ("--pool", "x"),
            ("positional",),
            ("--flight-id", valid),
            ("--attempt", "1"),
            ("--flight-id", valid, "--attempt"),
            ("--flight-id", valid, "--flight-id", valid, "--attempt", "1"),
            ("--flight-id", valid.upper(), "--attempt", "1"),
            ("--flight-id", valid[:-1], "--attempt", "1"),
            ("--flight-id", valid, "--attempt", "0"),
            ("--flight-id", valid, "--attempt", "one"),
        ]:
            with self.subTest(arguments=arguments):
                run = self.run_oracle(arguments=arguments)
                self.assertEqual(run.process.returncode, 64, run.stderr)
                self.assertIn("USAGE_ERROR", run.stderr)
                self.assertEqual(run.process.stdout, b"")
                self.assertFalse(self.sshx_home.exists())
                self.assertEqual(self.nyxid_calls(), [])

    def test_unusable_layout_projection_fails_before_any_receipt_or_call(self) -> None:
        run = self.run_oracle(SSHX_HOME="relative-root")
        self.assertEqual(run.process.returncode, 1, run.stderr)
        self.assertIn("PROJECTION_FAILED", run.stderr)
        self.assertIn("RUN_DIR_UNAVAILABLE", run.stderr)
        self.assertEqual(run.process.stdout, b"")
        self.assertEqual(self.nyxid_calls(), [])

    def launch(self, **extra: str) -> subprocess.Popen[bytes]:
        return subprocess.Popen(
            [sys.executable, str(ORACLE_RUNNER)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, env=self.environment(FAKE_CHAT_EXIT="0", **extra),
            preexec_fn=lambda: signal.signal(signal.SIGINT, signal.SIG_DFL),
        )

    def launch_until_broker_call(self, process: subprocess.Popen[bytes], ready_fd: int) -> dict[str, object]:
        """Read the receipt, send the brief, and return once the fake broker call is blocked."""
        assert process.stdout is not None and process.stdin is not None
        receipt = json.loads(process.stdout.readline())
        process.stdin.write(b"brief\n")
        process.stdin.close()
        readable, _, _ = select.select([ready_fd], [], [], WATCHDOG_SECONDS)
        self.assertTrue(readable, "watchdog expired waiting for the fake broker call")
        self.assertEqual(os.read(ready_fd, 64).strip(), b"ready")
        return receipt

    def gate_fifos(self) -> tuple[Path, int, int]:
        gate = self.temp_dir / f"chat-gate-{self.counter}"
        self.counter += 1
        os.mkfifo(f"{gate}.ready")
        os.mkfifo(f"{gate}.release")
        ready_fd = os.open(f"{gate}.ready", os.O_RDWR | os.O_NONBLOCK)
        release_fd = os.open(f"{gate}.release", os.O_RDWR | os.O_NONBLOCK)
        self.addCleanup(os.close, ready_fd)
        self.addCleanup(os.close, release_fd)
        return gate, ready_fd, release_fd

    def test_healthy_wait_boundaries_preserve_the_call_until_real_terminal_outcome(self) -> None:
        # No retry is requested; production zero-retry routing is checked in Lean.
        for stream, expected in ((COMPLETE_STREAM, "COMPLETE"),
                                 (content_chunk("unfinished"), "STREAM_NOT_TERMINAL")):
            with self.subTest(terminal_outcome=expected):
                gate, ready_fd, release_fd = self.gate_fifos()
                self.stream.write_text(stream)
                with self.launch(FAKE_CHAT_GATE=str(gate)) as process:
                    try:
                        receipt = self.launch_until_broker_call(process, ready_fd)
                        status_ref = Path(str(receipt["status_ref"]))
                        for _ in range(3):
                            with self.assertRaises(subprocess.TimeoutExpired):
                                process.wait(timeout=0.05)
                            self.assertFalse(status_ref.exists())
                        os.write(release_fd, b"release\n")
                        process.wait(timeout=WATCHDOG_SECONDS)
                    finally:
                        os.write(release_fd, b"release\n")
                        if process.poll() is None:
                            process.terminate()  # verification watchdog cleanup only
                        process.wait(timeout=WATCHDOG_SECONDS)
                self.assertEqual(process.returncode, 0 if expected == "COMPLETE" else 1)
                self.assertEqual(json.loads(status_ref.read_text())["reason_code"], expected)

    def test_status_appears_only_after_the_broker_call_exits(self) -> None:
        gate, ready_fd, release_fd = self.gate_fifos()
        self.stream.write_text(COMPLETE_STREAM)
        with self.launch(FAKE_CHAT_GATE=str(gate)) as process:
            receipt = self.launch_until_broker_call(process, ready_fd)
            status_ref = Path(str(receipt["status_ref"]))
            self.assertFalse(status_ref.exists())
            self.assertEqual([path.name for path in status_ref.parent.iterdir() if path.name.startswith(status_ref.name)], [])
            os.write(release_fd, b"release\n")
            process.wait(timeout=WATCHDOG_SECONDS)
        self.assertEqual(process.returncode, 0)
        self.assertEqual(json.loads(status_ref.read_text())["status"], "COMPLETE")

    def test_signalled_runner_publishes_no_status(self) -> None:
        self.stream.write_text(COMPLETE_STREAM)
        for signal_number in (signal.SIGTERM, signal.SIGINT):
            with self.subTest(signal=signal_number.name):
                gate, ready_fd, release_fd = self.gate_fifos()
                with self.launch(FAKE_CHAT_GATE=str(gate)) as process:
                    receipt = self.launch_until_broker_call(process, ready_fd)
                    process.send_signal(signal_number)
                    process.wait(timeout=WATCHDOG_SECONDS)
                self.assertEqual(process.returncode, -signal_number)
                os.write(release_fd, b"release\n")
                raw_response = Path(str(receipt["log_refs"]["stdout"]))  # type: ignore[index]
                deadline = time.monotonic() + WATCHDOG_SECONDS
                while raw_response.stat().st_size < len(COMPLETE_STREAM) and time.monotonic() < deadline:
                    time.sleep(0.05)
                self.assertEqual(raw_response.read_text(), COMPLETE_STREAM)
                self.assertFalse(Path(str(receipt["status_ref"])).exists())

    def mutated_runner(self, anchor: str, replacement: str) -> Path:
        source = ORACLE_RUNNER.read_text(encoding="utf-8")
        self.assertEqual(source.count(anchor), 1, f"mutation anchor must appear exactly once: {anchor}")
        directory = self.temp_dir / f"mutant-{self.counter}"
        self.counter += 1
        directory.mkdir()
        shutil.copy2(CODEX_RUNNER, directory / CODEX_RUNNER.name)
        mutated = directory / ORACLE_RUNNER.name
        mutated.write_text(source.replace(anchor, replacement), encoding="utf-8")
        return mutated

    def test_terminal_decision_mutants_are_detected(self) -> None:
        for conjunct, (anchor, replacement) in DECISION_MUTANTS.items():
            stream, chat_exit, reason = SINGLE_VIOLATIONS[conjunct]
            with self.subTest(conjunct=conjunct):
                mutant = self.run_oracle(stream, chat_exit=chat_exit, runner=self.mutated_runner(anchor, replacement))
                self.assertEqual(mutant.process.returncode, 0, f"mutant dropping {conjunct!r} must complete the {reason} case")
                assert mutant.status is not None
                self.assertEqual(mutant.status["status"], "COMPLETE")

    def test_stream_reading_mutants_are_detected(self) -> None:
        quoted_only = QUOTED_DONE_STREAM.replace(final_chunk(None), final_chunk())
        self.assert_not_complete(self.run_oracle(quoted_only), "STREAM_NOT_TERMINAL")
        substring_done = self.mutated_runner("if value == DONE_VALUE:", "if DONE_VALUE in line:")
        self.assertEqual(self.run_oracle(quoted_only, runner=substring_done).process.returncode, 0)
        separator_stream = content_chunk("a\u2028b") + final_chunk() + DONE
        self.assert_complete(self.run_oracle(separator_stream))
        unicode_lines = self.mutated_runner('text.split("\\n")', "text.splitlines()")
        self.assertEqual(self.run_oracle(separator_stream, runner=unicode_lines).process.returncode, 1)

    def run_codex(self, flight_id: str) -> None:
        process = subprocess.run(
            ["bash", str(CODEX_RUNNER), "--flight-id", flight_id, "--attempt", "1", "--stage", "implementation", "--work-target", str(ROOT)],
            input="MARKER=codex\n", capture_output=True, text=True, env=self.environment(), timeout=WATCHDOG_SECONDS,
            check=False,
        )
        self.assertEqual(process.returncode, 0, process.stderr)

    def clean(self, manifest: Path, *options: str) -> dict[str, object]:
        process = subprocess.run(
            ["bash", str(CLEANUP), "--manifest", str(manifest), *options],
            capture_output=True, text=True, env=self.environment(), timeout=WATCHDOG_SECONDS, check=False,
        )
        report = json.loads(process.stdout)
        report["exit"] = process.returncode
        return report

    def manifest(self, rows: list[tuple[str, int]]) -> Path:
        path = self.temp_dir / f"manifest-{self.counter}.json"
        self.counter += 1
        workers = [
            {"flight_id": flight_id, "attempt": attempt, "stage": "review", "work_target": str(ROOT), "brief_ref": str(self.temp_dir / "unused.brief")}
            for flight_id, attempt in rows
        ]
        path.write_text(json.dumps({"schema_version": 1, "workers": workers}))
        return path

    def test_clean_and_prune_share_the_run_root_with_codex_and_oracle_flights(self) -> None:
        codex_flight = self.next_flight_id()
        self.run_codex(codex_flight)
        oracle = self.run_oracle()
        self.assert_complete(oracle)
        oracle_flight = str(oracle.receipt["flight_id"])  # type: ignore[index]
        failed = self.run_oracle(arguments=("--flight-id", oracle_flight, "--attempt", "2"), chat_exit=1)
        self.assert_not_complete(failed, "CARRIER_EXIT_NONZERO")
        flight_dirs = {flight: self.sshx_home / flight for flight in (codex_flight, oracle_flight)}

        both = self.manifest([(codex_flight, 1), (oracle_flight, 1)])
        plan = self.clean(both)
        self.assertEqual((plan["exit"], plan["all_eligible"]), (0, True))
        self.assertEqual(set(plan["would_remove"]), {str(path) for path in flight_dirs.values()})  # type: ignore[arg-type]

        pending_flight = self.next_flight_id()
        (self.sshx_home / pending_flight / "attempt-1").mkdir(parents=True)
        refused = self.clean(self.manifest([(oracle_flight, 1), (pending_flight, 1)]), "--delete")
        self.assertEqual((refused["exit"], refused["all_eligible"], refused["removed"]), (1, False, []))
        self.assertTrue(flight_dirs[oracle_flight].is_dir())

        for path in self.sshx_home.iterdir():
            if path.name != codex_flight:
                for entry in [*path.rglob("*"), path]:
                    stamp = time.time() - 2 * DAY_SECONDS
                    os.utime(entry, (stamp, stamp), follow_symlinks=False)
        pruned = subprocess.run(
            ["bash", str(PRUNE)], capture_output=True, text=True, env=self.environment(), timeout=WATCHDOG_SECONDS,
            check=False,
        )
        self.assertEqual(pruned.returncode, 0, pruned.stderr)
        report = json.loads(pruned.stdout)
        self.assertEqual(
            {item["flight_id"]: item["state"] for item in report["flights"]},
            {codex_flight: "kept", oracle_flight: "removed", pending_flight: "removed"},
        )
        self.assertEqual(report["unrecognized"], [])
        self.assertFalse(flight_dirs[oracle_flight].exists())

        deleted = self.clean(self.manifest([(codex_flight, 1)]), "--delete")
        self.assertEqual((deleted["exit"], deleted["removed"]), (0, [str(flight_dirs[codex_flight])]))
        self.assertEqual(list(self.sshx_home.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
