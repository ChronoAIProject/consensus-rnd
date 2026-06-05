"""Controller-owned lifecycle helpers ported from controller_lib.sh."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from string import Template
from typing import Any, Mapping, Sequence

from .active_controller import require_active_controller, write_active_controller_status
from . import labels
from .banners import BannerRequest, build_status_banner, gh_comment_command
from .context import LoopContext
from .gh_invoke import build_gh_argv
from .github_actor import GitHubAuthenticatedActor
from .github_body import GitHubBodyError, validate_self_contained_github_body
from .implementation_pr_artifacts import (
    FINAL_SENTINEL,
    implementation_cluster_id,
    implementation_pr_body_path,
    implementation_pr_title_path,
    validate_implementation_pr_artifacts,
)
from .implement_lifecycle import classify_implement_attempt, clear_redispatchable_implement_log
from .issue_decomposition import load_issue_decomposition_plan
from .prompt_contracts import inline_prompt_contracts
from .release.publisher import ReleasePublisher
from .git import Git
from .review_fix_dispatch import (
    ReviewFixDispatchSpec,
    ReviewThreadCompletionEvidence,
    validate_review_thread_completion,
)
from .triage import apply_decision, load_triage_apply_config
from .work_items import extract_closing_issue_numbers
from .wakeup_plan import consensus_implementation_suppressed_reason
from .workflow_spec import WorkflowSpecError, load_validated_workflow_spec


# Removal sets list only canonical crnd:* labels that exist in the repository.
# gh issue/pr edit --remove-label hard-fails the whole edit on any name absent
# from the repo, and legacy emoji/alias labels are not maintained there, so they
# are intentionally excluded; historical labels are not managed by the loop.
PR_LABELS_REMOVE = (
    *labels.labels_for_group("phase"),
    labels.HUMAN_MAINTAINER_DECISION,
    labels.STUCK,
)
ISSUE_LABELS_REMOVE = (
    *labels.labels_for_group("phase"),
    labels.HUMAN_AUTO,
    labels.HUMAN_MAINTAINER_DECISION,
    labels.STUCK,
)
SAFE_WORKTREE_ITERATION_RE = re.compile(r"^[0-9]+$")
SAFE_WORKTREE_CLUSTER_RE = re.compile(r"^[A-Za-z0-9._-]+$")
GITHUB_LIFECYCLE_TARGET_RE = re.compile(r"^[1-9][0-9]*$")
BODY_CLOSING_ISSUE_TARGET_RE = re.compile(r"(?im)\bCloses\s+#([^\s,;:.)\]}\\]*)")
REVIEW_ROLES = ("architect", "tests", "quality")
PUBLISH_IMPLEMENTATION_FALLBACK_DELEGATED_EXIT = 75


class ControllerActions:
    def __init__(self, ctx: LoopContext, *, github_actor: GitHubAuthenticatedActor | None = None) -> None:
        self.ctx = ctx
        self.github_actor = github_actor
        merged_env = {**os.environ, **ctx.host_env}
        self.integration_branch = str(merged_env.get("INTEGRATION_BRANCH", "")).strip()
        self.review_base_branch = str(merged_env.get("REVIEW_BASE_BRANCH", "")).strip()
        if ctx.host_env and ctx.gh_repo_slug:
            self._require_branch_config()

    def _require_branch_config(self) -> tuple[str, str]:
        missing = [
            name
            for name, value in (("INTEGRATION_BRANCH", self.integration_branch), ("REVIEW_BASE_BRANCH", self.review_base_branch))
            if not value
        ]
        if missing:
            raise RuntimeError(f"missing required host branch env: {', '.join(missing)}")
        return self.integration_branch, self.review_base_branch

    def gh(self, args: Sequence[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
        argv = [str(a) for a in args]
        full = build_gh_argv(self.ctx.gh_repo_slug, ["gh", *argv])
        result = subprocess.run(full, cwd=str(self.ctx.repo_root), capture_output=True, text=True, check=False)
        if check and result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"gh {' '.join(argv)} failed")
        return result

    def git(self, args: Sequence[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(["git", "-C", str(self.ctx.repo_root), *args], capture_output=True, text=True, check=False)
        if check and result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"git {' '.join(args)} failed")
        return result

    def apply_human_label_or_skip(self, pr_number: str, source_marker: str = "", reason: str = "") -> int:
        if not self._require_owner_or_return("controller-label", code=3):
            return 3
        pr_target = self._normalize_lifecycle_target_or_block(
            pr_number,
            kind="pr",
            action="apply-human-label",
            source="argument",
        )
        if pr_target is None:
            if not pr_number:
                sys.stderr.write("apply_human_label_or_skip: missing pr_number\n")
            return 2
        env_marker = os.environ.get("HUMAN_LABEL_SOURCE_MARKER", "")
        if not source_marker.startswith("META_RESOLVED:escalate-human:") and env_marker.startswith(
            "META_RESOLVED:escalate-human:"
        ):
            if not reason:
                reason = source_marker
            source_marker = env_marker
        if not source_marker.startswith("META_RESOLVED:escalate-human:"):
            sys.stderr.write("ERROR: apply_human_label_or_skip requires META_RESOLVED:escalate-human marker source\n")
            return 2

        if not self._require_github_actor_or_return("controller-label", code=3):
            return 3
        result = self.gh(["pr", "edit", pr_target, "--add-label", labels.HUMAN_MAINTAINER_DECISION], check=False)
        return result.returncode

    def _git_in(self, cwd: Path, args: Sequence[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=False)
        if check and result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"git {' '.join(args)} failed")
        return result

    def _current_branch(self, worktree: Path | None = None) -> str:
        result = self._git_in(worktree or self.ctx.repo_root, ["rev-parse", "--abbrev-ref", "HEAD"], check=False)
        return result.stdout.strip() if result.returncode == 0 else ""

    def safe_push(self, remote: str = "origin", branch: str = "", worktree: str | Path | None = None) -> int:
        if not self._require_owner_or_return("safe-push", code=3):
            return 3
        push_worktree = Path(worktree) if worktree is not None else self.ctx.repo_root
        branch = branch or self._current_branch(push_worktree)
        if not branch or branch == "HEAD":
            sys.stderr.write("safe_push: cannot determine branch (HEAD detached?); aborting\n")
            return 2
        fetch = self._git_in(push_worktree, ["fetch", remote, branch], check=False)
        if fetch.stdout:
            print(fetch.stdout, end="")
        if fetch.stderr:
            print("\n".join(fetch.stderr.splitlines()[-3:]))
        behind = self._git_in(push_worktree, ["rev-list", "--count", f"HEAD..{remote}/{branch}"], check=False)
        try:
            behind_count = int((behind.stdout or "0").strip() or "0")
        except ValueError:
            behind_count = 0
        if behind_count > 0:
            print(f"safe_push: local behind {remote}/{branch} by {behind_count} commit(s); rebasing")
            pull = self._git_in(push_worktree, ["pull", "--rebase", "--autostash", remote, branch], check=False)
            if pull.stdout:
                print(pull.stdout, end="")
            if pull.stderr:
                sys.stderr.write(pull.stderr)
            if pull.returncode != 0:
                sys.stderr.write(f"safe_push: rebase conflict on {remote}/{branch} - resolve manually then push\n")
                return 3
        push = self._git_in(push_worktree, ["push", remote, branch], check=False)
        if push.stdout:
            print(push.stdout, end="")
        if push.stderr:
            sys.stderr.write(push.stderr)
        return push.returncode

    def publish_release_candidate(
        self,
        candidate_path: str = ".refactor-loop/state/release-candidate.json",
        target_ref: str = "",
    ) -> ReleasePublishResult:
        self._require_owner_or_raise("publish-release")
        target = target_ref or os.environ.get("RELEASE_TARGET_REF", "")
        if not target:
            raise RuntimeError("publish_release_candidate: RELEASE_TARGET_REF is required")
        self._require_github_actor_or_raise("publish-release")
        publisher = ReleasePublisher(self.ctx.repo_root)
        return publisher.publish(candidate_path=candidate_path, target_ref=target)

    def post_status_banner(self, request: BannerRequest) -> str:
        self._require_owner_or_raise("post-banner")
        target = self._normalize_lifecycle_target_or_raise(
            request.target,
            kind=request.kind,
            action="post-banner",
            source="argument",
        )
        normalized = BannerRequest(
            target=target,
            kind=request.kind,
            role=request.role,
            detail=request.detail,
            log=request.log,
            stall=request.stall,
        )
        self._require_github_actor_or_raise("post-banner")
        body = build_status_banner(normalized)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".md", delete=False) as handle:
            handle.write(body)
            tmp = handle.name
        try:
            result = self.gh(gh_comment_command(normalized, Path(tmp))[1:], check=False)
        finally:
            Path(tmp).unlink(missing_ok=True)
        if result.returncode != 0:
            raise RuntimeError(f"post_status_banner: {result.stderr.strip() or result.stdout.strip()}")
        return result.stdout.strip()

    def safe_sync_main(self, remote: str = "origin", branch: str = "") -> int:
        if not self._require_owner_or_return("safe-sync-main", code=3):
            return 3
        branch = branch or self._current_branch()
        if not branch or branch == "HEAD":
            sys.stderr.write("safe_sync_main: cannot determine branch; skipping\n")
            return 0
        fetch = self.git(["fetch", remote, branch], check=False)
        if fetch.stdout:
            print(fetch.stdout, end="")
        if fetch.stderr:
            print("\n".join(fetch.stderr.splitlines()[-3:]))
        behind = self.git(["rev-list", "--count", f"HEAD..{remote}/{branch}"], check=False)
        try:
            behind_count = int((behind.stdout or "0").strip() or "0")
        except ValueError:
            behind_count = 0
        if behind_count > 0:
            print(f"safe_sync_main: local behind {remote}/{branch} by {behind_count}; pulling --rebase --autostash")
            pull = self.git(["pull", "--rebase", "--autostash", remote, branch], check=False)
            if pull.stdout:
                print(pull.stdout, end="")
            if pull.stderr:
                sys.stderr.write(pull.stderr)
            return pull.returncode
        print(f"safe_sync_main: already up to date with {remote}/{branch}")
        return 0

    def safe_worktree(self, iteration: str, cluster: str, base: str) -> tuple[Path, str]:
        _validate_safe_worktree_fields(str(iteration), cluster)
        wt_path = self.ctx.repo_root / ".worktrees" / f"iter{iteration}-{cluster}"
        branch = f"refactor/iter{iteration}-{cluster}"
        if wt_path.is_dir():
            sys.stderr.write(f"  ✓ worktree exists: {wt_path}\n")
            return wt_path, branch
        (self.ctx.repo_root / ".worktrees").mkdir(parents=True, exist_ok=True)
        if self.git(["show-ref", "--quiet", f"refs/heads/{branch}"], check=False).returncode == 0:
            result = self.git(["worktree", "add", str(wt_path), branch])
        else:
            result = self.git(["worktree", "add", "-b", branch, str(wt_path), base])
        sys.stderr.write("\n".join(result.stderr.splitlines()[-2:]) + "\n")
        return wt_path, branch

    def fresh_safe_worktree(self, iteration: str, cluster: str, base: str) -> tuple[Path, str]:
        return Git(self.ctx.repo_root).fresh_safe_worktree(iteration, cluster, base)

    def _ensure_pr_ready_for_merge(self, pr_target: str) -> int:
        draft = self.gh(["pr", "view", pr_target, "--json", "isDraft", "--jq", ".isDraft"], check=False)
        if draft.returncode != 0:
            return draft.returncode
        if draft.stdout.strip() == "true":
            if not self._live_target_has_managed_label(kind="pr", target=pr_target):
                self._append_pending_event(f"CONTROLLER_ACTION_BLOCKED:target-not-managed:merge-pr:pr:{pr_target}")
                sys.stderr.write("merge_pr: live draft PR is not managed\n")
                return 2
            ready = self.gh(["pr", "ready", pr_target], check=False)
            if ready.returncode != 0:
                return ready.returncode
        return 0

    def merge_pr(self, pr: str, linked_issue: str = "") -> int:
        if not self._require_owner_or_return("merge-pr", code=3):
            return 3
        pr_target = self._normalize_lifecycle_target_or_block(pr, kind="pr", action="merge-pr", source="argument")
        if pr_target is None:
            return 1
        issue_target = ""
        if linked_issue:
            normalized = self._normalize_lifecycle_target_or_block(
                linked_issue,
                kind="issue",
                action="merge-pr",
                source="argument",
            )
            if normalized is None:
                return 1
            issue_target = normalized
        if not linked_issue:
            body = self.gh(["pr", "view", pr_target, "--json", "body", "--jq", ".body"], check=False).stdout
            linked_issue = self._single_body_linked_issue_or_block(body, action="close")
            if linked_issue is None:
                return 1
            if linked_issue:
                normalized = self._normalize_lifecycle_target_or_block(
                    linked_issue,
                    kind="issue",
                    action="close",
                    source="body-link",
                )
                if normalized is None:
                    return 1
                issue_target = normalized
        if not self._require_github_actor_or_return("merge-pr", code=3):
            return 3
        ready = self._ensure_pr_ready_for_merge(pr_target)
        if ready != 0:
            return ready
        merge = self.gh(["pr", "merge", pr_target, "--squash", "--delete-branch"], check=False)
        if merge.stdout:
            print(merge.stdout.splitlines()[-1])
        elif merge.stderr:
            print(merge.stderr.splitlines()[-1])
        if merge.returncode != 0:
            self._append_pending_event(f"CONTROLLER_ACTION_BLOCKED:blocked-by-host-policy:merge-pr:pr:{pr_target}")
            return merge.returncode
        self.record_recent_pr_merge(pr_target)
        args = ["pr", "edit", pr_target]
        for label in PR_LABELS_REMOVE:
            args.extend(["--remove-label", label])
        args.extend(["--add-label", labels.PHASE_MERGED])
        self.gh(args, check=False)
        if issue_target:
            comment = f"✅ Auto-merged via PR #{pr_target}.\n\n⟦AI:AUTO-LOOP⟧"
            close = self.gh(["issue", "close", issue_target, "--reason", "completed", "--comment", comment], check=False)
            if close.stdout:
                print(close.stdout.splitlines()[-1])
            args = ["issue", "edit", issue_target]
            for label in ISSUE_LABELS_REMOVE:
                args.extend(["--remove-label", label])
            args.extend(["--add-label", labels.PHASE_MERGED])
            self.gh(args, check=False)
        head = self.gh(["pr", "view", pr_target, "--json", "headRefName", "--jq", ".headRefName"], check=False).stdout.strip()
        if head:
            wt = self._worktree_for_branch(head)
            if wt and wt != self.ctx.repo_root:
                self.git(["worktree", "remove", str(wt), "--force"], check=False)
        return 0

    def open_pr_with_label(self, title: str, body_file: str, base: str | None = None, head: str = "") -> tuple[int, str]:
        self._require_owner_or_raise("open-pr")
        base = base or self._require_branch_config()[0]
        if not head:
            raise RuntimeError("open_pr_with_label: head branch required (avoid gh fallback to current branch = base)")
        self._validate_pr_body_file(body_file)
        linked_issue = self._single_body_linked_issue_or_raise(self._read_body_file(body_file), action="open-pr")
        issue_target = ""
        if linked_issue:
            issue_target = self._normalize_lifecycle_target_or_raise(
                linked_issue,
                kind="issue",
                action="open-pr",
                source="body-link",
            )
        self._require_github_actor_or_raise("open-pr")
        created = self.gh(["pr", "create", "--draft", "--base", base, "--head", head, "--title", title, "--body-file", body_file], check=False)
        output = created.stdout + created.stderr
        match = re.search(r"https://github\.com/[^/]+/[^/]+/pull/([0-9]+)", output)
        if not match:
            raise RuntimeError(f"open_pr_with_label: failed to extract PR num from: {output.strip()}")
        pr_target = self._normalize_lifecycle_target_or_raise(
            match.group(1),
            kind="pr",
            action="open-pr",
            source="github-pr-create-url",
        )
        self.gh(
            [
                "pr",
                "edit",
                pr_target,
                "--add-label",
                ",".join((labels.MANAGED, labels.PHASE_REVIEWING, labels.HUMAN_AUTO)),
            ],
            check=False,
        )
        if issue_target:
            args = ["issue", "edit", issue_target]
            for label in ISSUE_LABELS_REMOVE:
                args.extend(["--remove-label", label])
            args.extend(
                [
                    "--add-label",
                    ",".join((labels.PHASE_PR_OPEN, labels.HUMAN_AUTO, labels.MANAGED)),
                ]
            )
            self.gh(args, check=False)
        return int(pr_target), match.group(0)

    def open_design_issue_with_labels(self, title: str, body_file: str) -> tuple[int, str]:
        self._require_owner_or_raise("open-design-issue")
        if not title.strip():
            raise RuntimeError("open_design_issue_with_labels: title required")
        self._validate_design_issue_body_file(body_file)
        self._require_github_actor_or_raise("open-design-issue")
        created = self.gh(
            [
                "issue",
                "create",
                "--title",
                title,
                "--label",
                ",".join(labels.design_issue_label_bundle()),
                "--body-file",
                body_file,
            ],
            check=False,
        )
        output = created.stdout + created.stderr
        match = re.search(r"https://github\.com/[^/]+/[^/]+/issues/([0-9]+)", output)
        if created.returncode != 0 or not match:
            raise RuntimeError(f"open_design_issue_with_labels: failed to extract issue num from: {output.strip()}")
        return int(match.group(1)), match.group(0)

    def apply_issue_decomposition_plan(self, plan_path: str) -> tuple[tuple[int, str], ...]:
        self._require_owner_or_raise("apply-issue-decomposition-plan")
        plan = load_issue_decomposition_plan(self.ctx, plan_path)
        self._require_github_actor_or_raise("apply-issue-decomposition-plan")
        created: list[tuple[int, str]] = []
        for child in plan.children:
            created.append(self.open_design_issue_with_labels(child.title, child.body_artifact_path))
        parent_target = self._normalize_lifecycle_target_or_raise(
            plan.parent_issue,
            kind="issue",
            action="apply-issue-decomposition-plan",
            source="plan.parent_issue",
        )
        result = self.gh(
            [
                "issue",
                "comment",
                parent_target,
                "--body-file",
                plan.parent_comment_artifact_path,
            ],
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(f"apply_issue_decomposition_plan: parent comment failed: {result.stderr.strip() or result.stdout.strip()}")
        return tuple(created)

    def _validate_pr_body_file(self, body_file: str) -> None:
        body_path = Path(body_file)
        if not body_path.is_absolute():
            body_path = self.ctx.repo_root / body_path
        try:
            validate_self_contained_github_body(body_path.read_text(encoding="utf-8"), authority_required=False)
        except GitHubBodyError as exc:
            raise RuntimeError(str(exc)) from exc

    def _validate_design_issue_body_file(self, body_file: str) -> None:
        body_path = Path(body_file)
        if not body_path.is_absolute():
            body_path = self.ctx.repo_root / body_path
        try:
            validate_self_contained_github_body(body_path.read_text(encoding="utf-8"), authority_required=True)
        except GitHubBodyError as exc:
            raise RuntimeError(str(exc)) from exc

    def _read_body_file(self, body_file: str) -> str:
        body_path = Path(body_file)
        if not body_path.is_absolute():
            body_path = self.ctx.repo_root / body_path
        return body_path.read_text(encoding="utf-8")

    def open_release_rollup_pr_from_pending_event(
        self,
        event_json: str,
        body_file: str,
        title: str = "Release rollup",
    ) -> tuple[int, str]:
        self._require_owner_or_raise("open-release-rollup-pr")
        try:
            event = json.loads(event_json)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"open_release_rollup_pr_from_pending_event: invalid event json: {exc}") from exc
        if not isinstance(event, dict):
            raise RuntimeError("open_release_rollup_pr_from_pending_event: event must be a JSON object")

        default_integration, default_review_base = self._require_branch_config()
        integration_branch = str(event.get("integration_branch") or default_integration).strip()
        review_base_branch = str(event.get("review_base_branch") or default_review_base).strip()
        integration_sha = str(event.get("integration_sha") or "").strip()
        if not integration_branch or not review_base_branch or not integration_sha:
            raise RuntimeError("open_release_rollup_pr_from_pending_event: missing integration branch, review base, or integration sha")
        if not re.fullmatch(r"[0-9A-Za-z._-]+", integration_sha):
            raise RuntimeError("open_release_rollup_pr_from_pending_event: unsafe integration sha for rollup branch")
        self._validate_pr_body_file(body_file)

        remote = self.git(["ls-remote", "--exit-code", "--heads", "origin", integration_branch], check=False)
        if remote.returncode != 0 or not remote.stdout.strip():
            raise RuntimeError(f"open_release_rollup_pr_from_pending_event: missing remote integration branch {integration_branch}")
        remote_sha = remote.stdout.split()[0]
        if remote_sha != integration_sha:
            raise RuntimeError(
                "open_release_rollup_pr_from_pending_event: stale integration sha "
                f"{integration_sha}; origin/{integration_branch} is {remote_sha}"
            )

        rollup_head = f"rollup/{integration_sha}"
        pushed = self.git(["push", "origin", f"{integration_sha}:refs/heads/{rollup_head}"], check=False)
        if pushed.returncode != 0:
            raise RuntimeError(pushed.stderr.strip() or pushed.stdout.strip() or f"failed to push {rollup_head}")
        return self.open_pr_with_label(title, body_file, base=review_base_branch, head=rollup_head)

    def record_recent_pr_merge(self, pr: str) -> None:
        pr_target = self._normalize_lifecycle_target_or_raise(
            pr,
            kind="pr",
            action="record-recent-pr-merge",
            source="argument",
        )
        fact_json = ""
        for attempt in range(3):
            result = self.gh(["pr", "view", pr_target, "--json", "number,mergedAt,mergeCommit,baseRefName,headRefName"], check=False)
            fact_json = result.stdout
            try:
                facts = json.loads(fact_json)
                if facts.get("mergedAt") and isinstance(facts.get("mergeCommit"), dict) and facts["mergeCommit"].get("oid"):
                    break
            except Exception:
                pass
            if attempt < 2:
                time_sleep = float(os.environ.get("RECENT_PR_MERGE_RETRY_SLEEP_SECONDS", "1"))
                __import__("time").sleep(time_sleep)
        facts = json.loads(fact_json or "{}")
        merge_commit = facts.get("mergeCommit") if isinstance(facts, dict) else None
        sha = merge_commit.get("oid") if isinstance(merge_commit, dict) else None
        merged_at = facts.get("mergedAt") if isinstance(facts, dict) else None
        pr_num = self._normalize_lifecycle_target_or_raise(
            facts.get("number") or pr_target,
            kind="pr",
            action="record-recent-pr-merge",
            source="github-facts",
        )
        if not pr_num or not sha or not merged_at:
            raise RuntimeError(
                "merge_pr: recent-pr-merges projection failed: missing mergedAt or mergeCommit.oid after retry; "
                "recover by writing .refactor-loop/state/recent-pr-merges.json"
            )
        path = self.ctx.paths.recent_pr_merges
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(hours=2)
        try:
            existing = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        except Exception:
            existing = {}
        merges = existing.get("merges") if isinstance(existing, dict) else []
        kept = []
        for item in merges if isinstance(merges, list) else []:
            if not isinstance(item, dict):
                continue
            item_time = _parse_time(item.get("merged_at"))
            if item_time is None or item_time < cutoff:
                continue
            if item.get("pr") == int(pr_num) and item.get("sha") == str(sha):
                continue
            kept.append(item)
        kept.append(
            {
                "pr": int(pr_num),
                "sha": str(sha),
                "merged_at": str(merged_at),
                "base_ref": facts.get("baseRefName") or "",
                "head_ref": facts.get("headRefName") or "",
            }
        )
        data = {
            "count": len(kept),
            "window_hours": 2,
            "updated_at": now.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "merges": kept,
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=str(path.parent), delete=False) as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            tmp = handle.name
        Path(tmp).replace(path)

    def apply_triage_decision_marker(self, marker: str) -> int:
        if not self._require_owner_or_return("apply-triage", code=3):
            return 3
        match = re.fullmatch(r"TRIAGE_DECISION_DONE:([0-9]+):(accept|reject):(\.refactor-loop/runs/.*\.json)", marker)
        if not match:
            sys.stderr.write("apply_triage_decision_marker: invalid marker\n")
            return 2
        issue, verdict, rel_path = match.groups()
        if not self._require_github_actor_or_return("apply-triage", code=3):
            return 3
        triage_env = dict(self.ctx.host_env)
        triage_env["REPO_ROOT"] = str(self.ctx.repo_root)
        if self.ctx.gh_repo_slug:
            triage_env["GH_REPO_SLUG"] = self.ctx.gh_repo_slug
        config = load_triage_apply_config(repo_root=self.ctx.repo_root, env=triage_env, cwd=self.ctx.repo_root)
        return apply_decision(config, self.ctx.repo_root / rel_path, issue_number=int(issue), verdict=verdict)

    def publish_worker_output_from_action(self, action: Mapping[str, object]) -> int:
        if not self._require_owner_or_return("publish-worker-output", code=3):
            return 3
        head_ref = str(action.get("head_ref") or "").strip()
        worktree = Path(str(action.get("worktree") or ""))
        if not _safe_branch_name(head_ref):
            sys.stderr.write("publish_worker_output_from_action: invalid head_ref\n")
            return 2
        if not worktree.is_absolute() or not worktree.is_dir():
            sys.stderr.write("publish_worker_output_from_action: worktree must be an existing absolute path\n")
            return 2
        try:
            worktree.resolve().relative_to((self.ctx.repo_root / ".worktrees").resolve())
        except ValueError:
            sys.stderr.write("publish_worker_output_from_action: worktree outside controller-owned .worktrees\n")
            return 2
        clean = subprocess.run(["git", "-C", str(worktree), "diff", "--quiet"], capture_output=True, text=True, check=False)
        if clean.returncode != 0:
            sys.stderr.write("publish_worker_output_from_action: dirty scoped diff; worker commit required first\n")
            return 2
        return self.safe_push(branch=head_ref, worktree=worktree)

    def publish_implementation_output(self, action: Mapping[str, object]) -> int:
        if not self._require_owner_or_return("publish-implementation-output", code=3):
            return 3
        marker = str(action.get("source_marker") or "")
        if not marker.startswith("IMPLEMENT_DONE:") or not marker.endswith(":ok"):
            sys.stderr.write("publish_implementation_output: requires clean IMPLEMENT_DONE:*:ok marker\n")
            return 2
        head_ref = str(action.get("head_ref") or "").strip()
        worktree = Path(str(action.get("worktree") or ""))
        if not _safe_branch_name(head_ref):
            sys.stderr.write("publish_implementation_output: invalid head_ref\n")
            return 2
        if not worktree.is_absolute() or not worktree.is_dir():
            sys.stderr.write("publish_implementation_output: worktree must be an existing absolute path\n")
            return 2
        try:
            worktree.resolve().relative_to((self.ctx.repo_root / ".worktrees").resolve())
        except ValueError:
            sys.stderr.write("publish_implementation_output: worktree outside controller-owned .worktrees\n")
            return 2
        issue_target = self._normalize_lifecycle_target_or_block(
            action.get("linked_issue") or action.get("target_number"),
            kind="issue",
            action="publish-implementation-output",
            source="wakeup-runner-action",
        )
        if issue_target is None:
            return 2
        if action.get("target_kind") != "issue":
            sys.stderr.write("publish_implementation_output: target_kind must be issue\n")
            return 2
        if not self._live_target_has_managed_label(kind="issue", target=issue_target):
            sys.stderr.write("publish_implementation_output: linked issue is not managed\n")
            return 2
        identity_error = self._validate_publish_implementation_identity(action, issue_target, head_ref, worktree)
        if identity_error:
            sys.stderr.write(f"publish_implementation_output: {identity_error}\n")
            return 2
        diff_ready = self._require_publish_implementation_diff(worktree)
        if diff_ready != 0:
            return diff_ready
        title_error = self._implementation_pr_title_error(action, issue_target)
        if title_error:
            sys.stderr.write(f"publish_implementation_output: {title_error}\n")
            return 2
        body_error = self._implementation_pr_body_error(action, issue_target)
        if body_error:
            sys.stderr.write(f"publish_implementation_output: {body_error}\n")
            return 2
        pr_error, pr_target = self._matching_implementation_pr(head_ref, issue_target)
        if pr_error:
            sys.stderr.write(f"publish_implementation_output: {pr_error}\n")
            return 2
        committed = self._commit_publish_implementation_diff(action, issue_target, head_ref, worktree)
        if committed != 0:
            return committed
        base_error = self._recover_publish_implementation_base(worktree)
        if base_error:
            return self._delegate_publish_implementation_fallback(action, issue_target, head_ref, worktree, base_error)
        if self._run_host_command("BUILD_CMD", worktree) != 0:
            return 3
        if self._run_host_command("TEST_CMD", worktree) != 0:
            return 3
        pushed = self.safe_push(branch=head_ref, worktree=worktree)
        if pushed != 0:
            return pushed
        if pr_target is None:
            pr_target, _url = self.open_pr_with_label(
                self._implementation_pr_title(action, issue_target),
                str(self._implementation_pr_body_file(action, issue_target)),
                base=self.integration_branch,
                head=head_ref,
            )
        return self.dispatch_reviewers({"target_kind": "PR", "target_number": pr_target})

    def _validate_publish_implementation_identity(
        self,
        action: Mapping[str, object],
        issue_target: str,
        head_ref: str,
        worktree: Path,
    ) -> str | None:
        marker = str(action.get("source_marker") or "")
        marker_id = marker.removeprefix("IMPLEMENT_DONE:").removesuffix(":ok").strip(":")
        candidate = marker_id.replace("_", "-").strip("-") or f"issue-{issue_target}"
        expected_head = f"refactor/iter{issue_target}-{candidate}"
        expected_worktree = (self.ctx.repo_root / ".worktrees" / f"iter{issue_target}-{candidate}").resolve()
        if head_ref != expected_head or worktree.resolve() != expected_worktree:
            return "noncanonical identity"
        branch = self._git_in(worktree, ["rev-parse", "--abbrev-ref", "HEAD"], check=False)
        if branch.returncode != 0 or branch.stdout.strip() != head_ref:
            return "noncanonical branch"
        return None

    def _require_publish_implementation_diff(self, worktree: Path) -> int:
        diff = self._git_in(worktree, ["diff", "HEAD", "--quiet"], check=False)
        if diff.returncode == 1:
            return 0
        if diff.returncode != 0:
            sys.stderr.write("publish_implementation_output: publish_diff_unavailable\n")
            return 2
        committed_delta = self._has_committed_implementation_delta(worktree)
        if committed_delta is None:
            sys.stderr.write("publish_implementation_output: publish_diff_unavailable\n")
            return 2
        if committed_delta:
            return 0
        sys.stderr.write("publish_implementation_output: implementation_produced_no_diff\n")
        return 2

    def _has_committed_implementation_delta(self, worktree: Path) -> bool | None:
        integration, _review_base = self._require_branch_config()
        for ref in (f"origin/{integration}", integration):
            current = self._git_in(worktree, ["rev-parse", "--verify", ref], check=False)
            if current.returncode != 0:
                continue
            merge_base = self._git_in(worktree, ["merge-base", "HEAD", ref], check=False)
            if merge_base.returncode != 0:
                return None
            base_sha = merge_base.stdout.strip()
            if not base_sha:
                return None
            # A committed implementation diff is a valid publish input; compare merge-base..HEAD.
            diff = self._git_in(worktree, ["diff", "--quiet", base_sha, "HEAD"], check=False)
            if diff.returncode == 0:
                return False
            if diff.returncode == 1:
                return True
            return None
        return None

    def _commit_publish_implementation_diff(
        self,
        action: Mapping[str, object],
        issue_target: str,
        head_ref: str,
        worktree: Path,
    ) -> int:
        status = self._git_in(worktree, ["status", "--porcelain"], check=False)
        if status.returncode != 0:
            if status.stderr:
                sys.stderr.write(status.stderr)
            sys.stderr.write("publish_implementation_output: publish_commit_failed\n")
            return 2
        if not status.stdout.strip():
            return 0
        add = self._git_in(worktree, ["add", "-A"], check=False)
        if add.returncode != 0:
            sys.stderr.write("publish_implementation_output: publish_add_failed\n")
            return 2
        commit = self._git_in(worktree, ["commit", "-m", f"实现 issue #{issue_target}"], check=False)
        if commit.returncode == 0:
            return 0
        if commit.stderr:
            sys.stderr.write(commit.stderr)
        sys.stderr.write("publish_implementation_output: publish_commit_failed\n")
        return 2

    def _recover_publish_implementation_base(self, worktree: Path) -> str | None:
        integration, _review_base = self._require_branch_config()
        fetch = self._git_in(worktree, ["fetch", "origin"], check=False)
        if fetch.returncode != 0:
            return "publish_stale_base_fetch_failed"
        merge_base = self._git_in(worktree, ["merge-base", "HEAD", f"origin/{integration}"], check=False)
        current = self._git_in(worktree, ["rev-parse", "--verify", f"origin/{integration}"], check=False)
        if merge_base.returncode != 0 or current.returncode != 0:
            return "publish_stale_base_unavailable"
        if merge_base.stdout.strip() != current.stdout.strip():
            merge = self._git_in(worktree, ["merge", "--no-edit", f"origin/{integration}"], check=False)
            if merge.returncode != 0:
                return "publish_stale_base_merge_conflict"
        return None

    def _delegate_publish_implementation_fallback(
        self,
        action: Mapping[str, object],
        issue_target: str,
        head_ref: str,
        worktree: Path,
        reason: str,
    ) -> int:
        prompt = self.ctx.paths.prompts / f"publish-implementation-fallback-{issue_target}.md"
        log = self.ctx.paths.logs / f"publish-implementation-fallback-{issue_target}.log"
        output = self.ctx.paths.runs / f"publish-implementation-fallback-{issue_target}.md"
        prompt.parent.mkdir(parents=True, exist_ok=True)
        log.parent.mkdir(parents=True, exist_ok=True)
        output.parent.mkdir(parents=True, exist_ok=True)
        self.render_template(
            str(self.ctx.skill_root / "prompts" / "publish-implementation-fallback.md"),
            str(prompt),
            env={
                "ISSUE_NUMBER": issue_target,
                "WORKTREE_PATH": str(worktree),
                "BRANCH": head_ref,
                "BASE_BRANCH": self.integration_branch,
                "FALLBACK_REASON": reason,
                "PUBLISH_FALLBACK_OUTPUT_PATH": self.ctx.durable_artifact_path(output),
                "SOURCE_MARKER": str(action.get("source_marker") or ""),
            },
        )
        self._append_harness_spawn_intent(
            intent_id=f"publish-implementation-fallback:{issue_target}",
            task_id=f"publish-implementation-fallback-{issue_target}",
            route="publish-implementation-fallback",
            cd=worktree,
            prompt=prompt,
            log=log,
            stall=5400,
            reason=f"publish implementation fallback for issue #{issue_target}: {reason}",
        )
        sys.stderr.write(f"publish_implementation_output: delegated fallback resolver: {reason}\n")
        return PUBLISH_IMPLEMENTATION_FALLBACK_DELEGATED_EXIT

    def dispatch_consensus_implementation(self, action: Mapping[str, object]) -> int:
        if not self._require_owner_or_return("dispatch-consensus-implementation", code=3):
            return 3
        number = self._normalize_lifecycle_target_or_block(
            action.get("target_number"),
            kind="issue",
            action="dispatch-consensus-implementation",
            source="wakeup-runner-action",
        )
        if number is None:
            return 2
        if action.get("target_kind") != "issue":
            sys.stderr.write("dispatch_consensus_implementation: target_kind must be issue\n")
            return 2
        required_fields = (
            "consensus_artifact",
            "design_decision_path",
            "scope_paths",
            "old_pattern",
            "new_principle",
            "cluster_id",
            "iteration",
        )
        for field in required_fields:
            if not str(action.get(field) or "").strip():
                sys.stderr.write(f"dispatch_consensus_implementation: missing {field}\n")
                return 2
        if str(action.get("design_decision_path")) != str(action.get("consensus_artifact")):
            sys.stderr.write("dispatch_consensus_implementation: design_decision_path must match consensus_artifact\n")
            return 2
        readiness_reason = consensus_implementation_suppressed_reason(dict(action), self.ctx.repo_root)
        if readiness_reason:
            sys.stderr.write(f"dispatch_consensus_implementation: target not ready: {readiness_reason}\n")
            return 2
        phase_result = self._move_issue_to_implementing_phase(number)
        if phase_result != 0:
            return phase_result
        cluster_id = str(action["cluster_id"])
        iteration = str(action["iteration"])
        worktree, branch = self.fresh_safe_worktree(iteration, cluster_id, self.integration_branch)
        log = self.ctx.paths.logs / f"implement-{cluster_id}.log"
        self._clear_stale_implement_log_for_fresh_dispatch(log, action)
        prompt = self.ctx.paths.prompts / f"implement-{cluster_id}.md"
        prompt.parent.mkdir(parents=True, exist_ok=True)
        self.render_template(
            str(self.ctx.skill_root / "prompts" / "implement.md"),
            str(prompt),
            env={
                "WORK_UNIT_ID": cluster_id,
                "CLUSTER_ID": cluster_id,
                "ITERATION": iteration,
                "WORKTREE_PATH": str(worktree),
                "BRANCH": branch,
                "WORK_UNIT_SOURCE_REF": str(action.get("source_ref") or f"gh-issue-{number}"),
                "DESIGN_DECISION_PATH": str(action["design_decision_path"]),
                "OLD_PATTERN": str(action["old_pattern"]),
                "NEW_PRINCIPLE": str(action["new_principle"]),
                "SCOPE_PATHS": str(action["scope_paths"]),
                "VERIFICATION_HINTS": str(action.get("verification_hints") or ""),
            },
        )
        self._append_harness_spawn_intent(
            intent_id=f"dispatch-consensus-implementation:{number}",
            task_id=f"implement-{cluster_id}",
            route="dispatch-consensus-implementation",
            cd=worktree,
            prompt=prompt,
            log=log,
            stall=5400,
            reason=f"issue #{number} consensus implementation",
        )
        return 0

    def _move_issue_to_implementing_phase(self, issue_target: str) -> int:
        add_labels = (labels.MANAGED, labels.PHASE_IMPLEMENTING, labels.HUMAN_AUTO)
        remove_labels = ISSUE_LABELS_REMOVE
        args = ["issue", "edit", issue_target]
        for label in remove_labels:
            args.extend(["--remove-label", label])
        args.extend(["--add-label", ",".join(add_labels)])
        result = self.gh(args, check=False)
        if result.returncode != 0:
            self._write_phase_transition_blocked_event(
                issue_target=issue_target,
                result=result,
                add_labels=add_labels,
                remove_labels=remove_labels,
            )
        return result.returncode

    def _write_phase_transition_blocked_event(
        self,
        *,
        issue_target: str,
        result: subprocess.CompletedProcess[str],
        add_labels: Sequence[str],
        remove_labels: Sequence[str],
    ) -> None:
        line = self._format_phase_transition_blocked_event(
            issue_target=issue_target,
            gh_rc=result.returncode,
            gh_stderr=result.stderr,
            add_labels=add_labels,
            remove_labels=remove_labels,
        )
        self._append_pending_event(line)
        sys.stderr.write(f"{line}\n")

    def _format_phase_transition_blocked_event(
        self,
        *,
        issue_target: str,
        gh_rc: int,
        gh_stderr: str,
        add_labels: Sequence[str],
        remove_labels: Sequence[str],
    ) -> str:
        prefix = f"CONTROLLER_ACTION_BLOCKED:phase-transition:dispatch-consensus-implementation:issue:{issue_target}"
        fields: Mapping[str, object] = {
            "controller_action": "dispatch-consensus-implementation",
            "action": "move-to-implementing",
            "target_kind": "issue",
            "target_number": issue_target,
            "issue": issue_target,
            "helper": "gh",
            "gh_rc": gh_rc,
            "gh_stderr": _single_line(gh_stderr),
            "add_labels": ",".join(add_labels),
            "remove_labels": ",".join(remove_labels),
        }
        return f"{prefix} {_format_key_value_suffix(fields)}"

    def _clear_stale_implement_log_for_fresh_dispatch(self, log: Path, action: Mapping[str, object] | None = None) -> None:
        clear_redispatchable_implement_log(
            repo_root=self.ctx.repo_root,
            action=action,
            log_path=log,
            integration_branch=self.integration_branch,
            command_runner=lambda command: self._git_lifecycle_command(command),
        )

    def _git_lifecycle_command(self, command: Sequence[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(list(command), capture_output=True, text=True, check=False)

    def dispatch_reviewers(self, action: Mapping[str, object]) -> int:
        if not self._require_owner_or_return("dispatch-reviewers", code=3):
            return 3
        pr_target = self._normalize_lifecycle_target_or_block(
            action.get("target_number"),
            kind="pr",
            action="dispatch-reviewers",
            source="wakeup-runner-action",
        )
        if pr_target is None:
            return 2
        pr = self.gh(["pr", "view", pr_target, "--json", "title,baseRefName,headRefName,headRefOid"], check=False)
        if pr.returncode != 0:
            return pr.returncode
        try:
            facts = json.loads(pr.stdout or "{}")
        except json.JSONDecodeError:
            return 2
        base = str(facts.get("baseRefName") or self.integration_branch)
        head = str(facts.get("headRefName") or "")
        head_sha = str(facts.get("headRefOid") or "")
        title = str(facts.get("title") or f"PR {pr_target}")
        if not head or not head_sha:
            return 2
        stale_roles = action.get("stale_review_roles")
        if isinstance(stale_roles, list):
            roles = tuple(role for role in REVIEW_ROLES if role in {str(item) for item in stale_roles})
            if not roles:
                return 2
        else:
            roles = REVIEW_ROLES
        for role in roles:
            round_number = self._next_review_round(pr_target, role)
            if self._pending_review_spawn_exists(pr_target, role, round_number):
                continue
            prompt = self.ctx.paths.prompts / f"review-pr{pr_target}-{role}-r{round_number}.md"
            template = self.ctx.skill_root / "prompts" / f"reviewer-{role}.md"
            self.render_template(
                str(template),
                str(prompt),
                env={
                    "PR_NUMBER": pr_target,
                    "PR_TITLE": title,
                    "BASE_BRANCH": base,
                    "HEAD_BRANCH": head,
                    "HEAD_SHA": head_sha,
                    "REVIEW_OUTPUT_PATH": f".refactor-loop/runs/review-pr{pr_target}-{role}-r{round_number}.md",
                },
            )
            self._append_harness_spawn_intent(
                intent_id=f"dispatch-reviewers:{pr_target}:{role}:r{round_number}",
                task_id=f"review-pr{pr_target}-{role}-r{round_number}",
                route="dispatch-reviewers",
                cd=self.ctx.repo_root,
                prompt=prompt,
                log=self.ctx.paths.logs / f"review-pr{pr_target}-{role}-r{round_number}.log",
                stall=5400,
                reason=f"review PR #{pr_target} as {role}",
            )
        return 0

    def _next_review_round(self, pr_target: str, role: str) -> int:
        rounds: list[int] = []
        pattern = re.compile(rf"^review-pr{re.escape(pr_target)}-{re.escape(role)}-r([1-9][0-9]*)\.(?:md|log)$")
        for directory in (self.ctx.paths.prompts, self.ctx.paths.runs, self.ctx.paths.logs):
            for path in directory.glob(f"review-pr{pr_target}-{role}-r*.*"):
                match = pattern.match(path.name)
                if match:
                    rounds.append(int(match.group(1)))
        return (max(rounds) if rounds else 0) + 1

    def _pending_review_spawn_exists(self, pr_target: str, role: str, round_number: int) -> bool:
        try:
            lines = self.ctx.paths.pending_events.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return False
        intent_id = f"dispatch-reviewers:{pr_target}:{role}:r{round_number}"
        for line in lines:
            if " HARNESS_SPAWN_INTENT " not in line:
                continue
            try:
                intent = json.loads(line.split(" HARNESS_SPAWN_INTENT ", 1)[1])
            except json.JSONDecodeError:
                continue
            if isinstance(intent, dict) and intent.get("intent_id") == intent_id:
                return True
        return False

    def open_release_rollup_pr_from_action(self, action: Mapping[str, object]) -> int:
        event = action.get("event")
        event_json = json.dumps(event, sort_keys=True) if isinstance(event, dict) else str(action.get("event_json") or "")
        body_file = str(action.get("body_file") or "")
        title = str(action.get("title") or "Release rollup")
        self.open_release_rollup_pr_from_pending_event(event_json, body_file, title=title)
        return 0

    def _append_harness_spawn_intent(
        self,
        *,
        intent_id: str,
        task_id: str,
        route: str,
        cd: Path,
        prompt: Path,
        log: Path,
        stall: int,
        reason: str,
    ) -> None:
        intent = {
            "intent_id": intent_id,
            "source": "controller-actions",
            "route": route,
            "task_id": task_id,
            "priority": "p1",
            "command": "spawn-codex",
            "controller_action": "spawn_codex_harness_background",
            "cd": str(cd.resolve()),
            "prompt": self.ctx.durable_artifact_path(prompt),
            "log": self.ctx.durable_artifact_path(log),
            "stall": stall,
            "reason": reason,
            "run_in_background_required": True,
            "no_lifecycle_authority": True,
        }
        self._append_pending_event(
            f"{self._now()} HARNESS_SPAWN_INTENT {json.dumps(intent, ensure_ascii=False, sort_keys=True)}"
        )

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def close_managed_item_from_drop_marker(self, action: Mapping[str, object]) -> int:
        if not self._require_owner_or_return("close-managed-drop", code=3):
            return 3
        marker = str(action.get("source_marker") or action.get("marker") or "")
        if not marker.startswith("META_RESOLVED:drop:"):
            sys.stderr.write("close_managed_item_from_drop_marker: requires clean META_RESOLVED:drop marker\n")
            return 2
        kind = str(action.get("target_kind") or "").lower()
        issue_target = self._normalize_lifecycle_target_or_block(
            action.get("target_number"),
            kind="pr" if kind == "pr" else "issue",
            action="close-managed-drop",
            source="wakeup-runner-action",
        )
        if issue_target is None:
            return 2
        if not self._live_target_has_managed_label(kind="pr" if kind == "pr" else "issue", target=issue_target):
            self._append_pending_event(
                f"CONTROLLER_ACTION_BLOCKED:target-not-managed:close-managed-drop:{'pr' if kind == 'pr' else 'issue'}:{issue_target}"
            )
            sys.stderr.write("close_managed_item_from_drop_marker: live target is not managed\n")
            return 2
        if not self._require_github_actor_or_return("close-managed-drop", code=3):
            return 3
        comment = "Closed from drop marker.\n\n⟦AI:AUTO-LOOP⟧"
        if kind == "pr":
            pr_target = issue_target
            result = self.gh(["pr", "close", pr_target, "--comment", comment], check=False)
        else:
            result = self.gh(["issue", "close", issue_target, "--reason", "not planned", "--comment", comment], check=False)
        return result.returncode

    def _live_target_has_managed_label(self, *, kind: str, target: str) -> bool:
        result = self.gh([kind, "view", target, "--json", "labels,body"], check=False)
        if result.returncode != 0:
            return False
        try:
            payload = json.loads(result.stdout or "{}")
        except json.JSONDecodeError:
            return False
        raw_labels = payload.get("labels")
        if not isinstance(raw_labels, list):
            return False
        names = [item.get("name") for item in raw_labels if isinstance(item, dict)]
        return labels.MANAGED in labels.normalize_label_set(names).canonical

    def _matching_implementation_pr(self, head_ref: str, issue_target: str) -> tuple[str | None, int | None]:
        result = self.gh(
            ["pr", "list", "--state", "open", "--head", head_ref, "--json", "number,baseRefName,headRefName,labels,body"],
            check=False,
        )
        if result.returncode != 0:
            return "matching_pr_unavailable", None
        try:
            payload = json.loads(result.stdout or "[]")
        except json.JSONDecodeError:
            return "matching_pr_invalid_json", None
        if not isinstance(payload, list):
            return "matching_pr_invalid_json", None
        if len(payload) == 0:
            return None, None
        if len(payload) > 1:
            return "multiple_matching_open_pr", None
        pr = payload[0]
        if not isinstance(pr, dict):
            return "matching_pr_invalid_json", None
        number = pr.get("number")
        if not isinstance(number, int) or number <= 0:
            return "matching_pr_invalid_json", None
        if str(pr.get("headRefName") or "") != head_ref:
            return "matching_pr_head_mismatch", None
        if str(pr.get("baseRefName") or "") != self.integration_branch:
            return "matching_pr_base_mismatch", None
        raw_labels = pr.get("labels")
        if not isinstance(raw_labels, list):
            return "matching_pr_not_managed", None
        names = [item.get("name") for item in raw_labels if isinstance(item, dict)]
        if labels.MANAGED not in labels.normalize_label_set(names).canonical:
            return "matching_pr_not_managed", None
        if _single_linked_issue(str(pr.get("body") or "")) != issue_target:
            return "matching_pr_issue_mismatch", None
        return None, number

    def _run_host_command(self, name: str, cwd: Path) -> int:
        command = str(self.ctx.env_for_subprocess().get(name) or "").strip()
        if not command:
            sys.stderr.write(f"publish_implementation_output: missing {name}\n")
            return 2
        result = subprocess.run(
            ["bash", "-lc", command],
            cwd=str(cwd),
            env=self.ctx.env_for_subprocess(),
            capture_output=True,
            text=True,
            check=False,
        )
        if result.stdout:
            print(result.stdout, end="")
        if result.stderr:
            sys.stderr.write(result.stderr)
        return result.returncode

    def _implementation_pr_body_file(self, action: Mapping[str, object], issue_target: str) -> Path:
        return implementation_pr_body_path(self.ctx.repo_root, self.ctx.paths.runs, action, issue_target)

    def _implementation_pr_title_file(self, action: Mapping[str, object], issue_target: str) -> Path:
        return implementation_pr_title_path(self.ctx.repo_root, self.ctx.paths.runs, action, issue_target)

    def _implementation_pr_title(self, action: Mapping[str, object], issue_target: str) -> str:
        return self._implementation_pr_title_file(action, issue_target).read_text(encoding="utf-8", errors="replace").strip()

    def _implementation_pr_title_error(self, action: Mapping[str, object], issue_target: str) -> str | None:
        validation = validate_implementation_pr_artifacts(self.ctx.repo_root, self.ctx.paths.runs, action, issue_target)
        if validation.reason and validation.reason.startswith("implementation_pr_title_"):
            return _controller_implementation_pr_error(validation.reason, validation.detail)
        return None

    def _implementation_pr_body_error(self, action: Mapping[str, object], issue_target: str) -> str | None:
        validation = validate_implementation_pr_artifacts(self.ctx.repo_root, self.ctx.paths.runs, action, issue_target)
        if validation.reason and validation.reason.startswith("implementation_pr_body_"):
            return _controller_implementation_pr_error(validation.reason, validation.detail)
        return None

    def render_template(self, input_path: str, output_path: str, env: Mapping[str, str] | None = None) -> None:
        values = dict(os.environ)
        if env:
            values.update(env)
        aliases = {
            "work_unit_id": values.get("WORK_UNIT_ID") or values.get("CLUSTER_ID") or "",
            "cluster_id": values.get("CLUSTER_ID", ""),
            "iteration": values.get("ITERATION", ""),
            "worktree_path": values.get("WORKTREE_PATH", ""),
            "branch": values.get("BRANCH", ""),
            "old_pattern": values.get("OLD_PATTERN", ""),
            "new_principle": values.get("NEW_PRINCIPLE", ""),
            "scope_paths": values.get("SCOPE_PATHS", ""),
            "verification_hints": values.get("VERIFICATION_HINTS", ""),
        }
        template_path = self._resolve_template_input(input_path)
        template = template_path.read_text(encoding="utf-8")
        for key, value in aliases.items():
            template = template.replace("{{" + key + "}}", value)
        rendered = inline_prompt_contracts(Template(template).safe_substitute(values), skill_root=self.ctx.skill_root)
        Path(output_path).write_text(rendered, encoding="utf-8")

    def _review_fix_pr_facts(self, pr_number: str, existing: Mapping[str, str]) -> dict[str, str]:
        required = ("PR_TITLE", "HEAD_BRANCH", "BASE_BRANCH")
        if all(str(existing.get(key) or "") for key in required):
            return {key: str(existing.get(key) or "") for key in required}
        pr_target = _normalize_lifecycle_target(pr_number, kind="pr", action="render-review-fix", source="argument")
        result = self.gh(["pr", "view", pr_target, "--json", "title,headRefName,baseRefName"])
        try:
            payload = json.loads(result.stdout or "{}")
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"review-fix prompt render: invalid PR metadata for {pr_target}") from exc
        return {
            "PR_TITLE": str(payload.get("title") or f"PR {pr_target}"),
            "HEAD_BRANCH": str(payload.get("headRefName") or ""),
            "BASE_BRANCH": str(payload.get("baseRefName") or ""),
        }

    def _review_fix_review_paths(self, pr_number: str, existing: Mapping[str, str]) -> dict[str, str]:
        keys = tuple(f"REVIEW_{role.upper()}_PATH" for role in REVIEW_ROLES)
        if all(str(existing.get(key) or "") for key in keys):
            return {key: str(existing.get(key) or "") for key in keys}
        latest = self._latest_review_fix_round_paths(pr_number)
        result: dict[str, str] = {}
        for role in REVIEW_ROLES:
            key = f"REVIEW_{role.upper()}_PATH"
            result[key] = latest.get(role, "")
        return result

    def _latest_review_fix_round_paths(self, pr_number: str) -> dict[str, str]:
        by_round: dict[int, dict[str, str]] = {}
        artifact_keys: set[tuple[str, int]] = set()
        artifact_re = re.compile(rf"^review-pr{re.escape(pr_number)}-([A-Za-z][A-Za-z0-9_-]*)-r([1-9][0-9]*)\.md$")
        log_re = re.compile(rf"^review-pr{re.escape(pr_number)}-([A-Za-z][A-Za-z0-9_-]*)-r([1-9][0-9]*)\.log$")
        for path in sorted(self.ctx.paths.runs.glob(f"review-pr{pr_number}-*-r*.md")):
            match = artifact_re.match(path.name)
            if not match:
                continue
            role = match.group(1)
            if role not in REVIEW_ROLES:
                continue
            round_number = int(match.group(2))
            log_path = self.ctx.paths.logs / f"review-pr{pr_number}-{role}-r{round_number}.log"
            if not _review_fix_log_has_exit_zero(log_path):
                continue
            by_round.setdefault(round_number, {})[role] = self.ctx.durable_artifact_path(path)
            artifact_keys.add((role, round_number))
        for path in sorted(self.ctx.paths.logs.glob(f"review-pr{pr_number}-*-r*.log")):
            match = log_re.match(path.name)
            if not match:
                continue
            role = match.group(1)
            if role not in REVIEW_ROLES:
                continue
            round_number = int(match.group(2))
            if (role, round_number) in artifact_keys or not _review_fix_log_has_exit_zero(path):
                continue
            by_round.setdefault(round_number, {})[role] = self.ctx.durable_artifact_path(path)
        complete_rounds = [round_number for round_number, paths in by_round.items() if all(role in paths for role in REVIEW_ROLES)]
        if not complete_rounds:
            return {}
        return by_round[max(complete_rounds)]

    def render_review_fix_prompt(
        self,
        pr_number: int,
        round_number: int,
        env: Mapping[str, str] | None = None,
    ) -> ReviewFixDispatchSpec:
        spec = ReviewFixDispatchSpec.for_round(pr_number, round_number)
        render_env = {
            "AUDIT_PATH": "",
            "IMPLEMENT_SUMMARY_PATH": "",
            "CLUSTER_ID": "",
            "ISSUE_NUMBER": "",
            "ITERATION": "",
            "PROJECT_RULES": "CLAUDE.md",
            "HOST_REFACTOR_COMMENT_POLICY": "none",
        }
        render_env.update(env or {})
        render_env.update(self._review_fix_pr_facts(spec.pr_number, render_env))
        render_env.update(self._review_fix_review_paths(spec.pr_number, render_env))
        render_env.update(spec.as_render_env())
        prompt_path = self.ctx.repo_root / spec.prompt_path
        prompt_path.parent.mkdir(parents=True, exist_ok=True)
        self.render_template(
            str(self.ctx.skill_root / "prompts" / "review-fix.md"),
            str(prompt_path),
            env=render_env,
        )
        self._replace_review_fix_shell_defaults(prompt_path, render_env)
        self._ensure_review_fix_prompt_fully_rendered(prompt_path)
        self._write_review_thread_completion_seed(pr_number)
        return spec

    def _replace_review_fix_shell_defaults(self, prompt_path: Path, render_env: Mapping[str, str]) -> None:
        text = prompt_path.read_text(encoding="utf-8")
        text = text.replace("${PROJECT_RULES:-CLAUDE.md}", render_env.get("PROJECT_RULES") or "CLAUDE.md")
        prompt_path.write_text(text, encoding="utf-8")

    def _ensure_review_fix_prompt_fully_rendered(self, prompt_path: Path) -> None:
        text = prompt_path.read_text(encoding="utf-8")
        unresolved = sorted(set(re.findall(r"\$\{[^}]+\}", text)))
        if unresolved:
            raise RuntimeError(f"review-fix prompt render left unresolved placeholders: {', '.join(unresolved)}")

    def _write_review_thread_completion_seed(self, pr_number: int) -> None:
        state_dir = self.ctx.repo_root / ".refactor-loop" / "state" / "review-thread-completion"
        state_path = state_dir / f"pr{pr_number}.json"
        thread = self._first_unresolved_review_thread(pr_number)
        if thread is None:
            state_path.unlink(missing_ok=True)
            return
        state_dir.mkdir(parents=True, exist_ok=True)
        state_path.write_text(
            json.dumps(
                {
                    "review_thread_driven": True,
                    "thread_id": thread["id"],
                    "replied": False,
                    "resolved": False,
                    "source": thread["source"],
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )

    def _first_unresolved_review_thread(self, pr_number: int) -> dict[str, Any] | None:
        slug = self.ctx.gh_repo_slug
        if not slug:
            return {"id": "", "source": "live-pr-review-thread-unknown"}
        owner, _, repo = slug.partition("/")
        if not owner or not repo:
            return {"id": "", "source": "live-pr-review-thread-unknown"}
        query = (
            "query($owner:String!,$repo:String!,$number:Int!,$after:String){ "
            "repository(owner:$owner,name:$repo){ pullRequest(number:$number){ "
            "reviewThreads(first:100, after:$after){ "
            "nodes{ id isResolved } pageInfo{ hasNextPage endCursor } "
            "} } } }"
        )
        after = ""
        while True:
            args = [
                "api",
                "graphql",
                "-f",
                f"owner={owner}",
                "-f",
                f"repo={repo}",
                "-F",
                f"number={pr_number}",
                "-f",
                f"query={query}",
            ]
            if after:
                args.extend(["-f", f"after={after}"])
            result = subprocess.run(
                ["gh", *args],
                cwd=str(self.ctx.repo_root),
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode != 0:
                return {"id": "", "source": "live-pr-review-thread-unknown"}
            try:
                payload = json.loads(result.stdout or "{}")
            except json.JSONDecodeError:
                return {"id": "", "source": "live-pr-review-thread-unknown"}
            review_threads = (
                (((payload.get("data") or {}).get("repository") or {}).get("pullRequest") or {})
                .get("reviewThreads")
            )
            if not isinstance(review_threads, dict):
                return {"id": "", "source": "live-pr-review-thread-unknown"}
            nodes = review_threads.get("nodes")
            if not isinstance(nodes, list):
                return {"id": "", "source": "live-pr-review-thread-unknown"}
            for node in nodes:
                if not isinstance(node, dict):
                    return {"id": "", "source": "live-pr-review-thread-unknown"}
                thread_id = node.get("id")
                is_resolved = node.get("isResolved")
                if isinstance(thread_id, str) and thread_id and is_resolved is False:
                    return {"id": thread_id, "source": "live-pr-review-thread"}
                if not isinstance(is_resolved, bool):
                    return {"id": "", "source": "live-pr-review-thread-unknown"}
            page_info = review_threads.get("pageInfo")
            if not isinstance(page_info, dict):
                return {"id": "", "source": "live-pr-review-thread-unknown"}
            has_next_page = page_info.get("hasNextPage")
            if has_next_page is False:
                return None
            if has_next_page is not True:
                return {"id": "", "source": "live-pr-review-thread-unknown"}
            end_cursor = page_info.get("endCursor")
            if not isinstance(end_cursor, str) or not end_cursor:
                return {"id": "", "source": "live-pr-review-thread-unknown"}
            after = end_cursor

    def validate_review_fix_completion(self, evidence: ReviewThreadCompletionEvidence) -> None:
        validate_review_thread_completion(evidence)

    def _resolve_template_input(self, input_path: str) -> Path:
        if not input_path.startswith("host:"):
            return Path(input_path)
        try:
            spec = load_validated_workflow_spec(self.ctx)
        except WorkflowSpecError as exc:
            raise RuntimeError(str(exc)) from exc
        rel = spec.prompt_binding_path(input_path)
        if not rel:
            raise RuntimeError(f"unknown host prompt binding: {input_path}")
        return self.ctx.repo_root / rel

    def _worktree_for_branch(self, branch: str) -> Path | None:
        result = self.git(["worktree", "list", "--porcelain"], check=False)
        current: Path | None = None
        for line in result.stdout.splitlines():
            if line.startswith("worktree "):
                current = Path(line.removeprefix("worktree "))
            elif line == f"branch refs/heads/{branch}" and current:
                return current
        return None

    def _require_owner_or_return(self, action: str, *, code: int) -> bool:
        decision = require_active_controller(self.ctx, action)
        write_active_controller_status(self.ctx, decision)
        if not decision.allowed:
            sys.stderr.write(f"active_controller=noop:not-owner action={action} owner={decision.owner_device}\n")
            return False
        return True

    def _require_owner_or_raise(self, action: str) -> None:
        decision = require_active_controller(self.ctx, action)
        write_active_controller_status(self.ctx, decision)
        if not decision.allowed:
            raise RuntimeError(f"active_controller=noop:not-owner action={action} owner={decision.owner_device}")

    def _require_github_actor_or_return(self, action: str, *, code: int) -> bool:
        actor = self.github_actor or GitHubAuthenticatedActor(self.ctx)
        try:
            actor.require_admission(action)
        except RuntimeError as exc:
            sys.stderr.write(str(exc) + "\n")
            return False
        return True

    def _require_github_actor_or_raise(self, action: str) -> None:
        actor = self.github_actor or GitHubAuthenticatedActor(self.ctx)
        actor.require_admission(action)

    def _normalize_lifecycle_target_or_block(self, value: object, *, kind: str, action: str, source: str) -> str | None:
        try:
            return _normalize_lifecycle_target(value, kind=kind, action=action, source=source)
        except ValueError as exc:
            self._append_invalid_github_target_event(kind=kind, action=action, source=source)
            sys.stderr.write(str(exc) + "\n")
            return None

    def _normalize_lifecycle_target_or_raise(self, value: object, *, kind: str, action: str, source: str) -> str:
        target = self._normalize_lifecycle_target_or_block(value, kind=kind, action=action, source=source)
        if target is None:
            raise RuntimeError(f"{action}: invalid {kind} target from {source}")
        return target

    def _append_invalid_github_target_event(self, *, kind: str, action: str, source: str) -> None:
        self._append_pending_event(f"CONTROLLER_ACTION_BLOCKED:invalid-github-target:{action}:{kind}:{source}")

    def _append_pending_event(self, line: str) -> None:
        self.ctx.paths.pending_events.parent.mkdir(parents=True, exist_ok=True)
        with self.ctx.paths.pending_events.open("a", encoding="utf-8") as handle:
            handle.write(f"{line}\n")

    def _single_body_linked_issue_or_block(self, body: str, *, action: str) -> str | None:
        for target in _body_closing_issue_targets(body):
            if self._normalize_lifecycle_target_or_block(
                target,
                kind="issue",
                action=action,
                source="body-link",
            ) is None:
                return None
        return _single_linked_issue(body)

    def _single_body_linked_issue_or_raise(self, body: str, *, action: str) -> str:
        target = self._single_body_linked_issue_or_block(body, action=action)
        if target is None:
            raise RuntimeError(f"{action}: invalid issue target from body-link")
        return target


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _normalize_lifecycle_target(value: object, *, kind: str, action: str, source: str) -> str:
    """Return a canonical positive GitHub issue or PR number."""
    target = "" if value is None else str(value)
    if not GITHUB_LIFECYCLE_TARGET_RE.fullmatch(target):
        raise ValueError(f"{action}: invalid {kind} target from {source}: {target!r}")
    return target


def _single_linked_issue(body: str) -> str:
    numbers = extract_closing_issue_numbers(body)
    return str(numbers[0]) if len(numbers) == 1 else ""


def _body_closing_issue_targets(body: str) -> tuple[str, ...]:
    return tuple(match.group(1) for match in BODY_CLOSING_ISSUE_TARGET_RE.finditer(body or ""))


def _single_line(value: str) -> str:
    return " ".join(str(value or "").splitlines())


def _format_key_value_suffix(fields: Mapping[str, object]) -> str:
    return " ".join(f"{key}={json.dumps(str(value), ensure_ascii=False)}" for key, value in fields.items())


def _validate_safe_worktree_fields(iteration: str, cluster: str) -> None:
    """Validate worktree identity fields before constructing local paths."""
    if not SAFE_WORKTREE_ITERATION_RE.fullmatch(iteration):
        raise ValueError(f"safe_worktree iteration must be digits only: {iteration!r}")
    if not SAFE_WORKTREE_CLUSTER_RE.fullmatch(cluster):
        raise ValueError(f"safe_worktree cluster must match [A-Za-z0-9._-]+: {cluster!r}")


def _review_fix_log_has_exit_zero(path: Path) -> bool:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return False
    return any(line.strip() == "EXIT=0" for line in lines)


def _safe_branch_name(value: str) -> bool:
    return bool(value) and not value.startswith("-") and not any(ch.isspace() or ord(ch) < 32 for ch in value)


def _implementation_cluster_id(action: Mapping[str, object], issue_target: str) -> str:
    return implementation_cluster_id(action, issue_target)


def _controller_implementation_pr_error(reason: str, detail: str = "") -> str:
    messages = {
        "implementation_pr_title_artifact_invalid_path": "implementation PR title artifact outside runs",
        "implementation_pr_title_artifact_missing": "implementation PR title artifact missing",
        "implementation_pr_title_artifact_invalid": "implementation PR title must be exactly one non-empty line",
        "implementation_pr_title_placeholder": "implementation PR title is placeholder",
        "implementation_pr_title_contains_body_content": "implementation PR title contains body-only content",
        "implementation_pr_body_artifact_invalid_path": "implementation PR body artifact outside runs",
        "implementation_pr_body_artifact_missing": "implementation PR body artifact missing",
        "implementation_pr_body_sentinel_missing": "implementation PR body sentinel must be final standalone line",
        "implementation_pr_body_closes_mismatch": "implementation PR body must contain exactly one matching Closes link",
        "implementation_pr_body_required_section_missing": "implementation PR body missing required section",
        "implementation_pr_body_placeholder": "implementation PR body is placeholder",
        "implementation_pr_body_github_body_invalid": "implementation PR body invalid",
    }
    message = messages.get(reason, reason)
    return f"{message}: {detail}" if detail else message
