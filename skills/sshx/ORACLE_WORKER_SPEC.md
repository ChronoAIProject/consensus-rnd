# Oracle Worker Mechanical Specification

This is the single mechanical specification for the closed-set script
`skills/sshx/scripts/run-oracle-worker.py`, the only route by which the `nyxid-oracle`
worker carrier reaches NyxID. One invocation owns exactly one attempt through the NyxID
oracle broker (the NyxID service `oracle`): capability check, pool choice, request, one
streamed call, assembly, terminal decision, and the attempt's artifacts. The legacy
`nyxid oracle` CLI is not a route of this carrier and the runner never invokes it.

The completion predicate is defined once in `SKILL.md` under
`## Worker Completion Contract`. Caller-side dispatch, retry, fallback, conversation
isolation, and collection are governed by `SKILL.md` under `## Worker Delegation`. The run
layout, the identity shape, and the read-only path projection are owned by
`CODEX_WORKER_SPEC.md` (`## Invocation` and `## Run Directory`). This file references
those rules and does not restate them. It carries the broker mechanics this carrier needs
from the Chrono `oracle-broker` skill (version 1.3, verified end to end on 2026-10-07), so
no other skill is loaded or depended on.

## Invocation

```text
python3 <skill-root>/scripts/run-oracle-worker.py \
  [--flight-id <returned-id> --attempt <positive-integer>]
```

The brief is read from stdin. Identity options appear together or both are omitted, under
the same pending and bound rule as the Codex runner: omission mints a fresh identity in the
Codex runner's identity shape with attempt 1, and a bound retry passes the receipt's
identity with the next attempt. The runner checks an identity only through the Codex
runner's `--project-paths` query, so the shape check and every attempt path come from that
one projection and the runner holds no layout formula. There is no pool, model, timeout, or
conversation option. A missing, duplicate, unknown, or positional argument, or an identity
the projection rejects, is `USAGE_ERROR` (exit 64) with no receipt, directory, or call.

## Receipt and Attempt Directory

Before any directory write, stdin read, or broker call, the runner prints the projection
for its identity as its first and only stdout line. That receipt is byte-identical to the
Codex runner's `--project-paths` output for the same identity and environment. A receipt
that cannot be delivered ends the invocation with exit 1 and creates nothing.

The runner then creates the projected attempt directory with one atomic `mkdir`, creating
missing parent directories with the same private mode. An existing attempt directory is
`RUN_DIR_COLLISION`: exit 1, no broker call, and nothing written into it. Failures before
the runner owns the attempt directory (`PROJECTION_FAILED`, `RUN_DIR_UNAVAILABLE`,
`RUN_DIR_COLLISION`, `INTERNAL_ERROR`) are reported on stderr only.

Inside its attempt directory the runner uses these projected references and adds only its
own request file, `request.json`:

- `brief_ref`: the stdin brief, byte for byte;
- `log_refs.stdout`: the broker call's stdout, which is the saved raw response stream;
- `log_refs.stderr`: the broker call's stderr;
- `log_refs.last_message`: the compact final payload, written only on completion;
- `status_ref`: the terminal status.

It never writes `result_ref`, `completion_sentinel_ref`, or `carrier_exit_ref`. The caller
writes the canonical envelope at collection under `SKILL.md`.

## Allowed Calls

The runner calls `nyxid` for exactly two broker routes, each at most once per invocation,
with the attempt directory as working directory:

- the capability check, `nyxid proxy request oracle api/v1/oracle/pools --output json`,
  which is non-mutating;
- the dispatch, `nyxid proxy request oracle api/v1/oracle/openai/v1/chat/completions
  --method POST --data @request.json`, which opens a new ChatGPT conversation and is
  therefore not claimed non-mutating.

Every other route and subcommand is forbidden, including the legacy `nyxid oracle` CLI in
every form; the broker task API and its `?wait=` loop, which is status polling; task
cancel, conversation attach, page extract, and generic jobs; pool and worker management;
session close and every multi-turn continuation; and file or image attachments. The runner
has no internal retry, transport switch, pool failover, timeout, background process,
polling, or multi-turn behavior. An attempt that does not complete follows the retry and
fallback path in `SKILL.md`.

## Pool Choice

The capability check passes when the listing exits 0, parses as JSON, and its `pools` list
holds a pool with `is_active: true` and an integer `online_workers` greater than 0. The
runner chooses the active pool with the most online workers; among equals, the earlier
listed pool wins. No pool slug is configured or built in. The `pools/POOL/workers` route
is owner-only and is not the check.

## Request

The request body is `{"model": "oracle/<pool>", "stream": true, "messages": [{"role":
"user", "content": <brief>}]}` with the brief as one text message. The brief must be UTF-8.
The body carries no `metadata` key, so every submission opens a fresh ChatGPT
conversation and parallel seats receive disjoint conversations. It always sets
`"stream": true`: Cloudflare in front of NyxID cuts a silent request after 100 seconds,
and the stream's keep-alive lines prevent that. A Pro answer usually takes 1 to 5 minutes;
the runner keeps the call open with no time limit of its own, and time limits remain the
caller harness's responsibility.

## Terminal Decision

After the broker call exits, the runner reads the saved stream once. Lines end in LF or
CRLF, and only LF separates lines, so a separator such as U+2028 inside a JSON string stays
inside it. A line beginning `data:` carries one JSON chunk or the value `[DONE]`; every
other line, such as a blank line or a `: keep-alive` comment, is skipped. The attempt is
complete exactly when every condition below holds:

