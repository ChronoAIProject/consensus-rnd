# Codex Worker Runner Specification

This is the mechanical specification for
`skills/sshx/scripts/run-codex-worker.sh`. The completion predicate is defined
once in `SKILL.md` under `## Worker Completion Contract`; this file references
that contract and does not restate it.

## Invocation

```text
bash <skill-root>/scripts/run-codex-worker.sh \
  --flight-id <id> --attempt <positive-integer> \
  --stage <thinking|implementation|review> \
  --work-target <absolute-path> --sandbox <read-only|workspace-write>
```

The five options are required. Missing, duplicate, unknown, or positional
arguments are `USAGE_ERROR` (exit 64). `flight-id` is non-empty
`[A-Za-z0-9._-]+`, rejects `..` and `.`, `attempt` is a positive integer,
`stage` and `sandbox` use the enumerations above, and `work-target` is
absolute. The brief is read from stdin; artifact paths and extra Codex flags
are never caller-supplied.

## Run Directory

The runner derives exactly:

```text
${TMPDIR:-/tmp}/consensus-rnd/sshx/<flight-id>/attempt-<attempt>
```

Trailing slashes are removed except for `/`. The default `/tmp` may be a
symbolic link. An explicitly configured `TMPDIR` must be absolute, existing,
writable, and not a symbolic link. The runner-created `consensus-rnd`, `sshx`,
and flight directories are always rejected when symbolic links. Each is
created or validated before one atomic `mkdir` of the attempt directory;
an existing attempt is `RUN_DIR_COLLISION`.

The runner owns `brief.md`, the three diagnostic logs, `carrier.exit`, and
`status.json`. The worker alone owns `result.json` and `completion.sentinel`.
The runner never creates, repairs, copies, normalizes, touches, or substitutes
either worker artifact. The brief requests same-directory temporary files and
atomic rename for those artifacts.

## Carrier

After `jq`, directory, brief, log, and executable `codex` preflight checks, the
runner performs one synchronous foreground call:

```text
codex exec --json -C <work-target> --sandbox <sandbox> \
  --skip-git-repo-check -o <last-message.txt> -
```

The command's stdout and stderr go to the fixed diagnostic logs. There is no
timeout, supervisor, helper, PID state, signal propagation, process-group
handling, or KILL escalation. The carrier wait status is written atomically to
`carrier.exit`. A missing executable is `LAUNCH_FAILED`; once invoked, any
nonzero carrier status is `CARRIER_EXIT_NONZERO`.

`INT` and `TERM` traps set `reason_code=INTERRUPTED` and exit through the
normal `EXIT` trap. Bash may defer these traps while the synchronous foreground
command is running. The runner does not promise signal reachability in every
phase and does not tear down descendants.

## Status Projection

After the attempt directory is created and all derived paths are known, but
before the carrier is launched, the runner publishes a formatted JSON
`status.json` with `status: "RUNNING"`. It contains invocation identity,
derived artifact and log references, `work_target`, `sandbox`, `brief_ref`, and
the diagnostic fields `started_at`, `finished_at: null`, and
`duration_seconds: null`. `started_at` is UTC ISO-8601
(`%Y-%m-%dT%H:%M:%SZ`) when BSD `date` can provide it; a failed time lookup
writes `null` and does not fail the flight. The startup publication uses the
same `regular_or_absent` target check as the terminal publication and is
written through its temporary file and atomic rename.

After the carrier exits, `trap finish EXIT` replaces the current projection
with the terminal projection. Terminal `status` is `COMPLETE` or
`NOT_COMPLETE`; `finished_at` is the UTC terminal time and `duration_seconds`
is the integer epoch-second difference when both values are available, or
`null` otherwise. The terminal projection preserves `started_at`,
`work_target`, `sandbox`, and `brief_ref`.

`status.json` is a diagnostic projection, not evidence. In particular,
`RUNNING` never means success or completion: the fail-closed completion
predicate is the fixed four-way conjunction in `SKILL.md`, and it has never
included `status.json`. Diagnostic surfaces never participate in completion
checks.

The six context and timing fields have this fixed lifecycle:

| field | startup projection | terminal projection |
| --- | --- | --- |
| `started_at` | flight start time, or `null` | preserved |
| `finished_at` | `null` | terminal time, or `null` |
| `duration_seconds` | `null` | integer elapsed seconds, or `null` |
| `work_target` | invocation value | preserved |
| `sandbox` | invocation value | preserved |
| `brief_ref` | absolute `brief.md` path | preserved |

## Terminal Projection

