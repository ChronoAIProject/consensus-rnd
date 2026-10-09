# Heca Worker Mechanical Specification

## Baseline evidence

Recorded before implementation on 2026-10-09 with heca CLI/daemon
`0.1.0-nightly-20260929-1`, provider `codex`, mode `full-access`:
raw `heca run --json` acknowledged agent
`01a120f1-b0ed-70c3-b3cd-40774484c87e` immediately, while its turn was running,
without any sshx result or sentinel. A single `heca --json agent wait --state idle`
with no timeout option exited 0 with `timed_out: true` and `agent.state: running`.
Stopping that same agent and waiting for `exited` returned `timed_out: false`
and the matching agent in `exited`. This widens the existing wrong-convergence
baseline: spawn acknowledgments and successful wait-process exits are not worker
completion. Caller evidence E3 separately observed exit 0 on timeout for an archived
agent. Fakes supplement these observations; they do not establish external capability.

Before R1 repair, the omitted-duration runner wait also cut off seat-length turns:
implementation flights `6ac8f34a23fc7d56496facbb` and `6ac8f8c5b9f8d9216b4bf0c4`
ended WAIT_NOT_TERMINAL after 30.45 s and 30.18 s; reviewer flight
`6ac900e64054b01ce514bf25` requested a read-only 65-second command and ended
WAIT_NOT_TERMINAL at 30.63 s with no artifacts. Existing successful turns lasted
9–18 s. This is ordinary-operation failure of terminal observation, absorbed by
the same baseline class, not evidence that the carrier can carry realistic seats.

## Scope and invocation

This is the single mechanical specification for the seventh closed-set script,
`skills/sshx/scripts/run-heca-worker.py`. The other six scripts remain owned by
`CODEX_WORKER_SPEC.md` and `ORACLE_WORKER_SPEC.md`. The sole carrier-independent
completion definition is `SKILL.md`'s `## Worker Completion Contract`; no status
or event grants verdict authority. This script is a transport for one fresh
heca agent and one turn, not a heca orchestration skill.

```text
python3 <skill-root>/scripts/run-heca-worker.py --check \
  --provider codex --mode full-access
python3 <skill-root>/scripts/run-heca-worker.py \
  --provider <verified-provider> --mode <verified-unattended-mode> \
  [--thinking low] \
  --stage <thinking|implementation|review|termination> \
  --work-target <absolute-path> \
  [--flight-id <returned-id> --attempt <positive-integer>]
```

The host explicitly supplies provider and permission mode. The verified unattended
bindings are `codex` / `full-access` and `claude` / `bypassPermissions`; every other binding, including prompt-capable
modes, is rejected before any receipt or side effect. Other provider/mode combinations
are ASSUMED-UNVERIFIED and are not exposed as supported. Model identity is observed
per invocation, never a diversity guarantee. The optional explicit thinking input
accepts only `low`, forwarded unchanged; omission sends no thinking argument. CLI
acceptance and forwarding do not verify effective provider reasoning. The prior
Codex proof requested `low` but the daemon reported `thinking_option: x_high`;
the Claude proof reported `low`. Effective thinking behavior remains
ASSUMED-UNVERIFIED, with no promise that a requested value is honored. Status
`thinking` records the requested input; the opaque terminal observation retains
the daemon's report. No Settings or
`~/.heca/orchestration-preferences.json` default is read as a production source.
Missing, duplicate, unknown, positional or incompatible options, a relative target,
an unknown stage, and projection-rejected identity are usage errors (exit 64).
Identity options appear together or are omitted together. Omission mints a fresh
Codex-shaped identity and attempt 1; the layout owner's projection alone validates
identity and supplies every path. There is no artifact-path, retry, timeout,
continuation, batch, or arbitrary heca-command option.

## Non-mutating detection

`--check` reads only `heca --json daemon status` and `heca --json providers list`,
each at most once. Detection requires exit 0 and structured daemon version,
plus an enabled provider with a nonempty, non-`unavailable` status and the explicit
mode in its mode list. The binding must also be in the verified unattended set.
PATH and `HECA_*` presence are insufficient. The check reads no stdin and creates
no attempt directory or agent. It prints `{detected, provider, mode}` and exits
0 for detected or 1 for unavailable. The caller records this once before dispatch;
attempt invocations never repeat it. Availability is fallible, not a guarantee
of a later successful provider turn. Later failures use existing bounded fallback.

