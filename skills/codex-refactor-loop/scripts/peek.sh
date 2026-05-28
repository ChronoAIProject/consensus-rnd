#!/usr/bin/env bash
# peek.sh — quick controller wakeup sweep
#
# Controller should run this script first on every wakeup to see the full state
# at a glance and avoid manual grep/parse mistakes such as the pr1_num empty bug.
#
# Output:
#   1. Active codex count + each log name (harness-tracked vs detached tagged separately)
#   2. Open auto-loop PR CI + reviewer status
#   3. Monitor zero_streak max over the last 10 ticks
#   4. Phase 9 router ledger + pending events as facts only
#
# Usage: bash .claude/skills/codex-refactor-loop/scripts/peek.sh
#
# ⟦AI:AUTO-LOOP⟧

set -e
REPO_ROOT="${REPO_ROOT:-$(git rev-parse --show-toplevel 2>/dev/null || true)}"
if [ -z "${REPO_ROOT:-}" ]; then
  echo "FATAL: REPO_ROOT is unset and git rev-parse --show-toplevel failed" >&2
  exit 2
fi
cd "$REPO_ROOT"
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)/repo_slug.sh"
set_gh_repo_args 0 0 || exit $?
git fetch origin --quiet 2>/dev/null

list_loop_codex() {
  # Scope to THIS repo by absolute REPO_ROOT, not the relative `.refactor-loop/`
  # substring (which two loops on one machine share -> cross-host over-count).
  # Requires callers to pass an absolute --cd so REPO_ROOT is in the cmdline.
  # Exclude ` -c ` lines: each codex yields a real `bash spawn-codex.sh` supervisor
  # AND a shell `-c` wrapper that echoes the command; count only the supervisor.
  ps -eo command= | awk -v repo="$REPO_ROOT" 'repo != "" && /spawn-codex[.]sh/ && index($0, repo) && index($0, " -c ")==0 { print }'
}

REVIEW_MARKER_TAIL_LINES=30
extract_review_verdict_tail() {
  local log_path="$1"
  local pr_num="$2"
  local role="$3"
  tail -n "$REVIEW_MARKER_TAIL_LINES" "$log_path" 2>/dev/null |
    grep -E "REVIEW_DONE:${pr_num}:${role}:(approve|comment|reject)" |
    sed -E "s/^.*REVIEW_DONE:${pr_num}:${role}:(approve|comment|reject).*$/\1/" |
    tail -1
}

echo "═══════════════ peek $(date -u +%H:%M:%SZ) ═══════════════"

# 0. CRITICAL: maintainer comments (non-AI means no sentinel)
# Rules:
#   (a) Show any issue/PR maintainer comment from the last 12h.
#   (b) Show the latest maintainer comment on stuck-label issues regardless of
#       age to avoid missed reads.
echo ""
echo "▍🚨 maintainer comments (read first — missed read = controller bug):"
{
  gh issue list "${gh_repo_args[@]}" --label "auto-loop" --state open --json number 2>/dev/null | python3 -c "import json,sys; [print(f'i{x[\"number\"]}') for x in json.load(sys.stdin)]" 2>/dev/null
  gh pr list "${gh_repo_args[@]}" --label "auto-loop" --state open --json number 2>/dev/null | python3 -c "import json,sys; [print(f'p{x[\"number\"]}') for x in json.load(sys.stdin)]" 2>/dev/null
} | while read item; do
  kind=$(echo "$item" | cut -c1)
  num=$(echo "$item" | cut -c2-)
  [ -z "$num" ] && continue
  if [ "$kind" = "i" ]; then
    raw=$(gh issue view "$num" "${gh_repo_args[@]}" --json comments 2>/dev/null)
  else
    raw=$(gh pr view "$num" "${gh_repo_args[@]}" --json comments 2>/dev/null)
  fi
  echo "$raw" | python3 -c "
import json, sys
from datetime import datetime, timezone
try:
    data = json.load(sys.stdin)
    cs = data.get('comments', [])
except: sys.exit(0)
# non-AI = not AI sentinel, not status banner prefix, not codecov bot
non_ai = [c for c in cs if '⟦AI:AUTO-LOOP⟧' not in (c.get('body','') or '')
          and not (c.get('body','') or '').lstrip().startswith(('## 📊', '## 🤖', '## ✅', '## 🆘'))
          and not (c.get('author',{}) or {}).get('login','').endswith('[bot]')]
