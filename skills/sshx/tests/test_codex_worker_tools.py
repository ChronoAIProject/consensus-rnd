import json
import os
import select
import signal
import subprocess
import tempfile
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "skills" / "sshx" / "scripts"
RUNNER = SCRIPTS / "run-codex-worker.sh"
BATCH = SCRIPTS / "run-codex-worker-batch.sh"
STATUS_READER = SCRIPTS / "read-codex-worker-status.sh"
CLEANUP = SCRIPTS / "clean-codex-worker-runs.sh"
PRUNE = SCRIPTS / "prune-inactive-codex-worker-runs.sh"
VALID_ID = "0123456789abcdef01234567"
DAY_SECONDS = 24 * 3600
WATCHDOG_SECONDS = 60

FAKE_CODEX = r'''#!/bin/bash
set -u
last_message=
while [ "$#" -gt 0 ]; do
  case "$1" in
    -o) last_message=$2; shift 2 ;;
    -C|--sandbox) shift 2 ;;
    exec|--json|--skip-git-repo-check|-) shift ;;
    *) exit 97 ;;
  esac
done
brief=$(command cat)
run_dir=${last_message%/*}
result_ref="$run_dir/result.json"
sentinel_ref="$run_dir/completion.sentinel"
marker=$(printf '%s\n' "$brief" | sed -n '1s/^MARKER=//p')
printf '%s\n' "$brief" > "$run_dir/brief.seen"
printf '%s|%s\n' "$run_dir" "$marker" >> "$FAKE_LAUNCH_LOG"
case "$brief" in
  *BLOCK*)
    printf '%s\n' "$marker" > "$CARRIER_SYNC_DIR/$marker.ready"
    IFS= read -r _ < "$CARRIER_SYNC_DIR/$marker.release"
    ;;
esac
case "$brief" in
  *"Stage: implementation"*) printf '{"conclusion":{"marker":"%s"},"log_ref":"fake-log"}\n' "$marker" > "$result_ref.tmp" ;;
  *"Stage: thinking"*) printf '{"conclusion":{"verdict":"propose","marker":"%s"},"log_ref":"fake-log"}\n' "$marker" > "$result_ref.tmp" ;;
  *"Stage: review"*) printf '{"conclusion":{"verdict":"approve","marker":"%s"},"log_ref":"fake-log"}\n' "$marker" > "$result_ref.tmp" ;;
  *"Stage: termination"*) printf '{"conclusion":{"verdict":"abstain","marker":"%s"},"log_ref":"fake-log"}\n' "$marker" > "$result_ref.tmp" ;;
  *) exit 98 ;;
esac
mv "$result_ref.tmp" "$result_ref"
printf '%s\n' complete > "$sentinel_ref.tmp"
mv "$sentinel_ref.tmp" "$sentinel_ref"
case "$brief" in *FAIL*) exit 7 ;; esac
'''


class CodexWorkerToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_context = tempfile.TemporaryDirectory()
        self.temp_dir = Path(self.temp_context.name)
        self.bin_dir = self.temp_dir / "bin"
        self.bin_dir.mkdir()
        fake_codex = self.bin_dir / "codex"
        fake_codex.write_text(FAKE_CODEX)
        fake_codex.chmod(0o755)
        real_jq = subprocess.run(
            ["/bin/sh", "-c", "command -v jq"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        self.real_jq = Path(real_jq)
        (self.bin_dir / "jq").symlink_to(self.real_jq)
        self.launch_log = self.temp_dir / "launch.log"
        self.sshx_home = self.temp_dir / "sshx"
        self.counter = 0
        self.gate_fds: dict[Path, int] = {}

    def tearDown(self) -> None:
        for fd in self.gate_fds.values():
            os.close(fd)
        self.temp_context.cleanup()

    def environment(self, *, sshx_home: Path | None = None) -> dict[str, str]:
        env = os.environ.copy()
        env.update(
            {
                "PATH": f"{self.bin_dir}:/bin:/usr/bin",
                "SSHX_HOME": str(sshx_home or self.sshx_home),
                "FAKE_LAUNCH_LOG": str(self.launch_log),
                "CARRIER_SYNC_DIR": str(self.temp_dir),
            }
        )
        return env

    def next_flight_id(self) -> str:
        self.counter += 1
        return f"{int(time.time()):08x}{self.counter:016x}"

    def age_tree(self, root: Path, seconds: float) -> None:
        stamp = time.time() - seconds
        for path in sorted(root.rglob("*"), key=lambda entry: len(entry.parts), reverse=True):
            os.utime(path, (stamp, stamp), follow_symlinks=False)
        os.utime(root, (stamp, stamp), follow_symlinks=False)

    def project_root(self, *, env: dict[str, str] | None = None) -> dict[str, object]:
        process = subprocess.run(
            ["/bin/bash", str(RUNNER), "--project-root"],
            check=True,
            capture_output=True,
            text=True,
            env=env or self.environment(),
        )
        return json.loads(process.stdout)

    def prune(self, *arguments: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["/bin/bash", str(PRUNE), *arguments],
            capture_output=True,
            text=True,
            env=env or self.environment(),
            timeout=WATCHDOG_SECONDS,
        )

    def flight_states(self, report: dict[str, object]) -> dict[str, tuple[object, object, object]]:
        return {str(item["flight_id"]): (item["inactive"], item["state"], item["reason"]) for item in report["flights"]}  # type: ignore[index]

    def make_gate(self, name: str) -> Path:
        gate = self.temp_dir / name
        os.mkfifo(gate)
        self.gate_fds[gate] = os.open(gate, os.O_RDWR | os.O_NONBLOCK)
        return gate

    def await_gate(self, gate: Path, expected: str) -> None:
        readable, _, _ = select.select([self.gate_fds[gate]], [], [], WATCHDOG_SECONDS)
        self.assertTrue(readable, f"watchdog expired waiting for {gate.name}")
        observed = os.read(self.gate_fds[gate], 4096).decode().strip()
        self.assertEqual(observed, expected)

    def await_any_gate(self, gates: list[Path]) -> tuple[Path, str]:
        gate_by_fd = {self.gate_fds[gate]: gate for gate in gates}
        readable, _, _ = select.select(list(gate_by_fd), [], [], WATCHDOG_SECONDS)
        self.assertTrue(readable, "watchdog expired waiting for any gate")
        gate = gate_by_fd[readable[0]]
        observed = os.read(readable[0], 4096).decode().strip()
        return gate, observed

    def release_gate(self, gate: Path, token: str = "release") -> None:
        os.write(self.gate_fds[gate], f"{token}\n".encode())

    def make_carrier_gates(self, markers: list[str]) -> None:
        for marker in markers:
            self.make_gate(f"{marker}.ready")
            self.make_gate(f"{marker}.release")

    def await_carrier(self, marker: str) -> None:
        self.await_gate(self.temp_dir / f"{marker}.ready", marker)

    def release_carrier(self, marker: str) -> None:
        self.release_gate(self.temp_dir / f"{marker}.release")

    def run_dir(self, flight_id: str, attempt: int = 1) -> Path:
        return self.sshx_home / flight_id / f"attempt-{attempt}"

    def project(self, flight_id: str, attempt: int = 1, *, env: dict[str, str] | None = None) -> dict[str, object]:
        process = subprocess.run(
            ["/bin/bash", str(RUNNER), "--project-paths", "--flight-id", flight_id, "--attempt", str(attempt)],
            check=True,
            capture_output=True,
            text=True,
            env=env or self.environment(),
        )
        return json.loads(process.stdout)

    def project_flight(self, flight_id: str, *, env: dict[str, str] | None = None) -> dict[str, object]:
        process = subprocess.run(
            ["/bin/bash", str(RUNNER), "--project-flight", "--flight-id", flight_id],
            check=True,
            capture_output=True,
            text=True,
            env=env or self.environment(),
        )
        return json.loads(process.stdout)

    def run_worker(self, flight_id: str, attempt: int = 1, marker: str = "worker") -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                "/bin/bash",
                str(RUNNER),
                "--flight-id",
                flight_id,
                "--attempt",
                str(attempt),
                "--stage",
                "implementation",
                "--work-target",
                str(ROOT),
            ],
            input=f"MARKER={marker}\n",
            capture_output=True,
            text=True,
            env=self.environment(),
            timeout=WATCHDOG_SECONDS,
        )

    def worker(self, flight_id: str, attempt: int, brief_ref: Path, **overrides: object) -> dict[str, object]:
        item: dict[str, object] = {
            "flight_id": flight_id,
            "attempt": attempt,
            "stage": "implementation",
            "work_target": str(ROOT),
            "brief_ref": str(brief_ref),
        }
        item.update(overrides)
        return item

    def write_manifest(self, workers: list[dict[str, object]], name: str = "manifest.json") -> Path:
        path = self.temp_dir / name
        path.write_text(json.dumps({"schema_version": 1, "workers": workers}))
        return path

    def make_brief(self, marker: str, behavior: str = "") -> Path:
        path = self.temp_dir / f"{marker}.brief"
        path.write_text(f"MARKER={marker}\n{behavior}\n")
        return path

    def fresh_worker(self, brief_ref: Path, **overrides: object) -> dict[str, object]:
        return {"stage": "implementation", "work_target": str(ROOT), "brief_ref": str(brief_ref), **overrides}

    def dispatch(self, manifest: Path, report: Path, *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["/bin/bash", str(BATCH), "--manifest", str(manifest), "--report", str(report)], capture_output=True, text=True, env=env or self.environment(), timeout=WATCHDOG_SECONDS)

    def test_mixed_batch_resolves_failed_fresh_and_retry_rows_for_collection_and_cleanup(self) -> None:
        first = subprocess.run(["/bin/bash", str(RUNNER), "--stage", "implementation", "--work-target", str(ROOT)], input="MARKER=first\n", capture_output=True, text=True, env=self.environment(), check=True)
        identity = json.loads(first.stdout)["flight_id"]
        rows = [
            self.fresh_worker(self.make_brief("fresh-failed", "FAIL")),
            self.worker(identity, 2, self.make_brief("retry")),
            self.fresh_worker(self.make_brief("fresh-success"), sandbox="workspace-write"),
        ]
        manifest = self.write_manifest(rows)
        report = self.temp_dir / "resolved-report.json"
        process = self.dispatch(manifest, report)
        self.assertEqual(process.returncode, 1, process.stderr)
        document = json.loads(report.read_text())
        self.assertEqual([row["runner_exit_code"] for row in document["workers"]], [1, 0, 0])
        resolved = document["resolved_manifest"]
        ids = [row["flight_id"] for row in resolved["workers"]]
        self.assertEqual(len(set(ids)), 3)
        self.assertEqual(ids[1], identity)
        self.assertEqual([row["attempt"] for row in resolved["workers"]], [1, 2, 1])
        for original, assigned in zip(rows, resolved["workers"], strict=True):
            self.assertTrue(original.items() <= assigned.items())
        for capture in json.loads(process.stdout)["receipt_refs"]:
            self.assertFalse(Path(capture["receipt_ref"]).exists())
        resolved_ref = self.temp_dir / "resolved.json"
        resolved_ref.write_text(json.dumps(resolved))
        status = subprocess.run(["/bin/bash", str(STATUS_READER), "--manifest", str(resolved_ref)], capture_output=True, text=True, env=self.environment(), check=True)
        self.assertEqual([row["status_document"]["reason_code"] for row in json.loads(status.stdout)["workers"]], ["CARRIER_EXIT_NONZERO", "COMPLETE", "COMPLETE"])
        cleanup = subprocess.run(["/bin/bash", str(CLEANUP), "--manifest", str(resolved_ref), "--delete"], capture_output=True, text=True, env=self.environment(), check=True)
        self.assertEqual(set(json.loads(cleanup.stdout)["removed"]), {str(self.sshx_home / flight_id) for flight_id in ids})
        self.assertFalse(self.run_dir(identity, 1).exists())

    def test_fresh_batch_preflight_rejects_partial_pairs_and_checks_all_briefs_and_root(self) -> None:
        valid = self.fresh_worker(self.make_brief("fresh-preflight"))
        cases = [
            ({**valid, "flight_id": VALID_ID}, 64, self.environment()),
            ({**valid, "attempt": 1}, 64, self.environment()),
            ({**valid, "brief_ref": str(self.temp_dir / "missing")}, 64, self.environment()),
            (valid, 1, self.environment(sshx_home=Path("relative"))),
        ]
        for index, (row, code, env) in enumerate(cases):
            manifest = self.write_manifest([valid, row], f"preflight-{index}.json")
            report = self.temp_dir / f"preflight-{index}-report.json"
            process = self.dispatch(manifest, report, env=env)
            self.assertEqual(process.returncode, code, process.stderr)
            self.assertFalse(report.exists())
            self.assertFalse(self.launch_log.exists())
            self.assertFalse(self.sshx_home.exists())

    def test_batch_preallocation_failure_has_null_fields_and_no_fabricated_resolved_row(self) -> None:
        rejected_target = self.temp_dir / "rejected-target"
        wrapper = self.bin_dir / "bash"
        wrapper.write_text('#!/bin/bash\nif [ "${2:-}" = --stage ] && [ "${5:-}" = "$REJECT_TARGET" ]; then exit 17; fi\nexec /bin/bash "$@"\n')
        wrapper.chmod(0o755)
        env = {**self.environment(), "REJECT_TARGET": str(rejected_target)}
        rows = [self.fresh_worker(self.make_brief("unallocated"), work_target=str(rejected_target)), self.fresh_worker(self.make_brief("allocated"))]
        report = self.temp_dir / "partial-report.json"
        process = self.dispatch(self.write_manifest(rows), report, env=env)
        self.assertEqual(process.returncode, 1, process.stderr)
        document = json.loads(report.read_text())
        unallocated, allocated = document["workers"]
        self.assertEqual(unallocated["runner_exit_code"], 17)
        self.assertEqual(unallocated["receipt_error"], "RECEIPT_MISSING")
        for field in ["flight_id", "attempt", "run_dir", "status_ref"]:
            self.assertIsNone(unallocated[field])
        self.assertEqual(len(document["resolved_manifest"]["workers"]), 1)
        self.assertEqual(document["resolved_manifest"]["workers"][0]["flight_id"], allocated["flight_id"])
        self.assertIn("RECEIPT_MISSING: worker 0", process.stderr)

    def test_batch_report_publication_failure_preserves_joined_receipts(self) -> None:
        report = self.temp_dir / "unpublished-report.json"
        observer = self.temp_dir / "publication-failure.bash"
        observer.write_text('mv() {\n for target in "$@"; do :; done\n if [ "$0" = "$BATCH_SCRIPT" ] && [ "$target" = "$REPORT_TARGET" ]; then return 73; fi\n command mv "$@"\n}\n')
        env = {**self.environment(), "BASH_ENV": str(observer), "BATCH_SCRIPT": str(BATCH), "REPORT_TARGET": str(report)}
        rows = [self.fresh_worker(self.make_brief(f"retained-{index}")) for index in range(2)]
        process = self.dispatch(self.write_manifest(rows), report, env=env)
        self.assertEqual(process.returncode, 1, process.stderr)
        self.assertIn("cannot publish report", process.stderr)
        self.assertFalse(report.exists())
        refs = json.loads(process.stdout)["receipt_refs"]
        self.assertEqual([entry["worker_index"] for entry in refs], [0, 1])
        for ref in refs:
            capture = Path(ref["receipt_ref"])
            self.assertEqual(capture.stat().st_mode & 0o777, 0o600)
            receipt = json.loads(capture.read_text())
            self.assertEqual(receipt, self.project(receipt["flight_id"], receipt["attempt"]))
            self.assertEqual(json.loads(Path(receipt["status_ref"]).read_text())["reason_code"], "COMPLETE")

    def test_batch_recovery_receipt_delivery_failure_launches_nothing(self) -> None:
        rows = [self.fresh_worker(self.make_brief("recovery-failure"))]
        manifest = self.write_manifest(rows)
        report = self.temp_dir / "recovery-report.json"
        with manifest.open("rb") as output:
            process = subprocess.run(["/bin/bash", str(BATCH), "--manifest", str(manifest), "--report", str(report)], stdout=output, stderr=subprocess.PIPE, text=True, env=self.environment(), timeout=WATCHDOG_SECONDS)
        self.assertEqual(process.returncode, 1, process.stderr)
        self.assertIn("cannot publish receipt recovery references", process.stderr)
        self.assertFalse(self.launch_log.exists())
        self.assertFalse(report.exists())

    def test_batch_rejects_invalid_or_mismatched_receipts_after_join(self) -> None:
        wrapper = self.bin_dir / "bash"
        wrapper.write_text('#!/bin/bash\nif [ "${2:-}" = --stage ]; then\n receipt=$(/bin/bash "$@")\n if [ "$CORRUPT_RECEIPT" = invalid ]; then printf "%s\\nextra\\n" "$receipt"; else printf "%s\\n" "$receipt" | "$REAL_JQ" \'.run_dir = "/wrong"\'; fi\nelse exec /bin/bash "$@"; fi\n')
        wrapper.chmod(0o755)
        for mode, error in [("invalid", "RECEIPT_INVALID"), ("mismatch", "RECEIPT_PROJECTION_MISMATCH")]:
            rows = [self.fresh_worker(self.make_brief(mode))]
            report = self.temp_dir / f"{mode}-report.json"
            env = {**self.environment(), "REAL_JQ": str(self.real_jq), "CORRUPT_RECEIPT": mode}
            process = self.dispatch(self.write_manifest(rows, f"{mode}.json"), report, env=env)
            self.assertEqual(process.returncode, 1, process.stderr)
            document = json.loads(report.read_text())
            self.assertEqual(document["workers"][0]["receipt_error"], error)
            self.assertIsNone(document["workers"][0]["flight_id"])
            self.assertEqual(document["resolved_manifest"]["workers"], [])
            for ref in json.loads(process.stdout)["receipt_refs"]:
                self.assertTrue(Path(ref["receipt_ref"]).is_file(), "invalid receipts must remain diagnosable")

    def test_project_paths_is_pure_and_validates_query_shape(self) -> None:
        missing_tmp = self.temp_dir / "does-not-exist"
        env = self.environment(sshx_home=missing_tmp)
        projection = self.project(VALID_ID, 2, env=env)
        self.assertEqual(projection["attempt"], 2)
        self.assertFalse(missing_tmp.exists())
        self.assertEqual(
            set(projection),
            {
                "schema_version",
                "flight_id",
                "attempt",
                "run_dir",
                "brief_ref",
                "result_ref",
                "completion_sentinel_ref",
                "carrier_exit_ref",
                "status_ref",
                "log_refs",
            },
        )
        invalid_commands = [
            ["--project-paths", "--flight-id", VALID_ID, "--attempt", "1", "--stage", "thinking"],
            ["--project-paths", "--flight-id", VALID_ID, "--attempt", "1", "--work-target", str(ROOT)],
            ["--project-paths", "--flight-id", VALID_ID, "--attempt", "1", "--sandbox", "workspace-write"],
            ["--project-paths", "--flight-id", "bad/id", "--attempt", "1"],
            ["--project-paths", "--flight-id", VALID_ID, "--attempt", "0"],
            ["--project-paths", "--flight-id", VALID_ID],
        ]
        for arguments in invalid_commands:
            with self.subTest(arguments=arguments):
                process = subprocess.run(
                    ["/bin/bash", str(RUNNER), *arguments],
                    input="ignored",
                    capture_output=True,
                    text=True,
                    env=self.environment(),
                )
                self.assertEqual(process.returncode, 64, process.stderr)

    def test_projected_references_are_the_run_mode_references(self) -> None:
        flight_id = self.next_flight_id()
        projection = self.project(flight_id, 3)
        process = self.run_worker(flight_id, attempt=3)
        self.assertEqual(process.returncode, 0, process.stderr)
        status = json.loads(Path(str(projection["status_ref"])).read_text())
        for field in ["run_dir", "brief_ref", "result_ref", "completion_sentinel_ref"]:
            self.assertEqual(status[field], projection[field])
        self.assertEqual(status["log_refs"], projection["log_refs"])
        self.assertEqual(Path(str(projection["carrier_exit_ref"])), self.run_dir(flight_id, 3) / "carrier.exit")

    def test_run_mode_value_validation_precedes_environment_checks(self) -> None:
        base = [
            "/bin/bash",
            str(RUNNER),
            "--flight-id",
            VALID_ID,
            "--attempt",
            "1",
            "--stage",
            "thinking",
            "--work-target",
            str(ROOT),
            "--sandbox",
            "workspace-write",
        ]
        cases = []
        for option, invalid_value in [
            ("--flight-id", "bad/id"),
            ("--attempt", "0"),
            ("--stage", "other"),
            ("--work-target", "relative"),
            ("--work-target", "/tmp/line\nbreak"),
            ("--sandbox", "read-only"),
        ]:
            command = base.copy()
            command[command.index(option) + 1] = invalid_value
            cases.append(command)
        env = self.environment()
        env["SSHX_HOME"] = "relative"
        for command in cases:
            with self.subTest(command=command):
                process = subprocess.run(
                    command,
                    input="brief\n",
                    capture_output=True,
                    text=True,
                    env=env,
                )
                self.assertEqual(process.returncode, 64, process.stderr)
                self.assertIn("USAGE_ERROR", process.stderr)
                self.assertNotIn("RUN_DIR_UNAVAILABLE", process.stderr)

    def test_project_flight_is_pure_exclusive_and_enumerates_owned_attempt_paths(self) -> None:
        missing_tmp = self.temp_dir / "missing-flight-query-root"
        pure = self.project_flight(VALID_ID, env=self.environment(sshx_home=missing_tmp))
        self.assertEqual(
            set(pure),
            {"schema_version", "flight_id", "flight_dir", "sshx_root", "attempts"},
        )
        self.assertEqual(pure["attempts"], [])
        self.assertFalse(missing_tmp.exists())

        invalid_commands = [
            ["--project-flight", "--flight-id", VALID_ID, "--attempt", "1"],
            ["--project-flight", "--flight-id", VALID_ID, "--stage", "thinking"],
            ["--project-flight", "--flight-id", VALID_ID, "--work-target", str(ROOT)],
            ["--project-flight", "--flight-id", VALID_ID, "--sandbox", "workspace-write"],
            ["--project-flight", "--project-paths", "--flight-id", VALID_ID, "--attempt", "1"],
            ["--project-flight"],
        ]
        for arguments in invalid_commands:
            with self.subTest(arguments=arguments):
                process = subprocess.run(
                    ["/bin/bash", str(RUNNER), *arguments],
                    input="ignored",
                    capture_output=True,
                    text=True,
                    env=self.environment(),
                )
                self.assertEqual(process.returncode, 64, process.stderr)

        flight_id = self.next_flight_id()
        for attempt in [1, 3]:
            run = self.run_worker(flight_id, attempt=attempt)
            self.assertEqual(run.returncode, 0, run.stderr)
        projection = self.project_flight(flight_id)
        self.assertEqual([item["attempt"] for item in projection["attempts"]], [1, 3])
        for item in projection["attempts"]:
            attempt_projection = self.project(flight_id, int(item["attempt"]))
            self.assertEqual(item["run_dir"], attempt_projection["run_dir"])
            self.assertEqual(item["status_ref"], attempt_projection["status_ref"])

    def test_batch_overlaps_workers_waits_for_all_and_reports_mixed_exits(self) -> None:
        flights = [self.next_flight_id() for _ in range(3)]
        markers = ["failing", "held-a", "held-b"]
        self.make_carrier_gates(markers)
        briefs = [
            self.make_brief(markers[0], "BLOCK FAIL"),
            self.make_brief(markers[1], "BLOCK"),
            self.make_brief(markers[2], "BLOCK"),
        ]
        manifest = self.write_manifest([self.worker(flights[i], 1, briefs[i]) for i in range(3)])
        report = self.temp_dir / "batch-report.json"
        wait_log = self.temp_dir / "batch-waits.log"
        publication_log = self.temp_dir / "batch-publication.log"
        bash_env = self.temp_dir / "batch-observer.bash"
        bash_env.write_text(
            "wait() {\n"
            '  if [ "$0" != "$BATCH_SCRIPT" ]; then builtin wait "$@"; return $?; fi\n'
            '  builtin wait "$@"\n'
            "  wait_result=$?\n"
            '  printf "%s|%s\\n" "$1" "$wait_result" >> "$WAIT_LOG"\n'
            '  return "$wait_result"\n'
            "}\n"
            "mv() {\n"
            '  for argument in "$@"; do target=$argument; done\n'
            '  if [ "$0" = "$BATCH_SCRIPT" ] && [ "$target" = "$REPORT_TARGET" ]; then\n'
            '    completed=0; [ ! -f "$WAIT_LOG" ] || completed=$(wc -l < "$WAIT_LOG")\n'
            '    printf "%s\\n" "$completed" > "$PUBLICATION_LOG"\n'
            "  fi\n"
            '  command mv "$@"\n'
            "}\n"
        )
        env = self.environment()
        env.update(
            {
                "BASH_ENV": str(bash_env),
                "BATCH_SCRIPT": str(BATCH),
                "REPORT_TARGET": str(report),
                "WAIT_LOG": str(wait_log),
                "PUBLICATION_LOG": str(publication_log),
            }
        )
        process = subprocess.Popen(
            ["/bin/bash", str(BATCH), "--manifest", str(manifest), "--report", str(report)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
        for marker in markers:
            self.await_carrier(marker)
        self.assertIsNone(process.poll(), "dispatcher returned while slow siblings were active")
        self.assertTrue(report.is_file(), "dispatcher did not reserve the final report path")
        self.assertEqual(report.read_text(), "", "reserved report target became visible as a report")
        for flight_id, brief in zip(flights, briefs, strict=True):
            self.assertTrue((self.run_dir(flight_id) / "brief.seen").is_file())
            self.assertTrue((self.run_dir(flight_id) / "brief.seen").read_text().startswith(brief.read_text()))
        for flight_id in flights[1:]:
            self.assertFalse((self.run_dir(flight_id) / "carrier.exit").exists())
        for marker in markers:
            self.release_carrier(marker)
        _, stderr = process.communicate(timeout=WATCHDOG_SECONDS)
        self.assertEqual(process.returncode, 1, stderr)
        self.assertEqual(len(wait_log.read_text().splitlines()), len(flights))
        self.assertEqual(publication_log.read_text().strip(), str(len(flights)))
        document = json.loads(report.read_text())
        self.assertTrue(document["all_workers_waited"])
        self.assertFalse(document["interrupted"])
        self.assertEqual([item["runner_exit_code"] for item in document["workers"]], [1, 0, 0])
        self.assertEqual([item["flight_id"] for item in document["workers"]], flights)
        self.assertTrue(all(Path(item["status_ref"]).is_file() for item in document["workers"]))
        self.assertEqual(len(self.launch_log.read_text().splitlines()), 3, "dispatcher retried a worker")
        for item in document["workers"]:
            projected = self.project(item["flight_id"], item["attempt"])
            self.assertEqual(item["run_dir"], projected["run_dir"])
            self.assertEqual(item["status_ref"], projected["status_ref"])

    def test_batch_accepts_exact_runner_stage_domain(self) -> None:
        expected_verdicts = {
            "thinking": "propose",
            "implementation": None,
            "review": "approve",
            "termination": "abstain",
        }
        for stage, expected_verdict in expected_verdicts.items():
            with self.subTest(stage=stage):
                flight_id = self.next_flight_id()
                brief = self.make_brief(flight_id)
                manifest = self.write_manifest(
                    [self.worker(flight_id, 1, brief, stage=stage)],
                    f"batch-{stage}.json",
                )
                report = self.temp_dir / f"batch-{stage}-report.json"
                process = subprocess.run(
                    ["/bin/bash", str(BATCH), "--manifest", str(manifest), "--report", str(report)],
                    capture_output=True,
                    text=True,
                    env=self.environment(),
                )
                self.assertEqual(process.returncode, 0, process.stderr)
                worker_report = json.loads(report.read_text())["workers"][0]
                self.assertEqual(worker_report["runner_exit_code"], 0)
                status = json.loads(Path(worker_report["status_ref"]).read_text())
                self.assertEqual(status["stage"], stage)
                self.assertEqual(status["status"], "COMPLETE")
                if expected_verdict is None:
                    self.assertNotIn("verdict", status)
                else:
                    self.assertEqual(status["verdict"], expected_verdict)

        launch_count = len(self.launch_log.read_text().splitlines())
        invalid_brief = self.make_brief("invalid-stage")
        invalid_manifest = self.write_manifest(
            [self.worker(self.next_flight_id(), 1, invalid_brief, stage="other")],
            "batch-invalid-stage.json",
        )
        invalid_report = self.temp_dir / "batch-invalid-stage-report.json"
        invalid = subprocess.run(
            ["/bin/bash", str(BATCH), "--manifest", str(invalid_manifest), "--report", str(invalid_report)],
            capture_output=True,
            text=True,
            env=self.environment(),
        )
        self.assertEqual(invalid.returncode, 64, invalid.stderr)
        self.assertFalse(invalid_report.exists())
        self.assertEqual(len(self.launch_log.read_text().splitlines()), launch_count)

    def test_batch_signals_rewait_every_child_and_preserve_runner_exits(self) -> None:
        for dispatch_signal in [signal.SIGINT, signal.SIGTERM]:
            with self.subTest(signal=dispatch_signal):
                flights = [self.next_flight_id() for _ in range(2)]
                markers = [f"interrupted-{flight_id}" for flight_id in flights]
                self.make_carrier_gates(markers)
                briefs = [self.make_brief(marker, "BLOCK") for marker in markers]
                manifest = self.write_manifest(
                    [self.fresh_worker(briefs[i]) for i in range(2)],
                    f"interrupted-{dispatch_signal}.json",
                )
                report = self.temp_dir / f"interrupted-report-{dispatch_signal}.json"
                signal_ready = self.make_gate(f"signal-ready-{dispatch_signal}")
                signal_release = self.make_gate(f"signal-release-{dispatch_signal}")
                wait_log = self.temp_dir / f"interrupted-waits-{dispatch_signal}.log"
                bash_env = self.temp_dir / f"interrupt-wait-{dispatch_signal}.bash"
                bash_env.write_text(
                    "wait() {\n"
                    '  if [ "$0" != "$BATCH_SCRIPT" ]; then builtin wait "$@"; return $?; fi\n'
                    "  wait_call_count=$(( ${wait_call_count:-0} + 1 ))\n"
                    '  if [ "$wait_call_count" -eq 1 ]; then\n'
                    '    printf "ready\\n" > "$SIGNAL_READY"\n'
                    '    IFS= read -r _ < "$SIGNAL_RELEASE" || :\n'
                    '    printf "%s|%s\\n" "$1" "$INTERRUPT_STATUS" >> "$WAIT_LOG"\n'
                    '    return "$INTERRUPT_STATUS"\n'
                    "  fi\n"
                    '  builtin wait "$@"\n'
                    "  wait_result=$?\n"
                    '  printf "%s|%s\\n" "$1" "$wait_result" >> "$WAIT_LOG"\n'
                    '  return "$wait_result"\n'
                    "}\n"
                )
                env = self.environment()
                env.update(
                    {
                        "BASH_ENV": str(bash_env),
                        "BATCH_SCRIPT": str(BATCH),
                        "SIGNAL_READY": str(signal_ready),
                        "SIGNAL_RELEASE": str(signal_release),
                        "INTERRUPT_STATUS": str(128 + dispatch_signal),
                        "WAIT_LOG": str(wait_log),
                    }
                )
                process = subprocess.Popen(
                    ["/bin/bash", str(BATCH), "--manifest", str(manifest), "--report", str(report)],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    env=env,
                )
                for marker in markers:
                    self.await_carrier(marker)
                self.await_gate(signal_ready, "ready")
                process.send_signal(dispatch_signal)
                self.release_gate(signal_release)
                self.assertIsNone(process.poll(), "dispatcher exited before joining signalled children")
                self.assertTrue(report.is_file())
                self.assertEqual(report.read_text(), "")
                for marker in markers:
                    self.release_carrier(marker)
                _, stderr = process.communicate(timeout=WATCHDOG_SECONDS)
                self.assertEqual(process.returncode, 1, stderr)
                waits = [entry.split("|") for entry in wait_log.read_text().splitlines()]
                self.assertEqual(len(waits), len(flights) + 1)
                self.assertEqual(waits[0][0], waits[1][0], "interrupted child was not waited again")
                self.assertEqual(waits[0][1], str(128 + dispatch_signal))
                self.assertEqual(waits[1][1:], ["0"])
                document = json.loads(report.read_text())
                self.assertTrue(document["all_workers_waited"])
                self.assertTrue(document["interrupted"])
                self.assertEqual([item["runner_exit_code"] for item in document["workers"]], [0, 0])
                self.assertEqual(len(document["resolved_manifest"]["workers"]), 2)
                self.assertTrue(all(Path(item["status_ref"]).is_file() for item in document["workers"]))

    def test_batch_signal_between_joins_does_not_discard_completed_wait(self) -> None:
        flights = [self.next_flight_id() for _ in range(2)]
        markers = ["between-first", "between-second"]
        self.make_carrier_gates(markers)
        briefs = [self.make_brief(marker, "BLOCK") for marker in markers]
        manifest = self.write_manifest([self.worker(flights[i], 1, briefs[i]) for i in range(2)])
        report = self.temp_dir / "between-joins-report.json"
        wait_log = self.temp_dir / "between-joins-waits.log"
        between_ready = self.make_gate("between-joins.ready")
        between_release = self.make_gate("between-joins.release")
        term_handled = self.make_gate("between-joins-term-handled")
        bash_env = self.temp_dir / "between-joins.bash"
        bash_env.write_text(
            "wait() {\n"
            '  if [ "$0" != "$BATCH_SCRIPT" ]; then builtin wait "$@"; return $?; fi\n'
            "  wait_call_count=$(( ${wait_call_count:-0} + 1 ))\n"
            '  if [ "$wait_call_count" -eq 2 ]; then\n'
            '    printf "ready\\n" > "$BETWEEN_READY"\n'
            '    IFS= read -r _ < "$BETWEEN_RELEASE" || :\n'
            '    printf "handled\\n" > "$TERM_HANDLED"\n'
            "  fi\n"
            '  builtin wait "$@"\n'
            "  wait_result=$?\n"
            '  printf "%s|%s\\n" "$1" "$wait_result" >> "$WAIT_LOG"\n'
            '  return "$wait_result"\n'
            "}\n"
        )
        env = self.environment()
        env.update(
            {
                "BASH_ENV": str(bash_env),
                "BATCH_SCRIPT": str(BATCH),
                "BETWEEN_READY": str(between_ready),
                "BETWEEN_RELEASE": str(between_release),
                "TERM_HANDLED": str(term_handled),
                "WAIT_LOG": str(wait_log),
            }
        )
        process = subprocess.Popen(
            ["/bin/bash", str(BATCH), "--manifest", str(manifest), "--report", str(report)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
        for marker in markers:
            self.await_carrier(marker)
        self.release_carrier(markers[0])
        self.await_gate(between_ready, "ready")
        process.send_signal(signal.SIGTERM)
        self.release_gate(between_release)
        self.await_gate(term_handled, "handled")
        process.send_signal(signal.SIGINT)
        self.release_carrier(markers[1])
        _, stderr = process.communicate(timeout=WATCHDOG_SECONDS)
        self.assertEqual(process.returncode, 1, stderr)
        waits = wait_log.read_text().splitlines()
        self.assertEqual(len(waits), 2, f"a completed wait was discarded: {waits}")
        self.assertEqual(len({entry.split("|", 1)[0] for entry in waits}), 2)
        document = json.loads(report.read_text())
        self.assertTrue(document["interrupted"])
        self.assertEqual([item["runner_exit_code"] for item in document["workers"]], [0, 0])

    def test_batch_signal_during_launch_gives_every_runner_the_same_term_disposition(self) -> None:
        flights = [self.next_flight_id() for _ in range(2)]
        self.make_carrier_gates(flights)
        briefs = [self.make_brief(flight_id, "BLOCK") for flight_id in flights]
        manifest = self.write_manifest([self.worker(flights[i], 1, briefs[i]) for i in range(2)])
        report = self.temp_dir / "launch-signal-report.json"
        launch_ready = self.make_gate("launch-loop.ready")
        launch_release = self.make_gate("launch-loop.release")
        pid_log = self.temp_dir / "launch-pids.log"
        bash_env = self.temp_dir / "launch-signal.bash"
        bash_env.write_text(
            "trap '\n"
            '  if [ "$0" = "$BATCH_SCRIPT" ] && [ "$BASH_COMMAND" = "pids[\\$i]=\\$!" ]; then\n'
            '    printf "%s\\n" "$!" >> "$PID_LOG"\n'
            '    if [ "${launch_gate_used:-0}" -eq 0 ]; then\n'
            "      launch_gate_used=1\n"
            '      printf "ready\\n" > "$LAUNCH_READY"\n'
            '      IFS= read -r _ < "$LAUNCH_RELEASE"\n'
            "    fi\n"
            "  fi\n"
            "' DEBUG\n"
        )
        env = self.environment()
        env.update(
            {
                "BASH_ENV": str(bash_env),
                "BATCH_SCRIPT": str(BATCH),
                "PID_LOG": str(pid_log),
                "LAUNCH_READY": str(launch_ready),
                "LAUNCH_RELEASE": str(launch_release),
            }
        )
        process = subprocess.Popen(
            ["/bin/bash", str(BATCH), "--manifest", str(manifest), "--report", str(report)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
        self.await_gate(launch_ready, "ready")
        self.assertTrue(pid_log.exists(), "first runner PID was not recorded")
        process.send_signal(signal.SIGTERM)
        self.release_gate(launch_release)
        for marker in flights:
            self.await_carrier(marker)
        runner_pids = pid_log.read_text().splitlines()
        self.assertEqual(len(runner_pids), 2)
        for runner_pid in runner_pids:
            os.kill(int(runner_pid), signal.SIGTERM)
        for marker in flights:
            self.release_carrier(marker)
        _, stderr = process.communicate(timeout=WATCHDOG_SECONDS)
        self.assertEqual(process.returncode, 1, stderr)
        document = json.loads(report.read_text())
        self.assertTrue(document["interrupted"])
        self.assertEqual([item["runner_exit_code"] for item in document["workers"]], [1, 1])

    def test_batch_retains_colliding_child_status_and_ignores_second_recovery_signal(self) -> None:
        bash_wrapper = self.bin_dir / "bash"
        child_exited = self.make_gate("colliding-child-exited")
        bash_wrapper.write_text(
            "#!/bin/bash\n"
            'if [ "${1:-}" = "$RUNNER_SCRIPT" ] && [ "${2:-}" = "--stage" ]; then\n'
            '  printf "exited\\n" > "$CHILD_EXITED"\n'
            "  exit 143\n"
            "fi\n"
            'exec /bin/bash "$@"\n'
        )
        bash_wrapper.chmod(0o755)
        brief = self.make_brief("status-collision")
        manifest = self.write_manifest([self.worker(self.next_flight_id(), 1, brief)])
        report = self.temp_dir / "status-collision-report.json"
        wait_one = self.make_gate("wait-one")
        allow_wait_one = self.make_gate("allow-wait-one")
        wait_two = self.make_gate("wait-two")
        allow_wait_two = self.make_gate("allow-wait-two")
        wait_log = self.temp_dir / "status-collision-waits.log"
        int_disposition_log = self.temp_dir / "status-collision-int-disposition.log"
        bash_env = self.temp_dir / "status-collision.bash"
        bash_env.write_text(
            "wait() {\n"
            '  if [ "$0" != "$BATCH_SCRIPT" ]; then builtin wait "$@"; return $?; fi\n'
            "  wait_call_count=$(( ${wait_call_count:-0} + 1 ))\n"
            '  if [ "$wait_call_count" -eq 1 ]; then\n'
            '    printf "ready\\n" > "$WAIT_ONE"\n'
            '    IFS= read -r _ < "$ALLOW_WAIT_ONE" || :\n'
            '  elif [ "$wait_call_count" -eq 2 ]; then\n'
            '    trap -p INT > "$INT_DISPOSITION_LOG"\n'
            '    printf "ready\\n" > "$WAIT_TWO"\n'
            '    IFS= read -r _ < "$ALLOW_WAIT_TWO" || :\n'
            "  fi\n"
            '  builtin wait "$@"\n'
            "  wait_result=$?\n"
            '  printf "%s|%s\\n" "$1" "$wait_result" >> "$WAIT_LOG"\n'
            '  return "$wait_result"\n'
            "}\n"
        )
        env = self.environment()
        env.update(
            {
                "BASH_ENV": str(bash_env),
                "BATCH_SCRIPT": str(BATCH),
                "RUNNER_SCRIPT": str(RUNNER),
                "CHILD_EXITED": str(child_exited),
                "WAIT_ONE": str(wait_one),
                "ALLOW_WAIT_ONE": str(allow_wait_one),
                "WAIT_TWO": str(wait_two),
                "ALLOW_WAIT_TWO": str(allow_wait_two),
                "WAIT_LOG": str(wait_log),
                "INT_DISPOSITION_LOG": str(int_disposition_log),
            }
        )
        process = subprocess.Popen(
            ["/bin/bash", str(BATCH), "--manifest", str(manifest), "--report", str(report)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
        self.await_gate(child_exited, "exited")
        self.await_gate(wait_one, "ready")
        process.send_signal(signal.SIGTERM)
        self.release_gate(allow_wait_one)
        self.await_gate(wait_two, "ready")
        int_disposition = int_disposition_log.read_text().strip()
        process.send_signal(signal.SIGINT)
        self.release_gate(allow_wait_two)
        _, stderr = process.communicate(timeout=WATCHDOG_SECONDS)
        self.assertEqual(process.returncode, 1, stderr)
        self.assertEqual(int_disposition, "trap -- '' SIGINT")
        waits = wait_log.read_text().splitlines()
        self.assertEqual([entry.rsplit("|", 1)[1] for entry in waits], ["143", "143"])
        self.assertEqual(len({entry.rsplit("|", 1)[0] for entry in waits}), 1)
        document = json.loads(report.read_text())
        self.assertTrue(document["interrupted"])
        self.assertEqual(document["workers"][0]["runner_exit_code"], 143)

    def test_batch_manifest_preflight_launches_nothing(self) -> None:
        first_flight = self.next_flight_id()
        second_flight = self.next_flight_id()
        good_brief = self.make_brief("good")
        missing_brief = self.temp_dir / "missing.brief"
        manifest = self.write_manifest(
            [self.worker(first_flight, 1, good_brief), self.worker(second_flight, 1, missing_brief)]
        )
        report = self.temp_dir / "preflight-report.json"
        process = subprocess.run(
            ["/bin/bash", str(BATCH), "--manifest", str(manifest), "--report", str(report)],
            capture_output=True,
            text=True,
            env=self.environment(),
        )
        self.assertEqual(process.returncode, 64, process.stderr)
        self.assertFalse(report.exists())
        self.assertFalse(self.launch_log.exists())
        self.assertFalse(self.run_dir(first_flight).exists())
        duplicate = self.write_manifest(
            [self.worker(first_flight, 1, good_brief), self.worker(first_flight, 1, good_brief)],
            "duplicate.json",
        )
        duplicate_process = subprocess.run(
            ["/bin/bash", str(BATCH), "--manifest", str(duplicate), "--report", str(report)],
            capture_output=True,
            text=True,
            env=self.environment(),
        )
        self.assertEqual(duplicate_process.returncode, 64, duplicate_process.stderr)
        self.assertFalse(self.launch_log.exists())

        unreadable_brief = self.make_brief("unreadable")
        unreadable_brief.chmod(0o000)
        unreadable_manifest = self.write_manifest(
            [
                self.worker(self.next_flight_id(), 1, unreadable_brief),
                self.worker(self.next_flight_id(), 1, good_brief),
            ],
            "unreadable.json",
        )
        unreadable_process = subprocess.run(
            ["/bin/bash", str(BATCH), "--manifest", str(unreadable_manifest), "--report", str(report)],
            capture_output=True,
            text=True,
            env=self.environment(),
        )
        self.assertEqual(unreadable_process.returncode, 64, unreadable_process.stderr)
        self.assertFalse(report.exists())
        self.assertFalse(self.launch_log.exists())

        internal_report = self.temp_dir / "internal-preflight-report.json"
        internal_manifest = self.write_manifest(
            [self.worker(self.next_flight_id(), 1, good_brief)],
            "internal-preflight.json",
        )
        internal_env = self.environment()
        internal_env["SSHX_HOME"] = "relative"
        internal_process = subprocess.run(
            [
                "/bin/bash",
                str(BATCH),
                "--manifest",
                str(internal_manifest),
                "--report",
                str(internal_report),
            ],
            capture_output=True,
            text=True,
            env=internal_env,
        )
        self.assertEqual(internal_process.returncode, 1, internal_process.stderr)
        self.assertIn("INTERNAL_ERROR", internal_process.stderr)
        self.assertFalse(internal_report.exists())
        self.assertFalse(self.launch_log.exists())

    def test_manifest_attempt_domain_matches_runner_for_all_consumers(self) -> None:
        accepted_attempts = [1, 1234567890123456789012345678901234567890]
        for attempt in accepted_attempts:
            with self.subTest(attempt=attempt, accepted=True):
                flight_id = self.next_flight_id()
                brief = self.make_brief(f"attempt-{flight_id}")
                manifest = self.write_manifest(
                    [self.worker(flight_id, attempt, brief)], f"attempt-{attempt}.json"
                )
                report = self.temp_dir / f"attempt-{attempt}-report.json"
                batch = subprocess.run(
                    ["/bin/bash", str(BATCH), "--manifest", str(manifest), "--report", str(report)],
                    capture_output=True,
                    text=True,
                    env=self.environment(),
                )
                self.assertEqual(batch.returncode, 0, batch.stderr)
                self.assertEqual(json.loads(report.read_text())["workers"][0]["attempt"], attempt)
                status = subprocess.run(
                    ["/bin/bash", str(STATUS_READER), "--manifest", str(manifest)],
                    capture_output=True,
                    text=True,
                    env=self.environment(),
                )
                self.assertEqual(status.returncode, 0, status.stderr)
                self.assertTrue(json.loads(status.stdout)["workers"][0]["status_present"])
                cleanup = subprocess.run(
                    ["/bin/bash", str(CLEANUP), "--manifest", str(manifest)],
                    capture_output=True,
                    text=True,
                    env=self.environment(),
                )
                self.assertEqual(cleanup.returncode, 0, cleanup.stderr)
                self.assertEqual(
                    json.loads(cleanup.stdout)["would_remove"],
                    [str(self.project_flight(flight_id)["flight_dir"])],
                )

        rejected_attempts: list[object] = [1.0, "1", 0, -1]
        for index, attempt in enumerate(rejected_attempts):
            with self.subTest(attempt=attempt, accepted=False):
                flight_id = self.next_flight_id()
                brief = self.make_brief(f"invalid-attempt-{index}")
                manifest = self.write_manifest(
                    [self.worker(flight_id, attempt, brief)], f"invalid-attempt-{index}.json"
                )
                report = self.temp_dir / f"invalid-attempt-{index}-report.json"
                commands = [
                    ["/bin/bash", str(BATCH), "--manifest", str(manifest), "--report", str(report)],
                    ["/bin/bash", str(STATUS_READER), "--manifest", str(manifest)],
                    ["/bin/bash", str(CLEANUP), "--manifest", str(manifest)],
                ]
                launch_count = len(self.launch_log.read_text().splitlines())
                for command in commands:
                    process = subprocess.run(
                        command,
                        capture_output=True,
                        text=True,
                        env=self.environment(),
                    )
                    self.assertEqual(process.returncode, 64, process.stderr)
                    self.assertIn(
                        "manifest attempt must project as a positive decimal integer",
                        process.stderr,
                    )
                self.assertFalse(report.exists())
                self.assertEqual(len(self.launch_log.read_text().splitlines()), launch_count)

    def test_manifest_identity_shape_matches_runner_for_all_consumers(self) -> None:
        brief = self.make_brief("identity-shape")
        report_index = 0
        for label, flight_id in [
            ("legacy name", "csa-0909-think-worth"),
            ("uppercase", VALID_ID.upper()),
            ("short", VALID_ID[:-1]),
            ("long", VALID_ID + "0"),
            ("non-hex", VALID_ID[:-1] + "g"),
        ]:
            manifest = self.write_manifest([self.worker(flight_id, 1, brief)], f"identity-{report_index}.json")
            for script, arguments in [
                (BATCH, ["--manifest", str(manifest), "--report", str(self.temp_dir / f"identity-{report_index}-report.json")]),
                (STATUS_READER, ["--manifest", str(manifest)]),
                (CLEANUP, ["--manifest", str(manifest), "--delete"]),
            ]:
                with self.subTest(identity=label, script=script.name):
                    process = subprocess.run(["/bin/bash", str(script), *arguments], capture_output=True, text=True, env=self.environment())
                    self.assertEqual(process.returncode, 64, process.stderr)
                    self.assertIn("USAGE_ERROR", process.stderr)
                    self.assertEqual(process.stdout, "")
            report_index += 1
        self.assertFalse(self.launch_log.exists())
        self.assertFalse(self.sshx_home.exists())

    def test_batch_exclusively_reserves_report_target_and_temporary_before_launch(self) -> None:
        blocked_report = self.temp_dir / "blocked-report.json"
        blocked_report.write_text("existing\n")
        blocked_report.chmod(0o400)
        blocked_flight = self.next_flight_id()
        blocked_brief = self.make_brief("blocked-report")
        blocked_manifest = self.write_manifest(
            [self.worker(blocked_flight, 1, blocked_brief)], "blocked-report-manifest.json"
        )
        try:
            blocked = subprocess.run(
                ["/bin/bash", str(BATCH), "--manifest", str(blocked_manifest), "--report", str(blocked_report)],
                capture_output=True,
                text=True,
                env=self.environment(),
            )
        finally:
            blocked_report.chmod(0o600)
        self.assertEqual(blocked.returncode, 64, blocked.stderr)
        self.assertFalse(self.launch_log.exists())
        self.assertFalse(self.run_dir(blocked_flight).exists())

        shared_report = self.temp_dir / "shared-report.json"
        processes = []
        flights = []
        markers = []
        for index in range(2):
            flight_id = self.next_flight_id()
            flights.append(flight_id)
            marker = f"shared-report-{index}"
            markers.append(marker)
            self.make_carrier_gates([marker])
            brief = self.make_brief(marker, "BLOCK")
            manifest = self.write_manifest(
                [self.worker(flight_id, 1, brief)], f"shared-report-{index}.json"
            )
            processes.append(
                subprocess.Popen(
                    ["/bin/bash", str(BATCH), "--manifest", str(manifest), "--report", str(shared_report)],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    env=self.environment(),
                )
            )
        ready_gate, winning_marker = self.await_any_gate(
            [self.temp_dir / f"{marker}.ready" for marker in markers]
        )
        self.assertEqual(ready_gate.name, f"{winning_marker}.ready")
        temporaries = list(self.temp_dir.glob("shared-report.json.tmp.*"))
        self.assertEqual(len(temporaries), 1, "exactly one dispatcher may reserve the report identity")
        self.assertTrue(shared_report.is_file())
        self.release_carrier(winning_marker)
        outcomes = []
        for process in processes:
            _, stderr = process.communicate(timeout=WATCHDOG_SECONDS)
            outcomes.append((process.returncode, stderr))
        self.assertEqual(sorted(returncode for returncode, _ in outcomes), [0, 64])
        collision_stderr = next(stderr for returncode, stderr in outcomes if returncode == 64)
        self.assertIn("USAGE_ERROR", collision_stderr)
        self.assertIn("report target", collision_stderr)
        self.assertEqual(len(self.launch_log.read_text().splitlines()), 1)
        document = json.loads(shared_report.read_text())
        self.assertIn(document["workers"][0]["flight_id"], flights)
        self.assertEqual(list(self.temp_dir.glob("shared-report.json.tmp.*")), [])

    def test_batch_signal_in_reservation_window_cannot_leave_an_unowned_placeholder(self) -> None:
        flight_id = self.next_flight_id()
        brief = self.make_brief("reservation-signal")
        manifest = self.write_manifest([self.worker(flight_id, 1, brief)])
        report = self.temp_dir / "reservation-signal-report.json"
        bash_env = self.temp_dir / "reservation-signal.bash"
        bash_env.write_text(
            "trap '\n"
            '  if [ "$0" = "$BATCH_SCRIPT" ] && [ "$BASH_COMMAND" = "report_reserved=1" ]; then\n'
            '    builtin kill -TERM "$$"\n'
            "  fi\n"
            "' DEBUG\n"
        )
        env = self.environment()
        env.update({"BASH_ENV": str(bash_env), "BATCH_SCRIPT": str(BATCH)})
        process = subprocess.run(
            ["/bin/bash", str(BATCH), "--manifest", str(manifest), "--report", str(report)],
            capture_output=True,
            text=True,
            env=env,
            timeout=WATCHDOG_SECONDS,
        )
        self.assertEqual(process.returncode, 1, process.stderr)
        document = json.loads(report.read_text())
        self.assertTrue(document["interrupted"])
        self.assertEqual(document["workers"][0]["runner_exit_code"], 0)
        self.assertEqual(len(self.launch_log.read_text().splitlines()), 1)

    def test_status_reader_reemits_once_and_reports_only_file_facts(self) -> None:
        present_flight = self.next_flight_id()
        absent_flight = self.next_flight_id()
        run = self.run_worker(present_flight, marker="present")
        self.assertEqual(run.returncode, 0, run.stderr)
        original_status_text = (self.run_dir(present_flight) / "status.json").read_text()
        original_status = json.loads(original_status_text)
        placeholder = self.temp_dir / "unused.brief"
        present_worker = self.worker(present_flight, 1, placeholder)
        present_worker.update(
            {"stage": 17, "work_target": None, "brief_ref": [], "sandbox": "not-a-runner-value"}
        )
        workers = [present_worker, self.worker(absent_flight, 1, placeholder)]
        manifest = self.write_manifest(workers, "status-manifest.json")
        count_file = self.temp_dir / "status-reads.log"
        jq_link = self.bin_dir / "jq"
        jq_link.unlink()
        jq_link.write_text(
            "#!/bin/bash\n"
            "slurp=0\n"
            "for arg in \"$@\"; do\n"
            "  [ \"$arg\" != -s ] || slurp=1\n"
            "done\n"
            "if [ \"$slurp\" -eq 1 ]; then\n"
            "  for arg in \"$@\"; do\n"
            "    case \"$arg\" in */status.json) printf '%s\\n' \"$arg\" >> \"$STATUS_READ_LOG\" ;; esac\n"
            "  done\n"
            "fi\n"
            "exec \"$REAL_JQ\" \"$@\"\n"
        )
        jq_link.chmod(0o755)
        fake_cat = self.bin_dir / "cat"
        fake_cat.write_text(
            "#!/bin/bash\n"
            "for arg in \"$@\"; do\n"
            "  case \"$arg\" in */status.json) printf '%s\\n' \"$arg\" >> \"$STATUS_READ_LOG\" ;; esac\n"
            "done\n"
            "exec /bin/cat \"$@\"\n"
        )
        fake_cat.chmod(0o755)
        command_log = self.temp_dir / "status-commands.log"
        bash_env = self.temp_dir / "status-reader-trace.bash"
        bash_env.write_text(
            'if [ -z "${STATUS_READER_TRACE_PID:-}" ]; then STATUS_READER_TRACE_PID=$$; export STATUS_READER_TRACE_PID; fi\n'
            "trap 'if [ \"$$\" = \"$STATUS_READER_TRACE_PID\" ]; then printf \"%s\\n\" \"$BASH_COMMAND\" >> \"$STATUS_COMMAND_LOG\"; fi' DEBUG\n"
        )
        sleep_log = self.temp_dir / "sleep-calls.log"
        fake_sleep = self.bin_dir / "sleep"
        fake_sleep.write_text("#!/bin/bash\nprintf '%s\\n' \"$*\" >> \"$SLEEP_CALL_LOG\"\n")
        fake_sleep.chmod(0o755)
        env = self.environment()
        env.update(
            {
                "STATUS_READ_LOG": str(count_file),
                "STATUS_COMMAND_LOG": str(command_log),
                "BASH_ENV": str(bash_env),
                "REAL_JQ": str(self.real_jq),
                "SLEEP_CALL_LOG": str(sleep_log),
            }
        )
        process = subprocess.run(
            ["/bin/bash", str(STATUS_READER), "--manifest", str(manifest)],
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertEqual(process.returncode, 0, process.stderr)
        document = json.loads(process.stdout)
        self.assertEqual([item["flight_id"] for item in document["workers"]], [present_flight, absent_flight])
        present, absent = document["workers"]
        self.assertEqual(set(present), {"flight_id", "attempt", "status_present", "status_document"})
        self.assertTrue(present["status_present"])
        self.assertEqual(present["status_document"], original_status)
        self.assertNotEqual(
            original_status_text.strip(),
            json.dumps(present["status_document"], separators=(",", ":")),
            "the reader contract is semantic JSON equality, not byte-verbatim embedding",
        )
        self.assertFalse(absent["status_present"])
        self.assertIsNone(absent["status_document"])
        self.assertNotIn('"running"', process.stdout.lower())
        self.assertEqual(
            count_file.read_text().splitlines(),
            [str(self.run_dir(present_flight) / "status.json")],
            "the status path was read by more than one executable",
        )
        status_ref_commands = [
            command
            for command in command_log.read_text().splitlines()
            if '"$status_ref"' in command
            and not command.startswith("status_ref=")
            and not command.startswith('[ -f "$status_ref" ]')
            and not command.startswith('[ ! -L "$status_ref" ]')
        ]
        self.assertEqual(
            len(status_ref_commands),
            1,
            f"unexpected status-path content-read commands: {status_ref_commands}",
        )
        self.assertIn("status_document=", status_ref_commands[0])
        self.assertFalse(sleep_log.exists(), "status reader invoked a delay during its one-shot pass")

    def test_cleanup_dry_run_and_delete_are_whole_flight_and_exact(self) -> None:
        selected = [self.next_flight_id() for _ in range(2)]
        unrelated = self.next_flight_id()
        for flight_id in [*selected, unrelated]:
            process = self.run_worker(flight_id)
            self.assertEqual(process.returncode, 0, process.stderr)
        placeholder = self.temp_dir / "unused-clean.brief"
        manifest = self.write_manifest([self.worker(flight_id, 1, placeholder) for flight_id in selected], "clean.json")
        dry_run = subprocess.run(
            ["/bin/bash", str(CLEANUP), "--manifest", str(manifest)],
            capture_output=True,
            text=True,
            env=self.environment(),
        )
        self.assertEqual(dry_run.returncode, 0, dry_run.stderr)
        plan = json.loads(dry_run.stdout)
        expected = {str(self.project_flight(flight_id)["flight_dir"]) for flight_id in selected}
        self.assertEqual(set(plan["would_remove"]), expected)
        self.assertTrue(all(Path(path).exists() for path in expected))
        deletion = subprocess.run(
            ["/bin/bash", str(CLEANUP), "--manifest", str(manifest), "--delete"],
            capture_output=True,
            text=True,
            env=self.environment(),
        )
        self.assertEqual(deletion.returncode, 0, deletion.stderr)
        report = json.loads(deletion.stdout)
        self.assertEqual(set(report["removed"]), expected)
        self.assertEqual(report["failed"], [])
        self.assertEqual({item["state"] for item in report["flights"]}, {"removed"})
        self.assertTrue(all(not Path(path).exists() for path in expected))
        self.assertTrue(Path(str(self.project_flight(unrelated)["flight_dir"])).is_dir())

    def test_cleanup_refuses_all_when_any_attempt_lacks_terminal_status(self) -> None:
        eligible_flight = self.next_flight_id()
        blocked_flight = self.next_flight_id()
        for flight_id in [eligible_flight, blocked_flight]:
            process = self.run_worker(flight_id)
            self.assertEqual(process.returncode, 0, process.stderr)
        (self.run_dir(blocked_flight, 2)).mkdir()
        placeholder = self.temp_dir / "unused-blocked.brief"
        manifest = self.write_manifest(
            [self.worker(eligible_flight, 1, placeholder), self.worker(blocked_flight, 2, placeholder)],
            "blocked-clean.json",
        )
        process = subprocess.run(
            ["/bin/bash", str(CLEANUP), "--manifest", str(manifest), "--delete"],
            capture_output=True,
            text=True,
            env=self.environment(),
        )
        self.assertEqual(process.returncode, 1, process.stderr)
        report = json.loads(process.stdout)
        reasons = {item["flight_id"]: item["reason"] for item in report["flights"]}
        self.assertEqual(reasons[blocked_flight], "TERMINAL_STATUS_MISSING")
        self.assertEqual(report["failed"], [])
        self.assertEqual(report["removed"], [])
        self.assertEqual({item["state"] for item in report["flights"]}, {"untouched"})
        self.assertTrue(all(item["failure_reason"] is None for item in report["flights"]))
        self.assertTrue(Path(str(self.project_flight(eligible_flight)["flight_dir"])).is_dir())
        self.assertTrue(Path(str(self.project_flight(blocked_flight)["flight_dir"])).is_dir())

    def test_cleanup_refuses_symlinked_components_and_arbitrary_targets(self) -> None:
        flight_id = self.next_flight_id()
        projection = self.project_flight(flight_id)
        flight_dir = Path(str(projection["flight_dir"]))
        flight_dir.parent.mkdir(parents=True)
        outside = self.temp_dir / "outside-flight"
        (outside / "attempt-1").mkdir(parents=True)
        (outside / "attempt-1" / "status.json").write_text("{}")
        flight_dir.symlink_to(outside, target_is_directory=True)
        placeholder = self.temp_dir / "unused-linked.brief"
        manifest = self.write_manifest([self.worker(flight_id, 1, placeholder)], "linked-clean.json")
        process = subprocess.run(
            ["/bin/bash", str(CLEANUP), "--manifest", str(manifest), "--delete"],
            capture_output=True,
            text=True,
            env=self.environment(),
        )
        self.assertEqual(process.returncode, 1, process.stderr)
        self.assertEqual(json.loads(process.stdout)["flights"][0]["reason"], "FLIGHT_DIRECTORY_UNAVAILABLE")
        self.assertTrue(outside.is_dir())
        for forbidden in ["--path", "--run-dir", "--all"]:
            with self.subTest(forbidden=forbidden):
                command = ["/bin/bash", str(CLEANUP), "--manifest", str(manifest), forbidden]
                if forbidden != "--all":
                    command.append("/tmp/outside")
                refused = subprocess.run(command, capture_output=True, text=True, env=self.environment())
                self.assertEqual(refused.returncode, 64, refused.stderr)

    def test_cleanup_refuses_symlinked_terminal_status(self) -> None:
        flight_id = self.next_flight_id()
        run = self.run_worker(flight_id)
        self.assertEqual(run.returncode, 0, run.stderr)
        status_ref = self.run_dir(flight_id) / "status.json"
        outside_status = self.temp_dir / "outside-status.json"
        outside_status.write_text("{}")
        status_ref.unlink()
        status_ref.symlink_to(outside_status)
        placeholder = self.temp_dir / "unused-linked-status.brief"
        manifest = self.write_manifest([self.worker(flight_id, 1, placeholder)], "linked-status.json")
        process = subprocess.run(
            ["/bin/bash", str(CLEANUP), "--manifest", str(manifest), "--delete"],
            capture_output=True,
            text=True,
            env=self.environment(),
        )
        self.assertEqual(process.returncode, 1, process.stderr)
        report = json.loads(process.stdout)
        self.assertEqual(report["flights"][0]["reason"], "TERMINAL_STATUS_MISSING")
        self.assertEqual(report["removed"], [])
        self.assertTrue(Path(str(self.project_flight(flight_id)["flight_dir"])).is_dir())
        self.assertTrue(outside_status.is_file())

    def test_cleanup_rechecks_full_eligibility_immediately_before_each_removal(self) -> None:
        flights = [self.next_flight_id() for _ in range(2)]
        for flight_id in flights:
            run = self.run_worker(flight_id)
            self.assertEqual(run.returncode, 0, run.stderr)
        placeholder = self.temp_dir / "unused-recheck.brief"
        manifest = self.write_manifest(
            [self.worker(flight_id, 1, placeholder) for flight_id in flights], "recheck.json"
        )
        flight_dirs = [Path(str(self.project_flight(flight_id)["flight_dir"])) for flight_id in flights]
        fake_rm = self.bin_dir / "rm"
        fake_rm.write_text(
            "#!/bin/bash\n"
            "for argument in \"$@\"; do target=$argument; done\n"
            "if [ \"$target\" = \"$FIRST_RM_TARGET\" ]; then mkdir \"$CHANGED_FLIGHT/attempt-2\"; fi\n"
            "exec /bin/rm \"$@\"\n"
        )
        fake_rm.chmod(0o755)
        env = self.environment()
        env.update({"FIRST_RM_TARGET": str(flight_dirs[0]), "CHANGED_FLIGHT": str(flight_dirs[1])})
        process = subprocess.run(
            ["/bin/bash", str(CLEANUP), "--manifest", str(manifest), "--delete"],
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertEqual(process.returncode, 1, process.stderr)
        report = json.loads(process.stdout)
        self.assertEqual(report["removed"], [str(flight_dirs[0])])
        self.assertEqual(report["failed"][0]["flight_id"], flights[1])
        self.assertEqual(report["failed"][0]["state"], "untouched")
        self.assertEqual(report["failed"][0]["failure_reason"], "TERMINAL_STATUS_MISSING")
        self.assertFalse(flight_dirs[0].exists())
        self.assertTrue(flight_dirs[1].is_dir())
        self.assertTrue((flight_dirs[1] / "attempt-2").is_dir())

    def test_cleanup_distinguishes_projection_mismatch_from_comparison_failure(self) -> None:
        mismatch_flight = self.next_flight_id()
        run = self.run_worker(mismatch_flight)
        self.assertEqual(run.returncode, 0, run.stderr)
        placeholder = self.temp_dir / "unused-projection-mismatch.brief"
        mismatch_manifest = self.write_manifest(
            [self.worker(mismatch_flight, 1, placeholder)], "projection-mismatch.json"
        )
        mismatch_flight_dir = Path(str(self.project_flight(mismatch_flight)["flight_dir"]))
        changed_attempt = mismatch_flight_dir / "attempt-2"
        change_marker = self.temp_dir / "projection-changed"
        bash_env = self.temp_dir / "projection-mismatch.bash"
        bash_env.write_text(
            "trap '\n"
            '  if [ "$0" = "$CLEANUP_SCRIPT" ] && [ "$BASH_COMMAND" = "trap record_delete_interrupt INT TERM" ] && [ ! -e "$CHANGE_MARKER" ]; then\n'
            '    : > "$CHANGE_MARKER"\n'
            '    mkdir "$CHANGED_ATTEMPT"\n'
            '    printf "{}\\n" > "$CHANGED_ATTEMPT/status.json"\n'
            "  fi\n"
            "' DEBUG\n"
        )
        mismatch_env = self.environment()
        mismatch_env.update(
            {
                "BASH_ENV": str(bash_env),
                "CLEANUP_SCRIPT": str(CLEANUP),
                "CHANGE_MARKER": str(change_marker),
                "CHANGED_ATTEMPT": str(changed_attempt),
            }
        )
        mismatch = subprocess.run(
            ["/bin/bash", str(CLEANUP), "--manifest", str(mismatch_manifest), "--delete"],
            capture_output=True,
            text=True,
            env=mismatch_env,
        )
        self.assertEqual(mismatch.returncode, 1, mismatch.stderr)
        mismatch_report = json.loads(mismatch.stdout)
        self.assertEqual(mismatch_report["failed"][0]["failure_reason"], "FLIGHT_CHANGED")
        self.assertIsNone(mismatch_report["flights"][0]["reason"])
        self.assertEqual(mismatch_report["flights"][0]["failure_reason"], "FLIGHT_CHANGED")
        self.assertTrue(mismatch_flight_dir.is_dir())

        error_flight = self.next_flight_id()
        run = self.run_worker(error_flight)
        self.assertEqual(run.returncode, 0, run.stderr)
        error_manifest = self.write_manifest(
            [self.worker(error_flight, 1, placeholder)], "projection-error.json"
        )
        error_flight_dir = Path(str(self.project_flight(error_flight)["flight_dir"]))
        jq_link = self.bin_dir / "jq"
        jq_link.unlink()
        jq_link.write_text(
            "#!/bin/bash\n"
            'case " $* " in\n'
            "  *'$before == $after'*) exit 9 ;;\n"
            "esac\n"
            'exec "$REAL_JQ" "$@"\n'
        )
        jq_link.chmod(0o755)
        error_env = self.environment()
        error_env["REAL_JQ"] = str(self.real_jq)
        error = subprocess.run(
            ["/bin/bash", str(CLEANUP), "--manifest", str(error_manifest), "--delete"],
            capture_output=True,
            text=True,
            env=error_env,
        )
        self.assertEqual(error.returncode, 1, error.stderr)
        error_report = json.loads(error.stdout)
        self.assertEqual(error_report["failed"][0]["failure_reason"], "PROJECTION_COMPARE_FAILED")
        self.assertIsNone(error_report["flights"][0]["reason"])
        self.assertEqual(error_report["flights"][0]["failure_reason"], "PROJECTION_COMPARE_FAILED")
        self.assertTrue(error_flight_dir.is_dir())

    def test_cleanup_signal_during_removal_reports_exact_removed_subset(self) -> None:
        flights = [self.next_flight_id() for _ in range(2)]
        for flight_id in flights:
            run = self.run_worker(flight_id)
            self.assertEqual(run.returncode, 0, run.stderr)
        placeholder = self.temp_dir / "unused-signal-delete.brief"
        manifest = self.write_manifest(
            [self.worker(flight_id, 1, placeholder) for flight_id in flights], "signal-delete.json"
        )
        flight_dirs = [Path(str(self.project_flight(flight_id)["flight_dir"])) for flight_id in flights]
        fake_rm = self.bin_dir / "rm"
        delete_ready = self.make_gate("second-removal.ready")
        delete_release = self.make_gate("second-removal.release")
        fake_rm.write_text(
            "#!/bin/bash\n"
            'for argument in "$@"; do target=$argument; done\n'
            'if [ "$target" = "$SIGNAL_TARGET" ]; then\n'
            '  printf "ready\\n" > "$DELETE_READY"\n'
            '  IFS= read -r _ < "$DELETE_RELEASE"\n'
            "  exit 0\n"
            "fi\n"
            'exec /bin/rm "$@"\n'
        )
        fake_rm.chmod(0o755)
        env = self.environment()
        env.update(
            {
                "SIGNAL_TARGET": str(flight_dirs[1]),
                "DELETE_READY": str(delete_ready),
                "DELETE_RELEASE": str(delete_release),
            }
        )
        process = subprocess.Popen(
            ["/bin/bash", str(CLEANUP), "--manifest", str(manifest), "--delete"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
        self.await_gate(delete_ready, "ready")
        process.send_signal(signal.SIGTERM)
        self.release_gate(delete_release)
        stdout, stderr = process.communicate(timeout=WATCHDOG_SECONDS)
        self.assertEqual(process.returncode, 1, stderr)
        report = json.loads(stdout)
        self.assertEqual(report["removed"], [str(flight_dirs[0])])
        self.assertEqual(
            report["failed"],
            [
                {
                    "flight_id": flights[1],
                    "flight_dir": str(flight_dirs[1]),
                    "state": "untouched",
                    "failure_reason": "INTERRUPTED",
                }
            ],
        )
        self.assertEqual([item["state"] for item in report["flights"]], ["removed", "untouched"])
        self.assertFalse(flight_dirs[0].exists())
        self.assertTrue(flight_dirs[1].is_dir())

    def test_cleanup_reports_removed_subset_and_failed_target(self) -> None:
        flights = [self.next_flight_id() for _ in range(2)]
        for flight_id in flights:
            run = self.run_worker(flight_id)
            self.assertEqual(run.returncode, 0, run.stderr)
        placeholder = self.temp_dir / "unused-partial.brief"
        manifest = self.write_manifest([self.worker(flight_id, 1, placeholder) for flight_id in flights], "partial.json")
        flight_dirs = [Path(str(self.project_flight(flight_id)["flight_dir"])) for flight_id in flights]
        fake_rm = self.bin_dir / "rm"
        fake_rm.write_text(
            "#!/bin/bash\n"
            "for argument in \"$@\"; do target=$argument; done\n"
            "if [ \"$target\" = \"$FAIL_RM_TARGET\" ]; then exit 9; fi\n"
            "exec /bin/rm \"$@\"\n"
        )
        fake_rm.chmod(0o755)
        env = self.environment()
        env["FAIL_RM_TARGET"] = str(flight_dirs[1])
        process = subprocess.run(
            ["/bin/bash", str(CLEANUP), "--manifest", str(manifest), "--delete"],
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertEqual(process.returncode, 1, process.stderr)
        report = json.loads(process.stdout)
        self.assertEqual(report["removed"], [str(flight_dirs[0])])
        self.assertEqual(
            report["failed"],
            [
                {
                    "flight_id": flights[1],
                    "flight_dir": str(flight_dirs[1]),
                    "state": "untouched",
                    "failure_reason": "REMOVE_FAILED",
                }
            ],
        )
        failed_flight = report["flights"][1]
        self.assertTrue(failed_flight["eligible"])
        self.assertIsNone(failed_flight["reason"])
        self.assertEqual(failed_flight["failure_reason"], "REMOVE_FAILED")
        self.assertFalse(flight_dirs[0].exists())
        self.assertTrue(flight_dirs[1].is_dir())

    def test_cleanup_fallback_encoder_matches_jq_over_path_character_corpus(self) -> None:
        corpus = 'quote-"-backslash-\\-tab-\t-c1-\u0085-e-\u00e9-line-\u2028'
        corpus_tmp = self.temp_dir / corpus
        corpus_tmp.mkdir()
        env = self.environment(sshx_home=corpus_tmp)
        flight_id = self.next_flight_id()
        run = subprocess.run(
            [
                "/bin/bash",
                str(RUNNER),
                "--flight-id",
                flight_id,
                "--attempt",
                "1",
                "--stage",
                "implementation",
                "--work-target",
                str(ROOT),
            ],
            input="MARKER=encoder-corpus\n",
            capture_output=True,
            text=True,
            env=env,
            timeout=WATCHDOG_SECONDS,
        )
        self.assertEqual(run.returncode, 0, run.stderr)
        placeholder = self.temp_dir / "unused-encoder-corpus.brief"
        placeholder.write_text("unused\n")
        manifest = self.write_manifest([self.worker(flight_id, 1, placeholder)], "encoder-corpus.json")
        flight_dir = Path(str(self.project_flight(flight_id, env=env)["flight_dir"]))
        fake_rm = self.bin_dir / "rm"
        fake_rm.write_text("#!/bin/bash\nexit 9\n")
        fake_rm.chmod(0o755)
        jq_render = subprocess.run(
            ["/bin/bash", str(CLEANUP), "--manifest", str(manifest), "--delete"],
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertEqual(jq_render.returncode, 1, jq_render.stderr)
        jq_document = json.loads(jq_render.stdout)

        jq_link = self.bin_dir / "jq"
        jq_link.unlink()
        failure_marker = self.temp_dir / "encoder-jq-failed"
        jq_link.write_text(
            "#!/bin/bash\n"
            'if [ -e "$FAILURE_MARKER" ]; then exit 9; fi\n'
            'case " $* " in\n'
            "  *'state:$state'*) : > \"$FAILURE_MARKER\"; exit 9 ;;\n"
            "esac\n"
            'exec "$REAL_JQ" "$@"\n'
        )
        jq_link.chmod(0o755)
        env.update({"FAILURE_MARKER": str(failure_marker), "REAL_JQ": str(self.real_jq)})
        fallback_render = subprocess.run(
            ["/bin/bash", str(CLEANUP), "--manifest", str(manifest), "--delete"],
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertEqual(fallback_render.returncode, 1, fallback_render.stderr)
        fallback_document = json.loads(fallback_render.stdout)
        self.assertEqual(fallback_document, jq_document)
        self.assertEqual(fallback_document["flights"][0]["flight_dir"], str(flight_dir))
        self.assertEqual(fallback_document["failed"][0]["flight_dir"], str(flight_dir))

    def test_cleanup_reports_a_partially_removed_failed_flight(self) -> None:
        flight_id = self.next_flight_id()
        run = self.run_worker(flight_id)
        self.assertEqual(run.returncode, 0, run.stderr)
        placeholder = self.temp_dir / "unused-partial-removal.brief"
        manifest = self.write_manifest([self.worker(flight_id, 1, placeholder)], "partial-removal.json")
        flight_dir = Path(str(self.project_flight(flight_id)["flight_dir"]))
        fake_rm = self.bin_dir / "rm"
        fake_rm.write_text(
            "#!/bin/bash\n"
            "for argument in \"$@\"; do target=$argument; done\n"
            "/bin/rm -f \"$target/attempt-1/status.json\"\n"
            "exit 9\n"
        )
        fake_rm.chmod(0o755)
        process = subprocess.run(
            ["/bin/bash", str(CLEANUP), "--manifest", str(manifest), "--delete"],
            capture_output=True,
            text=True,
            env=self.environment(),
        )
        self.assertEqual(process.returncode, 1, process.stderr)
        report = json.loads(process.stdout)
        self.assertEqual(report["removed"], [])
        self.assertEqual(report["flights"][0]["state"], "partially-removed")
        self.assertEqual(report["failed"][0]["state"], "partially-removed")
        self.assertEqual(report["failed"][0]["failure_reason"], "REMOVE_FAILED")
        self.assertTrue(flight_dir.is_dir())
        self.assertFalse((flight_dir / "attempt-1" / "status.json").exists())

    def test_cleanup_persistent_final_renderer_failure_uses_non_jq_report(self) -> None:
        flight_id = self.next_flight_id()
        run = self.run_worker(flight_id)
        self.assertEqual(run.returncode, 0, run.stderr)
        placeholder = self.temp_dir / "unused-report-failure.brief"
        manifest = self.write_manifest([self.worker(flight_id, 1, placeholder)], "report-failure.json")
        flight_dir = Path(str(self.project_flight(flight_id)["flight_dir"]))
        jq_link = self.bin_dir / "jq"
        jq_link.unlink()
        failure_marker = self.temp_dir / "failed-removed-update"
        jq_link.write_text(
            "#!/bin/bash\n"
            'if [ -e "$FAILURE_MARKER" ]; then exit 9; fi\n'
            "case \" $* \" in\n"
            "  *'state:$state'*) touch \"$FAILURE_MARKER\"; exit 9 ;;\n"
            "esac\n"
            "exec \"$REAL_JQ\" \"$@\"\n"
        )
        jq_link.chmod(0o755)
        env = self.environment()
        env.update({"FAILURE_MARKER": str(failure_marker), "REAL_JQ": str(self.real_jq)})
        process = subprocess.run(
            ["/bin/bash", str(CLEANUP), "--manifest", str(manifest), "--delete"],
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertEqual(process.returncode, 1, process.stderr)
        report = json.loads(process.stdout)
        self.assertEqual(report["removed"], [str(flight_dir)])
        self.assertEqual(report["flights"][0]["state"], "removed")
        self.assertEqual(report["failed"][0]["state"], "removed")
        self.assertEqual(report["failed"][0]["failure_reason"], "REPORT_RENDER_FAILED")
        self.assertFalse(flight_dir.exists())

    def test_cleanup_successful_deletion_fails_closed_when_stdout_receipt_publication_fails(self) -> None:
        flight_id = self.next_flight_id()
        run = self.run_worker(flight_id)
        self.assertEqual(run.returncode, 0, run.stderr)
        placeholder = self.temp_dir / "unused-publication-failure.brief"
        manifest = self.write_manifest([self.worker(flight_id, 1, placeholder)], "publication-failure.json")
        flight_dir = Path(str(self.project_flight(flight_id)["flight_dir"]))
        read_only_stdout = self.temp_dir / "read-only-stdout"
        read_only_stdout.write_text("unchanged")
        with read_only_stdout.open("rb") as stdout:
            process = subprocess.run(
                ["/bin/bash", str(CLEANUP), "--manifest", str(manifest), "--delete"],
                stdout=stdout,
                stderr=subprocess.PIPE,
                text=True,
                env=self.environment(),
            )
        self.assertEqual(process.returncode, 1, process.stderr)
        self.assertFalse(flight_dir.exists())
        self.assertEqual(read_only_stdout.read_text(), "unchanged")
        receipt = json.loads(process.stderr)
        self.assertEqual(receipt["removed"], [str(flight_dir)])
        self.assertEqual(receipt["failed"], [])
        self.assertEqual(receipt["flights"][0]["state"], "removed")

    def test_cleanup_internal_recheck_and_reporter_jq_failures_use_non_jq_report(self) -> None:
        flights = [self.next_flight_id() for _ in range(2)]
        for flight_id in flights:
            run = self.run_worker(flight_id)
            self.assertEqual(run.returncode, 0, run.stderr)
        placeholder = self.temp_dir / "unused-internal-after-delete.brief"
        manifest = self.write_manifest(
            [self.worker(flight_id, 1, placeholder) for flight_id in flights], "internal-after-delete.json"
        )
        flight_dirs = [Path(str(self.project_flight(flight_id)["flight_dir"])) for flight_id in flights]
        after_removal_marker = self.temp_dir / "after-first-removal"
        failure_marker = self.temp_dir / "persistent-jq-failure"
        fake_rm = self.bin_dir / "rm"
        fake_rm.write_text(
            "#!/bin/bash\n"
            'for argument in "$@"; do target=$argument; done\n'
            '/bin/rm "$@"\n'
            "rm_result=$?\n"
            'if [ "$target" = "$FIRST_TARGET" ]; then : > "$AFTER_REMOVAL_MARKER"; fi\n'
            'exit "$rm_result"\n'
        )
        fake_rm.chmod(0o755)
        jq_link = self.bin_dir / "jq"
        jq_link.unlink()
        jq_link.write_text(
            "#!/bin/bash\n"
            'if [ -e "$FAILURE_MARKER" ]; then exit 9; fi\n'
            "case \" $* \" in\n"
            "  *' .flight_dir '*)\n"
            '    if [ -e "$AFTER_REMOVAL_MARKER" ]; then : > "$FAILURE_MARKER"; exit 9; fi ;;\n'
            "esac\n"
            'exec "$REAL_JQ" "$@"\n'
        )
        jq_link.chmod(0o755)
        env = self.environment()
        env.update(
            {
                "AFTER_REMOVAL_MARKER": str(after_removal_marker),
                "FAILURE_MARKER": str(failure_marker),
                "FIRST_TARGET": str(flight_dirs[0]),
                "REAL_JQ": str(self.real_jq),
            }
        )
        process = subprocess.run(
            ["/bin/bash", str(CLEANUP), "--manifest", str(manifest), "--delete"],
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertEqual(process.returncode, 1, process.stderr)
        report = json.loads(process.stdout)
        self.assertEqual(report["removed"], [str(flight_dirs[0])])
        self.assertEqual(report["failed"][0]["flight_id"], flights[1])
        self.assertEqual(report["failed"][0]["state"], "untouched")
        self.assertEqual(report["failed"][0]["failure_reason"], "INTERNAL_ERROR")
        self.assertIn("cannot read flight_dir", process.stderr)
        self.assertFalse(flight_dirs[0].exists())
        self.assertTrue(flight_dirs[1].is_dir())

    def test_project_root_classifies_entries_by_identity_shape_without_touching_them(self) -> None:
        absent_root = self.temp_dir / "absent-root"
        projection = self.project_root(env=self.environment(sshx_home=absent_root))
        self.assertEqual(
            projection,
            {"schema_version": 1, "sshx_root": str(absent_root), "root_present": False, "flights": [], "unrecognized": []},
        )
        self.assertFalse(absent_root.exists())
        flights = [self.next_flight_id() for _ in range(2)]
        for flight_id in flights:
            run = self.run_worker(flight_id)
            self.assertEqual(run.returncode, 0, run.stderr)
        (self.sshx_home / ".DS_Store").write_text("")
        (self.sshx_home / "legacy-name").mkdir()
        linked = "f" * 24
        (self.sshx_home / linked).symlink_to(self.temp_dir, target_is_directory=True)
        projection = self.project_root()
        self.assertEqual(projection["schema_version"], 1)
        self.assertEqual(projection["sshx_root"], str(self.sshx_home))
        self.assertTrue(projection["root_present"])
        self.assertEqual([item["flight_id"] for item in projection["flights"]], sorted([*flights, linked]))
        for item in projection["flights"]:
            self.assertEqual(item["flight_dir"], str(self.sshx_home / str(item["flight_id"])))
            self.assertEqual(item["flight_dir"], self.project_flight(str(item["flight_id"]))["flight_dir"])
        self.assertEqual(sorted(projection["unrecognized"]), [".DS_Store", "legacy-name"])
        self.assertTrue((self.sshx_home / linked).is_symlink())
        invalid_commands = [
            ["--project-root", "--flight-id", flights[0]],
            ["--project-root", "--attempt", "1"],
            ["--project-root", "--stage", "thinking"],
            ["--project-root", "--work-target", str(ROOT)],
            ["--project-root", "--sandbox", "workspace-write"],
            ["--project-root", "--project-flight", "--flight-id", flights[0]],
            ["--project-root", "--new-flight-id"],
            ["--project-root", "--project-root"],
        ]
        for arguments in invalid_commands:
            with self.subTest(arguments=arguments):
                process = subprocess.run(
                    ["/bin/bash", str(RUNNER), *arguments],
                    input="ignored",
                    capture_output=True,
                    text=True,
                    env=self.environment(),
                )
                self.assertEqual(process.returncode, 64, process.stderr)

    def test_prune_removes_only_flights_inactive_beyond_the_window(self) -> None:
        stale, fresh, touched, recent = (self.next_flight_id() for _ in range(4))
        for flight_id in (stale, fresh, touched, recent):
            run = self.run_worker(flight_id)
            self.assertEqual(run.returncode, 0, run.stderr)
        self.age_tree(self.sshx_home / stale, 2 * DAY_SECONDS)
        self.age_tree(self.sshx_home / touched, 2 * DAY_SECONDS)
        (self.run_dir(touched) / "worker.stdout.log").write_text("late output\n")
        self.age_tree(self.sshx_home / recent, 12 * 3600)
        (self.sshx_home / "notes.txt").write_text("keep me\n")
        wide = self.prune("--older-than", "3d", "--dry-run")
        self.assertEqual(wide.returncode, 0, wide.stderr)
        wide_plan = json.loads(wide.stdout)
        self.assertEqual(wide_plan["older_than_minutes"], 4320)
        self.assertEqual(self.flight_states(wide_plan), {flight_id: (False, "kept", None) for flight_id in (stale, fresh, touched, recent)})
        dry_run = self.prune("--dry-run")
        self.assertEqual(dry_run.returncode, 0, dry_run.stderr)
        plan = json.loads(dry_run.stdout)
        self.assertEqual(
            {key: plan[key] for key in ("schema_version", "mode", "sshx_root", "root_present", "older_than_minutes", "interrupted")},
            {"schema_version": 1, "mode": "dry-run", "sshx_root": str(self.sshx_home), "root_present": True, "older_than_minutes": 1440, "interrupted": False},
        )
        self.assertEqual(self.flight_states(plan), {stale: (True, "would-remove", None), fresh: (False, "kept", None), touched: (False, "kept", None), recent: (False, "kept", None)})
        self.assertEqual((plan["removed"], plan["failed"], plan["unrecognized"]), ([], [], ["notes.txt"]))
        self.assertTrue((self.sshx_home / stale).is_dir())
        deletion = self.prune()
        self.assertEqual(deletion.returncode, 0, deletion.stderr)
        report = json.loads(deletion.stdout)
        self.assertEqual(report["mode"], "delete")
        self.assertEqual(self.flight_states(report), {stale: (True, "removed", None), fresh: (False, "kept", None), touched: (False, "kept", None), recent: (False, "kept", None)})
        self.assertEqual(report["removed"], [str(self.sshx_home / stale)])
        self.assertEqual(report["failed"], [])
        self.assertFalse((self.sshx_home / stale).exists())
        self.assertTrue((self.sshx_home / fresh).is_dir())
        self.assertTrue((self.sshx_home / touched).is_dir())
        self.assertTrue((self.sshx_home / "notes.txt").is_file())
        self.age_tree(self.sshx_home / touched, 3 * 3600)
        shorter = self.prune("--older-than", "2h")
        self.assertEqual(shorter.returncode, 0, shorter.stderr)
        shorter_report = json.loads(shorter.stdout)
        self.assertEqual(shorter_report["older_than_minutes"], 120)
        self.assertEqual(self.flight_states(shorter_report), {fresh: (False, "kept", None), touched: (True, "removed", None), recent: (True, "removed", None)})
        self.assertEqual(shorter_report["removed"], [str(self.sshx_home / touched), str(self.sshx_home / recent)])
        absent = self.prune(env=self.environment(sshx_home=self.temp_dir / "absent-prune-root"))
        self.assertEqual(absent.returncode, 0, absent.stderr)
        absent_report = json.loads(absent.stdout)
        self.assertEqual((absent_report["root_present"], absent_report["flights"], absent_report["removed"]), (False, [], []))
        self.assertFalse((self.temp_dir / "absent-prune-root").exists())

    def test_prune_usage_errors_touch_nothing_and_windows_project_to_minutes(self) -> None:
        stale = self.next_flight_id()
        run = self.run_worker(stale)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.age_tree(self.sshx_home / stale, 3 * DAY_SECONDS)
        for arguments in [
            ["--older-than", "0d"],
            ["--older-than", "5"],
            ["--older-than", "1w"],
            ["--older-than", "-1d"],
            ["--older-than", ""],
            ["--older-than"],
            ["--older-than", "1d", "--older-than", "2d"],
            ["--dry-run", "--dry-run"],
            ["--unknown"],
            ["extra"],
            ["--delete"],
            ["--manifest", str(self.temp_dir / "unused.json")],
        ]:
            with self.subTest(arguments=arguments):
                process = self.prune(*arguments)
                self.assertEqual(process.returncode, 64, process.stderr)
                self.assertIn("USAGE_ERROR", process.stderr)
                self.assertEqual(process.stdout, "")
        self.assertTrue((self.sshx_home / stale).is_dir())
        for value, minutes in [("90m", 90), ("2h", 120), ("3d", 4320), ("007d", 10080)]:
            with self.subTest(value=value):
                process = self.prune("--older-than", value, "--dry-run")
                self.assertEqual(process.returncode, 0, process.stderr)
                self.assertEqual(json.loads(process.stdout)["older_than_minutes"], minutes)
        self.assertTrue((self.sshx_home / stale).is_dir())

    def test_prune_skips_entries_that_are_not_flight_directories_and_reports_them(self) -> None:
        outside = self.temp_dir / "outside"
        outside.mkdir()
        (outside / "victim").write_text("keep\n")
        self.age_tree(outside, 3 * DAY_SECONDS)
        self.sshx_home.mkdir()
        linked = "a" * 24
        (self.sshx_home / linked).symlink_to(outside, target_is_directory=True)
        regular = "b" * 24
        (self.sshx_home / regular).write_text("not a directory\n")
        (self.sshx_home / "stray.txt").write_text("")
        self.age_tree(self.sshx_home, 3 * DAY_SECONDS)
        process = self.prune()
        self.assertEqual(process.returncode, 0, process.stderr)
        report = json.loads(process.stdout)
        self.assertEqual(
            self.flight_states(report),
            {linked: (None, "skipped", "INVALID_FLIGHT_DIRECTORY"), regular: (None, "skipped", "INVALID_FLIGHT_DIRECTORY")},
        )
        self.assertEqual((report["unrecognized"], report["removed"], report["failed"]), (["stray.txt"], [], []))
        self.assertTrue((outside / "victim").is_file())
        self.assertTrue((self.sshx_home / linked).is_symlink())
        self.assertTrue((self.sshx_home / regular).is_file())
        self.assertTrue((self.sshx_home / "stray.txt").is_file())

    def test_prune_reports_failed_removal_and_continues_with_the_next_flight(self) -> None:
        first, second = self.next_flight_id(), self.next_flight_id()
        for flight_id in (first, second):
            run = self.run_worker(flight_id)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.age_tree(self.sshx_home / flight_id, 2 * DAY_SECONDS)
        fake_rm = self.bin_dir / "rm"
        fake_rm.write_text(
            "#!/bin/bash\n"
            'for target in "$@"; do [ "$target" != "$FAIL_TARGET" ] || exit 9; done\n'
            'exec /bin/rm "$@"\n'
        )
        fake_rm.chmod(0o755)
        env = self.environment()
        env["FAIL_TARGET"] = str(self.sshx_home / first)
        process = self.prune(env=env)
        self.assertEqual(process.returncode, 1, process.stderr)
        report = json.loads(process.stdout)
        self.assertEqual(self.flight_states(report), {first: (True, "failed", "REMOVE_FAILED"), second: (True, "removed", None)})
        self.assertEqual(report["failed"], [{"flight_id": first, "flight_dir": str(self.sshx_home / first), "reason": "REMOVE_FAILED"}])
        self.assertEqual(report["removed"], [str(self.sshx_home / second)])
        self.assertFalse(report["interrupted"])
        self.assertTrue((self.sshx_home / first).is_dir())
        self.assertFalse((self.sshx_home / second).exists())
        fake_rm.write_text("#!/bin/bash\nexit 0\n")
        remains = self.prune(env=env)
        self.assertEqual(remains.returncode, 1, remains.stderr)
        remains_report = json.loads(remains.stdout)
        self.assertEqual(self.flight_states(remains_report), {first: (True, "failed", "FLIGHT_REMAINS")})
        self.assertEqual(remains_report["removed"], [])
        self.assertTrue((self.sshx_home / first).is_dir())

    def test_prune_signal_stops_after_the_current_removal_and_reports_interrupted(self) -> None:
        first, second = self.next_flight_id(), self.next_flight_id()
        for flight_id in (first, second):
            run = self.run_worker(flight_id)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.age_tree(self.sshx_home / flight_id, 2 * DAY_SECONDS)
        ready = self.make_gate("prune-rm.ready")
        release = self.make_gate("prune-rm.release")
        fake_rm = self.bin_dir / "rm"
        fake_rm.write_text(
            "#!/bin/bash\n"
            'printf "%s\\n" blocked > "$PRUNE_RM_READY"\n'
            'IFS= read -r _ < "$PRUNE_RM_RELEASE"\n'
            'exec /bin/rm "$@"\n'
        )
        fake_rm.chmod(0o755)
        env = self.environment()
        env.update({"PRUNE_RM_READY": str(ready), "PRUNE_RM_RELEASE": str(release)})
        with subprocess.Popen(["/bin/bash", str(PRUNE)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env) as process:
            self.await_gate(ready, "blocked")
            process.send_signal(signal.SIGTERM)
            self.release_gate(release)
            stdout, stderr = process.communicate(timeout=WATCHDOG_SECONDS)
        self.assertEqual(process.returncode, 1, stderr)
        report = json.loads(stdout)
        self.assertTrue(report["interrupted"])
        self.assertEqual(self.flight_states(report), {first: (True, "removed", None)})
        self.assertEqual(report["removed"], [str(self.sshx_home / first)])
        self.assertEqual(report["failed"], [])
        self.assertFalse((self.sshx_home / first).exists())
        self.assertTrue((self.sshx_home / second).is_dir())


if __name__ == "__main__":
    unittest.main()
