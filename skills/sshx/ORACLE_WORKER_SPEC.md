# Oracle Worker Transport Specification

This is the single mechanical specification for the two NyxID transports of the
`nyxid-oracle` worker carrier:

- the oracle broker, the NyxID service `oracle`, reached through
  `nyxid proxy request oracle ...`;
- the legacy `nyxid oracle` CLI.

The completion predicate is defined once in `SKILL.md` under
`## Worker Completion Contract`. Transport order, retry accounting, conversation
isolation, collection, and recording are governed by `SKILL.md` under
`## Worker Delegation`. This file references those rules and does not restate them.
It carries the mechanics this carrier needs from the Chrono `oracle-broker` skill
(version 1.3, verified end to end on 2026-10-07), so no other skill is loaded or
depended on.

## Allowed calls

Each transport has exactly two allowed calls: one non-mutating capability check and
one dispatch per attempt. Every other route and subcommand is forbidden, including:

- the broker task API and its `?wait=` loop, which is status polling;
- task cancel, conversation attach, page extract, and generic jobs;
- pool and worker management: pool create or change, token rotation, worker
  commands, and forgetting workers;
- session close and every multi-turn continuation;
- file and image attachments;
- `nyxid oracle login`, `login-profile`, `worker`, `cancel`, `close-session`,
  `attach`, and `extract`, and every `nyxid oracle pool` subcommand except `list`.

## Pool selection

Each transport routes to one pool chosen at its capability check: the pool the host or
maintainer names for that transport; otherwise the listed pool that is active and has
the most online workers. Pool slugs are transport-local: the same slug can name
different pools on the two transports, so a slug chosen for one transport never
selects a pool on the other. The caller records the chosen pool with the attempt's
transport in `worker_delegation.reason`.

## Broker transport

### Capability check

```bash
nyxid proxy request oracle api/v1/oracle/pools --output json
```

The broker is available when this exits 0 and the selected pool in the returned
`pools` list has `is_active: true` and `online_workers` greater than 0. The
`pools/POOL/workers` route is owner-only and is not the check.

### Dispatch

Keep each attempt's files in its own directory outside the `work_target`; never
reuse a directory across attempts. Build the request from the brief file, so quoting,
newlines, and non-ASCII text never break the JSON body:

```bash
python3 -I - brief.md POOL > request.json <<'PY'
# sshx:oracle-broker-request
import json
import sys

brief_path, pool = sys.argv[1], sys.argv[2]
with open(brief_path, encoding="utf-8") as handle:
    brief = handle.read()
json.dump(
    {"model": f"oracle/{pool}", "stream": True, "messages": [{"role": "user", "content": brief}]},
    sys.stdout,
    ensure_ascii=False,
)
PY
```

The body carries no `metadata` key, so every submission opens a fresh ChatGPT
conversation; parallel seats therefore receive disjoint conversations. It always sets
`"stream": true`: Cloudflare in front of NyxID cuts a silent request after 100
seconds, and the stream's keep-alive lines prevent that. One brief goes in one call,
as text only.

Send it as one host-tracked background job that notifies the caller when it exits;
never background it with shell `&`, and never read `stream.sse` while it runs. A Pro
answer usually takes 1 to 5 minutes; keep the call open.

```bash
nyxid proxy request oracle api/v1/oracle/openai/v1/chat/completions \
  --method POST --data @request.json > stream.sse
```

### Assembly

After the job exits 0, assemble the stream once:

```bash
python3 -I - stream.sse meta.json > answer.txt <<'PY'
# sshx:oracle-broker-assemble
import json
import sys

stream_path, meta_path = sys.argv[1], sys.argv[2]
parts, oracle, finish_reason, done = [], {}, None, False
with open(stream_path, encoding="utf-8") as stream:
    for number, raw in enumerate(stream, start=1):
        line = raw.strip()
        if not line.startswith("data: "):
            continue  # blank lines and ": keep-alive" comments
        if line == "data: [DONE]":
            done = True
            continue
        try:
            chunk = json.loads(line[len("data: "):])
        except json.JSONDecodeError as error:
            sys.exit(f"broker stream line {number} is not JSON: {error}")
        if "error" in chunk:
            sys.exit("broker error: " + json.dumps(chunk["error"], ensure_ascii=False))
        for choice in chunk.get("choices", []):
            parts.append((choice.get("delta") or {}).get("content") or "")
            finish_reason = choice.get("finish_reason") or finish_reason
        oracle = chunk.get("oracle") or oracle
task_id = oracle.get("task_id")
if not done or finish_reason != "stop" or not task_id:
    sys.exit(f"broker stream not terminal: done={done} finish_reason={finish_reason} task_id={task_id}")
with open(meta_path, "w", encoding="utf-8") as meta:
    fields = ("task_id", "pool", "model_label", "observed_model_switcher", "observed_model_effort")
    json.dump({field: oracle.get(field) for field in fields}, meta, ensure_ascii=False)
sys.stdout.write("".join(parts))
PY
```

A broker attempt returned terminally only when the dispatch and the assembly both exit
0. Then `answer.txt` is the directly surfaced compact final payload that collection
interprets, `stream.sse` is the saved raw response, and the attempt's completion
evidence is `broker-task:<task_id>` from `meta.json`. `observed_model_switcher` and
`observed_model_effort` are the model identity reported for that invocation only.
Every other outcome is a failed attempt.

### Broker errors

The table and failure reasons help a human diagnose a failed attempt. They never
select a retry, a transport, or a carrier.

| Status | Meaning |
|---|---|
| 400 | Bad request body |
| 401 | Not logged in, or the login expired; the maintainer runs `nyxid login` |
| 403 | No access to the pool, or Cloudflare 1010 |
| 404 | Unknown pool |
| 429 | Per-user quota or full queue |
| 503 | Pool inactive |

A failed broker task reports `model_unavailable` or `usage_limit_reached` when no
worker could run Pro, and `prompt_delivery_uncertain` when the worker could not tell
whether ChatGPT received the prompt.

## Legacy transport

### Capability check

```bash
nyxid oracle pool list --output json
```

The legacy transport is available when this exits 0 and the selected pool is active
with at least one online worker.

### Dispatch

Send the same brief file as one host-tracked background job, with no
`--conversation`, `--new-conversation`, or `--no-wait`, so every ask is a fresh
one-shot conversation that blocks until its answer:

```bash
nyxid oracle ask POOL --file brief.md --output json > answer.json
```

A legacy attempt returned terminally only when the command exits 0 and `answer.json`
has `status` `completed`. Then its `response` string is the directly surfaced compact
final payload, `answer.json` is the saved raw response, and the attempt's completion
evidence is `legacy-task:<task_id>`. Its `observed_model_switcher` and
`observed_model_effort`, when present, are the model identity reported for that
invocation only; a 2026-10-07 legacy answer left both empty, which is no model evidence.
Every other outcome is a failed attempt.

## Known limits

Legacy prompts near 30 KB have failed with `extraction_failure`; the broker's size
limit is unverified. The legacy transport has opened SHA-pinned public GitHub URLs, but
in a 2026-10-07 check the broker answered that it could not open one. On the broker,
inline the content a seat needs, and expect any premise that depends on a URL to come
back `ASSUMED-UNVERIFIED` as `SKILL.md` requires.
