"""Run one `nyxid-oracle` broker attempt for the sshx skill.

The interface, outputs, reason codes, and allowed calls are specified in
`skills/sshx/ORACLE_WORKER_SPEC.md`; the shared run layout belongs to
`run-codex-worker.sh`, whose read-only `--project-paths` query supplies every
attempt path and the identity-shape check used here.
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path


PROGRAM = "run-oracle-worker"
EXIT_COMPLETE = 0
EXIT_NOT_COMPLETE = 1
EXIT_USAGE = 64
LAYOUT_OWNER = Path(__file__).resolve().with_name("run-codex-worker.sh")
REQUEST_NAME = "request.json"
POOL_LISTING_ROUTE = ("proxy", "request", "oracle", "api/v1/oracle/pools", "--output", "json")
CHAT_COMPLETION_ROUTE = (
    "proxy",
    "request",
    "oracle",
    "api/v1/oracle/openai/v1/chat/completions",
    "--method",
    "POST",
    "--data",
    "@" + REQUEST_NAME,
)
DONE_VALUE = "[DONE]"
MODEL_IDENTITY_FIELDS = ("model_label", "observed_model_switcher", "observed_model_effort")
COMPLETION_EVIDENCE_PREFIX = "broker-task:"


class UsageError(Exception):
    """The invocation itself is wrong; exit 64."""


class Failure(Exception):
    """A diagnosable failure with its reason code. Before the attempt directory is owned it is
    reported on stderr only; afterwards it is also recorded in the terminal status."""

    def __init__(self, reason_code: str, detail: str) -> None:
        super().__init__(f"{reason_code}: {detail}")
        self.reason_code = reason_code


@dataclass(frozen=True)
class Identity:
    flight_id: str
    attempt: str


@dataclass(frozen=True)
class AttemptPaths:
    """The layout owner's projection for one identity; `receipt` is its exact output."""

    receipt: bytes
    flight_id: str
    attempt: int
    run_dir: Path
    brief: Path
    status: Path
    raw_response: Path
    stderr_log: Path
    payload: Path

    @property
    def request(self) -> Path:
        return self.run_dir / REQUEST_NAME


@dataclass(frozen=True)
class StreamChunk:
    """One parsed `data:` JSON chunk, reduced to the fields the terminal decision reads."""

    error: bool
    contents: tuple[str, ...]
    finish_reasons: tuple[str, ...]
    oracle: dict[str, object] | None


@dataclass(frozen=True)
class StreamObservation:
    """What the saved stream shows after the broker call has exited."""

    lines_parse: bool
    error_chunk: bool
    done_line: bool
    finish_reason: str | None
    final_oracle: dict[str, object] | None
    payload: str


@dataclass(frozen=True)
class TerminalDecision:
    reason_code: str
    task_id: str | None

    @property
    def complete(self) -> bool:
        return self.reason_code == "COMPLETE"


@dataclass
class AttemptRecord:
    """Facts accumulated by one owned attempt, projected once into the terminal status."""

    paths: AttemptPaths
    reason_code: str = "INTERNAL_ERROR"
    pool: str | None = None
    request_written: bool = False
    carrier_exit: int | None = None
    task_id: str | None = None
    model: dict[str, object] | None = None

    @property
    def complete(self) -> bool:
        return self.reason_code == "COMPLETE"

    def status_document(self) -> dict[str, object]:
        dispatched = self.carrier_exit is not None
        document: dict[str, object] = {
            "schema_version": 1,
            "flight_id": self.paths.flight_id,
            "attempt": self.paths.attempt,
            "status": "COMPLETE" if self.complete else "NOT_COMPLETE",
            "reason_code": self.reason_code,
            "run_dir": str(self.paths.run_dir),
            "brief_ref": str(self.paths.brief),
            "pool": self.pool,
            "request_ref": str(self.paths.request) if self.request_written else None,
            "carrier_exit": self.carrier_exit,
            "raw_response_ref": str(self.paths.raw_response) if dispatched else None,
            "stderr_ref": str(self.paths.stderr_log) if dispatched else None,
        }
        if self.complete:
            document["payload_ref"] = str(self.paths.payload)
            document["completion_sentinel_ref"] = COMPLETION_EVIDENCE_PREFIX + str(self.task_id)
            document["model"] = self.model
        return document


