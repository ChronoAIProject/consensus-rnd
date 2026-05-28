#!/usr/bin/env bash
# peek.sh — controller wakeup 快速 sweep
#
# controller 每 wakeup 第一动作应该用这个 script 一眼看全状态,
# 避免人工 grep / parse 出错(pr1_num empty bug 那种)。
#
# 输出:
#   1. 活跃 codex 数 + 每个的 log 名(harness-tracked vs detached 分别标)
#   2. Open auto-loop PR CI + reviewer status
#   3. Monitor zero_streak max over the last 10 ticks
#   4. Phase 9 router ledger + pending events as facts only
#
# Usage: bash .claude/skills/codex-refactor-loop/scripts/peek.sh
#
# ⟦AI:AUTO-LOOP⟧

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
SKILL_ROOT="$(cd "$SCRIPT_DIR/.." && pwd -P)"
REPO_ROOT="${REPO_ROOT:-$(git rev-parse --show-toplevel 2>/dev/null || true)}"
if [ -z "${REPO_ROOT:-}" ]; then
  echo "FATAL: REPO_ROOT is unset and git rev-parse --show-toplevel failed" >&2
  exit 2
fi
export REPO_ROOT
cd "$REPO_ROOT"
source "$SCRIPT_DIR/repo_slug.sh"
set_gh_repo_args 0 0 || exit $?
git fetch origin --quiet 2>/dev/null

list_loop_codex() {
  python3 "$SKILL_ROOT/scripts/concurrency_monitor.py" --list-codex
}

