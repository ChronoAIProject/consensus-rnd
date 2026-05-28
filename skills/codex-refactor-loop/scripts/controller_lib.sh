#!/usr/bin/env bash
# controller_lib.sh — bash library for codex-refactor-loop controller actions
#
# Controller boilerplate bugs often come from manually repeating post/spawn/merge glue.
# (post_banner + spawn + commit + push + close issue + cleanup labels).
# This library centralizes those patterns to eliminate bash variable passing
# bugs, repeated sed usage, and worktree races.
#
# Usage (inside the controller):
#   source .claude/skills/codex-refactor-loop/scripts/controller_lib.sh
#   merge_pr <pr> <issue>   # PR <pr> merge + close <issue> + cleanup labels
#   safe_worktree iterN cluster-026 origin/auto-refact-dev
#   open_pr_with_label "iterN cluster-NNN: ..." pr-body.md auto-refact-dev
#
# ⟦AI:AUTO-LOOP⟧

set -u

if [ -z "${REPO_ROOT:-}" ]; then
  if [ "${ALLOW_GIT_ROOT_FALLBACK:-0}" = "1" ]; then
    REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
  fi
fi
if [ -z "${REPO_ROOT:-}" ]; then
  echo "FATAL: REPO_ROOT is unset; source .refactor-loop/host.env or set ALLOW_GIT_ROOT_FALLBACK=1 for interactive use" >&2
  return 2 2>/dev/null || exit 2
fi

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)/repo_slug.sh"
set_gh_repo_args 0 0 || return 2 2>/dev/null || exit 2
INTEGRATION_BRANCH="${INTEGRATION_BRANCH:-${INTEGRATION:-auto-refact-dev}}"
REVIEW_BASE_BRANCH="${REVIEW_BASE_BRANCH:-${REVIEW_BASE:-dev}}"

# Fixes "git worktree add already exists" — detect-or-create
# Usage: safe_worktree <iter> <cluster-id> <base-ref>
# Sets WT_PATH and BRANCH env vars on success.
# Refactor (iter4/skill-worktree-inside-repo): Old pattern: sibling `<repo>-wt-<name>/`. New principle: inside `<repo>/.worktrees/<name>/` + gitignored.
safe_worktree() {
  local iter="$1" cluster="$2" base="$3"
  WT_PATH="${REPO_ROOT}/.worktrees/iter${iter}-${cluster}"
  BRANCH="refactor/iter${iter}-${cluster}"
  if [ -d "$WT_PATH" ]; then
    echo "  ✓ worktree exists: $WT_PATH" >&2
    return 0
  fi
  mkdir -p "${REPO_ROOT}/.worktrees"
  if git -C "$REPO_ROOT" show-ref --quiet "refs/heads/$BRANCH"; then
    git -C "$REPO_ROOT" worktree add "$WT_PATH" "$BRANCH" 2>&1 | tail -2 >&2
  else
    git -C "$REPO_ROOT" worktree add -b "$BRANCH" "$WT_PATH" "$base" 2>&1 | tail -2 >&2
  fi
  export WT_PATH BRANCH
}

