#!/usr/bin/env python3
"""Behavior tests for IssueDecompositionPlan validation."""

from __future__ import annotations

import json
import inspect
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any, Callable

import sys

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from codex_refactor_loop.context import LoopContext
from codex_refactor_loop import issue_decomposition
from codex_refactor_loop.issue_decomposition import (
    IssueDecompositionError,
    IssueDecompositionTrackingChild,
    append_issue_decomposition_tracking_block,
    build_issue_decomposition_tracking_block,
    issue_decomposition_child_fingerprint,
    issue_decomposition_plan_digest,
    load_issue_decomposition_plan,
    parse_issue_decomposition_tracking_comments,
    reconcile_issue_decomposition_tracking_children,
)


class IssueDecompositionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="issue-decomposition-test-"))
        (self.tmp / ".refactor-loop" / "runs").mkdir(parents=True)
        (self.tmp / ".config" / "consensus-rnd").mkdir(parents=True, exist_ok=True)
        (self.tmp / ".config" / "consensus-rnd" / "host.env").write_text(
            f'export REPO_ROOT="{self.tmp}"\nexport GH_REPO_SLUG="owner/repo"\n',
            encoding="utf-8",
        )
        self.ctx = LoopContext.load(repo_root=self.tmp, env={"CONSENSUS_RND_HOST_ENV": ".config/consensus-rnd/host.env"})
        self.consensus = ".refactor-loop/runs/phase9-issue403-r6-judge.md"
        (self.tmp / self.consensus).write_text("consensus artifact\n", encoding="utf-8")

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write_body(self, name: str, *, parent: int = 403, scope: str, non_goals: str) -> str:
        path = f".refactor-loop/runs/{name}.md"
        (self.tmp / path).write_text(
            "\n".join(
                [
                    "## child issue",
                    "",
                    f"Parent issue: #{parent}",
                    f"Source consensus artifact: {Path(self.consensus).name}",
                    f"Scope: {scope}",
                    f"Non-goals: {non_goals}",
                    "",
                    "<details>",
                    "<summary>内联 artifact 1: decision.md</summary>",
                    "",
                    "```markdown",
                    "raw decision",
                    "```",
                    "",
                    "</details>",
                    "",
                    "⟦AI:AUTO-LOOP⟧",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        return path

    def write_parent_comment(self, parent: int = 403) -> str:
        path = ".refactor-loop/runs/parent-comment.md"
        (self.tmp / path).write_text(
            f"Parent issue: #{parent}\n\nTracking child design issues.\n\n⟦AI:AUTO-LOOP⟧\n",
            encoding="utf-8",
        )
        return path

    def write_plan(self, payload: Any) -> Path:
        path = self.tmp / ".refactor-loop" / "runs" / "decomposition-plan.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def valid_payload(self) -> dict:
        return {
            "schema": "IssueDecompositionPlan",
            "parent_issue": 403,
            "source_consensus_artifact": self.consensus,
            "children": [
                {
                    "slug": "first-child",
                    "title": "First child",
                    "scope": "First bounded scope",
                    "non_goals": "No parent lifecycle mutation",
                    "body_artifact_path": self.write_body(
                        "child-one",
                        scope="First bounded scope",
                        non_goals="No parent lifecycle mutation",
                    ),
                },
                {
                    "slug": "second-child",
                    "title": "Second child",
                    "scope": "Second bounded scope",
                    "non_goals": "No public issue factory",
                    "body_artifact_path": self.write_body(
                        "child-two",
                        scope="Second bounded scope",
                        non_goals="No public issue factory",
                    ),
                },
            ],
            "parent_update": {"comment_artifact_path": self.write_parent_comment()},
        }

    def assert_invalid_payload(self, mutate: Callable[[dict], None], pattern: str) -> None:
        payload = self.valid_payload()
        mutate(payload)
        with self.assertRaisesRegex(IssueDecompositionError, pattern):
            load_issue_decomposition_plan(self.ctx, self.write_plan(payload))

    def test_valid_plan_requires_parent_at_least_two_children_body_artifacts_parent_link_and_final_sentinel(self) -> None:
        plan = load_issue_decomposition_plan(self.ctx, self.write_plan(self.valid_payload()))

        self.assertEqual(plan.schema, "IssueDecompositionPlan")
        self.assertEqual(plan.parent_issue, 403)
        self.assertEqual(len(plan.children), 2)
        self.assertEqual(plan.children[0].slug, "first-child")
        self.assertEqual(plan.parent_comment_artifact_path, ".refactor-loop/runs/parent-comment.md")

    def test_rejects_minimum_command_lifecycle_fields_in_every_plan_object(self) -> None:
        minimum_forbidden_fields = (
            "cmd",
            "argv",
            "shell",
            "command_line",
            "commands",
            "env",
            "gh",
            "git",
            "executor",
            "lifecycle_authority",
            "lifecycle_owner",
        )
        for field in minimum_forbidden_fields:
            with self.subTest(field=field):
                payload = self.valid_payload()
                payload[field] = "forbidden"
                with self.assertRaisesRegex(IssueDecompositionError, "forbidden lifecycle/command fields"):
                    load_issue_decomposition_plan(self.ctx, self.write_plan(payload))

        for field in minimum_forbidden_fields:
            with self.subTest(parent_update_forbidden_field=field):
                payload = self.valid_payload()
                payload["parent_update"][field] = "forbidden"
                with self.assertRaisesRegex(IssueDecompositionError, "parent_update contains forbidden lifecycle/command fields"):
                    load_issue_decomposition_plan(self.ctx, self.write_plan(payload))

        for field in minimum_forbidden_fields:
            with self.subTest(child_forbidden_field=field):
                payload = self.valid_payload()
                payload["children"][0][field] = "forbidden"
                with self.assertRaisesRegex(IssueDecompositionError, r"children\[0\] contains forbidden lifecycle/command fields"):
                    load_issue_decomposition_plan(self.ctx, self.write_plan(payload))

    def test_rejects_compatibility_extra_lifecycle_fields_without_making_them_new_schema(self) -> None:
        for field in ("args", "close", "assignee", "milestone", "proof", "digest", "plan_digest", "controller_action", "kind"):
            with self.subTest(compatibility_forbidden_field=field):
                payload = self.valid_payload()
                payload["children"][0][field] = "forbidden"
                with self.assertRaisesRegex(IssueDecompositionError, r"children\[0\] contains forbidden lifecycle/command fields"):
                    load_issue_decomposition_plan(self.ctx, self.write_plan(payload))

    def test_rejects_absolute_paths_path_traversal_and_single_child_plans(self) -> None:
        payload = self.valid_payload()
        payload["children"][0]["scope"] = {"cmd": "forbidden"}
        with self.assertRaisesRegex(IssueDecompositionError, "forbidden lifecycle/command fields"):
            load_issue_decomposition_plan(self.ctx, self.write_plan(payload))

        payload = self.valid_payload()
        payload["children"][0]["scope"] = {"cmd": "forbidden"}
        with self.assertRaisesRegex(IssueDecompositionError, "forbidden lifecycle/command fields"):
            load_issue_decomposition_plan(self.ctx, self.write_plan(payload))

        payload = self.valid_payload()
        payload["children"][0]["body_artifact_path"] = str((self.tmp / ".refactor-loop/runs/child-one.md").resolve())
        with self.assertRaisesRegex(IssueDecompositionError, "repo-relative"):
            load_issue_decomposition_plan(self.ctx, self.write_plan(payload))

        payload = self.valid_payload()
        payload["children"][0]["body_artifact_path"] = "../outside.md"
        with self.assertRaisesRegex(IssueDecompositionError, "repo-relative"):
            load_issue_decomposition_plan(self.ctx, self.write_plan(payload))

        payload = self.valid_payload()
        payload["children"] = payload["children"][:1]
        with self.assertRaisesRegex(IssueDecompositionError, "at least two children"):
            load_issue_decomposition_plan(self.ctx, self.write_plan(payload))

    def test_rejects_non_object_plan_and_missing_or_unsupported_exact_schema_fields(self) -> None:
        with self.assertRaisesRegex(IssueDecompositionError, "must be a JSON object"):
            load_issue_decomposition_plan(self.ctx, self.write_plan(["not", "an", "object"]))

        for field in ("schema", "parent_issue", "source_consensus_artifact", "children", "parent_update"):
            with self.subTest(missing_plan_field=field):
                self.assert_invalid_payload(lambda payload, field=field: payload.pop(field), f"plan missing required fields: {field}")

        self.assert_invalid_payload(lambda payload: payload.__setitem__("extra", "forbidden"), "plan contains unsupported fields: extra")
        self.assert_invalid_payload(lambda payload: payload.__setitem__("schema", "OtherPlan"), "schema must be IssueDecompositionPlan")

    def test_rejects_invalid_parent_issue_and_missing_source_consensus_artifact(self) -> None:
        for value in (0, -1, "0", "abc", "", None):
            with self.subTest(parent_issue=value):
                self.assert_invalid_payload(
                    lambda payload, value=value: payload.__setitem__("parent_issue", value),
                    "parent_issue must be a positive GitHub issue number",
                )

        self.assert_invalid_payload(
            lambda payload: payload.__setitem__("source_consensus_artifact", ".refactor-loop/runs/missing-consensus.md"),
            "source_consensus_artifact artifact not found",
        )

    def test_rejects_parent_update_shape_fields_path_and_missing_parent_link(self) -> None:
        self.assert_invalid_payload(lambda payload: payload.__setitem__("parent_update", []), "parent_update must be an object")
        self.assert_invalid_payload(
            lambda payload: payload["parent_update"].__setitem__("extra", "forbidden"),
            "parent_update contains unsupported fields: extra",
        )
        self.assert_invalid_payload(
            lambda payload: payload["parent_update"].pop("comment_artifact_path"),
            "parent_update missing required fields: comment_artifact_path",
        )
        self.assert_invalid_payload(
            lambda payload: payload["parent_update"].__setitem__("comment_artifact_path", ".refactor-loop/runs/missing-parent-comment.md"),
            "parent_update.comment_artifact_path artifact not found",
        )
        self.assert_invalid_payload(
            lambda payload: payload["parent_update"].__setitem__("comment_artifact_path", self.write_parent_comment(parent=404)),
            "parent comment .* missing parent issue link",
        )

    def test_rejects_child_shape_exact_fields_slug_and_required_text(self) -> None:
        self.assert_invalid_payload(lambda payload: payload["children"].__setitem__(0, "not-an-object"), r"children\[0\] must be an object")
        self.assert_invalid_payload(
            lambda payload: payload["children"][0].__setitem__("extra", "forbidden"),
            r"children\[0\] contains unsupported fields: extra",
        )
        for field in ("slug", "title", "scope", "non_goals", "body_artifact_path"):
            with self.subTest(missing_child_field=field):
                self.assert_invalid_payload(
                    lambda payload, field=field: payload["children"][0].pop(field),
                    rf"children\[0\] missing required fields: {field}",
                )

        invalid_slugs = ("FirstChild", "first_child", "-first", "first-", "first--child", "1-first")
        for slug in invalid_slugs:
            with self.subTest(slug=slug):
                self.assert_invalid_payload(
                    lambda payload, slug=slug: payload["children"][0].__setitem__("slug", slug),
                    r"children\[0\]\.slug must be kebab-case",
                )
        self.assert_invalid_payload(
            lambda payload: payload["children"][0].__setitem__("slug", ""),
            r"children\[0\]\.slug must be non-empty text",
        )
        self.assert_invalid_payload(
            lambda payload: payload["children"][1].__setitem__("slug", payload["children"][0]["slug"]),
            "duplicate child slug: first-child",
        )
        for field in ("title", "scope", "non_goals"):
            with self.subTest(blank_child_text=field):
                self.assert_invalid_payload(
                    lambda payload, field=field: payload["children"][0].__setitem__(field, " "),
                    rf"children\[0\]\.{field} must be non-empty text",
                )
        self.assert_invalid_payload(
            lambda payload: payload["children"][0].__setitem__("body_artifact_path", ".refactor-loop/runs/missing-child.md"),
            r"children\[0\]\.body_artifact_path artifact not found",
        )

    def test_rejects_child_body_without_sentinel_or_self_contained_inline_authority(self) -> None:
        def remove_sentinel(payload: dict) -> None:
            body_path = self.tmp / payload["children"][0]["body_artifact_path"]
            body_path.write_text(body_path.read_text(encoding="utf-8").replace("\n⟦AI:AUTO-LOOP⟧\n", "\n"), encoding="utf-8")

        def remove_inline_artifact(payload: dict) -> None:
            body_path = self.tmp / payload["children"][0]["body_artifact_path"]
            body_path.write_text(
                "\n".join(
                    [
                        "## child issue",
                        "",
                        "Parent issue: #403",
                        f"Source consensus artifact: {Path(self.consensus).name}",
                        "Scope: First bounded scope",
                        "Non-goals: No parent lifecycle mutation",
                        "",
                        "⟦AI:AUTO-LOOP⟧",
                        "",
                    ]
                ),
                encoding="utf-8",
            )

        self.assert_invalid_payload(remove_sentinel, "child body invalid: .*missing final sentinel")
        self.assert_invalid_payload(remove_inline_artifact, "child body invalid: .*authority body must inline raw artifact text")

    def test_rejects_child_body_missing_required_parent_source_scope_or_non_goals_metadata(self) -> None:
        metadata_cases = (
            ("Parent issue: #403", "Parent issue: #404", "Parent issue: #403"),
            (Path(self.consensus).name, "different-consensus.md", Path(self.consensus).name),
            ("Scope: First bounded scope", "Scope: Wrong scope", "First bounded scope"),
            ("Non-goals: No parent lifecycle mutation", "Non-goals: Wrong non-goal", "No parent lifecycle mutation"),
        )
        for old, new, expected in metadata_cases:
            with self.subTest(missing_metadata=expected):
                def mutate(payload: dict, old: str = old, new: str = new) -> None:
                    body_path = self.tmp / payload["children"][0]["body_artifact_path"]
                    body_path.write_text(body_path.read_text(encoding="utf-8").replace(old, new), encoding="utf-8")

                self.assert_invalid_payload(mutate, f"missing required self-contained metadata: {expected}")

    def test_normalized_plan_digest_is_stable_across_json_key_order(self) -> None:
        payload = self.valid_payload()
        reordered = {
            "parent_update": payload["parent_update"],
            "children": [
                {
                    "body_artifact_path": child["body_artifact_path"],
                    "non_goals": child["non_goals"],
                    "scope": child["scope"],
                    "title": child["title"],
                    "slug": child["slug"],
                }
                for child in payload["children"]
            ],
            "source_consensus_artifact": payload["source_consensus_artifact"],
            "parent_issue": payload["parent_issue"],
            "schema": payload["schema"],
        }

        self.assertEqual(issue_decomposition_plan_digest(payload), issue_decomposition_plan_digest(reordered))

    def test_child_fingerprint_is_stable_and_bound_to_parent_digest_and_slug(self) -> None:
        first = issue_decomposition_child_fingerprint(403, "a" * 64, "first-child")
        self.assertEqual(first, issue_decomposition_child_fingerprint(403, "a" * 64, "first-child"))
        self.assertNotEqual(first, issue_decomposition_child_fingerprint(404, "a" * 64, "first-child"))
        self.assertNotEqual(first, issue_decomposition_child_fingerprint(403, "b" * 64, "first-child"))
        self.assertNotEqual(first, issue_decomposition_child_fingerprint(403, "a" * 64, "second-child"))

    def test_exact_helper_tracking_parser_ignores_sentinel_like_prose_and_reconciles_duplicates(self) -> None:
        plan_path = self.write_plan(self.valid_payload())
        plan = load_issue_decomposition_plan(self.ctx, plan_path)
        digest = issue_decomposition.issue_decomposition_plan_file_digest(self.ctx, plan_path)
        children = tuple(
            IssueDecompositionTrackingChild(
                slug=child.slug,
                issue_number=501 + index,
                url=f"https://github.com/owner/repo/issues/{501 + index}",
                fingerprint=issue_decomposition_child_fingerprint(plan.parent_issue, digest, child.slug),
            )
            for index, child in enumerate(plan.children)
        )
        block = build_issue_decomposition_tracking_block(plan.parent_issue, digest, children)
        comments = [
            {"body": f"solver prose says IssueDecompositionPlan digest: {digest} but is not helper tracking"},
            {"body": block},
            {"body": "prefix\n" + block + "\nsuffix"},
        ]

        projection = parse_issue_decomposition_tracking_comments(comments, expected_parent_issue=403, expected_digest=digest)
        reconciled = reconcile_issue_decomposition_tracking_children(plan, digest, projection)

        self.assertFalse(projection.conflicts)
        self.assertEqual({"first-child", "second-child"}, set(reconciled))
        self.assertEqual(501, reconciled["first-child"].issue_number)

    def test_tracking_parser_fails_closed_on_conflicting_digest_or_parent(self) -> None:
        plan_path = self.write_plan(self.valid_payload())
        plan = load_issue_decomposition_plan(self.ctx, plan_path)
        digest = issue_decomposition.issue_decomposition_plan_file_digest(self.ctx, plan_path)
        child = IssueDecompositionTrackingChild(
            slug="first-child",
            issue_number=501,
            url="https://github.com/owner/repo/issues/501",
            fingerprint=issue_decomposition_child_fingerprint(plan.parent_issue, digest, "first-child"),
        )
        wrong_digest = build_issue_decomposition_tracking_block(403, "b" * 64, [child])
        wrong_parent = build_issue_decomposition_tracking_block(404, digest, [child])

        for body in (wrong_digest, wrong_parent):
            with self.subTest(body=body):
                projection = parse_issue_decomposition_tracking_comments([{"body": body}], expected_parent_issue=403, expected_digest=digest)
                self.assertTrue(projection.conflicts)

    def test_append_tracking_block_requires_final_sentinel_and_exact_grammar(self) -> None:
        child = IssueDecompositionTrackingChild(
            slug="first-child",
            issue_number=501,
            url="https://github.com/owner/repo/issues/501",
            fingerprint="a" * 64,
        )
        text = append_issue_decomposition_tracking_block(
            "Parent issue: #403\n\n⟦AI:AUTO-LOOP⟧\n",
            403,
            "b" * 64,
            [child],
            "\n⟦AI:AUTO-LOOP⟧\n",
        )

        self.assertIn("<!-- crnd:issue-decomposition-tracking -->", text)
        self.assertIn("IssueDecompositionPlan digest: " + "b" * 64, text)
        self.assertIn("fingerprint=" + "a" * 64, text)
        self.assertTrue(text.endswith("\n⟦AI:AUTO-LOOP⟧\n"))

    def test_source_regression_issue_decomposition_validator_keeps_exact_schema_and_body_guards(self) -> None:
        self.assertEqual(
            issue_decomposition.PLAN_FIELDS,
            {"schema", "parent_issue", "source_consensus_artifact", "children", "parent_update"},
        )
        self.assertEqual(
            issue_decomposition.CHILD_FIELDS,
            {"slug", "title", "scope", "non_goals", "body_artifact_path"},
        )
        self.assertEqual(issue_decomposition.PARENT_UPDATE_FIELDS, {"comment_artifact_path"})
        minimum_forbidden_fields = {
            "cmd",
            "argv",
            "shell",
            "command_line",
            "commands",
            "env",
            "git",
            "gh",
            "executor",
            "lifecycle_authority",
            "lifecycle_owner",
        }
        self.assertEqual(minimum_forbidden_fields, issue_decomposition.MINIMUM_FORBIDDEN_PLAN_FIELDS)
        self.assertLessEqual(issue_decomposition.MINIMUM_FORBIDDEN_PLAN_FIELDS, issue_decomposition.FORBIDDEN_PLAN_FIELDS)
        compatibility_forbidden_fields = {
            "args",
            "close",
            "assignee",
            "milestone",
            "proof",
            "digest",
            "plan_digest",
            "controller_action",
            "kind",
        }
        self.assertLessEqual(compatibility_forbidden_fields, issue_decomposition.COMPATIBILITY_FORBIDDEN_PLAN_FIELDS)
        self.assertLessEqual(compatibility_forbidden_fields, issue_decomposition.FORBIDDEN_PLAN_FIELDS)

        validator_source = inspect.getsource(issue_decomposition.validate_issue_decomposition_plan)
        for needle in (
            '_require_exact_fields(raw, PLAN_FIELDS, "plan")',
            '_require_exact_fields(parent_update, PARENT_UPDATE_FIELDS, "parent_update")',
            '_require_exact_fields(child_raw, CHILD_FIELDS, f"children[{index}]")',
            "_validate_parent_comment(ctx, parent_comment_artifact_path, parent_issue)",
            "_validate_child_body(ctx, body_artifact_path, parent_issue, source_consensus_artifact, scope, non_goals)",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, validator_source)

        child_body_source = inspect.getsource(issue_decomposition._validate_child_body)
        self.assertIn("validate_self_contained_github_body(text, authority_required=True)", child_body_source)
        self.assertIn('f"Parent issue: #{parent_issue}"', child_body_source)
        self.assertIn("consensus_name", child_body_source)
        self.assertIn("scope, non_goals", child_body_source)

        parent_comment_source = inspect.getsource(issue_decomposition._validate_parent_comment)
        self.assertIn("validate_self_contained_github_body(text, authority_required=False)", parent_comment_source)
        self.assertIn('f"Parent issue: #{parent_issue}"', parent_comment_source)


if __name__ == "__main__":
    unittest.main()
