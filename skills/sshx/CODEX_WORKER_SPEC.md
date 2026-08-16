# Codex Worker Mechanical Specification

This is the single mechanical specification for these four closed-set scripts:

- `skills/sshx/scripts/run-codex-worker.sh`;
- `skills/sshx/scripts/run-codex-worker-batch.sh`;
- `skills/sshx/scripts/read-codex-worker-status.sh`;
- `skills/sshx/scripts/clean-codex-worker-runs.sh`.

The completion predicate is defined once in `SKILL.md` under
`## Worker Completion Contract`; this file references that contract and does
not restate it. Caller-side dispatch and collection are governed solely by
`SKILL.md` under `## Worker Delegation` and are likewise not restated here.

## Invocation

```text
bash <skill-root>/scripts/run-codex-worker.sh \
  --flight-id <id> --attempt <positive-integer> \
  --stage <thinking|implementation|review> \
  --work-target <absolute-path> [--sandbox <danger-full-access|workspace-write>]
```

The first four options are required; `--sandbox` is optional and defaults to
`danger-full-access` when omitted. Missing required, duplicate, unknown, or positional
arguments are `USAGE_ERROR` (exit 64). `flight-id` is non-empty
`[A-Za-z0-9._-]+`, rejects `..` and `.`, `attempt` is a positive integer,
`stage` and `sandbox` use the enumerations above, and `work-target` is
absolute and contains neither LF (`0x0A`) nor CR (`0x0D`). This is a
locale-independent POSIX text-line boundary: stdout lines are LF-separated,
not Unicode logical lines. Other characters, including TAB, C1 controls,
zero-width joiners/non-joiners, and U+2028/U+2029, are accepted in path values.
The brief is read from stdin;
artifact paths and extra Codex flags are never caller-supplied.

The runner also has two mutually exclusive pure query invocations:

```text
bash <skill-root>/scripts/run-codex-worker.sh \
  --project-paths --flight-id <id> --attempt <positive-integer>

bash <skill-root>/scripts/run-codex-worker.sh \
  --project-flight --flight-id <id>
```

Missing required identity options, combining the query modes, or combining
either query with `--stage`, `--work-target`, or `--sandbox` is `USAGE_ERROR`
(exit 64). `--project-flight` also refuses `--attempt`; it takes only the flight
identity. Identity validation and `TMPDIR` normalization are identical to run
mode. Neither query requires `TMPDIR` or any projected directory to exist or be
writable. They read no stdin, create no directory, launch no carrier, write no
projection, and delete nothing. On success `--project-paths` emits one strict
JSON object containing `schema_version`,
`flight_id`, `attempt`, `run_dir`, `brief_ref`, `result_ref`,
`completion_sentinel_ref`, `carrier_exit_ref`, `status_ref`, and `log_refs` for
stdout, stderr, and the last message. `--project-flight` emits `schema_version`,
`flight_id`, the runner-owned `flight_dir` and `sshx_root`, and every namespace
entry matching the runner's attempt naming rule. Each attempt entry contains
its positive integer `attempt`, `run_dir`, and terminal `status_ref`; an entry
whose suffix is not a canonical positive integer carries `attempt: null` so a
consumer can mark it invalid without parsing the name. Directory enumeration is
read-only. Both queries and run mode share the same single path-derivation
function; there is no parallel formula.

## Run Directory

The runner derives exactly:

```text
${TMPDIR:-/tmp}/consensus-rnd/sshx/<flight-id>/attempt-<attempt>
```

Trailing slashes are removed except for `/`. Whether the default or an
explicitly configured value is used, `TMPDIR` must be absolute, contain neither
LF nor CR, and resolve to an existing writable directory. The top-level
`TMPDIR` may be a symbolic link to an existing writable directory. The
runner-created `consensus-rnd`, `sshx`, and flight directories are always
rejected when symbolic links. Each is created or validated before one atomic
`mkdir` of the attempt directory; an existing attempt is `RUN_DIR_COLLISION`.