## Receipt, paths and ownership

`run-codex-worker.sh --project-paths` owns identity validation, layout and all
paths. An attempt prints that projection byte for byte as its first and only
stdout line, flushing before directory creation, stdin read or any heca call.
A failed receipt delivery creates nothing. The attempt directory is claimed by
atomic mkdir; collision never reuses or writes an existing attempt. Missing
parents use private permissions (umask 077). Projection/collision failures have
only stderr diagnostics and no agent side effect.

Projected `brief_ref` retains stdin byte for byte. The brief must be UTF-8;
the runner appends the worker-owned artifact contract to the submitted prompt.
The worker itself writes strict stage-valid `SshxResultEnvelope` JSON at projected
`result_ref`, publishing through a same-directory temporary and atomic rename,
then similarly publishes projected `completion_sentinel_ref`. The runner only
reads these artifacts after terminal evidence. It never writes, touches, repairs,
normalizes, copies or derives them from event text. Result and sentinel must be
regular non-symlink files. JSON must have exactly `conclusion` (an object) and
`log_ref` (a nonempty string), with no duplicate keys or non-JSON constants.
Thinking verdicts are propose/revise/reject/abstain; review verdicts
approve/comment/reject; termination verdicts satisfied/unsatisfied/abstain;
implementation requires no verdict. These sets follow `SKILL.md` and are checked
for cross-artifact consistency by the contract tests.

Projected `log_refs.last_message` stores the daemon's spawn response atomically,
including its agent identity, before any blocking terminal observation. It is an
identity receipt, never a reconstructed worker answer. Projected stdout/stderr
logs retain opaque carrier diagnostics; no reasoning, Text or tool-call content
is interpreted into a conclusion. Projected `carrier_exit_ref` is unused.

## Explicit placement and closed calls

A dispatch reads `heca --json worktree list` once and selects an existing,
non-archived worktree whose canonical path equals `work_target`. It calls exactly
one `heca --json run --provider P --mode M --worktree-id ID --prompt BRIEF`.
No matching placement is `PLACEMENT_UNAVAILABLE`, never implicit cwd, Loose,
creation or registration. Spawn must return an agent identity, `loose: false`
and the selected worktree id. A single `heca --json agent status AGENT` verifies
returned cwd, worktree, provider and mode. That snapshot is never completion.
A rejected placement is never retried without placement.

Other attempt calls are only the spawned agent's structured
`wait --state idle --timeout-ms 4294967295`,
and, on failure/interruption, `kill` followed by structured
`wait --state exited --timeout-ms 4294967295`. Every call uses explicit agent identity. The runner never
creates worktrees/workspaces, starts/stops/restarts daemons, archives/deletes
agents, opts into MCP servers, forwards generic commands, or uses heca
team/committee/epic/loop orchestration. It has no git, GitHub, release, account,
provider-settings or host-configuration authority.

## Terminal observation and agent lifetime

One blocking `heca --json agent wait --state idle --timeout-ms 4294967295 AGENT` must exit 0,
return `timed_out: false`, and match identity, cwd, worktree, provider, mode and
idle state. This is the daemon's positive wait observation for the freshly spawned
one-turn agent, not an idle status snapshot. Then the worker artifacts validate.
This is the mechanical projection of the shared completion predicate, not a
different completion definition. No logs are opened to reconstruct results or
completion. Spawn acknowledgment, last Text, a status snapshot or wait exit 0
alone cannot supply terminal evidence.

The CLI's omitted-duration wait has an observed approximately 30-second daemon
default. Both blocking observations explicitly use the CLI's maximum duration,
`4294967295` ms (u32 maximum, approximately 49.7 days); the live CLI rejects
`4294967296` before contacting the daemon. This overrides the short default, not
a tuned seat deadline or runner give-up policy. Host time limits remain external;
the runner has no timer/polling loop and never re-arms a timed-out wait or re-checks
status or logs. Every `timed_out: true` reply still fails closed and requires confirmed
own-agent stop before fallback. No indefinite or arbitrary-duration capability
is claimed; only the seat-length live proof below establishes duration support.
Provider-error and permission-prompt
shapes are ASSUMED-UNVERIFIED and are not supported success evidence. Other
bindings, permission-capable modes and streaming terminal observation are not
supported options. A `logs --follow` experiment did not deliver live state changes
through either pipe or PTY on this nightly build; it is not used by this route.

