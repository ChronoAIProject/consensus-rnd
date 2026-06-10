#!/usr/bin/env python3
"""Behavior fixtures for role artifact shape and routing-marker safety."""

from __future__ import annotations

from dataclasses import dataclass
import re
import unittest
from pathlib import Path


SCRIPT_PATH = Path(__file__)
SKILL_DIR = SCRIPT_PATH.parents[1]
PROMPTS_DIR = SKILL_DIR / "prompts"
RUNTIME_PROFILE_MODULE = SCRIPT_PATH.parent / "codex_refactor_loop" / "artifacts" / "profiles.py"
PROSE_PROFILE_RULES = PROMPTS_DIR / "_artifact-profile-rules.md"
SENTINEL = "⟦AI:AUTO-LOOP⟧"
PROFILE_AUTHORITY_PATH = "skills/consensus-loop/scripts/test_role_artifact_profiles.py"

# This mirrors test_marker_emission_contract.py while keeping profile rules test-only.
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
    "IMPLEMENTATION_PR_ARTIFACTS_DONE",
)


@dataclass(frozen=True)
class ArtifactProfile:
    required_metadata: tuple[str, ...]
    required_sections: tuple[str, ...]
    final_marker_patterns: tuple[str, ...]
    forbidden_marker_tokens: tuple[str, ...]
    sentinel_policy: str
    requires_folded_raw_artifact: bool = False
    requires_github_banner: bool = False


PROFILES = {
    "phase9-solver": ArtifactProfile(
        required_metadata=("solver", "issue", "cluster", "verdict"),
        required_sections=(
            "Recommended framing",
            "Concrete plan",
            "Risks",
            "Escalation triggers",
            "Reasoning trace",
        ),
        final_marker_patterns=(
            r"^SOLVER_DONE:(minimal|structural|delete):(propose|abstain|escalate|false-positive):.+$",
        ),
        forbidden_marker_tokens=tuple(token for token in ROLE_MARKER_TOKENS if token != "SOLVER_DONE"),
        sentinel_policy="penultimate-before-final-marker",
    ),
    "phase9-delete-solver": ArtifactProfile(
        required_metadata=("solver", "issue", "cluster", "verdict"),
        required_sections=(
            "Classification",
            "Recommended action",
            "Concrete plan",
            "Reverse-evidence",
            "Risks",
            "Escalation triggers",
            "Reasoning trace",
        ),
        final_marker_patterns=(
            r"^SOLVER_DONE:delete:(propose|abstain|escalate|false-positive):.+$",
        ),
        forbidden_marker_tokens=tuple(token for token in ROLE_MARKER_TOKENS if token != "SOLVER_DONE"),
        sentinel_policy="penultimate-before-final-marker",
    ),
    "phase9-meta-judge": ArtifactProfile(
        required_metadata=("issue", "cluster", "convergence_round", "solver_verdicts", "decision"),
        required_sections=("Decision", "If consensus", "If converge", "Round audit trail"),
        final_marker_patterns=(r"^META_JUDGE_DONE:(consensus|converge):.+$",),
        forbidden_marker_tokens=tuple(token for token in ROLE_MARKER_TOKENS if token != "META_JUDGE_DONE"),
        sentinel_policy="penultimate-before-final-marker",
    ),
    "phase8-reviewer": ArtifactProfile(
        required_metadata=("pr", "role", "verdict"),
        required_sections=("Verdict", "Evidence"),
        final_marker_patterns=(r"^REVIEW_DONE:[^:]+:(architect|tests|quality):.+$",),
        forbidden_marker_tokens=tuple(token for token in ROLE_MARKER_TOKENS if token != "REVIEW_DONE"),
        sentinel_policy="penultimate-before-final-marker",
    ),
    "review-fix": ArtifactProfile(
        required_metadata=("pr", "fix_round", "max_fix_rounds"),
        required_sections=(
            "Applied",
            "Rejected as false positive",
            "Blocked",
            "Build status",
            "Recommendation for next round",
        ),
        final_marker_patterns=(
            r"^FIX_DONE:[^:]+:round-\d+:applied-\d+:rejected-\d+:blocked-\d+$",
            r"^FIX_BLOCKED:[^:]+:round-\d+:(conflict|human-decision|build-broken|other):.+$",
        ),
        forbidden_marker_tokens=tuple(
            token for token in ROLE_MARKER_TOKENS if token not in {"FIX_DONE", "FIX_BLOCKED"}
        ),
        sentinel_policy="penultimate-before-final-marker",
    ),
    "marker-only-work-unit": ArtifactProfile(
        required_metadata=(),
        required_sections=(),
        final_marker_patterns=(
            r"^(AUDIT_DONE|AUDIT_INCOMPLETE|SCOPE_EXTEND|IMPLEMENT_DONE|VERIFY_DONE|REMOTE_CI_FIX_DONE|"
            r"META_RESOLVED|TEST_BLOCKED|TEST_ADD_DONE|TRIAGE_DECISION_DONE|"
            r"IMPLEMENTATION_PR_ARTIFACTS_DONE):.+$",
        ),
        forbidden_marker_tokens=("REVIEW_DONE", "FIX_DONE", "FIX_BLOCKED", "SOLVER_DONE", "META_JUDGE_DONE"),
        sentinel_policy="penultimate-before-final-marker",
    ),
    "github-ai-post-body": ArtifactProfile(
        required_metadata=(),
        required_sections=("TL;DR", "详细说明"),
        final_marker_patterns=(),
        forbidden_marker_tokens=(),
        sentinel_policy="final-sentinel",
        requires_folded_raw_artifact=True,
        requires_github_banner=True,
    ),
}