if not non_ai: sys.exit(0)
last = max(non_ai, key=lambda c: c.get('createdAt',''))
# Find latest AI reply (any AI sentinel or status banner)
ai = [c for c in cs if c.get('createdAt','') > last.get('createdAt','') and
      ('⟦AI:AUTO-LOOP⟧' in (c.get('body','') or '') or (c.get('body','') or '').lstrip().startswith(('## 📊', '## 🤖', '## ✅', '## 🆘')))]
has_ai_reply = bool(ai)
ts_str = last.get('createdAt','').rstrip('Z')
try:
    ts = datetime.fromisoformat(ts_str).replace(tzinfo=timezone.utc)
except: sys.exit(0)
delta_h = (datetime.now(timezone.utc) - ts).total_seconds() / 3600
# 12h cutoff OR no AI reply since; always show unanswered comments regardless of age.
if delta_h > 12 and has_ai_reply: sys.exit(0)
flag = '⏰ no-AI-reply' if not has_ai_reply else ''
author = (last.get('author', {}) or {}).get('login', '?')
body = (last.get('body','') or '').replace('\n',' ')[:200]
print(f'  {flag} [{author}] ${num} ${kind} ({delta_h:.1f}h ago): {body}')
" 2>/dev/null
done

# 1. Active codex
n=$(list_loop_codex | wc -l | tr -d ' ')
echo ""
echo "▍Active codex: ${n}"
if [ "$n" -gt 0 ]; then
  list_loop_codex | sed -E 's/.*--log [^ ]*\/([^ ]+)\.log.*/  • \1/' | sort
fi

# Refactor (iter5/issue-87-peek-status-lens):
# Old pattern: generic marker projector plus 60-minute route-hint table in peek output.
# New principle: observability-only status lens reads Phase 9 ledger/pending events as facts,
# while merge readiness remains tail-only REVIEW_DONE consensus below.
# 2. Phase 9 router ledger and pending events. Facts only; routing authority
# remains Phase Routing, clean-exit log-tail sweep, and phase9_router_daemon.py.
echo ""
echo "▍Phase 9 router / pending events:"
echo "  ledger tail:"
tail -10 .refactor-loop/phase9-router-ledger.jsonl 2>/dev/null | sed 's/^/    /' || true
echo "  pending events tail:"
tail -10 .refactor-loop/.controller-pending-events.log 2>/dev/null | sed 's/^/    /' || true
echo "  Skill degradation alerts:"
tail -n "${DEGRADATION_ALERT_TAIL_LINES:-10}" .refactor-loop/.degradation-alert.log 2>/dev/null | sed 's/^/    /' || true

# 3. Open auto-loop PRs + state
echo ""
echo "▍Open auto-loop PRs:"
gh pr list "${gh_repo_args[@]}" --label "auto-loop" --state open --json number,title --jq '.[]' | while IFS= read -r line; do
  num=$(echo "$line" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['number'])" 2>/dev/null)
  title=$(echo "$line" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['title'][:60])" 2>/dev/null)
  [ -z "$num" ] && continue
  fail=$(gh pr checks "$num" "${gh_repo_args[@]}" --json bucket --jq '[.[] | select(.bucket=="fail") | 1] | length' 2>/dev/null)
  pending=$(gh pr checks "$num" "${gh_repo_args[@]}" --json bucket --jq '[.[] | select(.bucket=="pending") | 1] | length' 2>/dev/null)
  pass=$(gh pr checks "$num" "${gh_repo_args[@]}" --json bucket --jq '[.[] | select(.bucket=="pass") | 1] | length' 2>/dev/null)
  state=$(gh pr view "$num" "${gh_repo_args[@]}" --json mergeStateStatus --jq '.mergeStateStatus' 2>/dev/null)
  echo "  • PR #${num} [${state}] CI: fail=${fail} pending=${pending} pass=${pass} — ${title}"
done