1. the `nyxid` process exited 0;
2. every data line other than `[DONE]` parses as a JSON object whose `choices` entries
   carry string or null `delta.content` and `finish_reason`, with an optional `oracle`
   object;
3. no chunk carries an `error` key;
4. a line whose whole value is `[DONE]` is present, so text inside a reply that quotes it
   does not count;
5. the last `finish_reason` any chunk reports is `stop`;
6. the final JSON chunk carries an `oracle` object whose `task_id` is a non-empty string.

Every other shape is not complete. The first failed condition in this order names the
reason code. The compact final payload is the concatenated `delta.content` text, written
byte for byte as UTF-8 and kept separate from the raw stream.

## Status

The status file at the projected `status_ref` appears only once the attempt is terminal:
after the broker call has exited, or when no call was started, it is written to a
temporary file in the attempt directory and renamed into place. Its existence is the
terminal fact that `clean-codex-worker-runs.sh` relies on. A runner ended by a signal
publishes no status: `TERM` and `INT` end it at once, except that an `INT` the host started
it with ignored stays ignored. Cleanup then refuses that flight and the inactivity sweep
retires it; tearing down a broker call that outlives the runner belongs to the host.

The status is a JSON object with `schema_version`, `flight_id`, `attempt`, `status`
(`COMPLETE` or `NOT_COMPLETE`), `reason_code`, `run_dir`, `brief_ref`, and these fields,
each null until its step ran: `pool`, `request_ref`, `carrier_exit`, `raw_response_ref`,
and `stderr_ref`. Only a `COMPLETE` status also has `payload_ref`, the compact final
payload; `completion_sentinel_ref`, which is `broker-task:<task_id>` from the final
chunk's broker-issued `task_id`; and `model`, which copies `model_label`,
`observed_model_switcher`, and `observed_model_effort` verbatim from that chunk's `oracle`
object, each null when absent. A model identity is evidence only for that invocation. The
status has no verdict and makes no claim that any result envelope is valid.

| `reason_code` | Meaning |
|---|---|
| `COMPLETE` | every terminal-decision condition holds |
| `BRIEF_INVALID` | the brief is not UTF-8; nothing is sent |
| `BROKER_UNAVAILABLE` | `nyxid` is missing, the listing fails or is not JSON, or no active pool has an online worker; nothing is sent |
| `CARRIER_EXIT_NONZERO` | the broker call exited nonzero; an HTTP error exits 1 with a JSON error on stderr |
| `STREAM_MALFORMED` | a data line does not parse |
| `BROKER_ERROR` | a chunk carries `error` |
| `STREAM_NOT_TERMINAL` | the stream is truncated before `[DONE]` or its last finish reason is not `stop` |
| `TASK_ID_MISSING` | the final chunk has no broker task id |
| `INTERNAL_ERROR` | a runner file operation failed |

Exit 0 means the attempt completed, 64 means a usage error, and 1 means anything else.
The exit code and the status are mechanical projections, never a completion or verdict
source; stderr is a human diagnostic log whose write failures change nothing. The caller
reads `status_ref` once after host completion notification; on completion,
`payload_ref` is the directly surfaced compact final payload, `raw_response_ref` is the
runner-saved raw response, and `completion_sentinel_ref` is the completion evidence.

## Broker Diagnostics

These help a human diagnose a failed attempt. They never select a retry, a pool, or a
carrier.

| HTTP status | Meaning |
|---|---|
| 400 | Bad request body |
| 401 | Not logged in, or the login expired; the maintainer runs `nyxid login` |
| 403 | No access to the pool, or Cloudflare 1010 |
| 404 | Unknown pool |
| 429 | Per-user quota or full queue |
| 503 | Pool inactive |

A failed broker task reports `model_unavailable` or `usage_limit_reached` when no worker
could run Pro, and `prompt_delivery_uncertain` when the worker could not tell whether
ChatGPT received the prompt. The broker's prompt size limit is unverified. In a
2026-10-07 check the broker could not open a public GitHub URL, which is why a brief
inlines the content a worker needs.

## Boundaries

The runner has no git, GitHub, label, release, host lifecycle, cleanup, or NyxID account
authority. It reads the Codex runner only through the read-only `--project-paths` query
and never launches it. No other skill may depend on this mechanism. To reverse the
exception completely, use this one recipe:

1. Delete `scripts/run-oracle-worker.py`, this specification,
   `tests/test_run_oracle_worker.py`, and `tests/fixtures/oracle_broker_stream.sse`.
2. In `SKILL.md`, remove the oracle runner from the closed set, which returns to five
   scripts governed by `CODEX_WORKER_SPEC.md`, from the runner launch rule, and from the
   completion-contract and `log_ref` pointers. With no route left, `nyxid-oracle` fails
   every capability check and the existing fallback rule applies; the legacy CLI stays
   excluded.
3. In `formal/`, remove the oracle runner from the script list and the launch guard and
   re-trace the changed clauses.
4. Remove the oracle-runner assertions from `tests/test_sshx_contract.py` and run the
   remaining sshx suite. No compatibility shell is retained.

A new design review is required before adding a retry, pool failover, multi-turn
behavior, a second route, or completion semantics that cease to be isomorphic to
`SKILL.md`'s `## Worker Completion Contract`.
