# Codex Worker Runner Specification

This is the mechanical specification for
`skills/sshx/scripts/run-codex-worker.sh`. The completion predicate is defined
once in `SKILL.md` under `## Worker Completion Contract`; this file references
that contract and does not restate it. Caller-side runner dispatch is governed
solely by `SKILL.md` under `## Worker Delegation` and is likewise not restated
here.

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

`status.json` is written only by terminal cleanup; there is no startup or
in-progress status projection. Its `status` is exactly `COMPLETE` or
`NOT_COMPLETE`. It contains invocation identity, derived artifact and log
references, `started_at`, `finished_at`, `duration_seconds`, `work_target`,
`sandbox`, and `brief_ref`. Times use UTC ISO-8601
(`%Y-%m-%dT%H:%M:%SZ`); duration is the integer epoch-second difference when
both lookups succeed. A failed time lookup writes `null` and does not fail the
flight.

Exit code is the sole authority: `0` means complete, `1` means not complete,
and `64` means usage error. `status.json` is a terminal, machine-readable
projection for callers that need structured data. stdout is a human-readable
streaming log; it carries no decision authority, is not guaranteed to be
parseable, and is not byte-for-byte identical to any file.

Before the synchronous carrier call, stdout reports all then-known invocation
identity and derived artifact paths followed by `carrier starting`. After the
call returns, it reports the carrier exit status; terminal cleanup then reports
`status`, `reason_code`, `verdict`, and duration. Every stdout line begins with
a UTC ISO-8601 timestamp. stdout write failure is diagnostic only and cannot
change the authoritative exit decision.

Each stdout write runs in a narrow subshell so a closed stream cannot override
that exit decision or change the carrier's signal disposition. Terminal
cleanup clears only its recursive `EXIT` trap and ignores
`INT` and `TERM` for the rest of publication, so a second signal cannot leave
the terminal projection half-published.

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

After the attempt directory is owned, `finish` renders formatted (pretty-print)
`status.json.tmp`, then renames the temporary file to `status.json`. The status
contains invocation identity, `status` (`COMPLETE` or
`NOT_COMPLETE`), `reason_code`, `carrier_exit` (or `null`), derived artifact
references, diagnostic log references, the six fields `started_at`,
`finished_at`, `duration_seconds`, `work_target`, `sandbox`, and `brief_ref`,
and a verdict only when the stage has a verdict mapping. Before either
runner-owned projection is written, its temporary
and final target must be absent or a non-symbolic-link regular file. After each
rename, the fixed `carrier.exit` or `status.json` path must be a non-symbolic-link
regular file; every target-type or publication failure fails closed.
Both the primary rendering and the `INTERNAL_ERROR` fallback must exit zero and
produce a non-empty temporary file before rename. If both renderings fail,
the runner installs no `status.json`, reports `INTERNAL_ERROR`, and exits 1.
The renderer is not asked to validate its own output.

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