The runner owns `brief.md`, the three diagnostic logs, `carrier.exit`, and
`status.json`. The worker alone owns `result.json` and `completion.sentinel`.
The runner never creates, repairs, copies, normalizes, touches, or substitutes
either worker artifact. The brief requests same-directory temporary files and
atomic rename for those artifacts. It also states the envelope's structural
acceptance conditions and includes one runner-rendered, stage-specific minimum
valid envelope example.

The pure queries are the only sanctioned path discovery mechanism for the
batch, status-read, and cleanup scripts. Those scripts contain no run-layout
formula, attempt naming rule, or artifact basename and do not parse the
runner's human stdout.

## Batch Dispatch

```text
bash <skill-root>/scripts/run-codex-worker-batch.sh \
  --manifest <absolute-path> --report <absolute-path>
```

The manifest is exactly one strict JSON object with `schema_version: 1` and a
non-empty `workers` array. Each worker has exactly `flight_id`, `attempt`,
`stage`, `work_target`, and `brief_ref`, plus optional `sandbox`. Identity,
stage, target, and sandbox values obey the runner's run-mode rules. For the
batch dispatcher, status reader, and cleanup tool alike, `attempt` must be a
JSON number whose `jq` text projection matches `[1-9][0-9]*`; that projected
decimal string is passed unchanged to the runner. Thus `1` and very large
digit-only integer values are accepted subject to parser and filesystem
resource limits, while `1.0`, `"1"`, `0`, and `-1` are rejected as
`USAGE_ERROR` (exit 64) with an attempt-specific diagnostic. Every
`brief_ref` is an absolute path to a regular non-symbolic-link file that the
dispatcher can open for reading. Duplicate identity pairs are invalid. The
report parent must be writable and the report target must be absent; an existing
file, symbolic link, or other entry is rejected rather than overwritten. The
dispatcher validates the entire document, probes every brief for readability,
invokes the runner's pure path projection for every worker and stores each
returned document, checks the report target, atomically reserves that final path
as an empty regular file, and exclusively creates one unique same-directory
report temporary file before launching any worker.

Caller-input, option, manifest, brief, and report-target failures are
`USAGE_ERROR` (exit 64): no worker launches, and this invocation creates no JSON
report or changes an existing target. An internal pre-launch failure, including
an unavailable parser or runner, a failed runner projection, or failure to
create or validate the report temporary, is `INTERNAL_ERROR` (exit 1): no worker
launches and no complete JSON report exists. Before reservation, the dispatcher
installs both the `EXIT` release handler and the `INT`/`TERM` recording handlers.
A catchable signal whose dispatcher trap is effective therefore cannot terminate
the dispatcher inside acquisition. The `EXIT` handler removes the final-path
reservation on a handled unpublished exit. A successfully reserved path is only
an empty ownership placeholder while the dispatcher runs, not a published
report.

A termination after the reservation file is created but before the
exclusive-creation subshell returns can leave that zero-byte reservation. This
includes an uncatchable dispatcher termination and a group-directed catchable
signal that terminates the subshell while the dispatcher survives. A later
invocation on the same path exits 64 with `report target must be absent`. After
confirming that no dispatcher still owns the path, recover with
`rm -- <report>`.

The brief probe opens and then closes each descriptor. It establishes file type
and readability only at the instant of that probe; each launch later reopens the
caller-owned path. Replacement, deletion, or permission changes between those
operations can therefore produce a partial dispatch. Preflight makes no
all-or-nothing claim that survives such replacement, consistent with `## Threat
Model`; the dispatcher does not copy, stage, or otherwise take ownership of a
caller brief.

After preflight, the dispatcher launches the runner exactly once per manifest
worker and passes that worker's brief on stdin. Omitted sandbox values remain
omitted so the runner owns its default. Children run concurrently under one
foreground dispatcher. The dispatcher records every PID and uses `wait` for
every child even after a sibling fails. It never detaches, polls artifacts or
logs, or uses file contents to infer child exit.

