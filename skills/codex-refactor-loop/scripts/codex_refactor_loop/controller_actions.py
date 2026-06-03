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
from typing import Mapping, Sequence

from .active_controller import require_active_controller, write_active_controller_status
from . import labels
from .banners import BannerRequest, build_status_banner, gh_comment_command
from .context import LoopContext
from .github_body import GitHubBodyError, validate_self_contained_github_body
from .issue_decomposition import load_issue_decomposition_plan
from .prompt_contracts import inline_prompt_contracts
from .release.publisher import ReleasePublishResult, ReleasePublisher
from .review_fix_dispatch import ReviewFixDispatchSpec
from .triage import apply_decision, load_triage_apply_config
from .work_items import extract_closing_issue_numbers
from .wakeup_plan import consensus_implementation_suppressed_reason
from .workflow_spec import WorkflowSpecError, load_validated_workflow_spec


PR_LABELS_REMOVE = (
    *labels.labels_for_group("phase"),
    labels.HUMAN_MAINTAINER_DECISION,
    labels.STUCK,
    *labels.cleanup_aliases(),
)
ISSUE_LABELS_REMOVE = (
    *labels.labels_for_group("phase"),
    labels.HUMAN_AUTO,
    labels.HUMAN_MAINTAINER_DECISION,
    labels.STUCK,
    *labels.cleanup_aliases(),
)
SAFE_WORKTREE_ITERATION_RE = re.compile(r"^[0-9]+$")
SAFE_WORKTREE_CLUSTER_RE = re.compile(r"^[A-Za-z0-9._-]+$")
GITHUB_LIFECYCLE_TARGET_RE = re.compile(r"^[1-9][0-9]*$")
BODY_CLOSING_ISSUE_TARGET_RE = re.compile(r"(?im)\bCloses\s+#([^\s,;:.)\]}\\]*)")
REVIEW_ROLES = ("architect", "tests", "quality")