count_loop_codex() {
  python3 "$SKILL_ROOT/scripts/concurrency_monitor.py" --count-only
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

# 0. CRITICAL: maintainer 评论(non-AI 即非 sentinel)
# 规则:
#   (a) 最近 12h 任何 issue/PR 的 maintainer 评论 — 显示
#   (b) stuck-label issue 的 LATEST maintainer 评论 — 无论多久都显示(避免漏读)
echo ""
echo "▍🚨 maintainer 评论(read first — 漏读 = controller bug):"
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
# 12h cutoff OR no AI reply since(无回应必显示,无论多久)
if delta_h > 12 and has_ai_reply: sys.exit(0)
flag = '⏰ no-AI-reply' if not has_ai_reply else ''
author = (last.get('author', {}) or {}).get('login', '?')
body = (last.get('body','') or '').replace('\n',' ')[:200]
print(f'  {flag} [{author}] ${num} ${kind} ({delta_h:.1f}h ago): {body}')
" 2>/dev/null
done

# 1. Active codex
n=$(count_loop_codex)
echo ""
echo "▍活跃 codex: ${n}"
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
echo "▍Monitor zero_streak (过去 10 tick):"
tail -10 .refactor-loop/logs/concurrency-monitor.log 2>/dev/null | \
  grep -oE "zero_streak=[0-9]+" | sort -t= -k2 -rn | head -1 | sed 's/^/  最大: /'
zero_now=$(tail -1 .refactor-loop/logs/concurrency-monitor.log 2>/dev/null | grep -oE "zero_streak=[0-9]+" | head -1)
[ -n "$zero_now" ] && echo "  当前: ${zero_now}"

# 5. Mergeable PRs (per reviewer consensus + CI green)
# Refactor (iter3/skill-merge-policy): Old pattern: unanimous-approve merge gate + Phase 8 文案矛盾  New principle: 固定真值表 reject=0 && approve>=1 → MERGE;comment 是 advisory(#26 minimal option B 共识)
echo ""
echo "▍可合并 PR(controller 应立即 merge):"
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
echo "▍Stale labels(已 CLOSED 但还挂 in-flight phase 标签):"
gh issue list "${gh_repo_args[@]}" --label "auto-loop" --state closed --limit 30 --json number,labels --jq '.[] | "\(.number)|\(.labels | map(.name) | map(select(. | startswith("🔍") or startswith("🛠") or startswith("🔧") or startswith("👀") or startswith("⏸") or startswith("auto-loop-stuck") or startswith("🆘"))) | join(","))"' 2>/dev/null | while IFS='|' read -r num labels; do
  [ -z "$num" ] || [ -z "$labels" ] && continue
  echo "  ⚠️ closed issue #${num} 仍挂: ${labels}  → controller 应清理 + 加 🎉 phase:merged"
done
gh pr list "${gh_repo_args[@]}" --label "auto-loop" --state closed --limit 30 --json number,labels --jq '.[] | "\(.number)|\(.labels | map(.name) | map(select(. | startswith("🔍") or startswith("🛠") or startswith("🔧") or startswith("👀") or startswith("⏸") or startswith("auto-loop-stuck") or startswith("🆘") or startswith("🚀"))) | join(","))"' 2>/dev/null | while IFS='|' read -r num labels; do
  [ -z "$num" ] || [ -z "$labels" ] && continue
  echo "  ⚠️ closed PR #${num} 仍挂: ${labels}"
done

# 5c. Linkage check:open issues phase:implementing 但没对应 in-flight PR / open PRs 没对应 design issue
echo ""
echo "▍Issue/PR linkage 不一致:"
gh issue list "${gh_repo_args[@]}" --label "🛠️ phase:implementing" --state open --json number,title --jq '.[] | "\(.number)|\(.title)"' 2>/dev/null | while IFS='|' read -r num title; do
  [ -z "$num" ] && continue
  # 找 closes #num 的 open PR
  pr_count=$(gh pr list "${gh_repo_args[@]}" --label "auto-loop" --state open --search "in:body Closes #${num}" --json number --jq 'length' 2>/dev/null || echo 0)
  if [ "$pr_count" = "0" ]; then
    # 还要找 closed PR 是否已 merge
    merged_pr=$(gh pr list "${gh_repo_args[@]}" --label "auto-loop" --state merged --search "in:body Closes #${num}" --json number --jq '.[0].number' 2>/dev/null)
    if [ -z "$merged_pr" ] || [ "$merged_pr" = "null" ]; then
      echo "  ⚠️ issue #${num} [🛠️ implementing] 无对应 in-flight 或 merged PR(implement codex 失败/未派?)"
    else
      echo "  ⚠️ issue #${num} [🛠️ implementing] PR #${merged_pr} 已 merged 但 issue 未关 — controller 应 gh issue close"
    fi
  fi
done

# 5d. Spawn drop detection: 3 solver artifact 完整但对应 judge log 没有
echo ""
echo "▍Spawn drop(solver 完成 N 但 judge 漏派):"
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
    echo "  ⚠️ issue #${issue} r${round} 3 solver done 但 judge log 不存在(需重派 judge)"
  fi
done

# 6. Drift detection: phase:* label set but no log file being actively written for that issue/PR
echo ""
echo "▍Drift(label vs codex 不一致):"
active_logs_file=$(mktemp)
# 用 log 文件最近活跃来判断:no EXIT= 末行 + mtime < 10 min = codex 在跑
for f in .refactor-loop/logs/*.log; do
  [ -f "$f" ] || continue
  # mtime > 10 min: 没在更新
  [ -n "$(find "$f" -mmin -10)" ] || continue
  # 已有 EXIT=:已完成
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
    echo "  ⚠️ ${kind} #${num} label=${phase} 但 0 codex referencing"
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

# 7. Stale worktree(branch 已 merged 但 worktree 还在)
echo ""
echo "▍Stale worktree(branch 已 merged 应清理):"
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
      echo "  ⚠️ $wt  branch=$branch(remote 已不存在 — git worktree remove $wt --force && git branch -D $branch)"
    fi
  fi
done

# 8. Stuck-too-long(issue/PR 有 stuck label 且 last non-AI comment > 6h)
echo ""
echo "▍Stuck too long(>6h 无 maintainer 回复;考虑 4h reflector 重判):"
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
