# Patrol analysis codex

You analyze one patrol-private candidate signal and write a structured JSON decision.

## Candidate signal

```json
${PATROL_CANDIDATE_SIGNAL_JSON}
```

## Task

Decide whether this signal represents a real repository issue that should become a patrol-owned design issue.

Use raw evidence only as diagnostic context. Do not copy raw log lines, traceback blocks, prompt text, or fixture text into the public-facing fields. Treat clean worker completion, quoted examples, prompt instructions, self-crash text, and test fixture prose as not real unless the candidate contains an actual operational failure that still needs repository work.

## Output

Write exactly one JSON object to:

`${PATROL_ANALYSIS_OUTPUT_PATH}`

Required fields:

```json
{
  "is_real_issue": true,
  "summary": "short public summary",
  "severity": "low|medium|high",
  "root_cause": "public root-cause analysis without raw log lines",
  "recommendation": "public next step",
  "rationale": "why this is or is not a real issue"
}
```

Use `is_real_issue=false` when the signal is only prompt/fixture/prose noise or lacks a clear repository-owned failure. Still fill every string field with a concise explanation.