The dispatcher uses join-then-publish interruption handling. When effective,
its `INT` and `TERM` traps record only the first signal but do not exit and do
not forward the signal to runners or carriers. Until launch completes, later
signals with effective traps remain caught and cannot replace that first
record. Only after every child has been launched does the dispatcher change
both dispositions to ignore, immediately when a signal was already recorded or
from the first later signal handler, so a second signal cannot interrupt a
recovery wait. Every asynchronous runner is therefore launched with the same
Bash dispositions independent of signal
timing: `INT` is ignored by Bash's non-job-control background launch and `TERM`
is default on entry because the dispatcher's caught handler is reset in the
child; the runner then installs its own `INT`/`TERM` handlers, although an
inherited ignored `INT` remains ignored. The dispatcher repeats a wait only
when that wait's own return status equals the recorded signal status. It joins
every recorded child before publication. It then publishes the report with
`interrupted: true` and exits nonzero when a signal was recorded. This preserves
ownership of recorded children but does not promise prompt cancellation or
carrier teardown. The runner may itself defer traps during its synchronous
carrier call and does not propagate signals, so teardown of the whole job tree
remains the host's responsibility.

The dispatcher's `INT` half has the same inherited-disposition limit as its
children: if the host starts it with `SIGINT` ignored, Bash cannot make its
`INT` trap effective, so those signals are inert and the dispatcher can finish
with `interrupted: false`. The stated `TERM` behavior likewise presumes that
`TERM` is trappable on dispatcher entry.

This guarantee has a shell prerequisite: the supported Bash must retain an
exited child's status when `wait` is repeated for that PID. The repository
collision/recovery behavior test distinguishes this behavior by recording a
signal outside `wait`, consuming a genuine child status of 143, repeating the
wait, and requiring both waits and the published report to retain 143. Separate
behavior tests cover a signal during a running child and a signal between
joins. A shell that does not retain the status is not a supported dispatcher
runtime.

Only after all children have been waited does the dispatcher render into the
exclusively created temporary file and atomically replace its own empty
reservation at `<report>`. The temporary file is unique to the invocation and
was created in the report directory during preflight; the reserved final target
is revalidated before publication, and the published report must be a regular
non-symbolic-link file. Failure is closed.
The report has
`schema_version`, `all_workers_waited: true`, and manifest-order worker records
containing `flight_id`, `attempt`, `runner_exit_code`, `run_dir`, and
`status_ref`, plus the batch-level `interrupted` boolean; both paths come from
the pure runner query. It is
dispatcher-owned orchestration evidence, not a worker artifact or a completion
or verdict source.

The dispatcher exits 0 only after every runner exits 0 and a complete report is
published. It exits 1 after publishing the report when any runner exits nonzero
or the dispatcher was interrupted. The two pre-launch classes are the exit 64
caller/manifest class and exit 1 internal class described above; neither
launches a worker or publishes a complete report. An internal failure after
launch exits 1 after joining every recorded child, but no complete report is
promised because rendering or publication itself failed. It has no retry, fallback,
identity-selection, result-reading,
completion-recognition, or lifecycle authority. It covers only the Codex
subset of a multi-seat stage, never the reserved non-Codex seats and therefore
never the whole stage.

## One-Shot Status Read

```text
bash <skill-root>/scripts/read-codex-worker-status.sh \
  --manifest <absolute-path>
```

This command accepts the batch manifest shape but validates only the identity
fields it consumes; run-only values are not reinterpreted. For each manifest
worker in order it obtains `status_ref` through the pure runner query and makes
one filesystem read when that reference is a regular non-symbolic-link file.
There is no loop over time, delay, retry, glob, watch, process inspection, or
log parsing.

The command emits one strict JSON object with `schema_version` and a `workers`
array. Each item contains `flight_id`, `attempt`, `status_present`, and
`status_document`. `status_present` reports only whether a regular
non-symbolic-link file existed during that one pass. When present, the parsed
runner document is embedded with semantic JSON equality as `status_document`;
byte-for-byte whitespace and object-key formatting are not preserved. When
absent, that field is `null`. The present/absent file fact is required. What is
forbidden is any derived completion or lifecycle boolean, lifecycle inference,
verdict checking, or `reason_code` remapping. Absence is ambiguous and carries
no process-state meaning.