# 4. Monitor recent zero_streak max
echo ""
echo "▍Monitor zero_streak (last 10 ticks):"
tail -10 .refactor-loop/logs/concurrency-monitor.log 2>/dev/null | \
  grep -oE "zero_streak=[0-9]+" | sort -t= -k2 -rn | head -1 | sed 's/^/  max: /'
zero_now=$(tail -1 .refactor-loop/logs/concurrency-monitor.log 2>/dev/null | grep -oE "zero_streak=[0-9]+" | head -1)
[ -n "$zero_now" ] && echo "  current: ${zero_now}"

# 5. Mergeable PRs (per reviewer consensus + CI green)
# Refactor (iter3/skill-merge-policy): Old pattern: unanimous-approve merge
# gate + contradictory Phase 8 wording. New principle: fixed truth table
# reject=0 && approve>=1 -> MERGE; comments are advisory (#26 minimal option B consensus).
echo ""
echo "▍Mergeable PRs (controller should merge immediately):"
gh pr list "${gh_repo_args[@]}" --label "auto-loop" --state open --json number,title --jq '.[].number' 2>/dev/null | while read pr_num; do
  [ -z "$pr_num" ] && continue
  # CI must have 0 fail + 0 pending
  fail=$(gh pr checks "$pr_num" "${gh_repo_args[@]}" --json bucket --jq '[.[] | select(.bucket=="fail") | 1] | length' 2>/dev/null)
  pending=$(gh pr checks "$pr_num" "${gh_repo_args[@]}" --json bucket --jq '[.[] | select(.bucket=="pending") | 1] | length' 2>/dev/null)
  [ "$fail" != "0" ] || [ "$pending" != "0" ] && continue
  state=$(gh pr view "$pr_num" "${gh_repo_args[@]}" --json mergeStateStatus --jq '.mergeStateStatus' 2>/dev/null)
  # Reviewer consensus: latest round per role, count approve/reject/comment
  # find latest round N for this PR
  max_round=0
  for r in 1 2 3 4 5 6; do
    cnt=$(ls .refactor-loop/logs/review-pr${pr_num}-*-r${r}.log 2>/dev/null | wc -l | tr -d ' ')
    [ "$cnt" -ge 3 ] && max_round=$r
  done
  [ "$max_round" = "0" ] && continue
  approve=0; comment=0; reject=0
  for role in architect tests quality; do
    f=".refactor-loop/logs/review-pr${pr_num}-${role}-r${max_round}.log"
    [ -f "$f" ] || continue
    v=$(extract_review_verdict_tail "$f" "$pr_num" "$role")
    case "$v" in
      approve) approve=$((approve+1)) ;;
      comment) comment=$((comment+1)) ;;
      reject)  reject=$((reject+1)) ;;
    esac
  done
  # Merge rule: latest complete required round, 0 reject AND >= 1 approve (mixed comment OK)
  if [ "$reject" = "0" ] && [ "$approve" -ge 1 ]; then
    echo "  ✅ PR #${pr_num} [${state}] r${max_round}: MERGE_READY approve=${approve} comment=${comment} reject=0 — gh pr merge ${pr_num} --admin --squash --delete-branch"
  elif [ "$reject" = "0" ] && [ "$approve" = "0" ] && [ "$comment" -ge 1 ]; then
    echo "  ⏸ PR #${pr_num} [${state}] r${max_round}: WAIT_EXPLICIT_APPROVAL approve=0 comment=${comment} reject=0 — do not merge"
  fi
done

# 5b. Stale-label detection on CLOSED issues/PRs
echo ""
echo "▍Stale labels (CLOSED but still carrying in-flight phase labels):"
gh issue list "${gh_repo_args[@]}" --label "auto-loop" --state closed --limit 30 --json number,labels --jq '.[] | "\(.number)|\(.labels | map(.name) | map(select(. | startswith("🔍") or startswith("🛠") or startswith("🔧") or startswith("👀") or startswith("⏸") or startswith("auto-loop-stuck") or startswith("🆘"))) | join(","))"' 2>/dev/null | while IFS='|' read -r num labels; do
  [ -z "$num" ] || [ -z "$labels" ] && continue
  echo "  ⚠️ closed issue #${num} still has: ${labels}  → controller should clean up + add 🎉 phase:merged"