# Post-merge cleanup: merge PR + close linked issue + cleanup labels on both
# Usage: merge_pr <pr-number> [linked-issue]
record_recent_pr_merge_artifact() {
  # Refactor (iter1/issue-145):
  #   Old pattern: merge_pr 成功 merge 后未写 .refactor-loop/state/recent-pr-merges.json,导致 auto_release_gate 的 recent_pr_merges_min 信号永红(missing artifact),阻塞发版。
  #   New principle: 按 .refactor-loop/runs/phase9-issue145-r5-judge.md consensus(structural):保留 recent_pr_merges_min 信号;merge_pr 成功后由私有 writer append recent-pr-merges.json(sha/time/pr,滚动窗口),artifact-only,release gate 不新增 standalone telemetry。硬约束:不重建 REFERENCE.md;refactor 注释自含 Old/New 不用 see-issue placeholder;不超范围。
  local pr="$1"
  local fact_json=""
  local attempt
  for attempt in 1 2 3; do
    fact_json=$(gh pr view "$pr" "${gh_repo_args[@]}" --json number,mergedAt,mergeCommit,baseRefName,headRefName 2>/dev/null) || fact_json=""
    if RECENT_PR_MERGE_FACTS="$fact_json" RECENT_PR_MERGE_PR="$pr" python3 - <<'PY' >/dev/null 2>&1
import json
import os
import sys

try:
    facts = json.loads(os.environ["RECENT_PR_MERGE_FACTS"])
except Exception:
    sys.exit(1)
merge_commit = facts.get("mergeCommit")
if (
    facts.get("number") is None
    and not str(os.environ.get("RECENT_PR_MERGE_PR", "")).strip()
):
    sys.exit(1)
if not facts.get("mergedAt") or not isinstance(merge_commit, dict) or not merge_commit.get("oid"):
    sys.exit(1)
PY
    then
      break
    fi
    [ "$attempt" -lt 3 ] && sleep "${RECENT_PR_MERGE_RETRY_SLEEP_SECONDS:-1}"
  done

  RECENT_PR_MERGE_FACTS="$fact_json" RECENT_PR_MERGE_PR="$pr" REPO_ROOT="$REPO_ROOT" python3 - <<'PY'
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

def parse_time(value):
    if not isinstance(value, str) or not value:
        return None
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)

def isoformat(value):
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

try:
    facts = json.loads(os.environ["RECENT_PR_MERGE_FACTS"])
except Exception:
    raise SystemExit("merge_pr: recent-pr-merges projection failed: invalid PR facts; recover by writing .refactor-loop/state/recent-pr-merges.json from PR merge facts before cleanup")

merge_commit = facts.get("mergeCommit")
sha = merge_commit.get("oid") if isinstance(merge_commit, dict) else None
merged_at = facts.get("mergedAt")
pr = facts.get("number") or os.environ.get("RECENT_PR_MERGE_PR")
if not pr or not sha or not merged_at:
    raise SystemExit("merge_pr: recent-pr-merges projection failed: missing mergedAt or mergeCommit.oid after retry; recover by writing .refactor-loop/state/recent-pr-merges.json from PR merge facts before cleanup")

now = datetime.now(timezone.utc)
window_hours = 2
cutoff = now - timedelta(hours=window_hours)
path = Path(os.environ["REPO_ROOT"]) / ".refactor-loop" / "state" / "recent-pr-merges.json"
try:
    existing = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
except Exception:
    existing = {}

merges = existing.get("merges") if isinstance(existing, dict) else []
if not isinstance(merges, list):
    merges = []

entry = {
    "pr": int(pr),
    "sha": str(sha),
    "merged_at": str(merged_at),
    "base_ref": facts.get("baseRefName") or "",
    "head_ref": facts.get("headRefName") or "",
}

kept = []
for item in merges:
    if not isinstance(item, dict):
        continue
    item_time = parse_time(item.get("merged_at"))
    if item_time is None or item_time < cutoff:
        continue
    if item.get("pr") == entry["pr"] and item.get("sha") == entry["sha"]:
        continue
    kept.append(item)
kept.append(entry)

data = {
    "count": len(kept),
    "window_hours": window_hours,
    "updated_at": isoformat(now),
    "merges": kept,
}
path.parent.mkdir(parents=True, exist_ok=True)
with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=str(path.parent), delete=False) as handle:
    json.dump(data, handle, ensure_ascii=False, indent=2)
    handle.write("\n")
    tmp_name = handle.name
Path(tmp_name).replace(path)
PY
}

