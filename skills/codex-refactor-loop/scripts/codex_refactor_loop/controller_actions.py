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

from . import labels
from .context import LoopContext
from .github_body import GitHubBodyError, validate_self_contained_github_body
from .triage import apply_decision, load_triage_apply_config


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


class ControllerActions:
    # Refactor (iter201/issue-201): Old pattern: public consensus-rnd-cli exposed
    # merge/open/safe-push/apply lifecycle commands as generic callable verbs.
    # New principle: keep these as controller-internal primitives only; callers
    # construct ControllerActions directly and public CLI routing cannot reach them.
    def __init__(self, ctx: LoopContext) -> None:
        self.ctx = ctx
        self.integration_branch = os.environ.get("INTEGRATION_BRANCH") or os.environ.get("INTEGRATION") or "auto-refact-dev"
        self.review_base_branch = os.environ.get("REVIEW_BASE_BRANCH") or os.environ.get("REVIEW_BASE") or "dev"

    def gh(self, args: Sequence[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
        full = ["gh", *args]
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

        directive_dir = self.ctx.paths.runs / "maintainer-directives"
        if directive_dir.is_dir():
            target = pr_number.lstrip("#")
            pr_pattern = re.compile(rf"(^|[^0-9])(PR[ -]?)?#?{re.escape(target)}([^0-9]|$)")
            reason_pattern = (
                re.compile(rf"(^|[^A-Za-z0-9_-]){re.escape(reason)}([^A-Za-z0-9_-]|$)") if reason else None
            )
            for path in directive_dir.glob("*.md"):
                try:
                    text = path.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
                if pr_pattern.search(text) or (reason_pattern and reason_pattern.search(text)):
                    print("skip-label: maintainer-directive already covers this; see .refactor-loop/runs/maintainer-directives/")
                    return 1

        result = self.gh(["pr", "edit", pr_number, "--add-label", labels.HUMAN_MAINTAINER_DECISION], check=False)
        return result.returncode

    def _current_branch(self) -> str:
        result = self.git(["rev-parse", "--abbrev-ref", "HEAD"], check=False)
        return result.stdout.strip() if result.returncode == 0 else ""

    def safe_push(self, remote: str = "origin", branch: str = "") -> int:
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

    def safe_sync_main(self, remote: str = "origin", branch: str = "") -> int:
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
        if not pr:
            sys.stderr.write("merge_pr: missing pr number\n")
            return 1
        if not linked_issue:
            body = self.gh(["pr", "view", pr, "--json", "body", "--jq", ".body"], check=False).stdout
            match = re.search(r"Closes #([0-9]+)", body)
            linked_issue = match.group(1) if match else ""
        merge = self.gh(["pr", "merge", pr, "--admin", "--squash", "--delete-branch"], check=False)
        if merge.stdout:
            print(merge.stdout.splitlines()[-1])
        elif merge.stderr:
            print(merge.stderr.splitlines()[-1])
        if merge.returncode != 0:
            return merge.returncode
        self.record_recent_pr_merge(pr)
        args = ["pr", "edit", pr]
        for label in PR_LABELS_REMOVE:
            args.extend(["--remove-label", label])
        args.extend(["--add-label", labels.PHASE_MERGED])
        self.gh(args, check=False)
        if linked_issue:
            comment = f"✅ Auto-merged via PR #{pr}.\n\n⟦AI:AUTO-LOOP⟧"
            close = self.gh(["issue", "close", linked_issue, "--reason", "completed", "--comment", comment], check=False)
            if close.stdout:
                print(close.stdout.splitlines()[-1])
            args = ["issue", "edit", linked_issue]
            for label in ISSUE_LABELS_REMOVE:
                args.extend(["--remove-label", label])
            args.extend(["--add-label", labels.PHASE_MERGED])
            self.gh(args, check=False)
        head = self.gh(["pr", "view", pr, "--json", "headRefName", "--jq", ".headRefName"], check=False).stdout.strip()
        if head:
            wt = self._worktree_for_branch(head)
            if wt and wt != self.ctx.repo_root:
                self.git(["worktree", "remove", str(wt), "--force"], check=False)
        return 0

    def open_pr_with_label(self, title: str, body_file: str, base: str | None = None, head: str = "") -> tuple[int, str]:
        base = base or self.integration_branch
        if not head:
            raise RuntimeError("open_pr_with_label: head branch required (avoid gh fallback to current branch = base)")
        self._validate_pr_body_file(body_file)
        created = self.gh(["pr", "create", "--base", base, "--head", head, "--title", title, "--body-file", body_file], check=False)
        output = created.stdout + created.stderr
        match = re.search(r"https://github\.com/[^/]+/[^/]+/pull/([0-9]+)", output)
        if not match:
            raise RuntimeError(f"open_pr_with_label: failed to extract PR num from: {output.strip()}")
        pr_num = int(match.group(1))
        self.gh(
            [
                "pr",
                "edit",
                str(pr_num),
                "--add-label",
                ",".join((labels.MANAGED, labels.PHASE_REVIEWING, labels.HUMAN_AUTO)),
            ],
            check=False,
        )
        return pr_num, match.group(0)

    def _validate_pr_body_file(self, body_file: str) -> None:
        body_path = Path(body_file)
        if not body_path.is_absolute():
            body_path = self.ctx.repo_root / body_path
        try:
            validate_self_contained_github_body(body_path.read_text(encoding="utf-8"), authority_required=False)
        except GitHubBodyError as exc:
            raise RuntimeError(str(exc)) from exc

    def open_release_rollup_pr_from_pending_event(
        self,
        event_json: str,
        body_file: str,
        title: str = "Release rollup",
    ) -> tuple[int, str]:
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
        fact_json = ""
        for attempt in range(3):
            result = self.gh(["pr", "view", pr, "--json", "number,mergedAt,mergeCommit,baseRefName,headRefName"], check=False)
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
        pr_num = facts.get("number") or pr
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
        template = Path(input_path).read_text(encoding="utf-8")
        for key, value in aliases.items():
            template = template.replace("{{" + key + "}}", value)
        rendered = Template(template).safe_substitute(values)
        Path(output_path).write_text(rendered, encoding="utf-8")

    def _worktree_for_branch(self, branch: str) -> Path | None:
        result = self.git(["worktree", "list", "--porcelain"], check=False)
        current: Path | None = None
        for line in result.stdout.splitlines():
            if line.startswith("worktree "):
                current = Path(line.removeprefix("worktree "))
            elif line == f"branch refs/heads/{branch}" and current:
                return current
        return None


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