This is an after-terminal collection convenience. It may be called only after
host completion notification. Calling it repeatedly while a runner is active
violates the caller-side no-polling rule. A completed single pass exits 0 even
when files are absent; usage or manifest failure exits 64 and internal failure
exits 1.

## Whole-Flight Cleanup

```text
bash <skill-root>/scripts/clean-codex-worker-runs.sh \
  --manifest <absolute-path> [--delete]
```

Cleanup accepts the batch manifest document shape: exactly one strict JSON
object with `schema_version: 1` and a non-empty `workers` array whose items have
exactly `flight_id`, `attempt`, `stage`, `work_target`, and `brief_ref`, plus
optional `sandbox`; identity pairs must be unique. Like the status reader, it
validates only the identity fields it consumes, including the shared projected
decimal `attempt` domain defined under `## Batch Dispatch`, and does not
reinterpret the run-only values. Missing or invalid options, a non-absolute or
non-regular manifest, and an invalid manifest document are `USAGE_ERROR` (exit
64).

Cleanup granularity is the whole flight directory, never one attempt. Removing
one attempt while siblings remain would allow a later launch of the same
identity to pass the runner's collision guard while a surviving flight record
still refers to the vanished attempt. Targets are only the distinct flight
directories returned for manifest identities by the runner's pure
`--project-flight` query. The same projection supplies the runner-owned
`sshx_root` and every attempt's number, `run_dir`, and terminal `status_ref`.
Cleanup derives no ancestry, naming pattern, or artifact basename.
There is no arbitrary-path option, whole-root option, age sweep, or retention
rule; those choices are caller or maintainer policy.

All named flights pass one all-or-nothing preflight before any deletion. A
flight is eligible only when the runner-published flight directory and sshx
root are mutually consistent before and after canonicalization, every
runner-published path component from that root through each attempt is
non-symbolic-link, at least one canonical attempt entry exists, and every
runner-published terminal status reference is a regular non-symbolic-link file.
These comparisons are consistency checks on values from the layout owner, not
an independently derived confinement boundary. The terminal file fact is the
only mechanical evidence available that runner cleanup ended and no carrier can
still be writing. Any ineligible flight makes the whole request fail before
deletion with exit 1 and a machine-readable per-flight reason. In every cleanup
document, `all_eligible`, `flights[].eligible`, and `flights[].reason` are the
immutable preflight snapshot: `reason` is null for a preflight-eligible flight
and otherwise names only its preflight ineligibility. The dry-run document then
has `mode: "dry-run"`, `all_eligible: false`, those per-flight snapshot records,
and `removed: []`. The delete document instead has `mode: "delete"`,
`all_eligible: false`, the same records extended with `state: "untouched"` and
`failure_reason: null`, plus `removed: []` and `failed: []`. No deletion has
begun in either shape. There is no force override.

Immediately before each individual removal, cleanup repeats that same runner
projection, canonical containment, attempt, symlink, terminal-status, and
entry-snapshot eligibility check and compares the projection with preflight. It
refuses the flight if the check fails or the projection changed. This narrows
the authorization window but does not eliminate TOCTOU between the repeated
check and `rm`, consistent with `## Threat Model`.

Dry-run is the default and exits 0 with `mode: "dry-run"`,
`all_eligible: true`, the per-flight eligibility records, and `would_remove`
naming exactly the eligible flight directories. Usage and internal failures
before a mode-specific document can be rendered emit no JSON. `--delete` grants
narrow, irreversible artifact-retirement authority for this invocation only:
it removes only flight directories returned by `--project-flight`. Every flight
in every `mode: "delete"` document has `state` equal to `removed`,
`partially-removed`, or `untouched` and has `failure_reason`, which is null when
no delete-phase failure is attributed to that flight and otherwise names only
the current eligibility, interruption, removal, or reporting failure. The
preflight `reason` never changes meaning after deletion starts. Every delete
document also has `removed` and `failed` arrays. A successful report names the
exact `removed` paths and has an empty `failed` array. After a failed removal,
absence means `removed`; otherwise cleanup compares an immediate
filesystem-entry count with the count taken just before `rm` to distinguish
`untouched` from `partially-removed`. A `failed[]` record carries `flight_id`,
`flight_dir`, `state`, and `failure_reason`; an already absent flight is also
included in `removed`.