`trap finish EXIT` is the only terminal publisher and the script has one
process-terminating `exit`. The default is fail-closed `INTERNAL_ERROR` with
exit 1; only the final successful path changes the reason to `COMPLETE` and
exit 0. Other ordinary failures also exit 1. The first failure encountered in
the fixed check order determines `reason_code`:

`RUN_DIR_COLLISION`, `RUN_DIR_UNAVAILABLE`, `PARSER_UNAVAILABLE`,
`LAUNCH_FAILED`, `CARRIER_EXIT_NONZERO`, `RESULT_MISSING`,
`ENVELOPE_INVALID`, `VERDICT_INVALID`, `SENTINEL_MISSING`,
`INTERRUPTED`, or `INTERNAL_ERROR`.

Exit code is authoritative because it is the process return value. `status.json`
and stdout are projections of that decision. If either projection cannot be
published, `reason_code=INTERNAL_ERROR` and exit 1; callers use the exit code
as the authority rather than assuming three-way atomic consistency.

After the attempt directory is owned, `finish` renders formatted (pretty-print)
`status.json.tmp`,
publishes the same JSON to stdout, then renames the temporary file to
`status.json`. The status contains invocation identity, `status` (`COMPLETE` or
`NOT_COMPLETE`), `reason_code`, `carrier_exit` (or `null`), derived artifact
references, diagnostic log references, the six fields `started_at`,
`finished_at`, `duration_seconds`, `work_target`, `sandbox`, and `brief_ref`,
and a verdict only when the stage has a verdict mapping. Before either
runner-owned projection is written, its temporary
and final target must be absent or a non-symbolic-link regular file. After each
rename, the fixed `carrier.exit` or `status.json` path must be a non-symbolic-link
regular file; every target-type or publication failure fails closed.
The stdout payload is the complete formatted JSON document, byte-for-byte equal
to the terminal `status.json` content; callers parse it as one JSON document.

`jq` is mandatory. Parsing or structural failure is `ENVELOPE_INVALID`; no
text matching fallback exists. `SKILL.md` is the sole source of the exact stage
verdict sets. The runner's executable projection is kept bidirectionally equal
to that contract by tests; this specification does not repeat the sets.

No diagnostic surface participates in completion or verdict recognition:
stdout, stderr, `last-message.txt`, log tails, marker text, event streams,
process snapshots, repository state, and hashes are diagnostic only.

## Teardown Prerequisite

Verified in two independent Claude Code harness experiments: `TaskStop`
terminates the entire process tree, including a child that actively ignores
`TERM` and `INT`; this indicates the harness uses SIGKILL or process-group
teardown. The runner therefore does not propagate signals.

Codex, Cursor, and Gemini host teardown behavior is unverified. An interactive
Ctrl-C normally sends `INT` to the foreground process group, which includes the
synchronous carrier. A default `TERM` sent only to the runner PID may be
deferred by Bash until the foreground carrier returns. An uncatchable `SIGKILL`
sent only to the runner PID can leave the carrier orphaned and running. The
runner does not attempt to compensate for any of these host behaviors.

## Threat Model

The carrier is an internally dispatched Codex worker: trusted is
non-adversarial, not infallible. The runner rejects normal malformed output,
missing artifacts, and accidental projection-path type collisions, but it is
not a sandbox against a hostile carrier. This design does not defend TOCTOU
races, an active `setsid` escape, forged runner artifact paths, or deliberate
replacement of files inside the owned attempt directory. An untrusted carrier
or a requirement to cover those attacks requires a new design review rather
than more checks in this runner.

## Boundaries

The runner has no git, GitHub, label, release, host lifecycle, cleanup, or
global-state authority. Time limits and whole-job teardown belong to the
caller harness. Power-loss durability is not guaranteed.

No other skill may depend on this runner. To reverse the exception completely:

1. Delete `scripts/run-codex-worker.sh`, this specification, and `tests/test_run_codex_worker.py`.
2. Restore the three narrow `SKILL.md` clauses: make `codex-cli` a direct caller
   dispatch with caller-assigned artifact paths; describe the completion result
   and sentinel as caller-assigned; and restore `## Boundaries` to a prompt-only
   contract that forbids helper scripts.
3. Remove runner-specific assertions from `tests/test_sshx_contract.py` and run
   the remaining sshx contract suite. No compatibility shell is retained.

A new design review is required before adding daemon behavior, lifecycle
authority, a second consuming skill, or completion semantics cease to be
isomorphic to `SKILL.md`'s `## Worker Completion Contract`.