# Refactor (iter1/issue-145):
#   Old pattern: merge_pr 成功 merge 后未写 .refactor-loop/state/recent-pr-merges.json,导致 auto_release_gate 的 recent_pr_merges_min 信号永红(missing artifact),阻塞发版。
#   New principle: 按 .refactor-loop/runs/phase9-issue145-r5-judge.md consensus(structural):保留 recent_pr_merges_min 信号;merge_pr 成功后由私有 writer append recent-pr-merges.json(sha/time/pr,滚动窗口),artifact-only,release gate 不新增 standalone telemetry。硬约束:不重建 REFERENCE.md;refactor 注释自含 Old/New 不用 see-issue placeholder;不超范围。
merge_pr() {
  # Refactor (iter3/skill-human-label-taxonomy):
  #   Old: four Human labels, including two 🆘 labels, scattered no-gap and
  #   escalation decisions across the codebase.
  #   New principle: exactly two active Human labels; causes move to the
  #   reason surface (#15 structural consensus).
  local pr="$1" linked_issue="${2:-}"
  if [ -z "$pr" ]; then
    echo "merge_pr: missing pr number" >&2
    return 1
  fi
  # Auto-extract Closes #N if linked_issue not provided
  if [ -z "$linked_issue" ]; then
    linked_issue=$(gh pr view "$pr" "${gh_repo_args[@]}" --json body --jq '.body' 2>/dev/null | grep -oE "Closes #[0-9]+" | head -1 | grep -oE "[0-9]+")
  fi
  local merge_output merge_status
  merge_output=$(gh pr merge "$pr" "${gh_repo_args[@]}" --admin --squash --delete-branch 2>&1)
  merge_status=$?
  printf '%s\n' "$merge_output" | tail -1
  if [ "$merge_status" -ne 0 ]; then
    return "$merge_status"
  fi
  record_recent_pr_merge_artifact "$pr" || return "$?"
  # Cleanup PR labels: in-flight → merged
  gh pr edit "$pr" "${gh_repo_args[@]}" \
    --remove-label "🚀 phase:pr-open" \
    --remove-label "👀 phase:reviewing" \
    --remove-label "🔧 phase:fixing" \
    --remove-label "⏸️ phase:blocked" \
    --remove-label "auto-loop-stuck" \
    --remove-label "👤 human:需-maintainer-决策" \
    --remove-label "🆘 human:卡死" \
    --remove-label "🆘 human:卡死-需-rework" \
    --add-label "🎉 phase:merged" 2>&1 >/dev/null
  # Close linked issue + cleanup its labels
  if [ -n "$linked_issue" ]; then
    local close_comment
    close_comment=$(printf '✅ Auto-merged via PR #%s。\n\n⟦AI:AUTO-LOOP⟧' "$pr")
    gh issue close "$linked_issue" "${gh_repo_args[@]}" --reason "completed" --comment "$close_comment" 2>&1 | tail -1
    gh issue edit "$linked_issue" "${gh_repo_args[@]}" \
      --remove-label "🔍 phase:design-solving" \
      --remove-label "🛠️ phase:implementing" \
      --remove-label "🤖 human:auto-推进" \
      --remove-label "👤 human:需-maintainer-决策" \
      --remove-label "⏸️ phase:blocked" \
      --remove-label "auto-loop-stuck" \
      --remove-label "🆘 human:卡死" \
      --remove-label "🆘 human:卡死-需-rework" \
      --add-label "🎉 phase:merged" 2>&1 >/dev/null
  fi
  # Remove worktree if exists
  local wt
  wt=$(git -C "$REPO_ROOT" worktree list --porcelain | awk -v b="$(gh pr view "$pr" "${gh_repo_args[@]}" --json headRefName --jq '.headRefName' 2>/dev/null)" '
    $1 == "worktree" { path=$2 }
    $1 == "branch" && $2 == "refs/heads/"b { print path; exit }
  ')
  [ -n "$wt" ] && [ "$wt" != "$REPO_ROOT" ] && git -C "$REPO_ROOT" worktree remove "$wt" --force 2>&1 | tail -1
}

# Open PR + add auto-loop label atomically + capture PR number into PR_NUM
# Usage: open_pr_with_label <title> <body-file> [base] [head]
# Sets PR_NUM env var.
# head must be passed explicitly because the controller's current branch may
# not be the cluster branch; this prevents gh from falling back to the current
# branch and making head/base identical.
open_pr_with_label() {
  local title="$1" body_file="$2" base="${3:-$INTEGRATION_BRANCH}" head="${4:-}"
  if [ -z "$head" ]; then
    echo "open_pr_with_label: head branch required (avoid gh fallback to current branch = base)" >&2
    return 1
  fi
  local pr_url
  # gh pr create may emit multiple lines (warnings + URL), and the URL may not
  # be on the last line; grep the first occurrence.
  pr_url=$(gh pr create "${gh_repo_args[@]}" --base "$base" --head "$head" --title "$title" --body-file "$body_file" 2>&1 | grep -oE "https://github.com/[^/]+/[^/]+/pull/[0-9]+" | head -1)
  PR_NUM=$(echo "$pr_url" | grep -oE "[0-9]+$")
  if [ -z "$PR_NUM" ]; then
    echo "open_pr_with_label: failed to extract PR num from: $pr_url" >&2
    return 1
  fi
  gh pr edit "$PR_NUM" "${gh_repo_args[@]}" --add-label "auto-loop,🚀 phase:pr-open,👀 phase:reviewing,🤖 human:auto-推进" 2>&1 >/dev/null
  echo "$pr_url"
  export PR_NUM
}

_parse_release_rollup_pending_event() {
  local event_json="$1"
  if [ -z "$event_json" ]; then
    echo "open_release_rollup_pr_from_pending_event: missing event json" >&2
    return 2
  fi

  local parsed
  parsed=$(EVENT_JSON="$event_json" python3 - <<'PY'
import json
import os
import sys

try:
    event = json.loads(os.environ["EVENT_JSON"])
except Exception:
    print("invalid json", file=sys.stderr)
    sys.exit(2)

fields = [
    "integration_branch",
    "review_base_branch",
    "integration_sha",
    "review_base_sha",
    "ahead_count",
]
missing = [field for field in fields if event.get(field) in ("", None)]
if missing:
    print("missing facts: " + ",".join(missing), file=sys.stderr)
    sys.exit(2)

try:
    ahead = int(event["ahead_count"])
except Exception:
    print("ahead_count must be an integer", file=sys.stderr)
    sys.exit(2)

if ahead <= 0:
    print("ahead_count must be positive", file=sys.stderr)
    sys.exit(2)

values = [
    event["integration_branch"],
    event["review_base_branch"],
    event["integration_sha"],
    event["review_base_sha"],
    str(ahead),
    event.get("reason", "release-rollup-needed"),
]
print("\t".join(str(value) for value in values))
PY
  ) || {
    local status=$?
    echo "open_release_rollup_pr_from_pending_event: invalid event" >&2
    return "$status"
  }

  IFS=$'\t' read -r RELEASE_ROLLUP_HEAD RELEASE_ROLLUP_BASE RELEASE_ROLLUP_INTEGRATION_SHA_VALUE RELEASE_ROLLUP_REVIEW_BASE_SHA_VALUE RELEASE_ROLLUP_AHEAD_COUNT_VALUE RELEASE_ROLLUP_REASON_VALUE <<< "$parsed"
  if [ -z "$RELEASE_ROLLUP_HEAD" ] || [ -z "$RELEASE_ROLLUP_BASE" ]; then
    echo "open_release_rollup_pr_from_pending_event: head/base required" >&2
    return 2
  fi
  if [ "$RELEASE_ROLLUP_HEAD" = "$RELEASE_ROLLUP_BASE" ]; then
    echo "open_release_rollup_pr_from_pending_event: head and base must differ" >&2
    return 2
  fi
  if [ "$RELEASE_ROLLUP_HEAD" != "$INTEGRATION_BRANCH" ]; then
    echo "open_release_rollup_pr_from_pending_event: head must equal INTEGRATION_BRANCH" >&2
    return 2
  fi
  if [ "$RELEASE_ROLLUP_BASE" != "$REVIEW_BASE_BRANCH" ]; then
    echo "open_release_rollup_pr_from_pending_event: base must equal REVIEW_BASE_BRANCH" >&2
    return 2
  fi
  if [ -z "$RELEASE_ROLLUP_INTEGRATION_SHA_VALUE" ] || [ -z "$RELEASE_ROLLUP_REVIEW_BASE_SHA_VALUE" ] || [ -z "$RELEASE_ROLLUP_AHEAD_COUNT_VALUE" ]; then
    echo "open_release_rollup_pr_from_pending_event: sha and ahead facts required" >&2
    return 2
  fi
}

_release_rollup_pr_exists() {
  local head="$1" base="$2"
  gh pr list "${gh_repo_args[@]}" --state open --head "$head" --base "$base" --limit 1 --json number --jq '.[0].number // ""' 2>/dev/null || true
}

_controller_skill_root() {
  if [ -n "${CODEX_REFACTOR_LOOP_SKILL_ROOT:-}" ]; then
    printf '%s\n' "$CODEX_REFACTOR_LOOP_SKILL_ROOT"
  else
    cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P
  fi
}

# Refactor (iter5/issue-65-release-rollup-pending-event):
#   Old pattern: release-rollup event parsing, duplicate-open check, and PR creation were packed into one large controller helper.
#   New principle: keep the public lifecycle helper as orchestration; isolate event parsing and existing-rollup lookup behind narrow helpers.
# Open the release rollup PR from a daemon pending event.
# Usage: open_release_rollup_pr_from_pending_event <event-json> <body-file>
# Validates the event is exactly $INTEGRATION_BRANCH -> $REVIEW_BASE_BRANCH
# with non-empty SHA/ahead facts, then delegates lifecycle creation to
# open_pr_with_label. This helper belongs to the controller lifecycle surface;
# dev_sync_daemon.py must only emit the pending event.
open_release_rollup_pr_from_pending_event() {
  local event_json="$1" body_file="$2"
  if [ -z "$body_file" ] || [ ! -f "$body_file" ]; then
    echo "open_release_rollup_pr_from_pending_event: missing body file" >&2
    return 2
  fi

  _parse_release_rollup_pending_event "$event_json" || return "$?"

  local existing
  existing=$(_release_rollup_pr_exists "$RELEASE_ROLLUP_HEAD" "$RELEASE_ROLLUP_BASE")
  if [[ -n "$existing" && "$existing" =~ ^[0-9]+$ ]]; then
    PR_NUM="$existing"
    export PR_NUM
    echo "release-rollup PR already exists: #$existing"
    return 0
  fi

  RELEASE_ROLLUP_REASON="$RELEASE_ROLLUP_REASON_VALUE" \
  RELEASE_ROLLUP_INTEGRATION_SHA="$RELEASE_ROLLUP_INTEGRATION_SHA_VALUE" \
  RELEASE_ROLLUP_REVIEW_BASE_SHA="$RELEASE_ROLLUP_REVIEW_BASE_SHA_VALUE" \
  RELEASE_ROLLUP_AHEAD_COUNT="$RELEASE_ROLLUP_AHEAD_COUNT_VALUE" \
    open_pr_with_label "Release rollup: ${RELEASE_ROLLUP_HEAD} to ${RELEASE_ROLLUP_BASE}" "$body_file" "$RELEASE_ROLLUP_BASE" "$RELEASE_ROLLUP_HEAD"
}

# Apply a DEV_SYNC_REQUEST:<path> marker by delegating to the controller-owned
# helper. This wrapper only parses the bounded marker shape; it does not decide
# sync semantics.
apply_dev_sync_request_marker() {
  local marker="$1" rel_path skill_root
  case "$marker" in
    DEV_SYNC_REQUEST:.refactor-loop/runs/*.json) rel_path="${marker#DEV_SYNC_REQUEST:}" ;;
    *) echo "apply_dev_sync_request_marker: invalid marker" >&2; return 2 ;;
  esac
  skill_root="$(_controller_skill_root)"
  REPO_ROOT="$REPO_ROOT" python3 "$skill_root/scripts/apply_integration_sync_request.py" "$REPO_ROOT/$rel_path"
}

# Refactor (iter5/cluster-issue70-controller-owned-apply):
# Old: triage worker edited GitHub issues directly, and bash parsed the
# TriageLifecycleRequestV1 Markdown artifact inline.
# New: triage worker emits ManualIssueTriageDecision JSON artifact + TRIAGE_DECISION_DONE marker; controller-owned apply_triage_decision.py re-reads live labels before lifecycle apply.
# Apply a TRIAGE_DECISION_DONE:<issue>:<accept|reject>:<path> marker by
# delegating to the controller-owned helper. This wrapper does not inline
# triage judgment.
apply_triage_decision_marker() {
  local marker="$1" rest issue verdict rel_path skill_root
  case "$marker" in
    TRIAGE_DECISION_DONE:*:accept:.refactor-loop/runs/*.json|TRIAGE_DECISION_DONE:*:reject:.refactor-loop/runs/*.json) ;;
    *) echo "apply_triage_decision_marker: invalid marker" >&2; return 2 ;;
  esac
  rest="${marker#TRIAGE_DECISION_DONE:}"
  issue="${rest%%:*}"
  rest="${rest#*:}"
  verdict="${rest%%:*}"
  rel_path="${rest#*:}"
  [[ "$issue" =~ ^[0-9]+$ ]] || { echo "apply_triage_decision_marker: invalid issue" >&2; return 2; }
  skill_root="$(_controller_skill_root)"
  REPO_ROOT="$REPO_ROOT" python3 "$skill_root/scripts/apply_triage_decision.py" "$issue" "$verdict" "$REPO_ROOT/$rel_path"
}

# Refactor (iter4/human-label-semantics-guard): Old pattern: label used as an
# architect-reject workaround. New principle: strict semantics + reflector
# self-check + controller helper guard + source-regression test.
# Apply the maintainer-decision label only after checking maintainer-directive artifacts.
# Usage: apply_human_label_or_skip <pr-number> <source-marker> <reason-or-topic>
apply_human_label_or_skip() {
  local pr_number="$1" source_marker="${2:-}" reason="${3:-}"
  if [ -z "$pr_number" ]; then
    echo "apply_human_label_or_skip: missing pr_number" >&2
    return 2
  fi
  if [[ "$source_marker" != META_RESOLVED:escalate-human:* && "${HUMAN_LABEL_SOURCE_MARKER:-}" == META_RESOLVED:escalate-human:* ]]; then
    [ -n "$reason" ] || reason="$source_marker"
    source_marker="$HUMAN_LABEL_SOURCE_MARKER"
  fi
  case "$source_marker" in
    META_RESOLVED:escalate-human:*) ;;
    *)
      echo "ERROR: apply_human_label_or_skip requires META_RESOLVED:escalate-human marker source" >&2
      return 2
      ;;
  esac

  local directive_dir="${REPO_ROOT}/.refactor-loop/runs/maintainer-directives"
  if [ -d "$directive_dir" ]; then
    local target escaped_reason
    target="${pr_number#\#}"
    if grep -RIlE "(^|[^0-9])(PR[ -]?)?#?${target}([^0-9]|$)" "$directive_dir"/*.md >/dev/null 2>&1; then
      echo "skip-label: maintainer-directive already covers this; see .refactor-loop/runs/maintainer-directives/"
      return 1
    fi
    if [ -n "$reason" ]; then
      escaped_reason=$(printf '%s\n' "$reason" | sed 's/[][(){}.^$*+?|\\]/\\&/g')
      if grep -RIlE "(^|[^[:alnum:]_-])${escaped_reason}([^[:alnum:]_-]|$)" "$directive_dir"/*.md >/dev/null 2>&1; then
        echo "skip-label: maintainer-directive already covers this; see .refactor-loop/runs/maintainer-directives/"
        return 1
      fi
    fi
  fi

  gh pr edit "$pr_number" "${gh_repo_args[@]}" --add-label "👤 human:需-maintainer-决策" 2>&1 >/dev/null
}

# Substitute {{handlebars}} placeholders in a template using current env vars
# Usage: render_template <template-file> <output-file>
# Reads $WORK_UNIT_ID, $CLUSTER_ID, $ITERATION, $WORKTREE_PATH, $BRANCH, $OLD_PATTERN, $NEW_PRINCIPLE, $SCOPE_PATHS, $VERIFICATION_HINTS, and $VAR in template.
render_template() {
  local in="$1" out="$2"
  perl -pe '
    s/\{\{work_unit_id\}\}/($ENV{WORK_UNIT_ID} || $ENV{CLUSTER_ID})/ge;
    s/\{\{cluster_id\}\}/$ENV{CLUSTER_ID}/g;
    s/\{\{iteration\}\}/$ENV{ITERATION}/g;
    s/\{\{worktree_path\}\}/$ENV{WORKTREE_PATH}/g;
    s/\{\{branch\}\}/$ENV{BRANCH}/g;
    s/\{\{old_pattern\}\}/$ENV{OLD_PATTERN}/g;
    s/\{\{new_principle\}\}/$ENV{NEW_PRINCIPLE}/g;
    s/\{\{scope_paths\}\}/$ENV{SCOPE_PATHS}/g;
    s/\{\{verification_hints\}\}/$ENV{VERIFICATION_HINTS}/g;
  ' < "$in" | envsubst > "$out"
}

# Sweep all closed auto-loop issues/PRs and clean stale in-flight phase labels
# Usage: sweep_stale_labels
sweep_stale_labels() {
  # Refactor (iter3/skill-human-label-taxonomy):
  #   Old: four Human labels, including two 🆘 labels, scattered no-gap and
  #   escalation decisions across the codebase.
  #   New principle: exactly two active Human labels; causes move to the
  #   reason surface (#15 structural consensus).
  # Refactor (iter3/skill-hygiene-scripts):
  #   Old: stale labels were interpolated into a shell-built gh edit string.
  #   New: build a real argv array and append one --remove-label "$label" pair.
  #   This preserves labels with spaces/quotes as single arguments and removes command injection risk.
  local n_fixed=0
  for kind in issue pr; do
    gh "$kind" list "${gh_repo_args[@]}" --label "auto-loop" --state closed --limit 50 --json number,labels 2>/dev/null | \
      python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
except Exception:
    sys.exit(0)
stale = ['🚀 phase:pr-open', '👀 phase:reviewing', '🔧 phase:fixing', '⏸️ phase:blocked', 'auto-loop-stuck', '👤 human:需-maintainer-决策', '🆘 human:卡死', '🆘 human:卡死-需-rework', '🔍 phase:design-solving', '🛠️ phase:implementing', '🤖 human:auto-推进']
for x in d:
    bad = [l['name'] for l in x['labels'] if l['name'] in stale]
    if bad:
        print(x['number'], ','.join(bad))
" | while read num bad; do
        [ -z "$num" ] && continue
        local cmd=(gh "$kind" edit "$num" "${gh_repo_args[@]}")
        old_IFS="$IFS"; IFS=,
        for l in $bad; do
          cmd+=(--remove-label "$l")
        done
        IFS="$old_IFS"
        # Add 🎉 phase:merged if it's MERGED
        if [ "$kind" = "pr" ]; then
          local state
          state=$(gh pr view "$num" "${gh_repo_args[@]}" --json state --jq '.state' 2>/dev/null)
          [ "$state" = "MERGED" ] && cmd+=(--add-label "🎉 phase:merged")
        else
          cmd+=(--add-label "🎉 phase:merged")
        fi
        "${cmd[@]}" 2>&1 >/dev/null && n_fixed=$((n_fixed+1)) && echo "  cleaned $kind #$num: $bad"
      done
  done
  echo "  total fixed: $n_fixed"
}

# Validate prompt file has no unresolved {{var}}
# Usage: validate_prompt <prompt-file>
validate_prompt() {
  local f="$1"
  local n
  n=$(grep -c "{{" "$f" 2>/dev/null | tr -d ' \n' || echo "0")
  [ -z "$n" ] && n=0
  if [ "$n" -gt 0 ] 2>/dev/null; then
    echo "❌ prompt $f still has $n unresolved {{var}} placeholder(s):" >&2
    grep -n "{{" "$f" | head -5 >&2
    return 1
  fi
  return 0
}

# Verify trunk builds after merge — call after merge_pr to catch cross-PR API breaks
# Usage: verify_trunk_build
# Catch cross-PR API mismatch after independently green PRs merge into trunk.
verify_trunk_build() {
  cd "$REPO_ROOT" || return 1
  git pull --ff-only origin "$INTEGRATION_BRANCH" 2>&1 | tail -1
  if [ -z "${BUILD_CMD:-}" ]; then
    echo "❌ BUILD_CMD unset; source .refactor-loop/host.env before verify_trunk_build" >&2
    return 2
  fi
  if ! $BUILD_CMD; then
    echo "❌ trunk build broken after merge — dispatch hotfix codex (see SKILL Phase 4 hotfix section)" >&2
    return 2
  fi
  # Add architecture guards, including docs lint, to prevent post-merge
  # docs/architecture regressions.
  if [ -n "${CI_GUARDS:-}" ]; then
    if ! $CI_GUARDS > /tmp/_verify_trunk_guards.log 2>&1; then
      echo "❌ trunk architecture/docs guards failed (post-merge lint regression)" >&2
      tail -5 /tmp/_verify_trunk_guards.log >&2
      return 3
    fi
    echo "✓ trunk build + guards green"
  else
    echo "✓ trunk build green; guards skipped: CI_GUARDS unset"
  fi
  return 0
}

# Refactor (iter4/skill-safe-push-helper): Old pattern: controller pushed
# directly after every commit, but dev_sync_daemon could push first while
# syncing origin/dev -> auto-refact-dev from its dedicated worktree. That left
# main repo HEAD behind remote, causing controller push to hit non-fast-forward
# and require manual pull --rebase.
# New principle: force safe_push, with built-in fetch and rebase --autostash
# when needed before pushing again. Also expose safe_sync_main so session entry
# and pre-commit can proactively catch up to remote.
# (2026-05-26 maintainer-directive equivalent to Phase 9 consensus)

# Usage: safe_push <remote> <branch>
# Behavior: fetch remote/branch; if local diverges or is behind, rebase --autostash;
#   then push. Returns non-zero if rebase has conflicts (caller must resolve).
safe_push() {
  local remote="${1:-origin}"
  local branch="${2:-$(git -C "$REPO_ROOT" rev-parse --abbrev-ref HEAD)}"
  if [ -z "$branch" ] || [ "$branch" = "HEAD" ]; then
    echo "safe_push: cannot determine branch (HEAD detached?); aborting" >&2
    return 2
  fi
  ( cd "$REPO_ROOT" || return 1
    git fetch "$remote" "$branch" 2>&1 | tail -3
    local behind
    behind=$(git rev-list --count "HEAD..$remote/$branch")
    if [ "${behind:-0}" -gt 0 ]; then
      echo "safe_push: local behind $remote/$branch by $behind commit(s); rebasing"
      if ! git pull --rebase --autostash "$remote" "$branch"; then
        echo "❌ safe_push: rebase conflict on $remote/$branch — resolve manually then push" >&2
        return 3
      fi
    fi
    git push "$remote" "$branch"
  )
}

# Usage: safe_sync_main [remote] [branch]
# Idempotent fast-forward of the current working tree to the remote tip. Use at session
# start or before commits to avoid the safe_push rebase path. No-op when already current.
safe_sync_main() {
  local remote="${1:-origin}"
  local branch="${2:-$(git -C "$REPO_ROOT" rev-parse --abbrev-ref HEAD)}"
  if [ -z "$branch" ] || [ "$branch" = "HEAD" ]; then
    echo "safe_sync_main: cannot determine branch; skipping" >&2
    return 0
  fi
  ( cd "$REPO_ROOT" || return 1
    git fetch "$remote" "$branch" 2>&1 | tail -3
    local behind
    behind=$(git rev-list --count "HEAD..$remote/$branch")
    if [ "${behind:-0}" -gt 0 ]; then
      echo "safe_sync_main: local behind $remote/$branch by $behind; pulling --rebase --autostash"
      git pull --rebase --autostash "$remote" "$branch"
    else
      echo "safe_sync_main: already up to date with $remote/$branch"
    fi
  )
}

# ⟦AI:AUTO-LOOP⟧