The repeated projection equality check maps a false comparison to
`failure_reason: "FLIGHT_CHANGED"`. Any other nonzero `jq` status maps to
`failure_reason: "PROJECTION_COMPARE_FAILED"`; both refuse deletion, but the
latter makes no factual claim that the flight changed.

Before entering the delete loop, cleanup installs `INT` and `TERM` handlers.
The first signal records interruption and changes both dispositions to ignore;
after the current operation returns, cleanup classifies its observed effect,
emits the exact fully removed subset, and exits 1. Signals are not forwarded to
`rm`. State accounting itself uses shell-owned arrays. The normal report
renderer uses `jq`, and every eligibility or removal failure after deletion
begins routes through that reporter. If normal rendering fails, including
because `jq` fails, the reporter uses a pure-Bash JSON fallback that does not
invoke `jq`. The fallback escapes quote, backslash, and C0 controls and passes
unsigned bytes at or above `0x80` through. For valid UTF-8 paths its document is
semantically equal to the `jq` document but is not necessarily byte-identical:
`jq` escapes U+007F as `\u007f`, while the fallback emits the raw `0x7F` byte.
For a non-UTF-8 path the fallback passes invalid high bytes through, so its
document is not decodable as UTF-8 JSON; it deliberately does not imitate
`jq`'s U+FFFD substitution, which could name a path that was never removed. If
stdout is unwritable, the same strict JSON document is attempted on stderr.
Any stdout publication failure exits 1 whether or not that stderr attempt
succeeds; when stderr is writable, its document carries the delete states and
exact fully removed subset. A successful dry-run or complete delete exits 0
only after its stdout document is published. Ineligibility and internal failure
also exit 1. All-or-nothing applies to preflight, not to rollback after deletion
begins. Cleanup makes no
independent confinement claim: it never constructs a target above or below the
runner-returned flight directory. It has
no process-teardown or host-lifecycle authority. A runner that never publishes
terminal status intentionally leaves its flight ineligible.

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

`status.json` and the batch report are mechanical projections only. Neither is
a completion or verdict source under `SKILL.md`.

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
Callers that need structured output must read `status.json`; a Unicode-aware
splitter such as Python `str.splitlines()` may treat U+2028, U+2029, or U+0085
inside a path as logical separators even though those bytes are valid under
the POSIX text-line contract.

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
races, including caller-brief replacement after the dispatcher's readability
probe or flight changes after cleanup's repeated eligibility check; those checks
narrow their respective windows but do not make object identity immutable. It
also does not defend an active `setsid` escape, forged runner artifact paths, or
deliberate replacement of files inside the owned attempt directory. An
untrusted carrier or a requirement to cover those attacks requires a new design
review rather than more checks in this runner.

## Boundaries

The runner has no git, GitHub, label, release, host lifecycle, cleanup, or
global-state authority. Time limits and whole-job teardown belong to the
caller harness. Power-loss durability is not guaranteed. Deletion authority
lives only in `clean-codex-worker-runs.sh` and is bounded to terminal-only,
whole-flight artifact retirement with dry-run default and no force override.

No other skill may depend on these mechanisms. To reverse the exception
completely, use this one recipe:

1. Delete `scripts/run-codex-worker.sh`,
   `scripts/run-codex-worker-batch.sh`,
   `scripts/read-codex-worker-status.sh`, and
   `scripts/clean-codex-worker-runs.sh`.
2. Delete this specification, `tests/test_run_codex_worker.py`, and
   `tests/test_codex_worker_tools.py`.
3. Restore the narrow `SKILL.md` clauses to direct caller dispatch with
   caller-assigned artifact paths and a prompt-only boundary that forbids every
   helper script.
4. Remove mechanical-script assertions from `tests/test_sshx_contract.py` and
   run the remaining sshx contract suite. No compatibility shell is retained.

A new design review is required before adding daemon behavior, lifecycle
authority, a second consuming skill, or completion semantics cease to be
isomorphic to `SKILL.md`'s `## Worker Completion Contract`.