class ControllerActions:
    def __init__(self, ctx: LoopContext) -> None:
        self.ctx = ctx
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
        full = ["gh", *(str(a) for a in args)]
        if self.ctx.gh_repo_slug:
            insert_at = 4 if len(full) > 3 and not full[3].startswith("-") else min(3, len(full))
            full[insert_at:insert_at] = ["--repo", self.ctx.gh_repo_slug]
        result = subprocess.run(full, cwd=str(self.ctx.repo_root), capture_output=True, text=True, check=False)
        if check and result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"gh {' '.join(args)} failed")
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

    def _ensure_pr_ready_for_merge(self, pr_target: str) -> int:
        draft = self.gh(["pr", "view", pr_target, "--json", "isDraft", "--jq", ".isDraft"], check=False)
        if draft.returncode != 0:
            return draft.returncode
        if draft.stdout.strip() == "true":
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
        config = load_triage_apply_config(repo_root=self.ctx.repo_root, env=self.ctx.env_for_subprocess(), cwd=self.ctx.repo_root)
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
        if self._open_pr_exists_for_head(head_ref):
            sys.stderr.write("publish_implementation_output: duplicate open PR for head_ref\n")
            return 2
        if self._run_host_command("BUILD_CMD", worktree) != 0:
            return 3
        if self._run_host_command("TEST_CMD", worktree) != 0:
            return 3
        if self._git_in(worktree, ["diff", "--quiet"], check=False).returncode == 0:
            sys.stderr.write("publish_implementation_output: empty scoped diff\n")
            return 2
        add = self._git_in(worktree, ["add", "-A"], check=False)
        if add.returncode != 0:
            return add.returncode
        commit = self._git_in(worktree, ["commit", "-m", f"实现 issue #{issue_target}"], check=False)
        if commit.returncode != 0:
            if commit.stderr:
                sys.stderr.write(commit.stderr)
            return commit.returncode
        pushed = self.safe_push(branch=head_ref, worktree=worktree)
        if pushed != 0:
            return pushed
        body_file = self._implementation_pr_body_file(action, issue_target)
        title = str(action.get("title") or f"实现 issue #{issue_target}")
        pr_target, _url = self.open_pr_with_label(title, str(body_file), base=self.integration_branch, head=head_ref)
        return self.dispatch_reviewers({"target_kind": "PR", "target_number": pr_target})

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
        cluster_id = str(action["cluster_id"])
        iteration = str(action["iteration"])
        worktree, branch = self.safe_worktree(iteration, cluster_id, self.integration_branch)
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
        log = self.ctx.paths.logs / f"implement-{cluster_id}.log"
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
            if self._pending_review_spawn_exists(pr_target, role):
                continue
            prompt = self.ctx.paths.prompts / f"review-pr{pr_target}-{role}-r1.md"
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
                    "REVIEW_OUTPUT_PATH": f".refactor-loop/runs/review-pr{pr_target}-{role}-r1.md",
                },
            )
            self._append_harness_spawn_intent(
                intent_id=f"dispatch-reviewers:{pr_target}:{role}:r1",
                task_id=f"review-pr{pr_target}-{role}-r1",
                route="dispatch-reviewers",
                cd=self.ctx.repo_root,
                prompt=prompt,
                log=self.ctx.paths.logs / f"review-pr{pr_target}-{role}-r1.log",
                stall=5400,
                reason=f"review PR #{pr_target} as {role}",
            )
        return 0

    def _pending_review_spawn_exists(self, pr_target: str, role: str) -> bool:
        try:
            lines = self.ctx.paths.pending_events.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return False
        intent_id = f"dispatch-reviewers:{pr_target}:{role}:r1"
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
            "cd": self.ctx.durable_artifact_path(cd),
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

    def _open_pr_exists_for_head(self, head_ref: str) -> bool:
        result = self.gh(["pr", "list", "--state", "open", "--head", head_ref, "--json", "number"], check=False)
        if result.returncode != 0:
            return True
        try:
            payload = json.loads(result.stdout or "[]")
        except json.JSONDecodeError:
            return True
        return isinstance(payload, list) and len(payload) > 0

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
        raw = str(action.get("body_file") or "").strip()
        if raw:
            path = Path(raw)
            return path if path.is_absolute() else self.ctx.repo_root / path
        path = self.ctx.paths.runs / f"implementation-pr-{issue_target}-body.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"## 🤖 实现 issue #{issue_target}\n\n"
            f"Closes #{issue_target}\n\n"
            "⟦AI:AUTO-LOOP⟧\n",
            encoding="utf-8",
        )
        return path

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

    def render_review_fix_prompt(
        self,
        pr_number: int,
        round_number: int,
        env: Mapping[str, str] | None = None,
    ) -> ReviewFixDispatchSpec:
        spec = ReviewFixDispatchSpec.for_round(pr_number, round_number)
        render_env = dict(env or {})
        render_env.update(spec.as_render_env())
        prompt_path = self.ctx.repo_root / spec.prompt_path
        prompt_path.parent.mkdir(parents=True, exist_ok=True)
        self.render_template(
            str(self.ctx.skill_root / "prompts" / "review-fix.md"),
            str(prompt_path),
            env=render_env,
        )
        return spec

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
        if decision.allowed:
            return True
        sys.stderr.write(f"active_controller=noop:not-owner action={action} owner={decision.owner_device}\n")
        return False

    def _require_owner_or_raise(self, action: str) -> None:
        decision = require_active_controller(self.ctx, action)
        write_active_controller_status(self.ctx, decision)
        if not decision.allowed:
            raise RuntimeError(f"active_controller=noop:not-owner action={action} owner={decision.owner_device}")

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
    """refactor helper, no behavior change except rejecting unsafe GitHub target ids."""
    target = "" if value is None else str(value)
    if not GITHUB_LIFECYCLE_TARGET_RE.fullmatch(target):
        raise ValueError(f"{action}: invalid {kind} target from {source}: {target!r}")
    return target


def _single_linked_issue(body: str) -> str:
    numbers = extract_closing_issue_numbers(body)
    return str(numbers[0]) if len(numbers) == 1 else ""


def _body_closing_issue_targets(body: str) -> tuple[str, ...]:
    return tuple(match.group(1) for match in BODY_CLOSING_ISSUE_TARGET_RE.finditer(body or ""))


def _validate_safe_worktree_fields(iteration: str, cluster: str) -> None:
    """refactor helper, no behavior change except rejecting unsafe path fields."""
    if not SAFE_WORKTREE_ITERATION_RE.fullmatch(iteration):
        raise ValueError(f"safe_worktree iteration must be digits only: {iteration!r}")
    if not SAFE_WORKTREE_CLUSTER_RE.fullmatch(cluster):
        raise ValueError(f"safe_worktree cluster must match [A-Za-z0-9._-]+: {cluster!r}")


def _safe_branch_name(value: str) -> bool:
    return bool(value) and not value.startswith("-") and not any(ch.isspace() or ord(ch) < 32 for ch in value)