VALID_SOLVER = """---
solver: minimal
issue: 169
cluster: issue-169
verdict: propose
---

## Recommended framing
Use a test-only profile inventory.

## Concrete plan
- Add fixture tests.

## Risks
- Future runtime consumers need a separate design.

## Escalation triggers (if any)
- none

## Reasoning trace
The profile checks remain shape-only.

⟦AI:AUTO-LOOP⟧
SOLVER_DONE:minimal:propose:test-only profile inventory
"""

VALID_DELETE_SOLVER = """---
solver: delete
issue: 169
cluster: issue-169
verdict: propose
---

## Classification
a

## Recommended action
Delete the dead path.

## Concrete plan (if propose)
- Files to delete: one file

## Reverse-evidence (why this is safe to delete)
- No callers.

## Risks
- Caller search could be incomplete.

## Escalation triggers (if any)
- none

## Reasoning trace
The profile matches the delete solver template.

⟦AI:AUTO-LOOP⟧
SOLVER_DONE:delete:propose:delete dead path
"""

VALID_META_JUDGE = """---
issue: 169
cluster: issue-169
convergence_round: 2
solver_verdicts:
  minimal: propose
  structural: propose
  delete: propose
decision: consensus
---

## Decision
Consensus reached on test-only profile fixtures.

## If consensus
- Chosen framing: test-only

## If converge
- Not applicable.

## Round audit trail
- solver-minimal: path

⟦AI:AUTO-LOOP⟧
META_JUDGE_DONE:consensus:test-only:all solvers aligned
"""

VALID_REVIEWER = """---
pr: 42
role: architect
verdict: approve
---

## Verdict
Approve.

## Evidence
- None.

⟦AI:AUTO-LOOP⟧
REVIEW_DONE:42:architect:approve
"""

VALID_REVIEW_FIX = """---
pr: 42
fix_round: 2
max_fix_rounds: 5
---

## Applied
- Fixed one demand.

## Rejected as false positive
- None.

## Blocked
- None.

## Build status
- build: pass
- tests: pass

## Recommendation for next round
- Re-review.

⟦AI:AUTO-LOOP⟧
FIX_DONE:42:round-2:applied-1:rejected-0:blocked-0
"""

VALID_MARKER_ONLY = """---
cluster_id: issue-169
status: ok
---

## Summary
Implementation finished.

⟦AI:AUTO-LOOP⟧
IMPLEMENT_DONE:issue-169:ok
"""

VALID_GITHUB_POST = """## 🤖 Profile check reached consensus

### TL;DR
- 这是什么:test-only artifact profile fixture。
- 现在到哪一步:已完成。
- 下一步:controller 继续。

---

### 详细说明

正文保持中文,raw artifact 折叠。

---

<details>
<summary>📎 完整 codex 原始输出(存档备查)</summary>

---
solver: minimal
---

⟦AI:AUTO-LOOP⟧
SOLVER_DONE:minimal:propose:test-only

</details>

⟦AI:AUTO-LOOP⟧
"""


def frontmatter_keys(text: str) -> set[str]:
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        return set()
    keys = set()
    for line in lines[1:]:
        if line == "---":
            return keys
        if line and not line.startswith(" ") and ":" in line:
            keys.add(line.split(":", 1)[0])
    return keys


def section_headings(text: str) -> set[str]:
    return set(re.findall(r"(?m)^#{2,3} (.+)$", text))


def missing_required_sections(text: str, required_sections: tuple[str, ...]) -> list[str]:
    headings = section_headings(text)
    missing = []
    for section in required_sections:
        if not any(heading == section or heading.startswith(f"{section} ") for heading in headings):
            missing.append(section)
    return missing


