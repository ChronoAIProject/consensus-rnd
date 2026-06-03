#!/usr/bin/env python3
"""Behavior tests for read-only GitHub label drift planning."""

from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from codex_refactor_loop import labels


class LabelGithubCheckTests(unittest.TestCase):
    def live_catalog(self) -> list[dict[str, str]]:
        return [
            {"name": spec.name, "description": spec.description, "color": spec.color}
            for spec in labels.LABEL_SPECS
        ]

    def test_clean_catalog_has_no_create_update_or_unknown_crnd(self) -> None:
        plan = labels.migration_plan(self.live_catalog() + [{"name": "enhancement"}, {"name": "wontfix"}])

        self.assertEqual(plan.create, ())
        self.assertEqual(plan.update, ())
        self.assertEqual(plan.unknown_crnd, ())
        self.assertEqual(plan.external_defaults, ("enhancement", "wontfix"))

    def test_missing_canonical_label_is_planned_for_create(self) -> None:
        live = [item for item in self.live_catalog() if item["name"] != labels.PHASE_FIXING]

        plan = labels.migration_plan(live)

        self.assertIn(labels.PHASE_FIXING, [spec.name for spec in plan.create])

    def test_wrong_description_or_color_is_planned_for_update(self) -> None:
        live = self.live_catalog()
        live[0] = {**live[0], "description": "wrong", "color": "ffffff"}

        plan = labels.migration_plan(live)

        self.assertEqual([spec.name for spec in plan.update], [labels.LABEL_SPECS[0].name])

    def test_unknown_crnd_fails_closed(self) -> None:
        plan = labels.migration_plan(self.live_catalog() + [{"name": "crnd:phase:not-registered"}])

        self.assertEqual(plan.unknown_crnd, ("crnd:phase:not-registered",))

    def test_legacy_aliases_are_reported_as_migrations(self) -> None:
        plan = labels.migration_plan(self.live_catalog() + [{"name": "auto-loop"}, {"name": "phase9-auto-solve"}])

        migrations = {step.live_label: step.add_labels for step in plan.alias_migrations}
        self.assertEqual(migrations["auto-loop"], (labels.MANAGED,))
        self.assertEqual(
            set(migrations["phase9-auto-solve"]),
            {labels.MANAGED, labels.PHASE_DESIGN_SOLVING, labels.HUMAN_AUTO},
        )

    def run_main_with_live_labels(self, argv: list[str], live: list[dict[str, str]]) -> tuple[int, dict[str, object]]:
        with mock.patch("codex_refactor_loop.labels._load_gh_labels", return_value=live) as load:
            stdout = io.StringIO()
            with mock.patch("sys.stdout", stdout):
                code = labels.main(argv)
        load.assert_called_once_with()
        return code, json.loads(stdout.getvalue())

    def test_check_github_plan_prints_json_and_allows_create_update_drift(self) -> None:
        live = self.live_catalog()
        live = [item for item in live if item["name"] != labels.PHASE_FIXING]
        live[0] = {**live[0], "description": "wrong", "color": "ffffff"}

        code, payload = self.run_main_with_live_labels(["check-github", "--plan"], live)

        self.assertEqual(code, 0)
        self.assertIn(labels.PHASE_FIXING, [item["name"] for item in payload["create"]])
        self.assertEqual([item["name"] for item in payload["update"]], [labels.LABEL_SPECS[0].name])
        self.assertEqual(payload["unknown_crnd"], [])

    def test_check_github_fails_on_create_update_drift_without_plan(self) -> None:
        live = [item for item in self.live_catalog() if item["name"] != labels.PHASE_FIXING]

        code, payload = self.run_main_with_live_labels(["check-github"], live)

        self.assertEqual(code, 1)
        self.assertIn(labels.PHASE_FIXING, [item["name"] for item in payload["create"]])

    def test_check_github_fails_closed_on_unknown_crnd_even_with_plan(self) -> None:
        code, payload = self.run_main_with_live_labels(
            ["check-github", "--plan"],
            self.live_catalog() + [{"name": "crnd:phase:not-registered"}],
        )

        self.assertEqual(code, 1)
        self.assertEqual(payload["unknown_crnd"], ["crnd:phase:not-registered"])

    def test_load_gh_labels_invokes_read_only_label_list_and_parses_json(self) -> None:
        completed = labels.subprocess.CompletedProcess(
            ["gh"],
            0,
            stdout=json.dumps([{"name": labels.PHASE_FIXING, "description": "x", "color": "ffffff"}]),
            stderr="",
        )
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            host_env = repo / ".config" / "consensus-rnd" / "host.env"
            host_env.parent.mkdir(parents=True)
            host_env.write_text(
                f'export REPO_ROOT="{repo}"\nexport GH_REPO_SLUG="owner/repo"\n',
                encoding="utf-8",
            )
            source_env = {"PATH": "/usr/bin", "CONSENSUS_RND_HOST_ENV": ".config/consensus-rnd/host.env"}
            with mock.patch("codex_refactor_loop.labels.subprocess.run", return_value=completed) as run:
                with mock.patch("codex_refactor_loop.labels.os.getcwd", return_value=str(repo)):
                    with mock.patch("codex_refactor_loop.labels.os.environ", source_env):
                        self.assertEqual(labels._load_gh_labels(), [{"name": labels.PHASE_FIXING, "description": "x", "color": "ffffff"}])
        run.assert_called_once_with(
            ["gh", "label", "list", "--repo", "owner/repo", "--json", "name,description,color", "--limit", "1000"],
            cwd=str(repo.resolve()),
            env=mock.ANY,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(run.call_args.kwargs["env"]["GH_REPO_SLUG"], "owner/repo")

    def test_load_gh_labels_fails_closed_when_repo_slug_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            host_env = repo / ".config" / "consensus-rnd" / "host.env"
            host_env.parent.mkdir(parents=True)
            host_env.write_text(f'export REPO_ROOT="{repo}"\n', encoding="utf-8")
            source_env = {"PATH": "/usr/bin", "CONSENSUS_RND_HOST_ENV": ".config/consensus-rnd/host.env"}
            with mock.patch("codex_refactor_loop.labels.subprocess.run") as run:
                with mock.patch("codex_refactor_loop.labels.os.getcwd", return_value=str(repo)):
                    with mock.patch("codex_refactor_loop.labels.os.environ", source_env):
                        with self.assertRaisesRegex(RuntimeError, "GH_REPO_SLUG is unset"):
                            labels._load_gh_labels()
        run.assert_not_called()

    def test_design_issue_labels_cli_returns_catalog_bundle(self) -> None:
        stdout = io.StringIO()
        with mock.patch("sys.stdout", stdout):
            code = labels.main(["design-issue-labels"])

        self.assertEqual(code, 0)
        self.assertEqual(stdout.getvalue().strip(), ",".join(labels.design_issue_label_bundle()))

    def test_validate_catalog_cli_reports_success(self) -> None:
        stdout = io.StringIO()
        with mock.patch("sys.stdout", stdout):
            code = labels.main(["validate-catalog"])

        self.assertEqual(code, 0)
        self.assertEqual(stdout.getvalue().strip(), "labels catalog valid")


if __name__ == "__main__":
    unittest.main()
