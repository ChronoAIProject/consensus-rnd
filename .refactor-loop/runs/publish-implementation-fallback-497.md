publish implementation fallback for issue 497

State:
- No merge is in progress.
- Branch `refactor/iter497-issue-497` is at `37c4a68d88290c2dd8f036b35f1c9c24398dcf8b`.
- `origin/auto-refact-dev` is `947a2f6da03862a38b309852a01b94ac34f486e2` and is already an ancestor of `HEAD`.
- `git rev-list --left-right --count HEAD...origin/auto-refact-dev` reported `2 0`.
- The stale publish state was a committed merge plus unresolved conflict-marker text in the sshx contract test.

Changed files:
- `skills/sshx/tests/test_sshx_contract.py`: removed stale conflict markers and preserved both the worker-mode-gate/fallback-reason assertions and the worker-flight/retry assertions.

Verification:
- `rg -n '^(<<<<<<<|=======|>>>>>>>)' skills/sshx/tests/test_sshx_contract.py skills/sshx/SKILL.md` returned no matches.
- `python3 -m unittest discover -s skills/sshx/tests -p 'test_*.py'` passed: 20 tests OK.

Unresolved risk:
- None identified in the narrow fallback scope. Commit, push, PR updates, labels, and reviewer dispatch remain for the controller.

⟦AI:AUTO-LOOP⟧
PUBLISH_FALLBACK_DONE:497:resolved
