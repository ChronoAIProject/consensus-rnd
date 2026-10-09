"""One heca-supervised sshx attempt; mechanics belong to HECA_WORKER_SPEC.md.

All identity checks and paths are consumed from the Codex runner's pure projection.
The host supplies the binding; this runner never reads orchestration preferences.
"""
from __future__ import annotations

import argparse
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

LAYOUT_OWNER = Path(__file__).resolve().with_name("run-codex-worker.sh")
# Supported bindings require real composed-route evidence in the mechanical spec.
UNATTENDED_BINDINGS = frozenset({("codex", "full-access"), ("claude", "bypassPermissions")})
# CLI u32 maximum, overriding the daemon's short default; host deadlines remain external.
HECA_MAX_WAIT_MS = (1 << 32) - 1
STAGE_VERDICTS = {
    "thinking": ("propose", "revise", "reject", "abstain"),
    "implementation": (),
    "review": ("approve", "comment", "reject"),
    "termination": ("satisfied", "unsatisfied", "abstain"),
}


class Failure(Exception):
    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason


class Interrupted(Exception):
    """A caught host interruption; cleanup still owns only this attempt's agent."""


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise Failure("USAGE_ERROR", message)


@dataclass(frozen=True)
class Options:
    provider: str
    mode: str
    thinking: str | None
    check: bool
    stage: str | None
    target: Path | None
    flight_id: str | None
    attempt: str | None


@dataclass(frozen=True)
class Paths:
    receipt: bytes
    flight_id: str
    attempt: int
    directory: Path
    brief: Path
    result: Path
    sentinel: Path
    status: Path
    identity: Path
    stdout: Path
    stderr: Path


@dataclass(frozen=True)
class Placement:
    worktree_id: str
    target: Path


@dataclass
class Signals:
    deferred: bool = False
    received: int | None = None

    def handle(self, number: int, _frame: object) -> None:
        self.received = number
        if not self.deferred:
            raise Interrupted(f"signal {number}")

    def checkpoint(self) -> None:
        if self.received is not None:
            raise Interrupted(f"signal {self.received}")


@dataclass
class Attempt:
    paths: Paths
    options: Options
    agent_id: str | None = None
    spawn_started: bool = False
    not_running: bool = False
    reason: str = "INTERNAL_ERROR"
    terminal_observation: object = None

    def document(self) -> dict[str, object]:
        document: dict[str, object] = {
            "schema_version": 1, "flight_id": self.paths.flight_id, "attempt": self.paths.attempt,
            "stage": self.options.stage, "status": "COMPLETE" if self.reason == "COMPLETE" else "NOT_COMPLETE",
            "reason_code": self.reason, "run_dir": str(self.paths.directory),
            "brief_ref": str(self.paths.brief), "work_target": str(self.options.target),
            "provider": self.options.provider, "mode": self.options.mode, "thinking": self.options.thinking,
            "agent_id": self.agent_id, "agent_identity_ref": str(self.paths.identity) if self.agent_id else None,
            "agent_not_running": self.not_running, "terminal_observation": self.terminal_observation,
            "log_refs": {"stdout": str(self.paths.stdout), "stderr": str(self.paths.stderr)},
        }
        if self.reason == "COMPLETE":
            document.update(result_ref=str(self.paths.result), completion_sentinel_ref=str(self.paths.sentinel))
        return document


def parse_arguments(argv: list[str]) -> Options:
    parser = Parser(add_help=False)
    parser.add_argument("--check", action="store_true")
    for name in ("provider", "mode", "thinking", "stage", "work-target", "flight-id", "attempt"):
        parser.add_argument("--" + name)
    seen = [arg for arg in argv if arg.startswith("--")]
    if len(seen) != len({arg.split("=", 1)[0] for arg in seen}):
        raise Failure("USAGE_ERROR", "duplicate option")
    args = parser.parse_args(argv)
    if not args.provider or not args.mode:
        raise Failure("USAGE_ERROR", "explicit --provider and --mode are required")
    if (args.provider, args.mode) not in UNATTENDED_BINDINGS:
        raise Failure("USAGE_ERROR", "provider/mode is not a verified unattended binding")
    if args.thinking not in (None, "low"):
        raise Failure("USAGE_ERROR", "only the explicit thinking input low is supported")
    if args.check:
        if any((args.stage, args.work_target, args.flight_id, args.attempt)):
            raise Failure("USAGE_ERROR", "--check cannot be combined with attempt options")
    else:
        if args.stage not in STAGE_VERDICTS or not args.work_target or not Path(args.work_target).is_absolute():
            raise Failure("USAGE_ERROR", "valid --stage and absolute --work-target are required")
        if (args.flight_id is None) != (args.attempt is None):
            raise Failure("USAGE_ERROR", "identity options must appear together")
    return Options(args.provider, args.mode, args.thinking, args.check, args.stage,
                   Path(args.work_target).resolve() if args.work_target else None, args.flight_id, args.attempt)