def role_marker_occurrences(text: str) -> list[str]:
    token_pattern = "|".join(re.escape(token) for token in ROLE_MARKER_TOKENS)
    return re.findall(rf"(?<![A-Z_])(?:{token_pattern}):[^\n`]*", text)


def final_nonempty_line(text: str) -> str:
    for line in reversed(text.splitlines()):
        if line.strip():
            return line.strip()
    return ""


def fenced_marker_templates(section: str) -> tuple[str, ...]:
    templates = []
    token_pattern = "|".join(re.escape(token) for token in ROLE_MARKER_TOKENS)
    for template in re.findall(r"`([^`\n]+)`", section):
        if re.match(rf"^(?:{token_pattern}):", template):
            templates.append(template)
    return tuple(templates)


def review_fix_terminal_marker_section(body: str) -> str:
    match = re.search(
        r"End your output with EXACTLY one of:\n(?P<section>.*?)\n## Marker emission allowlist",
        body,
        flags=re.S,
    )
    return match.group("section") if match else ""


def marker_allowlist_section(body: str) -> str:
    match = re.search(
        r"## Marker emission allowlist(?:\(强制\)| \(required\))\n(?P<section>.*?)\n## Hard rules",
        body,
        flags=re.S,
    )
    return match.group("section") if match else ""


def validate_internal_artifact(profile_id: str, text: str) -> list[str]:
    profile = PROFILES[profile_id]
    errors: list[str] = []
    lines = [line.rstrip() for line in text.strip().splitlines()]
    final_line = final_nonempty_line(text)

    missing_metadata = set(profile.required_metadata) - frontmatter_keys(text)
    if missing_metadata:
        errors.append(f"missing metadata: {sorted(missing_metadata)}")

    missing_sections = missing_required_sections(text, profile.required_sections)
    if missing_sections:
        errors.append(f"missing sections: {missing_sections}")

    markers = role_marker_occurrences(text)
    if len(markers) != 1:
        errors.append(f"expected exactly one routing marker, found {len(markers)}")

    if not any(re.match(pattern, final_line) for pattern in profile.final_marker_patterns):
        errors.append("final line does not match allowed marker pattern")

    if len(lines) < 2 or lines[-2] != SENTINEL:
        errors.append("sentinel must be the penultimate line before the final marker")
    if text.count(SENTINEL) != 1:
        errors.append("internal artifact must contain exactly one sentinel")

    for token in profile.forbidden_marker_tokens:
        if re.search(rf"(?<![A-Z_]){re.escape(token)}:", text):
            errors.append(f"forbidden routing marker token: {token}")

    return errors


def validate_github_post_body(text: str) -> list[str]:
    profile = PROFILES["github-ai-post-body"]
    errors: list[str] = []
    lines = [line.rstrip() for line in text.strip().splitlines()]
    final_line = final_nonempty_line(text)

    if profile.requires_github_banner and not lines[0].startswith("## 🤖 "):
        errors.append("first line must start with GitHub AI banner")

    missing_sections = missing_required_sections(text, profile.required_sections)
    if missing_sections:
        errors.append(f"missing sections: {missing_sections}")

    if profile.requires_folded_raw_artifact and not re.search(r"(?ms)<details>.+</details>", text):
        errors.append("raw artifact must be folded in details")

    if final_line != SENTINEL:
        errors.append("GitHub post body must end with final sentinel")

    if any(final_line.startswith(f"{token}:") for token in ROLE_MARKER_TOKENS):
        errors.append("GitHub post body must not end with a role-routing marker")

    return errors


