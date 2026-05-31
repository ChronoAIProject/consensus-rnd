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
from .context import LoopContext
from .github_body import GitHubBodyError, validate_self_contained_github_body
from .release.publisher import ReleasePublishResult, ReleasePublisher
from .triage import apply_decision, load_triage_apply_config
from .work_items import extract_closing_issue_numbers
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
# Refactor (issue-276): validate body-linked lifecycle targets without swallowing escaped line boundaries.
BODY_CLOSING_ISSUE_TARGET_RE = re.compile(r"(?im)\bCloses\s+#([^\s,;:.)\]}\\]*)")


class ControllerActions:
    # Refactor (iter201/issue-201): Old pattern: public consensus-rnd-cli exposed
    # merge/open/safe-push/apply lifecycle commands as generic callable verbs.
    # New principle: keep these as controller-internal primitives only; callers
    # construct ControllerActions directly and public CLI routing cannot reach them.
    #
    # Refactor (iter217/issue-217):
    #   Old pattern: release.yml 保留 tag/release mutation,无法可靠读本地 runtime fact,绕过 release-gate decider-only 边界
    #   New principle: controller-only publication:新增 ReleasePublishPreflight+ReleasePublisher 替代 workflow 发布权;release.yml 降为 read-only preview(contents:read,禁 gh release create)。严格按 plan 'Concrete plan' 逐条改。
    def __init__(self, ctx: LoopContext) -> None:
        self.ctx = ctx
        self.integration_branch = os.environ.get("INTEGRATION_BRANCH") or os.environ.get("INTEGRATION") or "auto-refact-dev"
        self.review_base_branch = os.environ.get("REVIEW_BASE_BRANCH") or os.environ.get("REVIEW_BASE") or "dev"

    def gh(self, args: Sequence[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
        # Refactor (loop/gh-arg-coercion): Old pattern: gh() assumed every arg was
        # already a str, so an int caller (e.g. a raw PR number via merge_pr) crashed
        # with AttributeError on full[3].startswith before any gh process ran.
        # New principle: coerce all args to str at the gh() boundary.
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

    def _current_branch(self) -> str:
        result = self.git(["rev-parse", "--abbrev-ref", "HEAD"], check=False)
        return result.stdout.strip() if result.returncode == 0 else ""

    def safe_push(self, remote: str = "origin", branch: str = "") -> int:
        if not self._require_owner_or_return("safe-push", code=3):
            return 3
        branch = branch or self._current_branch()
        if not branch or branch == "HEAD":
            sys.stderr.write("safe_push: cannot determine branch (HEAD detached?); aborting\n")
            return 2
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
            print(f"safe_push: local behind {remote}/{branch} by {behind_count} commit(s); rebasing")
            pull = self.git(["pull", "--rebase", "--autostash", remote, branch], check=False)
            if pull.stdout:
                print(pull.stdout, end="")
            if pull.stderr:
                sys.stderr.write(pull.stderr)
            if pull.returncode != 0:
                sys.stderr.write(f"safe_push: rebase conflict on {remote}/{branch} - resolve manually then push\n")
                return 3
        push = self.git(["push", remote, branch], check=False)
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
        # Refactor (iter217/issue-217):
        #   Old pattern: release.yml 保留 tag/release mutation,无法可靠读本地 runtime fact,绕过 release-gate decider-only 边界
        #   New principle: controller-only publication:新增 ReleasePublishPreflight+ReleasePublisher 替代 workflow 发布权;release.yml 降为 read-only preview(contents:read,禁 gh release create)。严格按 plan 'Concrete plan' 逐条改。
        target = target_ref or os.environ.get("RELEASE_TARGET_REF", "")
        if not target:
            raise RuntimeError("publish_release_candidate: RELEASE_TARGET_REF is required")
        publisher = ReleasePublisher(self.ctx.repo_root)
        return publisher.publish(candidate_path=candidate_path, target_ref=target)

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
        # Refactor (iter81/issue-81):
        #   Old pattern: 文件/分支/marker/label/role 命名混乱;松散 regex(parse_target ^phase9-issue([0-9]+).*)解析,缺 owner-local operational-name 契约
        #   New principle: owner-local operational-name contract:CLAUDE.md 扩写命名不动点为 operational-name invariant + SKILL.md 增 owner map;收窄现有 owner parser/validation(progress.py parse_target 精确文法、safe_worktree 字段校验);behavior test + source-regression production-literal allowlist 防偷抄;**无**生产 OperationalNameRegistry/names.py/check_naming.py/全仓审美 lint
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
        merge = self.gh(["pr", "merge", pr_target, "--admin", "--squash", "--delete-branch"], check=False)
        if merge.stdout:
            print(merge.stdout.splitlines()[-1])
        elif merge.stderr:
            print(merge.stderr.splitlines()[-1])
        if merge.returncode != 0:
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
        base = base or self.integration_branch
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
        created = self.gh(["pr", "create", "--base", base, "--head", head, "--title", title, "--body-file", body_file], check=False)
        output = created.stdout + created.stderr
        match = re.search(r"https://github\.com/[^/]+/[^/]+/pull/([0-9]+)", output)
        if not match:
            self._normalize_lifecycle_target_or_raise("", kind="pr", action="open-pr", source="github-pr-create-url")
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

    def _validate_pr_body_file(self, body_file: str) -> None:
        body_path = Path(body_file)
        if not body_path.is_absolute():
            body_path = self.ctx.repo_root / body_path
        try:
            validate_self_contained_github_body(body_path.read_text(encoding="utf-8"), authority_required=False)
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
        # Refactor (issue174-rollup-throwaway-head):
        # Old pattern: the rollup PR used the shared integration branch as
        # its head, so GitHub merge/delete-branch flows could delete the
        # integration branch itself. New principle: re-check the pending-event
        # SHA, push a controller-owned rollup/<integration_sha> head, and open
        # the PR from that disposable head only.
        try:
            event = json.loads(event_json)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"open_release_rollup_pr_from_pending_event: invalid event json: {exc}") from exc
        if not isinstance(event, dict):
            raise RuntimeError("open_release_rollup_pr_from_pending_event: event must be a JSON object")

        integration_branch = str(event.get("integration_branch") or self.integration_branch).strip()
        review_base_branch = str(event.get("review_base_branch") or self.review_base_branch).strip()
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
        # Refactor (iter201/issue-201): Old pattern: controller marker handling
        # subprocessed consensus-rnd-cli apply-triage, preserving public lifecycle
        # reachability. New principle: direct internal call keeps validation and
        # applied/rejected artifacts without exposing a public lifecycle command.
        match = re.fullmatch(r"TRIAGE_DECISION_DONE:([0-9]+):(accept|reject):(\.refactor-loop/runs/.*\.json)", marker)
        if not match:
            sys.stderr.write("apply_triage_decision_marker: invalid marker\n")
            return 2
        issue, verdict, rel_path = match.groups()
        config = load_triage_apply_config(repo_root=self.ctx.repo_root, env=self.ctx.env_for_subprocess(), cwd=self.ctx.repo_root)
        return apply_decision(config, self.ctx.repo_root / rel_path, issue_number=int(issue), verdict=verdict)

    def render_template(self, input_path: str, output_path: str, env: Mapping[str, str] | None = None) -> None:
        # Refactor (iter219/issue-219):
        #   Old pattern: host 无法按 GitHub 模板自定义事件流/工作流/issue/prompt;workflow vocabulary 是闭集硬编码
        #   New principle: 引入 data-only HostWorkflowSpec(HOST_WORKFLOW_SPEC,repo-relative JSON)+ WorkflowInvariantValidator;空/未设=built-in 行为;host 只能在 host: 命名空间加 data,不能覆盖 built-in/降共识闸/夺 lifecycle authority。严格按 plan 'Concrete plan' 逐条改,首版 scope 受限。
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
        rendered = Template(template).safe_substitute(values)
        Path(output_path).write_text(rendered, encoding="utf-8")

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
        # Refactor (impl/issue191-single-active-controller): Old pattern:
        # controller lifecycle helpers could mutate GitHub/git from any device.
        # New principle: every lifecycle mutation fails closed unless this
        # process owns the singleton active-controller lease.
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
        # Refactor (iter276/issue-276): Old pattern: controller lifecycle
        # targets accepted empty or non-canonical GitHub ids before gh calls.
        # New principle: require canonical positive decimal target ids and
        # record invalid target blocks before lifecycle side effects.
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
        self.ctx.paths.pending_events.parent.mkdir(parents=True, exist_ok=True)
        with self.ctx.paths.pending_events.open("a", encoding="utf-8") as handle:
            handle.write(f"CONTROLLER_ACTION_BLOCKED:invalid-github-target:{action}:{kind}:{source}\n")

    def _single_body_linked_issue_or_block(self, body: str, *, action: str) -> str | None:
        # Refactor (issue-276): Old pattern: body-derived `Closes #...`
        # targets used the read-only projection parser, so malformed body links
        # looked identical to no link and skipped lifecycle target validation.
        # New principle: body-link lifecycle targets fail closed before any
        # gh side effect; absent or ambiguous valid links still mean no issue
        # lifecycle mutation.
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
    # Refactor (impl/issue239-linkage):
    #   Old pattern: controller parsed `Closes #N` with a caller-local regex
    #   while other runtime surfaces used different interpretations.
    #   New principle: use the shared managed-work projection parser and only
    #   mutate a parent issue when there is exactly one durable PR-body link.
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
