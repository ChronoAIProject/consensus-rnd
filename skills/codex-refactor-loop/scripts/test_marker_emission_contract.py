#!/usr/bin/env python3
"""Source-regression tests for narrow per-role marker emission allowlists."""

from __future__ import annotations

import re
import unittest
from pathlib import Path


SCRIPT_PATH = Path(__file__)
PROMPTS_DIR = SCRIPT_PATH.parents[1] / "prompts"

CONTRACT_MARKER = "MarkerEmissionContractV1: single-valid-invalid-role-marker-source"
SECTION_HEADING = "## Marker emission allowlist(强制)"
REQUIRED_PROHIBITION = (
    "Only the markers listed above are valid role-routing markers for this prompt. "
    "Do not emit any other role-routing marker."
)

ROLE_MARKER_TOKENS = (
    "AUDIT_DONE",
    "AUDIT_INCOMPLETE",
    "SCOPE_EXTEND",
    "IMPLEMENT_DONE",
    "VERIFY_DONE",
    "REMOTE_CI_FIX_DONE",
    "REVIEW_DONE",
    "FIX_DONE",
    "FIX_BLOCKED",
    "SOLVER_DONE",
    "META_JUDGE_DONE",
    "META_RESOLVED",
    "TEST_BLOCKED",
    "TEST_ADD_DONE",
    "TRIAGE_DONE",
)

PROMPT_ALLOWLISTS = {
    "audit.md": (
        "AUDIT_DONE:$REPO_ROOT/.refactor-loop/runs/audit-iter-${ITERATION}.md:<N>",
        "AUDIT_INCOMPLETE:<reason>",
    ),
    "implement.md": (
        "SCOPE_EXTEND:<file>:<reason>",
        "IMPLEMENT_DONE:${CLUSTER_ID}:<status>",
    ),
    "verify.md": (
        "VERIFY_DONE:${CLUSTER_ID}:<verdict>",
    ),
    "remote-ci-fix.md": (
        "REMOTE_CI_FIX_DONE:${CHECK_NAME}:<status>",
    ),
    "review-fix.md": (
        "SCOPE_EXTEND:<file>:<reason>",
        "FIX_DONE:${PR_NUMBER}:round-${FIX_ROUND}:applied-<N>:rejected-<M>:blocked-<K>",
        "FIX_BLOCKED:${PR_NUMBER}:round-${FIX_ROUND}:<conflict|human-decision|build-broken|other>:<short>",
    ),
    "reviewer-architect.md": (
        "REVIEW_DONE:${PR_NUMBER}:architect:<verdict>",
    ),
    "reviewer-tests.md": (
        "REVIEW_DONE:${PR_NUMBER}:tests:<verdict>",
    ),
    "reviewer-quality.md": (
        "REVIEW_DONE:${PR_NUMBER}:quality:<verdict>",
    ),
    "solver-minimal.md": (
        "SOLVER_DONE:minimal:propose:<one-line summary>",
        "SOLVER_DONE:minimal:abstain:<reason>",
        "SOLVER_DONE:minimal:escalate:gpg-ratification:<reason>",
        "SOLVER_DONE:minimal:escalate:no-plan:<reason>",
        "SOLVER_DONE:minimal:false-positive:<reason>",
    ),
    "solver-structural.md": (
        "SOLVER_DONE:structural:propose:<summary>",
        "SOLVER_DONE:structural:abstain:<reason>",
        "SOLVER_DONE:structural:escalate:gpg-ratification:<reason>",
        "SOLVER_DONE:structural:escalate:no-plan:<reason>",
        "SOLVER_DONE:structural:false-positive:<reason>",
    ),
    "solver-delete.md": (
        "SOLVER_DONE:delete:propose:<summary>",
        "SOLVER_DONE:delete:abstain:<reason>",
        "SOLVER_DONE:delete:escalate:gpg-ratification:<reason>",
        "SOLVER_DONE:delete:escalate:no-plan:<reason>",
        "SOLVER_DONE:delete:false-positive:<reason>",
    ),
    "meta-judge.md": (
        "META_JUDGE_DONE:consensus:<framing>:<summary>",
        "META_JUDGE_DONE:converge:round-N:<question>",
        "META_JUDGE_DONE:escalate:stalled:<short>",
    ),
    "meta-reflector-stalled.md": (
        "META_RESOLVED:retry-fix:<reason>",
        "META_RESOLVED:re-design:<reason>",
        "META_RESOLVED:re-cluster:<reason>",
        "META_RESOLVED:drop:<reason>",
        "META_RESOLVED:escalate-human:<reason>",
    ),
    "test-add.md": (
        "TEST_BLOCKED:<reason>",
        "TEST_ADD_DONE:${CLUSTER_ID}:<status>",
    ),
    "triage-external-issue.md": (
        "TRIAGE_DONE:${ISSUE_NUMBER}:accept:issue-${ISSUE_NUMBER}",
        "TRIAGE_DONE:${ISSUE_NUMBER}:reject:<reject-type>",
    ),
}


def allowlist_section(body: str) -> str:
    start = body.find(SECTION_HEADING)
    if start == -1:
        return ""
    rest = body[start + len(SECTION_HEADING):]
    next_heading = re.search(r"\n## (?!Marker emission allowlist)", rest)
    if next_heading:
        rest = rest[: next_heading.start()]
    return SECTION_HEADING + rest


def marker_token(marker: str) -> str:
    return marker.split(":", 1)[0]


class MarkerEmissionContractTests(unittest.TestCase):
    def test_each_prompt_declares_exact_allowlist_section(self) -> None:
        for filename, allowed_markers in PROMPT_ALLOWLISTS.items():
            with self.subTest(prompt=filename):
                body = (PROMPTS_DIR / filename).read_text(encoding="utf-8")
                section = allowlist_section(body)

                self.assertIn(SECTION_HEADING, section)
                self.assertIn(CONTRACT_MARKER, section)
                self.assertIn("ALLOWED markers:", section)
                self.assertIn(REQUIRED_PROHIBITION, section)
                for marker in allowed_markers:
                    self.assertIn(f"- `{marker}`", section)

    def test_allowlist_sections_do_not_authorize_other_role_marker_tokens(self) -> None:
        for filename, allowed_markers in PROMPT_ALLOWLISTS.items():
            with self.subTest(prompt=filename):
                body = (PROMPTS_DIR / filename).read_text(encoding="utf-8")
                section = allowlist_section(body)
                allowed_tokens = {marker_token(marker) for marker in allowed_markers}
                forbidden_tokens = set(ROLE_MARKER_TOKENS) - allowed_tokens

                for token in forbidden_tokens:
                    self.assertNotIn(
                        f"`{token}:",
                        section,
                        f"{filename} allowlist must not authorize other role marker token {token}",
                    )
                    self.assertNotIn(
                        f"- `{token}",
                        section,
                        f"{filename} allowlist must not list other role marker token {token}",
                    )

    def test_no_prompt_missing_from_contract(self) -> None:
        role_prompt_files = {
            path.name for path in PROMPTS_DIR.glob("*.md")
            if path.name not in {"_github-post-rules.md", "design-issue-body.md", "design-issue-reply.md"}
        }

        self.assertEqual(role_prompt_files, set(PROMPT_ALLOWLISTS))


if __name__ == "__main__":
    unittest.main()