On any failure or caught TERM/INT the route stops only its own recovered agent,
then calls wait for exited once. It confirms exit only from exit 0, `timed_out:
false`, the exact agent id and state exited. A kill acknowledgment, CLI teardown
or runner exit alone cannot confirm this. Caught signals are deferred while spawn
is acknowledging/persisting identity and during the one cleanup operation;
otherwise they interrupt the blocking client. An inherited ignored INT stays
ignored. SIGKILL or a lost spawn acknowledgment cannot be repaired by this process:
the saved identity, when available, lets the caller stop only that agent and obtain
structured confirmation out of band. With no recoverable identity or no confirmed
stop, no terminal status is written; the flight and target remain protected and
the caller reports the unresolved outcome. No retry/fallback/replacement is
permitted before confirmation. Heca agents are daemon-owned and killing the runner
is not agent teardown.

## Atomic terminal status

Projected status appears once, by temporary write and atomic rename, only after
terminal turn completion with artifacts or confirmed agent exit (or pre-spawn
failure). It contains schema_version, flight_id, attempt, stage, status,
reason_code, run_dir, brief_ref, work_target, provider, mode, thinking, agent_id,
agent_identity_ref, agent_not_running, terminal_observation and log_refs.
Only COMPLETE includes result_ref and completion_sentinel_ref. There is no verdict
field. Status and runner exit are orchestration projections, never verdict authority.
Exit 0 is COMPLETE, 64 usage error, and 1 anything else. A missing status protects
the flight; do not retire it merely because its runner is gone.

Reasons are COMPLETE, HECA_UNAVAILABLE, PLACEMENT_UNAVAILABLE, PLACEMENT_INVALID,
SPAWN_UNCONFIRMED, BRIEF_INVALID, WAIT_NOT_TERMINAL, RESULT_MISSING, ENVELOPE_INVALID,
VERDICT_INVALID, SENTINEL_MISSING, INTERRUPTED and INTERNAL_ERROR. Projection,
collision and usage failures happen before ownership. STOP_UNCONFIRMED is an
explicit stderr diagnostic for a protected flight with no terminal status.

## Reversal and verification

To reverse the exception completely:

1. Delete `scripts/run-heca-worker.py`, this specification and
   `tests/test_run_heca_worker.py`.
2. Remove the heca carrier, detection, composition branch, launch and lifetime rules,
   and this spec's references from SKILL.md; return to six scripts and two specs.
   Codex, oracle and subagent behavior then uses the retained heca-absent branch.
3. Remove heca from formal carriers, runner and lifetime guards; retain the
   pre-heca correspondence proofs as regression evidence and retrace changed clauses.
4. Remove heca assertions from `tests/test_sshx_contract.py`, then run the remaining
   suite and formal build. No compatibility shell is retained.

A new design review is required before adding bindings without real verification,
retry, continuation, placement creation, runner-chosen deadline, polling, generic forwarding or
another completion route. `tests/test_run_heca_worker.py` exercises the real script
with a closed-route fake heca, including receipt ordering, placement failures,
terminal evidence, atomic worker ownership, signals and unconfirmed-stop protection.
The fake models the observed 30000 ms default, explicit u32 wait duration and
turn duration in logical milliseconds. Its five-minute-turn test failed against
the pre-R1 runner with WAIT_NOT_TERMINAL, and requires COMPLETE with exactly one
maximum-duration idle wait after repair. A delayed stop test likewise requires
one maximum-duration exited wait; timeouts never re-arm terminal observation.

## Real end-to-end evidence

Verified on 2026-10-09, heca CLI and standalone daemon
`0.1.0-nightly-20260929-1`. Placement was an existing caller-authorized worktree,
passed explicitly and validated by id/cwd; every worker kept the checkout read-only.
The pre-R1 short review-stage proofs executed `git rev-parse --show-toplevel HEAD` and
published strict JSON envelopes and atomic sentinels themselves. Results carried
`approve`, the git outputs and transport-only scope, with the actual projected
stdout log as opaque `log_ref`. Positive waits returned matching agent, idle,
`timed_out: false`; runner statuses were COMPLETE. The interruption proof sent
TERM to the runner during a requested sleep turn; only its own agent was killed,
and the matching exited/non-timed-out wait preceded NOT_COMPLETE publication.