def project_paths(options: Options) -> Paths:
    identity = options.flight_id or f"{int(time.time()):08x}{secrets.token_hex(8)}"
    response = subprocess.run(["bash", str(LAYOUT_OWNER), "--project-paths", "--flight-id", identity,
                               "--attempt", options.attempt or "1"], stdin=subprocess.DEVNULL,
                              capture_output=True, check=False)
    if response.returncode:
        raise Failure("USAGE_ERROR" if response.returncode == 64 else "PROJECTION_FAILED",
                      response.stderr.decode("utf-8", "backslashreplace"))
    try:
        value = json.loads(response.stdout)
        refs = value["log_refs"]
        return Paths(response.stdout, value["flight_id"], value["attempt"], Path(value["run_dir"]),
                     Path(value["brief_ref"]), Path(value["result_ref"]), Path(value["completion_sentinel_ref"]),
                     Path(value["status_ref"]), Path(refs["last_message"]), Path(refs["stdout"]), Path(refs["stderr"]))
    except (ValueError, KeyError, TypeError) as error:
        raise Failure("PROJECTION_FAILED", str(error)) from error


def atomic_json(path: Path, document: object) -> None:
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("x", encoding="utf-8") as handle:
        json.dump(document, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")
    os.replace(temporary, path)


def heca_path() -> str:
    executable = shutil.which("heca")
    if executable is None:
        raise Failure("HECA_UNAVAILABLE", "heca is not on PATH")
    return executable


def command(executable: str, arguments: list[str], paths: Paths | None = None) -> tuple[int, object]:
    """A closed call selected by a caller below; never arbitrary CLI forwarding."""
    child = subprocess.Popen([executable, "--json", *arguments], stdin=subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        stdout, stderr = child.communicate()
    except Interrupted:
        child.terminate()  # Only the CLI client, never treated as agent teardown.
        child.communicate()
        raise
    if paths is not None:
        with paths.stdout.open("ab") as handle:
            handle.write(stdout + b"\n")
        with paths.stderr.open("ab") as handle:
            handle.write(stderr + b"\n")
    try:
        document = json.loads(stdout)
    except ValueError:
        document = None
    return child.returncode, document


def capability_check(executable: str, options: Options) -> bool:
    rc, daemon = command(executable, ["daemon", "status"])
    if rc or not isinstance(daemon, dict) or not isinstance(daemon.get("server_version"), str):
        return False
    rc, providers = command(executable, ["providers", "list"])
    if rc or not isinstance(providers, list):
        return False
    return any(isinstance(p, dict) and p.get("id") == options.provider and p.get("enabled") is True
               and isinstance(p.get("status"), str) and p["status"] not in ("", "unavailable")
               and isinstance(p.get("modes"), list) and options.mode in p["modes"] for p in providers)


def wait_for_state(executable: str, agent_id: str, state: str, paths: Paths) -> tuple[int, object]:
    """One blocking observation at the CLI maximum, never a re-armed wait or runner deadline."""
    return command(executable, ["agent", "wait", "--state", state,
                                "--timeout-ms", str(HECA_MAX_WAIT_MS), agent_id], paths)


def find_placement(executable: str, target: Path, paths: Paths) -> Placement:
    rc, worktrees = command(executable, ["worktree", "list"], paths)
    if rc == 0 and isinstance(worktrees, list):
        for item in worktrees:
            if (isinstance(item, dict) and isinstance(item.get("path"), str)
                    and isinstance(item.get("id"), str) and item["id"] and item.get("archived_at") is None
                    and Path(item["path"]).resolve() == target):
                return Placement(item["id"], target)
    raise Failure("PLACEMENT_UNAVAILABLE", "no existing heca worktree matches work_target")


def matching_agent(value: object, agent_id: str, placement: Placement, options: Options) -> bool:
    return (isinstance(value, dict) and value.get("id") == agent_id
            and value.get("worktree_id") == placement.worktree_id
            and isinstance(value.get("cwd"), str) and Path(value["cwd"]).resolve() == placement.target
            and value.get("provider") == options.provider and value.get("mode") == options.mode)


def terminal_wait(value: object, agent_id: str, placement: Placement, options: Options, state: str) -> bool:
    """Positive structured wait evidence, never a process exit or a status snapshot alone."""
    return (isinstance(value, dict) and value.get("timed_out") is False
            and matching_agent(value.get("agent"), agent_id, placement, options)
            and value["agent"].get("state") == state)


def worker_brief(raw: str, paths: Paths, stage: str) -> str:
    allowed = STAGE_VERDICTS[stage]
    return raw + f'''\n\n## Runner-owned artifact contract
The worker itself publishes the artifacts; the runner never writes or repairs them.
Result envelope: {paths.result}
Completion sentinel: {paths.sentinel}
Stage: {stage}; allowed verdict: {", ".join(allowed) if allowed else "no-verdict-required"}
Write strict JSON with exactly the keys "conclusion" (a structured object containing the complete
task result) and "log_ref" (a nonempty opaque diagnostic reference; {paths.stdout} is available).
For stages other than implementation, conclusion.verdict must be a string in the allowed set.
Publish {paths.result.name}.tmp then atomically rename to {paths.result.name}; only afterwards
publish {paths.sentinel.name}.tmp and atomically rename to {paths.sentinel.name}.
'''



def strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def reject_constant(value: str) -> object:
    raise ValueError(f"non-JSON constant: {value}")


def validate_artifacts(paths: Paths, stage: str) -> None:
    if not paths.result.is_file() or paths.result.is_symlink():
        raise Failure("RESULT_MISSING", "worker result is missing or a symlink")
    try:
        result = json.loads(paths.result.read_text(encoding="utf-8"), object_pairs_hook=strict_object,
                            parse_constant=reject_constant)
    except (ValueError, UnicodeError) as error:
        raise Failure("ENVELOPE_INVALID", str(error)) from error
    if (not isinstance(result, dict) or set(result) != {"conclusion", "log_ref"}
            or not isinstance(result["conclusion"], dict)
            or not isinstance(result["log_ref"], str) or not result["log_ref"]):
        raise Failure("ENVELOPE_INVALID", "invalid envelope shape")
    allowed = STAGE_VERDICTS[stage]
    if allowed and result["conclusion"].get("verdict") not in allowed:
        raise Failure("VERDICT_INVALID", "stage verdict is missing or invalid")
    if not paths.sentinel.is_file() or paths.sentinel.is_symlink():
        raise Failure("SENTINEL_MISSING", "worker sentinel is missing or a symlink")


def spawn(executable: str, record: Attempt, placement: Placement, brief: str, signals: Signals) -> None:
    record.spawn_started = True
    # Defer caught signals through the spawn acknowledgment and identity persistence.
    # Killing a CLI client before recovering its daemon-issued identity loses stop authority.
    signals.deferred = True
    try:
        arguments = ["run", "--provider", record.options.provider, "--mode", record.options.mode]
        if record.options.thinking is not None:
            arguments += ["--thinking", record.options.thinking]
        arguments += ["--worktree-id", placement.worktree_id, "--prompt", brief]
        rc, reply = command(executable, arguments, record.paths)
        if isinstance(reply, dict) and isinstance(reply.get("agent_id"), str) and reply["agent_id"]:
            record.agent_id = reply["agent_id"]
            atomic_json(record.paths.identity, reply)
        if record.agent_id is None:
            raise Failure("SPAWN_UNCONFIRMED", "spawn did not yield an agent identity; flight remains protected")
        if rc or reply.get("loose") is not False or reply.get("worktree_id") != placement.worktree_id:
            raise Failure("PLACEMENT_INVALID", "spawn failed or returned a different/loose placement")
        # One read-only check of returned cwd before the blocking wait; never a completion source.
        rc, status = command(executable, ["agent", "status", record.agent_id], record.paths)
        if rc or not isinstance(status, dict) or not matching_agent(status.get("agent"), record.agent_id, placement, record.options):
            raise Failure("PLACEMENT_INVALID", "returned agent cwd/binding does not match work_target")
    finally:
        signals.deferred = False
    signals.checkpoint()


def stop_owned_agent(executable: str, record: Attempt) -> None:
    """Stop only the recovered daemon-issued identity, then obtain one structured wait."""
    if record.agent_id is None:
        record.not_running = not record.spawn_started
        return
    command(executable, ["agent", "kill", record.agent_id], record.paths)
    rc, observation = wait_for_state(executable, record.agent_id, "exited", record.paths)
    # Placement may itself be the failure, so stop confirmation binds identity and state only.
    record.not_running = (rc == 0 and isinstance(observation, dict) and observation.get("timed_out") is False
                          and isinstance(observation.get("agent"), dict)
                          and observation["agent"].get("id") == record.agent_id
                          and observation["agent"].get("state") == "exited")
    record.terminal_observation = observation


def run_attempt(record: Attempt, signals: Signals) -> None:
    executable = heca_path()
    try:
        raw = sys.stdin.buffer.read()
        record.paths.brief.write_bytes(raw)
        try:
            brief = raw.decode("utf-8")
        except UnicodeError as error:
            raise Failure("BRIEF_INVALID", str(error)) from error
        placement = find_placement(executable, record.options.target, record.paths)
        spawn(executable, record, placement, worker_brief(brief, record.paths, record.options.stage), signals)
        rc, observation = wait_for_state(executable, record.agent_id, "idle", record.paths)
        record.terminal_observation = observation
        if rc or not terminal_wait(observation, record.agent_id, placement, record.options, "idle"):
            raise Failure("WAIT_NOT_TERMINAL", "wait did not return positive matching terminal evidence")
        record.not_running = True
        validate_artifacts(record.paths, record.options.stage)
        record.reason = "COMPLETE"
    except Failure as error:
        record.reason = error.reason
        diagnostic(str(error))
    except Interrupted as error:
        record.reason = "INTERRUPTED"
        diagnostic(str(error))
    except OSError as error:
        record.reason = "INTERNAL_ERROR"
        diagnostic(str(error))
    finally:
        if record.reason != "COMPLETE":
            # Further caught signals do not interrupt the one cleanup attempt.
            signals.deferred = True
            try:
                stop_owned_agent(executable, record)
            except OSError as error:
                record.not_running = False
                diagnostic(f"STOP_UNCONFIRMED: {error}")
            finally:
                signals.deferred = False


def diagnostic(message: str) -> None:
    print(f"run-heca-worker: {message}", file=sys.stderr, flush=True)


def main(argv: list[str]) -> int:
    os.umask(0o077)
    try:
        options = parse_arguments(argv)
        if options.check:
            try:
                detected = capability_check(heca_path(), options)
            except (Failure, OSError) as error:
                diagnostic(str(error))
                detected = False
            print(json.dumps({"detected": detected, "provider": options.provider, "mode": options.mode}))
            return 0 if detected else 1
        paths = project_paths(options)
        sys.stdout.buffer.write(paths.receipt)
        sys.stdout.buffer.flush()
        paths.directory.parent.mkdir(parents=True, exist_ok=True)
        try:
            paths.directory.mkdir()
        except FileExistsError as error:
            raise Failure("RUN_DIR_COLLISION", str(error)) from error
    except Failure as error:
        diagnostic(str(error))
        return 64 if error.reason == "USAGE_ERROR" else 1
    except OSError as error:
        diagnostic(f"INTERNAL_ERROR: {error}")
        return 1
    record = Attempt(paths, options)
    signals = Signals()
    signal.signal(signal.SIGTERM, signals.handle)
    if signal.getsignal(signal.SIGINT) is not signal.SIG_IGN:
        signal.signal(signal.SIGINT, signals.handle)
    try:
        run_attempt(record, signals)
    except (Failure, OSError) as error:
        diagnostic(str(error))
        record.reason = error.reason if isinstance(error, Failure) else "INTERNAL_ERROR"
        record.not_running = not record.spawn_started
    if not record.not_running:
        diagnostic("STOP_UNCONFIRMED: no terminal status; keep flight and work_target protected")
        return 1
    # Never publish a terminal flag while the daemon may still be running this agent.
    signals.deferred = True
    try:
        atomic_json(paths.status, record.document())
    except OSError as error:
        diagnostic(f"INTERNAL_ERROR: status publication failed: {error}")
        return 1
    diagnostic(record.reason)
    return 0 if record.reason == "COMPLETE" else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
