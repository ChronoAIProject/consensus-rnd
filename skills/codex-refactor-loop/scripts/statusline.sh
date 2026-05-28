#!/bin/bash
# Claude Code statusline reader (per #51 r3 consensus C)
#
# Refactor (iter4/issue51-r3-consensus):
#   Old pattern: no ambient visibility; maintainers had to run peek.sh manually.
#   New principle: concurrency_monitor tick atomically writes statusline-snapshot.json;
#     statusline.sh is a read-only < 200ms consumer with no new daemon and no checked-in installer
#     (per #51 r3 META_JUDGE_DONE:consensus:C-minimal-statusline-via_concurrency_monitor-snapshot).

set -e

repo_root="${REPO_ROOT:-$(git rev-parse --show-toplevel 2>/dev/null || true)}"
if [ -z "${repo_root:-}" ]; then
  echo "⏸ no-snapshot"
  exit 0
fi

SNAPSHOT="${repo_root}/.refactor-loop/state/statusline-snapshot.json"
if [ ! -f "$SNAPSHOT" ]; then
  echo "⏸ no-snapshot"
  exit 0
fi

actual=$(jq -r '.actual' "$SNAPSHOT")
floor=$(jq -r '.floor' "$SNAPSHOT")
expected=$(jq -r '.expected' "$SNAPSHOT")
p0=$(jq -r '.p0_streak' "$SNAPSHOT")
prs=$(jq -r '.open_pr_count' "$SNAPSHOT")
issues=$(jq -r '.open_issue_count' "$SNAPSHOT")
freeze=$(jq -r '.freeze_minutes' "$SNAPSHOT")
d_healthy=$(jq -r '.daemons_healthy // 0' "$SNAPSHOT")
d_total=$(jq -r '.daemons_total // 0' "$SNAPSHOT")

icon="⚙"
color=""
if [ "$actual" -lt "$floor" ]; then
  icon="⚠"
  color="\033[31m"
fi
if [ "$d_total" -gt 0 ] && [ "$d_healthy" -lt "$d_total" ]; then
  icon="⚠"
  color="\033[31m"
fi
if [ "$freeze" -gt 10 ]; then
  icon="🔴"
  color="\033[31m"
fi

reset="\033[0m"
freeze_seg=""
if [ "$freeze" -gt 5 ]; then
  freeze_seg=" [STUCK ${freeze}m]"
fi
p0_seg=""
if [ "$p0" -gt 2 ]; then
  p0_seg=" P0×${p0}"
fi
d_seg=""
if [ "$d_total" -gt 0 ]; then
  d_seg=" d:${d_healthy}/${d_total}"
fi

printf "${color}${icon} ${actual}/${floor} PR:${prs} issue:${issues}${d_seg}${p0_seg}${freeze_seg}${reset}\n"
