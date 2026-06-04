#!/usr/bin/env python3
"""Source-regression tests for narrow per-role marker emission allowlists."""

from __future__ import annotations

import re
import unittest
from pathlib import Path


SCRIPT_PATH = Path(__file__)
PROMPTS_DIR = SCRIPT_PATH.parents[1] / "prompts"

CONTRACT_MARKER = "MarkerEmissionContract: single-valid-invalid-role-marker-source"
SECTION_HEADING_PATTERNS = (
    "## Marker emission allowlist(强制)",
    "## Marker emission allowlist (required)",
)
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
    "TRIAGE_DECISION_DONE",
    "REBASE_RESOLVE_DONE",
    "REBASE_RESOLVE_BLOCKED",
    "PUBLISH_FALLBACK_DONE",
    "PUBLISH_FALLBACK_BLOCKED",
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
        "TRIAGE_DECISION_DONE:${ISSUE_NUMBER}:accept:.refactor-loop/runs/triage-issue-${ISSUE_NUMBER}.json",
        "TRIAGE_DECISION_DONE:${ISSUE_NUMBER}:reject:.refactor-loop/runs/triage-issue-${ISSUE_NUMBER}.json",
    ),
    "rebase-resolve.md": (
        "REBASE_RESOLVE_DONE:${PR_NUMBER}:<status>",
        "REBASE_RESOLVE_BLOCKED:${PR_NUMBER}:<conflict|human-decision|build-broken|other>:<short>",
    ),
    "publish-implementation-fallback.md": (
        "PUBLISH_FALLBACK_DONE:${ISSUE_NUMBER}:<status>",
        "PUBLISH_FALLBACK_BLOCKED:${ISSUE_NUMBER}:<conflict|human-decision|build-broken|other>:<short>",
    ),
}

KNOWN_ARTIFACT_PROFILES = {
    "phase9-solver",
    "phase9-delete-solver",
    "phase9-meta-judge",
    "phase8-reviewer",
    "review-fix",
    "marker-only-work-unit",
    "github-ai-post-body",
}

PROMPT_ARTIFACT_PROFILES = {
    "audit.md": "marker-only-work-unit",
    "implement.md": "marker-only-work-unit",
    "verify.md": "marker-only-work-unit",
    "remote-ci-fix.md": "marker-only-work-unit",
    "review-fix.md": "review-fix",
    "reviewer-architect.md": "phase8-reviewer",
    "reviewer-tests.md": "phase8-reviewer",
    "reviewer-quality.md": "phase8-reviewer",
    "solver-minimal.md": "phase9-solver",
    "solver-structural.md": "phase9-solver",
    "solver-delete.md": "phase9-delete-solver",
    "meta-judge.md": "phase9-meta-judge",
    "meta-reflector-stalled.md": "marker-only-work-unit",
    "test-add.md": "marker-only-work-unit",
    "triage-external-issue.md": "marker-only-work-unit",
    "rebase-resolve.md": "marker-only-work-unit",
    "publish-implementation-fallback.md": "marker-only-work-unit",
}

PROFILE_TERMINAL_MARKER_TOKENS = {
    "marker-only-work-unit": {
        "AUDIT_DONE",
        "AUDIT_INCOMPLETE",
        "SCOPE_EXTEND",
        "IMPLEMENT_DONE",
        "VERIFY_DONE",
        "REMOTE_CI_FIX_DONE",
        "META_RESOLVED",
        "TEST_BLOCKED",
        "TEST_ADD_DONE",
        "TRIAGE_DECISION_DONE",
        "REBASE_RESOLVE_DONE",
        "REBASE_RESOLVE_BLOCKED",
        "PUBLISH_FALLBACK_DONE",
        "PUBLISH_FALLBACK_BLOCKED",
    },
    "review-fix": {"FIX_DONE", "FIX_BLOCKED"},
    "phase8-reviewer": {"REVIEW_DONE"},
    "phase9-solver": {"SOLVER_DONE"},
    "phase9-delete-solver": {"SOLVER_DONE"},
    "phase9-meta-judge": {"META_JUDGE_DONE"},
}


def allowlist_section(body: str) -> str:
    starts = [(body.find(heading), heading) for heading in SECTION_HEADING_PATTERNS]
    starts = [(start, heading) for start, heading in starts if start != -1]
    if not starts:
        return ""
    start, heading = min(starts)
    rest = body[start + len(heading):]
    next_heading = re.search(r"\n## (?!Marker emission allowlist)", rest)
    if next_heading:
        rest = rest[: next_heading.start()]
    return heading + rest


def marker_token(marker: str) -> str:
    return marker.split(":", 1)[0]


def artifact_profile_anchors(body: str) -> list[str]:
    return re.findall(r"(?m)^Artifact profile: ([a-z0-9-]+)$", body)