def parse_arguments(argv: list[str]) -> Identity | None:
    values: dict[str, str] = {}
    index = 0
    while index < len(argv):
        option = argv[index]
        if option not in ("--flight-id", "--attempt"):
            if option.startswith("-"):
                raise UsageError(f"unknown option {option}")
            raise UsageError(f"unexpected positional argument {option}")
        if option in values:
            raise UsageError(f"duplicate option {option}")
        if index + 1 >= len(argv):
            raise UsageError(f"missing value for {option}")
        values[option] = argv[index + 1]
        index += 2
    if not values:
        return None
    if len(values) != 2:
        raise UsageError("--flight-id and --attempt must be given together")
    return Identity(values["--flight-id"], values["--attempt"])


def mint_flight_id() -> str:
    """The codex runner's identity shape: big-endian UNIX seconds, then OS randomness."""
    return f"{int(time.time()):08x}{secrets.token_hex(8)}"


def project_paths(identity: Identity) -> AttemptPaths:
    completed = subprocess.run(
        ["bash", str(LAYOUT_OWNER), "--project-paths", "--flight-id", identity.flight_id, "--attempt", identity.attempt],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=False,
    )
    diagnostic = completed.stderr.decode("utf-8", "backslashreplace").strip()
    if completed.returncode == EXIT_USAGE:
        raise UsageError(diagnostic or "identity rejected by the path projection")
    if completed.returncode != 0:
        raise Failure("PROJECTION_FAILED", diagnostic or f"path projection exited {completed.returncode}")
    try:
        projection = json.loads(completed.stdout)
        log_refs = projection["log_refs"]
        return AttemptPaths(
            receipt=completed.stdout,
            flight_id=str(projection["flight_id"]),
            attempt=int(projection["attempt"]),
            run_dir=Path(projection["run_dir"]),
            brief=Path(projection["brief_ref"]),
            status=Path(projection["status_ref"]),
            raw_response=Path(log_refs["stdout"]),
            stderr_log=Path(log_refs["stderr"]),
            payload=Path(log_refs["last_message"]),
        )
    except (ValueError, KeyError, TypeError) as error:
        raise Failure("PROJECTION_FAILED", f"unreadable path projection: {error}") from error


def deliver_receipt(paths: AttemptPaths) -> None:
    try:
        sys.stdout.buffer.write(paths.receipt)
        sys.stdout.buffer.flush()
    except OSError as error:
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
        os.close(devnull)
        raise Failure("INTERNAL_ERROR", f"cannot deliver the receipt: {error}") from error


def claim_attempt_directory(paths: AttemptPaths) -> None:
    """Create the attempt directory atomically; an existing entry is never reused."""
    try:
        os.makedirs(paths.run_dir.parent, exist_ok=True)
    except OSError as error:
        raise Failure("RUN_DIR_UNAVAILABLE", str(error)) from error
    try:
        os.mkdir(paths.run_dir)
    except FileExistsError as error:
        raise Failure("RUN_DIR_COLLISION", f"{paths.run_dir} already exists") from error
    except OSError as error:
        raise Failure("RUN_DIR_UNAVAILABLE", str(error)) from error


def choose_pool(listing: object) -> str | None:
    """The active pool with the most online workers; ties keep listing order."""
    if not isinstance(listing, dict):
        return None
    pools = listing.get("pools")
    if not isinstance(pools, list):
        return None
    chosen: str | None = None
    most_workers = 0
    for pool in pools:
        if not isinstance(pool, dict):
            continue
        slug, active, workers = pool.get("slug"), pool.get("is_active"), pool.get("online_workers")
        if not (isinstance(slug, str) and slug and active is True):
            continue
        if isinstance(workers, int) and not isinstance(workers, bool) and workers > most_workers:
            chosen, most_workers = slug, workers
    return chosen


def build_request(pool: str, brief: str) -> bytes:
    body = {"model": f"oracle/{pool}", "stream": True, "messages": [{"role": "user", "content": brief}]}
    return json.dumps(body, ensure_ascii=False).encode("utf-8")


