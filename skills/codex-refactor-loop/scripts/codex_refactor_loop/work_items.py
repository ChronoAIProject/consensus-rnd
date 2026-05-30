"""Read-only managed work-item projection."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Mapping

from . import labels as label_catalog


@dataclass(frozen=True)
class ManagedItem:
    kind: str
    number: int
    phase: str
    human: str
    labels: tuple[str, ...]
    body: str = ""
    state: str = "open"
    title: str = ""


@dataclass(frozen=True)
class IssuePrLink:
    issue_number: int
    pr_number: int
    source: str = "pr-body-closing-ref"


_CLOSING_ISSUE_RE = re.compile(r"(?im)\bCloses\s+#([0-9]+)\b")


def extract_closing_issue_numbers(body: str) -> tuple[int, ...]:
    seen: set[int] = set()
    numbers: list[int] = []
    for match in _CLOSING_ISSUE_RE.finditer(body or ""):
        number = int(match.group(1))
        if number in seen:
            continue
        seen.add(number)
        numbers.append(number)
    return tuple(numbers)


closing_issue_numbers = extract_closing_issue_numbers


class ManagedWorkProjection:
    # Refactor (impl/issue239-linkage):
    #   Old pattern: controller, concurrency, wakeup, and peek each interpreted
    #   PR body `Closes #N` linkage independently or not at all.
    #   New principle: one read-only projection owns the represented-parent
    #   semantics; lifecycle mutations stay in ControllerActions.
    def __init__(self, items: Iterable[ManagedItem | Mapping[str, object]]) -> None:
        self.items = tuple(_coerce_item(item) for item in items)
        self.links = self._links()

    def represented_issue_numbers(self) -> frozenset[int]:
        return frozenset(link.issue_number for link in self.links if self._open_pr_count_for_issue(link.issue_number) == 1)

    def effective_worker_items(self) -> tuple[ManagedItem, ...]:
        represented = self.represented_issue_numbers()
        return tuple(
            item
            for item in self.items
            if not (item.kind == "issue" and item.state == "open" and item.number in represented)
        )

    def linkage_mismatches(self) -> tuple[str, ...]:
        out: list[str] = []
        open_links_by_issue: dict[int, list[IssuePrLink]] = {}
        merged_links_by_issue: dict[int, list[IssuePrLink]] = {}
        for item in self.items:
            if item.kind != "pr":
                continue
            closes = extract_closing_issue_numbers(item.body)
            if item.state == "open" and _is_managed(item) and _is_action_pr(item) and not closes:
                out.append(f"  ⚠️ PR #{item.number} has no `Closes #N` parent link in body")
            if item.state == "open" and len(closes) > 1:
                joined = ",".join(f"#{number}" for number in closes)
                out.append(f"  ⚠️ PR #{item.number} has multiple `Closes #N` parent links ({joined}); controller will not guess parent")
            for number in closes:
                link = IssuePrLink(issue_number=number, pr_number=item.number)
                if item.state == "merged":
                    merged_links_by_issue.setdefault(number, []).append(link)
                elif item.state == "open":
                    open_links_by_issue.setdefault(number, []).append(link)

        for issue, links in sorted(open_links_by_issue.items()):
            if len(links) > 1:
                prs = ",".join(f"#{link.pr_number}" for link in links)
                out.append(f"  ⚠️ issue #{issue} is closed by multiple open managed PRs ({prs})")

        for item in sorted(self.items, key=lambda value: (value.kind, value.number)):
            if item.kind != "issue" or item.state != "open":
                continue
            if item.phase != label_catalog.PHASE_IMPLEMENTING:
                continue
            open_links = open_links_by_issue.get(item.number, [])
            merged_links = merged_links_by_issue.get(item.number, [])
            if open_links:
                continue
            if merged_links:
                out.append(
                    f"  ⚠️ issue #{item.number} [{label_catalog.PHASE_IMPLEMENTING}] PR #{merged_links[0].pr_number} is merged but issue is still open - controller should gh issue close"
                )
            else:
                out.append(
                    f"  ⚠️ issue #{item.number} [{label_catalog.PHASE_IMPLEMENTING}] has no matching in-flight or merged PR body `Closes #N` link"
                )
        return tuple(out)

    def _links(self) -> tuple[IssuePrLink, ...]:
        links: list[IssuePrLink] = []
        for item in self.items:
            if item.kind != "pr" or item.state not in {"open", "merged"} or not _is_managed(item):
                continue
            closes = extract_closing_issue_numbers(item.body)
            if item.state == "open" and len(closes) != 1:
                continue
            links.extend(IssuePrLink(issue_number=number, pr_number=item.number) for number in closes)
        return tuple(links)

    def _open_pr_count_for_issue(self, issue_number: int) -> int:
        return sum(
            1
            for item in self.items
            if item.kind == "pr"
            and item.state == "open"
            and _is_managed(item)
            and extract_closing_issue_numbers(item.body) == (issue_number,)
        )


def represented_issue_numbers(items: Iterable[ManagedItem | Mapping[str, object]]) -> frozenset[int]:
    return ManagedWorkProjection(items).represented_issue_numbers()


def effective_worker_items(items: Iterable[ManagedItem | Mapping[str, object]]) -> tuple[ManagedItem, ...]:
    return ManagedWorkProjection(items).effective_worker_items()


def linkage_mismatches(items: Iterable[ManagedItem | Mapping[str, object]]) -> tuple[str, ...]:
    return ManagedWorkProjection(items).linkage_mismatches()


def _coerce_item(item: ManagedItem | Mapping[str, object]) -> ManagedItem:
    if isinstance(item, ManagedItem):
        return item
    labels = tuple(str(label) for label in item.get("labels", ()) if str(label))
    projection = label_catalog.normalize_label_set(labels)
    phase = str(item.get("phase") or projection.phase or "")
    human = str(item.get("human") or projection.human or "")
    return ManagedItem(
        kind=_normalize_kind(str(item.get("kind") or "")),
        number=int(item.get("number") or 0),
        phase=label_catalog.normalize_label_set([phase]).phase or phase,
        human=label_catalog.normalize_label_set([human]).human or human,
        labels=labels,
        body=str(item.get("body") or ""),
        state=_normalize_state(str(item.get("state") or "open")),
        title=str(item.get("title") or ""),
    )


def _normalize_kind(kind: str) -> str:
    lowered = kind.lower()
    return "pr" if lowered in {"pr", "pull_request", "pull request"} else "issue"


def _normalize_state(state: str) -> str:
    lowered = state.lower()
    return "merged" if lowered == "merged" else "open" if lowered in {"", "open"} else lowered


def _is_managed(item: ManagedItem) -> bool:
    return label_catalog.MANAGED in label_catalog.normalize_label_set(item.labels).canonical


def _is_action_pr(item: ManagedItem) -> bool:
    return item.phase in {
        label_catalog.PHASE_PR_OPEN,
        label_catalog.PHASE_REVIEWING,
        label_catalog.PHASE_FIXING,
        label_catalog.PHASE_CI_RUNNING,
    }
