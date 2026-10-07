"""Behavior tests for the inline broker mechanics documented in ``ORACLE_WORKER_SPEC.md``.

Each test executes the exact heredoc body the spec tells the caller to run, so a drift
between the documented command and its behavior fails here. The broker stream fixture is
a real response captured on 2026-10-07 with only ``chatgpt_url`` replaced.
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SPEC = ROOT / "skills" / "sshx" / "ORACLE_WORKER_SPEC.md"
BROKER_STREAM = Path(__file__).with_name("fixtures") / "oracle_broker_stream.sse"
REQUEST_MARKER = "# sshx:oracle-broker-request"
ASSEMBLE_MARKER = "# sshx:oracle-broker-assemble"
CAPTURED_ANSWER = (
    '{"conclusion":{"verdict":"approve","reason":"In ordinary integer arithmetic, adding 2 and 2 '
    'gives 4."},"log_ref":"inline"}'
)


def heredoc_body(marker: str) -> str:
    """The python heredoc in the spec whose first body line is ``marker``."""
    lines = SPEC.read_text(encoding="utf-8").splitlines()
    start = lines.index(marker)
    if not lines[start - 1].endswith("<<'PY'"):
        raise AssertionError(f"{marker} does not open a python heredoc")
    end = lines.index("PY", start)
    return "\n".join(lines[start:end]) + "\n"


def run_snippet(marker: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-I", "-", *args],
        input=heredoc_body(marker),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )


def data_line(payload: dict[str, object]) -> str:
    return "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"


def content_chunk(text: str) -> str:
    return data_line({"choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}]})


def final_chunk(finish_reason: str = "stop", task_id: str | None = "task-1") -> str:
    oracle = {"task_id": task_id, "pool": "pool-x", "observed_model_switcher": "gpt_6_pro"}
    return data_line({"choices": [{"index": 0, "delta": {}, "finish_reason": finish_reason}], "oracle": oracle})


class OracleBrokerRequestTests(unittest.TestCase):
    def build(self, brief: str) -> dict[str, object]:
        with tempfile.TemporaryDirectory() as tmp:
            brief_path = Path(tmp) / "brief.md"
            brief_path.write_text(brief, encoding="utf-8")
            completed = run_snippet(REQUEST_MARKER, str(brief_path), "pool-x")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return json.loads(completed.stdout)

    def test_request_carries_the_brief_verbatim_in_a_fresh_streaming_call(self) -> None:
        brief = 'Say "hi"\n\tthen 中文 and a back\\slash\n'
        request = self.build(brief)
        self.assertEqual(
            request,
            {"model": "oracle/pool-x", "stream": True, "messages": [{"role": "user", "content": brief}]},
        )
        self.assertNotIn("metadata", request)


class OracleBrokerAssemblyTests(unittest.TestCase):
    def assemble(self, stream: str) -> tuple[subprocess.CompletedProcess[str], dict[str, object] | None]:
        with tempfile.TemporaryDirectory() as tmp:
            stream_path = Path(tmp) / "stream.sse"
            meta_path = Path(tmp) / "meta.json"
            stream_path.write_text(stream, encoding="utf-8")
            completed = run_snippet(ASSEMBLE_MARKER, str(stream_path), str(meta_path))
            meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else None
        return completed, meta

    def assert_not_terminal(self, stream: str) -> str:
        completed, meta = self.assemble(stream)
        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, "")
        self.assertIsNone(meta)
        self.assertNotIn("Traceback", completed.stderr)
        return completed.stderr

    def test_captured_broker_stream_yields_answer_and_invocation_evidence(self) -> None:
        completed, meta = self.assemble(BROKER_STREAM.read_text(encoding="utf-8"))
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout, CAPTURED_ANSWER)
        self.assertEqual(
            meta,
            {
                "task_id": "1ad0c7c5-9622-4e3e-8fc5-26752e4c501c",
                "pool": "chrono-chatgpt-pro-pool",
                "model_label": "chatgpt-6-pro",
                "observed_model_switcher": "gpt_6_pro",
                "observed_model_effort": "pro",
            },
        )

    def test_content_split_across_chunks_and_keep_alives_is_joined_in_order(self) -> None:
        stream = content_chunk("{\"a\":") + ": keep-alive\n\n" + content_chunk("\"中 文\"}") + final_chunk()
        completed, meta = self.assemble(stream + "data: [DONE]\n\n")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout, "{\"a\":\"中 文\"}")
        self.assertEqual(meta and meta["task_id"], "task-1")

    def test_crlf_line_endings_are_accepted(self) -> None:
        stream = (content_chunk("ok") + final_chunk() + "data: [DONE]\n\n").replace("\n", "\r\n")
        completed, _ = self.assemble(stream)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout, "ok")

    def test_error_chunk_fails_closed(self) -> None:
        stream = content_chunk("partial") + data_line({"error": {"code": "usage_limit_reached"}}) + "data: [DONE]\n\n"
        self.assertIn("usage_limit_reached", self.assert_not_terminal(stream))

    def test_stream_without_done_marker_fails_closed(self) -> None:
        self.assertIn("done=False", self.assert_not_terminal(content_chunk("x") + final_chunk()))

    def test_unfinished_choice_fails_closed(self) -> None:
        stream = content_chunk("x") + final_chunk(finish_reason="length") + "data: [DONE]\n\n"
        self.assertIn("finish_reason=length", self.assert_not_terminal(stream))

    def test_missing_task_id_fails_closed(self) -> None:
        stream = content_chunk("x") + final_chunk(task_id=None) + "data: [DONE]\n\n"
        self.assertIn("task_id=None", self.assert_not_terminal(stream))

    def test_malformed_data_line_fails_closed_with_its_line_number(self) -> None:
        stream = content_chunk("x") + "data: {not json\n\n" + final_chunk() + "data: [DONE]\n\n"
        self.assertIn("line 3 is not JSON", self.assert_not_terminal(stream))


if __name__ == "__main__":
    unittest.main()