def parse_chunk(value: str) -> StreamChunk | None:
    """One `data:` JSON value, or None when it does not parse into the expected shape."""
    try:
        chunk = json.loads(value)
    except ValueError:
        return None
    if not isinstance(chunk, dict):
        return None
    choices = chunk.get("choices", [])
    oracle = chunk.get("oracle")
    if not isinstance(choices, list) or not (oracle is None or isinstance(oracle, dict)):
        return None
    contents: list[str] = []
    finish_reasons: list[str] = []
    for choice in choices:
        if not isinstance(choice, dict):
            return None
        delta = choice.get("delta")
        if not (delta is None or isinstance(delta, dict)):
            return None
        content = (delta or {}).get("content")
        finish_reason = choice.get("finish_reason")
        if not (content is None or isinstance(content, str)):
            return None
        if not (finish_reason is None or isinstance(finish_reason, str)):
            return None
        if content:
            contents.append(content)
        if finish_reason is not None:
            finish_reasons.append(finish_reason)
    return StreamChunk("error" in chunk, tuple(contents), tuple(finish_reasons), oracle)


def observe_stream(raw: bytes) -> StreamObservation:
    """Read a saved server-sent-event stream; LF and CRLF line endings are both accepted."""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return StreamObservation(False, False, False, None, None, "")
    lines_parse, error_chunk, done_line = True, False, False
    finish_reason: str | None = None
    final_oracle: dict[str, object] | None = None
    parts: list[str] = []
    # Split on LF only: JSON strings may carry U+2028 and similar separators raw.
    for line in text.split("\n"):
        line = line.removesuffix("\r")
        if not line.startswith("data:"):
            continue
        value = line[len("data:"):].removeprefix(" ")
        if value == DONE_VALUE:
            done_line = True
            continue
        chunk = parse_chunk(value)
        if chunk is None:
            lines_parse = False
            continue
        error_chunk = error_chunk or chunk.error
        parts.extend(chunk.contents)
        if chunk.finish_reasons:
            finish_reason = chunk.finish_reasons[-1]
        final_oracle = chunk.oracle
    return StreamObservation(lines_parse, error_chunk, done_line, finish_reason, final_oracle, "".join(parts))


def broker_task_id(observation: StreamObservation) -> str | None:
    task_id = (observation.final_oracle or {}).get("task_id")
    return task_id if isinstance(task_id, str) and task_id else None


def decide(carrier_exit: int, observation: StreamObservation) -> TerminalDecision:
    """Completion is one positive conjunction; every other shape is not complete."""
    task_id = broker_task_id(observation)
    if carrier_exit != 0:
        return TerminalDecision("CARRIER_EXIT_NONZERO", None)
    if not observation.lines_parse:
        return TerminalDecision("STREAM_MALFORMED", None)
    if observation.error_chunk:
        return TerminalDecision("BROKER_ERROR", None)
    if not observation.done_line:
        return TerminalDecision("STREAM_NOT_TERMINAL", None)
    if observation.finish_reason != "stop":
        return TerminalDecision("STREAM_NOT_TERMINAL", None)
    if task_id is None:
        return TerminalDecision("TASK_ID_MISSING", None)
    return TerminalDecision("COMPLETE", task_id)


def model_identity(observation: StreamObservation) -> dict[str, object]:
    oracle = observation.final_oracle or {}
    return {field: oracle.get(field) for field in MODEL_IDENTITY_FIELDS}


def read_brief(paths: AttemptPaths) -> str:
    raw = sys.stdin.buffer.read()
    paths.brief.write_bytes(raw)
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise Failure("BRIEF_INVALID", f"the brief is not UTF-8: {error}") from error


def capability_check(nyxid: str, run_dir: Path) -> str:
    """The non-mutating pool listing; returns the chosen pool slug."""
    try:
        completed = subprocess.run(
            [nyxid, *POOL_LISTING_ROUTE], cwd=run_dir, stdin=subprocess.DEVNULL, capture_output=True, check=False
        )
    except OSError as error:
        raise Failure("BROKER_UNAVAILABLE", f"cannot run nyxid: {error}") from error
    if completed.returncode != 0:
        diagnostic = completed.stderr.decode("utf-8", "backslashreplace").strip()
        raise Failure("BROKER_UNAVAILABLE", f"pool listing exited {completed.returncode}: {diagnostic}")
    try:
        listing = json.loads(completed.stdout)
    except ValueError as error:
        raise Failure("BROKER_UNAVAILABLE", f"pool listing is not JSON: {error}") from error
    pool = choose_pool(listing)
    if pool is None:
        raise Failure("BROKER_UNAVAILABLE", "no active pool has an online worker")
    return pool