done
gh pr list "${gh_repo_args[@]}" --label "auto-loop" --state closed --limit 30 --json number,labels --jq '.[] | "\(.number)|\(.labels | map(.name) | map(select(. | startswith("🔍") or startswith("🛠") or startswith("🔧") or startswith("👀") or startswith("⏸") or startswith("auto-loop-stuck") or startswith("🆘") or startswith("🚀"))) | join(","))"' 2>/dev/null | while IFS='|' read -r num labels; do
  [ -z "$num" ] || [ -z "$labels" ] && continue
  echo "  ⚠️ closed PR #${num} still has: ${labels}"
done

# 5c. Linkage check: open issues in phase:implementing without matching
# in-flight PR / open PRs without matching design issue.
echo ""
echo "▍Issue/PR linkage mismatch:"
gh issue list "${gh_repo_args[@]}" --label "🛠️ phase:implementing" --state open --json number,title --jq '.[] | "\(.number)|\(.title)"' 2>/dev/null | while IFS='|' read -r num title; do
  [ -z "$num" ] && continue
  # Find open PRs that close #num.
  pr_count=$(gh pr list "${gh_repo_args[@]}" --label "auto-loop" --state open --search "in:body Closes #${num}" --json number --jq 'length' 2>/dev/null || echo 0)
  if [ "$pr_count" = "0" ]; then
    # Also check whether a closed PR was merged.
    merged_pr=$(gh pr list "${gh_repo_args[@]}" --label "auto-loop" --state merged --search "in:body Closes #${num}" --json number --jq '.[0].number' 2>/dev/null)
    if [ -z "$merged_pr" ] || [ "$merged_pr" = "null" ]; then
      echo "  ⚠️ issue #${num} [🛠️ implementing] has no matching in-flight or merged PR (implement codex failed/not dispatched?)"
    else
      echo "  ⚠️ issue #${num} [🛠️ implementing] PR #${merged_pr} is merged but issue is still open — controller should gh issue close"
    fi
  fi
done

# 5d. Spawn drop detection: 3 solver artifacts complete but matching judge log absent.
echo ""
echo "▍Spawn drop (N solvers complete but judge was not dispatched):"
for f in .refactor-loop/runs/phase9-issue*-r*-minimal.md; do
  [ -f "$f" ] || continue
  base=$(basename "$f" -minimal.md)
  issue=$(echo "$base" | sed -E 's/phase9-issue([0-9]+)-r.*/\1/')
  round=$(echo "$base" | sed -E 's/.*-r([0-9]+)/\1/')
  s_min="$f"
  s_str=".refactor-loop/runs/${base}-structural.md"
  s_del=".refactor-loop/runs/${base}-delete.md"
  judge_log=".refactor-loop/logs/${base}-judge.log"
  if [ -f "$s_str" ] && [ -f "$s_del" ] && [ ! -f "$judge_log" ]; then
    issue_state=$(gh issue view "$issue" "${gh_repo_args[@]}" --json state --jq '.state' 2>/dev/null)
    [ "$issue_state" = "OPEN" ] || continue  # only flag if issue still open
    echo "  ⚠️ issue #${issue} r${round} 3 solvers done but judge log absent (redispatch judge)"
  fi
done