| Flight (attempt 1) | Daemon-issued agent | Observation |
|---|---|---|
| raw baseline (no flight) | `01a120f1-b0ed-70c3-b3cd-40774484c87e` | spawn running; wait timeout; stopped and confirmed exited |
| `6ac8f34a23fc7d56496facbb` | `01a120f6-5b22-7871-9e30-2e70f6a3e059` | codex/full-access; wait timeout; NOT_COMPLETE, confirmed exited |
| `6ac8f4b60e83b1a20fd058dd` | `01a120fb-e9bd-7803-b0cb-8902a952e2cf` | discarded pipe subscription experiment; INTERRUPTED, confirmed exited |
| `6ac8f73aaddfad5813fc694b` | `01a12105-bcfd-78d0-8e59-6db5bb2a6b40` | discarded PTY subscription experiment; INTERRUPTED, confirmed exited |
| `6ac8f8c5b9f8d9216b4bf0c4` | `01a1210b-c334-7f33-ac8c-34458eae1365` | codex/full-access/low; wait timeout; NOT_COMPLETE, confirmed exited |
| `6ac8f931519097c2746be1b8` | `01a1210d-697f-7cc3-a341-dbe6f1ec642e` | claude/bypassPermissions/low; COMPLETE, initial structural capture |
| `6ac8f99e65e1eb4c5552ca91` | `01a1210f-14a7-7ff3-84d4-95b4fcdf5bbd` | claude/bypassPermissions/low; mid-turn TERM; NOT_COMPLETE, confirmed exited |
| `6ac8f99f50b7101fb9eea452` | `01a1210f-18dd-78e3-9b9a-cf293303654a` | codex/full-access/low; COMPLETE, initial structural capture |
| `6ac8fa6a8bd90bcd10556369` | `01a12112-3221-7a33-8cfb-6d8d67667e3d` | codex/full-access/low; COMPLETE, final envelope and sentinel validated |
| `6ac8fb78d0b2ad12e6bb1773` | `01a12116-4efc-7783-bcb1-6a82c4766d25` | claude/bypassPermissions/low; COMPLETE, final envelope and sentinel validated |

The initial structural captures used a nonempty diagnostic label; the final two
pre-R1 proofs use an actual projected log reference. In the table, `/low` denotes
requested thinking, not verified effective reasoning; Codex reported `x_high`.
Artifacts remain private under the
layout owner's projected run root; only coordinates are retained here. Idle agent
records are host-owned housekeeping, not permission to archive/delete them.

R1 repair was verified on the same date, nightly CLI/daemon and explicit placement,
using `claude` / `bypassPermissions` with requested thinking `low` (daemon report
`low`, no claim about effective provider reasoning). Each flight ran the real
`run-heca-worker.py --stage implementation --work-target <authorized-checkout>`.
Both structured waits requested `--timeout-ms 4294967295`; no wait was re-armed.
The long worker executed real `sleep 310` followed by
`git rev-parse --show-toplevel HEAD`, keeping the checkout read-only, then
atomically published its valid result and sentinel. Its recorded sleep was
310.012 s; daemon creation-to-terminal timestamps span 325.862 s, and the runner
flight elapsed 326.009 s. The short worker ran the same repository command and
also published valid artifacts. The interrupted runner received host TERM at
approximately +12 s during a requested 120-second turn; the matching exited wait
confirmed its own agent not running before NOT_COMPLETE, with no worker artifacts.

| R1 flight (attempt 1) | Daemon-issued agent | Elapsed seconds | Observation |
|---|---|---|---|
| `6ac903650161c5a592d9add5` | `01a12135-44c8-7131-9ffc-2dcf9a7ad92f` | 326.009 | COMPLETE; idle, timed_out false; worker result and sentinel validated |
| `6ac9037af869a3cfa907230d` | `01a12135-9544-7043-af39-332e017d489a` | 13.246 | COMPLETE; idle, timed_out false; worker result and sentinel validated |
| `6ac9037a2b0de1e506fde4ab` | `01a12135-9544-7043-af39-33346fb83911` | 12.772 | INTERRUPTED; NOT_COMPLETE after exited, timed_out false |

These are all three agents spawned during R1. The live proof establishes a turn
longer than five minutes for this binding on this daemon. Durations beyond this
observation, including a full maximum-duration wait, remain ASSUMED-UNVERIFIED;
CLI acceptance of the maximum alone is not such evidence.