def dispatch(nyxid: str, paths: AttemptPaths) -> int:
    """One streamed chat completion in the foreground; returns the nyxid exit status."""
    with paths.raw_response.open("xb") as stdout, paths.stderr_log.open("xb") as stderr:
        try:
            completed = subprocess.run(
                [nyxid, *CHAT_COMPLETION_ROUTE],
                cwd=paths.run_dir,
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                check=False,
            )
        except OSError as error:
            raise Failure("BROKER_UNAVAILABLE", f"cannot run nyxid: {error}") from error
    return completed.returncode


def run_owned_attempt(record: AttemptRecord) -> None:
    """Every step after the attempt directory is owned; failures land in `record`."""
    paths = record.paths
    try:
        brief = read_brief(paths)
        nyxid = shutil.which("nyxid")
        if nyxid is None:
            raise Failure("BROKER_UNAVAILABLE", "nyxid is not on PATH")
        record.pool = capability_check(nyxid, paths.run_dir)
        with paths.request.open("xb") as request:
            request.write(build_request(record.pool, brief))
        record.request_written = True
        log(f"broker call starting: pool {record.pool}")
        record.carrier_exit = dispatch(nyxid, paths)
        log(f"broker call exited rc={record.carrier_exit}")
        observation = observe_stream(paths.raw_response.read_bytes())
        decision = decide(record.carrier_exit, observation)
        if decision.complete:
            with paths.payload.open("xb") as payload:
                payload.write(observation.payload.encode("utf-8"))
            record.task_id = decision.task_id
            record.model = model_identity(observation)
        record.reason_code = decision.reason_code
    except Failure as failure:
        record.reason_code = failure.reason_code
        log(str(failure))
    except OSError as error:
        record.reason_code = "INTERNAL_ERROR"
        log(f"INTERNAL_ERROR: {error}")


def publish_status(record: AttemptRecord) -> None:
    """Write the terminal status atomically; it appears only after the broker call has exited."""
    status = record.paths.status
    temporary = status.with_name(status.name + ".tmp")
    text = json.dumps(record.status_document(), indent=2, ensure_ascii=False) + "\n"
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(temporary, status)
    except OSError:
        temporary.unlink(missing_ok=True)
        raise


def log(message: str) -> None:
    """Human diagnostics on stderr; a failed diagnostic write never changes the exit decision."""
    try:
        print(f"{PROGRAM}: {message}", file=sys.stderr, flush=True)
    except OSError:
        return


def main(argv: list[str]) -> int:
    # A signal ends the runner without a status, as SIGTERM does; an inherited ignore stays.
    if signal.getsignal(signal.SIGINT) is signal.default_int_handler:
        signal.signal(signal.SIGINT, signal.SIG_DFL)
    os.umask(0o077)
    try:
        identity = parse_arguments(argv)
        fresh = identity is None
        if identity is None:
            identity = Identity(mint_flight_id(), "1")
        try:
            paths = project_paths(identity)
        except UsageError as error:
            if fresh:
                raise Failure("INTERNAL_ERROR", f"minted identity rejected: {error}") from error
            raise
        deliver_receipt(paths)
        claim_attempt_directory(paths)
    except UsageError as error:
        log(f"USAGE_ERROR: {error}")
        return EXIT_USAGE
    except Failure as failure:
        log(str(failure))
        return EXIT_NOT_COMPLETE
    record = AttemptRecord(paths)
    run_owned_attempt(record)
    try:
        publish_status(record)
    except OSError as error:
        log(f"INTERNAL_ERROR: cannot publish the terminal status: {error}")
        return EXIT_NOT_COMPLETE
    log(f"{record.status_document()['status']} {record.reason_code}")
    return EXIT_COMPLETE if record.complete else EXIT_NOT_COMPLETE


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