# 6. Drift detection: phase:* label set but no log file being actively written for that issue/PR
echo ""
echo "▍Drift (label vs codex mismatch):"
active_logs_file=$(mktemp)
# Use recent log activity to infer running codex: no EXIT= tail and mtime < 10 min.
for f in .refactor-loop/logs/*.log; do
  [ -f "$f" ] || continue
  # mtime > 10 min: not updating.
  [ -n "$(find "$f" -mmin -10)" ] || continue
  # EXIT= present: already complete.
  if grep -q "^EXIT=" "$f" 2>/dev/null; then continue; fi
  basename "$f" .log >> "$active_logs_file"
done
check_drift() {
  local kind="$1" num="$2" phase="$3"
  case "$phase" in
    *design-solving* | *implementing* | *fixing* | *reviewing*) ;;
    *) return ;;
  esac
  if ! grep -qE "(pr${num}|issue${num})" "$active_logs_file" 2>/dev/null; then
    echo "  ⚠️ ${kind} #${num} label=${phase} but 0 codex referencing it"
  fi
}
gh issue list "${gh_repo_args[@]}" --label "auto-loop" --state open --json number,labels --jq '.[] | "\(.number)|\(.labels | map(.name) | map(select(. | startswith("🔍") or startswith("🛠") or startswith("🔧") or startswith("👀"))) | first)"' 2>/dev/null | while IFS='|' read -r num phase; do
  [ -z "$num" ] || [ -z "$phase" ] && continue
  check_drift issue "$num" "$phase"
done
gh pr list "${gh_repo_args[@]}" --label "auto-loop" --state open --json number,labels --jq '.[] | "\(.number)|\(.labels | map(.name) | map(select(. | startswith("🔍") or startswith("🛠") or startswith("🔧") or startswith("👀"))) | first)"' 2>/dev/null | while IFS='|' read -r num phase; do
  [ -z "$num" ] || [ -z "$phase" ] && continue
  check_drift pr "$num" "$phase"
done

# 7. Stale worktree (branch was merged but worktree remains)
echo ""
echo "▍Stale worktree (branch merged and should be cleaned):"
git worktree list --porcelain | grep "^worktree " | sed 's/^worktree //' | while read wt; do
  base=$(basename "$wt")
  # skip main + dev-sync
  case "$base" in
    "$(basename "$REPO_ROOT")" | "$(basename "$REPO_ROOT")-wt-dev-sync" | "dev-sync") continue ;;
  esac
  # If worktree branch is in remote merged-to-master list, mark stale
  branch=$(git -C "$wt" branch --show-current 2>/dev/null)
  if [ -n "$branch" ]; then
    # branch exists on remote? if not, stale
    if ! git ls-remote --exit-code --heads origin "$branch" >/dev/null 2>&1; then
      echo "  ⚠️ $wt  branch=$branch(remote no longer exists — git worktree remove $wt --force && git branch -D $branch)"
    fi
  fi
done

# 8. Stuck too long (issue/PR has stuck label and last non-AI comment > 6h)
echo ""
echo "▍Stuck too long (>6h without maintainer reply; consider 4h reflector re-evaluation):"
gh issue list "${gh_repo_args[@]}" --label "auto-loop-stuck" --state open --json number,title 2>/dev/null | GH_REPO_SLUG_FOR_PEEK="${GH_REPO_SLUG:-}" python3 -c "
import json, sys, subprocess
from datetime import datetime, timezone
import os
data = json.load(sys.stdin)
for it in data:
    num = it['number']
    cmd = ['gh', 'issue', 'view', str(num)]
    repo = os.environ.get('GH_REPO_SLUG_FOR_PEEK')
    if repo:
        cmd.extend(['--repo', repo])
    cmd.extend(['--json', 'comments'])
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0: continue
    try:
        comments = json.loads(r.stdout).get('comments', [])
    except:
        continue
    # filter non-AI(no ⟦AI:AUTO-LOOP⟧ sentinel)
    non_ai = [c for c in comments if '⟦AI:AUTO-LOOP⟧' not in (c.get('body','') or '')]
    if not non_ai: continue
    last = max(non_ai, key=lambda c: c.get('createdAt',''))
    ts_str = last.get('createdAt','').rstrip('Z')
    try:
        ts = datetime.fromisoformat(ts_str).replace(tzinfo=timezone.utc)
    except:
        continue
    delta_h = (datetime.now(timezone.utc) - ts).total_seconds() / 3600
    if delta_h > 6:
        print(f'  ⚠️ #{num} last maintainer comment {delta_h:.1f}h ago — {it[\"title\"][:50]}')
" 2>/dev/null

# 9. Open auto-loop issues + label state
echo ""
echo "▍Open auto-loop issues:"
gh issue list "${gh_repo_args[@]}" --label "auto-loop" --state open --json number,title,labels --jq '.[] | "  • #\(.number) labels=[\(.labels | map(.name) | map(select(. | startswith("🔍") or startswith("🛠") or startswith("⚙") or startswith("⏸") or startswith("🆘") or startswith("👤") or startswith("🤖"))) | join(", "))] — \(.title | .[0:55])"' 2>/dev/null

echo ""
echo "═══════════════════════════════════════════════════"