class RoleArtifactProfileTests(unittest.TestCase):
    def assertInvalidBecause(self, profile_id: str, text: str, needle: str) -> None:
        errors = validate_internal_artifact(profile_id, text)
        self.assertTrue(any(needle in error for error in errors), errors)

    def test_phase9_solver_valid_fixture_passes_shape_only(self) -> None:
        self.assertEqual(validate_internal_artifact("phase9-solver", VALID_SOLVER), [])

    def test_phase9_delete_solver_valid_fixture_passes_shape_only(self) -> None:
        self.assertEqual(validate_internal_artifact("phase9-delete-solver", VALID_DELETE_SOLVER), [])

    def test_phase9_solver_rejects_delete_solver_output_shape(self) -> None:
        self.assertInvalidBecause("phase9-solver", VALID_DELETE_SOLVER, "missing sections")

    def test_phase9_solver_rejects_embedded_foreign_marker(self) -> None:
        artifact = VALID_SOLVER.replace(
            "The profile checks remain shape-only.",
            "The profile checks remain shape-only.\nREVIEW_DONE:42:architect:approve",
        )

        self.assertInvalidBecause("phase9-solver", artifact, "forbidden routing marker token: REVIEW_DONE")

    def test_meta_judge_missing_audit_trail_fails(self) -> None:
        artifact = VALID_META_JUDGE.replace("\n## Round audit trail\n- solver-minimal: path\n", "\n")

        self.assertInvalidBecause("phase9-meta-judge", artifact, "missing sections")

    def test_meta_judge_rejects_fresh_stalled_marker_fixture(self) -> None:
        # Refactor (issue-304): Old: phase9-meta-judge profile accepted
        # escalate markers. New: fresh meta-judge artifacts are limited to
        # consensus/converge; stalled is a router-owned derived route.
        artifact = VALID_META_JUDGE.replace(
            "META_JUDGE_DONE:consensus:test-only:all solvers aligned",
            "META_JUDGE_DONE:escalate:stalled:no-change",
        ).replace(
            "decision: consensus",
            "decision: converge",
        )

        self.assertInvalidBecause("phase9-meta-judge", artifact, "final line does not match")

    def test_reviewer_with_wrong_marker_fails(self) -> None:
        artifact = VALID_REVIEWER.replace("REVIEW_DONE:42:architect:approve", "SOLVER_DONE:minimal:propose:no")

        self.assertInvalidBecause("phase8-reviewer", artifact, "final line does not match")

    def test_review_fix_marker_count_shape(self) -> None:
        self.assertEqual(validate_internal_artifact("review-fix", VALID_REVIEW_FIX), [])
        artifact = VALID_REVIEW_FIX.replace("applied-1:rejected-0:blocked-0", "applied-one:rejected-0:blocked-0")

        self.assertInvalidBecause("review-fix", artifact, "final line does not match")

    def test_marker_only_work_unit_requires_sentinel_before_final_marker(self) -> None:
        self.assertEqual(validate_internal_artifact("marker-only-work-unit", VALID_MARKER_ONLY), [])
        artifact = VALID_MARKER_ONLY.replace(f"\n{SENTINEL}\nIMPLEMENT_DONE", "\nIMPLEMENT_DONE")

        self.assertInvalidBecause("marker-only-work-unit", artifact, "sentinel must be the penultimate")

    def test_github_ai_post_body_requires_banner_details_and_final_sentinel(self) -> None:
        self.assertEqual(validate_github_post_body(VALID_GITHUB_POST), [])
        missing_details = re.sub(r"(?ms)\n<details>.+?</details>\n", "\nraw artifact inline\n", VALID_GITHUB_POST)
        missing_sentinel = VALID_GITHUB_POST.replace(f"\n{SENTINEL}\n", "\n")

        self.assertTrue(
            any("raw artifact must be folded" in error for error in validate_github_post_body(missing_details))
        )
        self.assertTrue(
            any("must end with final sentinel" in error for error in validate_github_post_body(missing_sentinel))
        )

    def test_profile_inventory_is_shape_safety_only(self) -> None:
        allowed_fields = {
            "required_metadata",
            "required_sections",
            "final_marker_patterns",
            "forbidden_marker_tokens",
            "sentinel_policy",
            "requires_folded_raw_artifact",
            "requires_github_banner",
        }
        for profile in PROFILES.values():
            with self.subTest(profile=profile):
                self.assertEqual(set(profile.__dataclass_fields__), allowed_fields)
        source = SCRIPT_PATH.read_text(encoding="utf-8")
        forbidden_terms = (
            "plan " + "quality",
            "reviewer " + "quality",
            "verdict " + "correctness",
            "prose " + "quality",
            "good " + "plan",
            "bad " + "plan",
        )
        for forbidden in forbidden_terms:
            self.assertNotIn(forbidden, source)

    def test_profile_authority_stays_in_unittest_not_runtime_or_prompt_prose(self) -> None:
        self.assertTrue(str(SCRIPT_PATH).endswith(PROFILE_AUTHORITY_PATH))
        self.assertFalse(RUNTIME_PROFILE_MODULE.exists(), "do not add runtime artifact profile authority")
        self.assertFalse(PROSE_PROFILE_RULES.exists(), "do not add a second prose profile authority")
        skill_body = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
        self.assertNotIn("Artifact profile:", skill_body)

    def test_github_post_rules_profile_source_regression(self) -> None:
        body = (PROMPTS_DIR / "_github-post-rules.md").read_text(encoding="utf-8")
        self.assertIn("Artifact profile: github-ai-post-body", body)
        self.assertIn("## Body Structure (Mandatory)", body)
        self.assertIn("First line starts with `## 🤖 `", body)
        self.assertIn("Raw artifacts must be collapsed", body)

    def test_phase8_reviewer_profile_matches_live_prompt_output_sections(self) -> None:
        for filename in ("reviewer-architect.md", "reviewer-tests.md", "reviewer-quality.md"):
            with self.subTest(prompt=filename):
                body = (PROMPTS_DIR / filename).read_text(encoding="utf-8")

                self.assertIn("Artifact profile: phase8-reviewer", body)
                for section in PROFILES["phase8-reviewer"].required_sections:
                    self.assertRegex(body, rf"(?m)^## {re.escape(section)}(?:\b|$)")
                self.assertNotIn("## Findings", body)
                self.assertNotIn("## Risks", body)
                self.assertNotIn("## Recommendation", body)

    def test_review_fix_profile_matches_live_prompt_output_sections(self) -> None:
        body = (PROMPTS_DIR / "review-fix.md").read_text(encoding="utf-8")

        self.assertIn("Artifact profile: review-fix", body)
        for section in PROFILES["review-fix"].required_sections:
            self.assertRegex(body, rf"(?m)^## {re.escape(section)}(?:\b|$)")
        self.assertNotIn("Rejected as false-positive findings", body)

    def test_review_fix_terminal_marker_contract_is_single_source(self) -> None:
        body = (PROMPTS_DIR / "review-fix.md").read_text(encoding="utf-8")
        expected_templates = (
            "FIX_DONE:${PR_NUMBER}:round-${FIX_ROUND}:applied-<N>:rejected-<M>:blocked-<K>",
            "FIX_BLOCKED:${PR_NUMBER}:round-${FIX_ROUND}:<conflict|human-decision|build-broken|other>:<short>",
        )

        self.assertEqual(fenced_marker_templates(review_fix_terminal_marker_section(body)), expected_templates)
        self.assertEqual(fenced_marker_templates(marker_allowlist_section(body)), expected_templates)
        self.assertEqual(
            PROFILES["review-fix"].final_marker_patterns,
            (
                r"^FIX_DONE:[^:]+:round-\d+:applied-\d+:rejected-\d+:blocked-\d+$",
                r"^FIX_BLOCKED:[^:]+:round-\d+:(conflict|human-decision|build-broken|other):.+$",
            ),
        )
        self.assertEqual(
            PROFILES["review-fix"].forbidden_marker_tokens,
            tuple(token for token in ROLE_MARKER_TOKENS if token not in {"FIX_DONE", "FIX_BLOCKED"}),
        )

    def test_phase9_solver_profiles_match_live_prompt_output_sections(self) -> None:
        prompt_profiles = {
            "solver-minimal.md": "phase9-solver",
            "solver-structural.md": "phase9-solver",
            "solver-delete.md": "phase9-delete-solver",
        }
        for filename, profile_id in prompt_profiles.items():
            with self.subTest(prompt=filename):
                body = (PROMPTS_DIR / filename).read_text(encoding="utf-8")

                self.assertIn(f"Artifact profile: {profile_id}", body)
                for section in PROFILES[profile_id].required_sections:
                    self.assertRegex(body, rf"(?m)^## {re.escape(section)}(?:\b|$)")
                for other_profile_id, other_profile in PROFILES.items():
                    if not other_profile_id.startswith("phase9-") or other_profile_id == profile_id:
                        continue
                    for section in set(other_profile.required_sections) - set(PROFILES[profile_id].required_sections):
                        self.assertNotRegex(body, rf"(?m)^## {re.escape(section)}(?:\b|$)")

    def test_marker_only_profile_does_not_invent_shared_output_sections(self) -> None:
        self.assertEqual(PROFILES["marker-only-work-unit"].required_metadata, ())
        self.assertEqual(PROFILES["marker-only-work-unit"].required_sections, ())

    def test_profiled_internal_prompts_distinguish_sentinel_from_final_marker(self) -> None:
        contradicted = []
        for path in sorted(PROMPTS_DIR.glob("*.md")):
            body = path.read_text(encoding="utf-8")
            if "Artifact profile:" not in body or "Artifact profile: github-ai-post-body" in body:
                continue
            if "runs/*.md` artifact" in body and "必须末尾独立一行**加 sentinel" in body:
                contradicted.append(path.name)
            if not re.search(r"sentinel.*penultimate line.*final routing marker", body):
                contradicted.append(f"{path.name}: missing internal sentinel policy")

        self.assertEqual(contradicted, [])


if __name__ == "__main__":
    unittest.main()