class MarkerEmissionContractTests(unittest.TestCase):
    # Refactor (iter205/issue-205):
    #   Old pattern: dogfood 实测的运维经验(audit 并行撞 iteration 号、新 role prompt 漏注册 marker contract、review verdict grep log tail 误判、daemon 恢复手 kill)只靠 agent 记忆,没落进 skill 合同与机械验证。
    #   New principle: 把四条经验写回局部合同:SKILL.md 增 #205 反面规则段(audit 同一时刻单 active iteration、新 role prompt 必同步 marker inventory、review verdict 权威源优先 review artifact frontmatter、daemon 恢复只走 restart-daemons);audit.md 渲染后 ITERATION 空则 fail-closed;peek.py 局部优先读 review artifact frontmatter verdict;配套 source-regression + behavior test。不新增跨模块抽象层。
    def test_each_prompt_declares_exact_allowlist_section(self) -> None:
        for filename, allowed_markers in PROMPT_ALLOWLISTS.items():
            with self.subTest(prompt=filename):
                body = (PROMPTS_DIR / filename).read_text(encoding="utf-8")
                section = allowlist_section(body)

                self.assertTrue(any(heading in section for heading in SECTION_HEADING_PATTERNS))
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
            if path.name
            not in {"_github-post-rules.md", "design-issue-body.md", "design-issue-reply.md", "release-rollup-body.md"}
        }

        self.assertEqual(role_prompt_files, set(PROMPT_ALLOWLISTS))
        self.assertEqual(role_prompt_files, set(PROMPT_ARTIFACT_PROFILES))

    def test_active_prompts_do_not_require_parallel_language_sections(self) -> None:
        forbidden_patterns = (
            r"##\s+Concrete plan \(English\)",
            r"##\s+Concrete plan \(中文\)",
            r"##\s+English\b",
            r"##\s+中文\b",
            r"(?<!do not add a )mandatory parallel English",
            r"required peer to 中文",
            r"_en\s*\+\s*_zh",
        )
        offenders: list[str] = []
        for path in sorted(PROMPTS_DIR.glob("*.md")):
            text = path.read_text(encoding="utf-8")
            for pattern in forbidden_patterns:
                if re.search(pattern, text):
                    offenders.append(f"{path.name}: {pattern}")

        self.assertEqual(offenders, [])

    def test_each_marker_prompt_declares_exactly_one_known_artifact_profile(self) -> None:
        self.assertEqual(set(PROMPT_ARTIFACT_PROFILES), set(PROMPT_ALLOWLISTS))
        for filename, expected_profile in PROMPT_ARTIFACT_PROFILES.items():
            with self.subTest(prompt=filename):
                body = (PROMPTS_DIR / filename).read_text(encoding="utf-8")
                anchors = artifact_profile_anchors(body)

                self.assertEqual(anchors, [expected_profile])
                self.assertIn(expected_profile, KNOWN_ARTIFACT_PROFILES)

    def test_prompt_artifact_profile_terminal_marker_policy_matches_allowlist(self) -> None:
        for filename, allowed_markers in PROMPT_ALLOWLISTS.items():
            with self.subTest(prompt=filename):
                profile = PROMPT_ARTIFACT_PROFILES[filename]
                profile_tokens = PROFILE_TERMINAL_MARKER_TOKENS[profile]
                allowed_tokens = {marker_token(marker) for marker in allowed_markers}

                self.assertLessEqual(
                    allowed_tokens,
                    profile_tokens,
                    f"{filename} allowlist tokens must fit profile {profile}",
                )

    def test_meta_judge_prompt_does_not_authorize_fresh_stalled_marker(self) -> None:
        # Refactor (issue-304): Old: meta-judge allowlist authorized a fresh
        # `META_JUDGE_DONE:escalate:stalled` output. New: only router-owned
        # predicate logic can derive the stalled reflector continuation.
        body = (PROMPTS_DIR / "meta-judge.md").read_text(encoding="utf-8")
        section = allowlist_section(body)

        self.assertNotIn("META_JUDGE_DONE:escalate:stalled:<short>", section)
        self.assertNotIn("Escalate stalled", body)
        self.assertIn("meta-judge emits only consensus/converge", body)
        self.assertIn("router-owned stalled predicate", body)

    def test_github_post_rules_declares_post_body_artifact_profile(self) -> None:
        body = (PROMPTS_DIR / "_github-post-rules.md").read_text(encoding="utf-8")
        self.assertEqual(artifact_profile_anchors(body), ["github-ai-post-body"])

    def test_release_rollup_body_prompt_is_github_body_not_role_marker_prompt(self) -> None:
        body = (PROMPTS_DIR / "release-rollup-body.md").read_text(encoding="utf-8")

        self.assertEqual(artifact_profile_anchors(body), ["github-ai-post-body"])
        self.assertEqual(allowlist_section(body), "")
        self.assertIn("Do not run `gh`.", body)
        self.assertIn("Do not create, edit, label, close, merge, tag, or release anything.", body)

    def test_skill_documents_prompt_inventory_sync_for_new_role_prompts(self) -> None:
        skill = (PROMPTS_DIR.parents[0] / "SKILL.md").read_text(encoding="utf-8")
        section_start = skill.find("## Dogfood anti-rules(per #205)")
        section_end = skill.find("## Wakeup Skeleton", section_start)
        section = skill[section_start:section_end]

        self.assertIn("Any new role prompt", section)
        self.assertIn("test_marker_emission_contract.py", section)
        self.assertIn("PROMPT_ALLOWLISTS", section)
        self.assertIn("PROMPT_ARTIFACT_PROFILES", section)


if __name__ == "__main__":
    unittest.main()
